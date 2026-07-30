import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

/// Turn-by-turn model + progress math for the low-stress bike router.
///
/// The API (`/map-layers/route`) returns a LineString plus a `steps` list of
/// maneuvers; everything here is about answering "where on that line am I, and
/// what do I do next?" from a stream of GPS fixes.

const _mPerDegLat = 111320.0;

double metersBetween(LatLng a, LatLng b) {
  final cosLat = math.cos(a.latitude * math.pi / 180.0);
  final dx = (b.longitude - a.longitude) * _mPerDegLat * cosLat;
  final dy = (b.latitude - a.latitude) * _mPerDegLat;
  return math.sqrt(dx * dx + dy * dy);
}

/// Compass bearing (degrees) from [a] to [b].
double bearingBetween(LatLng a, LatLng b) {
  final lat1 = a.latitude * math.pi / 180.0;
  final lat2 = b.latitude * math.pi / 180.0;
  final dLon = (b.longitude - a.longitude) * math.pi / 180.0;
  final y = math.sin(dLon) * math.cos(lat2);
  final x = math.cos(lat1) * math.sin(lat2) -
      math.sin(lat1) * math.cos(lat2) * math.cos(dLon);
  return (math.atan2(y, x) * 180.0 / math.pi + 360.0) % 360.0;
}

/// One maneuver from the API's `steps` array.
class RouteStep {
  final String maneuver;
  final String instruction;
  final String? name;
  final double distanceM;
  final int startIndex;

  RouteStep({
    required this.maneuver,
    required this.instruction,
    required this.name,
    required this.distanceM,
    required this.startIndex,
  });

  factory RouteStep.fromJson(Map<String, dynamic> j) => RouteStep(
        maneuver: (j['maneuver'] ?? 'straight').toString(),
        instruction: (j['instruction'] ?? 'Continue').toString(),
        name: j['name']?.toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        startIndex: (j['start_index'] as num?)?.toInt() ?? 0,
      );

  IconData get icon {
    switch (maneuver) {
      case 'left':
        return Icons.turn_left;
      case 'right':
        return Icons.turn_right;
      case 'slight-left':
        return Icons.turn_slight_left;
      case 'slight-right':
        return Icons.turn_slight_right;
      case 'sharp-left':
        return Icons.turn_sharp_left;
      case 'sharp-right':
        return Icons.turn_sharp_right;
      case 'uturn':
        return Icons.u_turn_left;
      case 'depart':
        return Icons.navigation;
      case 'arrive':
        return Icons.sports_score;
      default:
        return Icons.straight;
    }
  }
}

/// A computed route: the line, its steps and the cumulative distance table
/// used to place the rider along it.
class NavRoute {
  final List<LatLng> points;
  final List<RouteStep> steps;
  final List<double> cumulative; // meters from start to points[i]
  final double distanceM;
  final double durationMin;

  NavRoute._(this.points, this.steps, this.cumulative, this.distanceM,
      this.durationMin);

  factory NavRoute.fromFeature(Map<String, dynamic> feature) {
    final coords = (feature['geometry']?['coordinates'] as List? ?? [])
        .map<LatLng>((c) => LatLng(
              (c[1] as num).toDouble(),
              (c[0] as num).toDouble(),
            ))
        .toList();
    final props = Map<String, dynamic>.from(feature['properties'] ?? {});
    final steps = (props['steps'] as List? ?? [])
        .map<RouteStep>((s) => RouteStep.fromJson(Map<String, dynamic>.from(s)))
        .toList();
    final cumulative = <double>[0.0];
    for (var i = 1; i < coords.length; i++) {
      cumulative.add(cumulative[i - 1] + metersBetween(coords[i - 1], coords[i]));
    }
    return NavRoute._(
      coords,
      steps,
      cumulative,
      (props['distance_m'] as num?)?.toDouble() ??
          (cumulative.isEmpty ? 0.0 : cumulative.last),
      (props['duration_min'] as num?)?.toDouble() ?? 0.0,
    );
  }

  bool get isEmpty => points.length < 2;

  LatLng get destination => points.last;
}

/// Where the rider is relative to [NavRoute] right now.
class NavProgress {
  final int segmentIndex;
  final LatLng snapped;
  final double offRouteM;
  final double traveledM;
  final double remainingM;
  final int stepIndex;
  final double distanceToManeuverM;
  final double courseBearing;

  const NavProgress({
    required this.segmentIndex,
    required this.snapped,
    required this.offRouteM,
    required this.traveledM,
    required this.remainingM,
    required this.stepIndex,
    required this.distanceToManeuverM,
    required this.courseBearing,
  });

  static NavProgress? of(NavRoute route, LatLng at) {
    if (route.isEmpty) return null;

    var bestIdx = 0;
    var bestDist = double.infinity;
    var bestT = 0.0;
    for (var i = 0; i < route.points.length - 1; i++) {
      final r = _project(at, route.points[i], route.points[i + 1]);
      if (r.$2 < bestDist) {
        bestDist = r.$2;
        bestIdx = i;
        bestT = r.$1;
      }
    }

    final a = route.points[bestIdx];
    final b = route.points[bestIdx + 1];
    final snapped = LatLng(
      a.latitude + (b.latitude - a.latitude) * bestT,
      a.longitude + (b.longitude - a.longitude) * bestT,
    );
    final traveled =
        route.cumulative[bestIdx] + metersBetween(a, snapped);

    // The step in progress is the last one that started at or before us.
    var stepIndex = 0;
    for (var i = 0; i < route.steps.length; i++) {
      if (route.steps[i].startIndex <= bestIdx) {
        stepIndex = i;
      } else {
        break;
      }
    }
    final nextIndex = math.min(stepIndex + 1, route.steps.length - 1);
    final maneuverAt = route.steps[nextIndex].startIndex
        .clamp(0, route.cumulative.length - 1);

    return NavProgress(
      segmentIndex: bestIdx,
      snapped: snapped,
      offRouteM: bestDist,
      traveledM: traveled,
      remainingM: math.max(route.distanceM - traveled, 0.0),
      stepIndex: stepIndex,
      distanceToManeuverM:
          math.max(route.cumulative[maneuverAt] - traveled, 0.0),
      courseBearing: bearingBetween(a, b),
    );
  }

  /// (t along ab, distance in meters from p to the segment)
  static (double, double) _project(LatLng p, LatLng a, LatLng b) {
    final cosLat = math.cos(a.latitude * math.pi / 180.0);
    final ax = a.longitude * cosLat, ay = a.latitude;
    final bx = b.longitude * cosLat, by = b.latitude;
    final px = p.longitude * cosLat, py = p.latitude;
    final dx = bx - ax, dy = by - ay;
    final denom = dx * dx + dy * dy;
    var t = denom == 0 ? 0.0 : ((px - ax) * dx + (py - ay) * dy) / denom;
    t = t.clamp(0.0, 1.0);
    final cx = ax + dx * t, cy = ay + dy * t;
    final dist = math.sqrt(
          math.pow((px - cx) * _mPerDegLat, 2) +
              math.pow((py - cy) * _mPerDegLat, 2),
        );
    return (t, dist);
  }
}

/// "400 ft" / "0.6 mi" — imperial, because Greenville.
String formatDistance(double meters) {
  final feet = meters * 3.28084;
  if (feet < 1000) {
    final rounded = feet < 100 ? (feet / 10).round() * 10 : (feet / 50).round() * 50;
    return '$rounded ft';
  }
  final miles = meters / 1609.344;
  return '${miles.toStringAsFixed(miles < 10 ? 1 : 0)} mi';
}

String formatDuration(double minutes) {
  if (minutes < 1) return '<1 min';
  if (minutes < 60) return '${minutes.round()} min';
  final h = minutes ~/ 60;
  final m = (minutes % 60).round();
  return m == 0 ? '$h hr' : '$h hr $m min';
}
