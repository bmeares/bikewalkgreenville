import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

/// Turn-by-turn model + progress math for the bike / walk / transit router.
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

  /// `no_sidewalk` / `no_bike_lane` when this step travels on a street missing
  /// the infrastructure the mode needs, else null.
  final String? warn;

  /// How much of [distanceM] is missing that infrastructure.
  final double warnM;

  /// Feet of ascent within this step (0 when flat or unknown).
  final int climbFt;

  RouteStep({
    required this.maneuver,
    required this.instruction,
    required this.name,
    required this.distanceM,
    required this.startIndex,
    this.warn,
    this.warnM = 0.0,
    this.climbFt = 0,
  });

  factory RouteStep.fromJson(Map<String, dynamic> j) => RouteStep(
        maneuver: (j['maneuver'] ?? 'straight').toString(),
        instruction: (j['instruction'] ?? 'Continue').toString(),
        name: j['name']?.toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        startIndex: (j['start_index'] as num?)?.toInt() ?? 0,
        warn: j['warn']?.toString(),
        warnM: (j['warn_m'] as num?)?.toDouble() ?? 0.0,
        climbFt: (j['climb_ft'] as num?)?.round() ?? 0,
      );

  /// A climb worth flagging on this step: steeper than ~8% (ADA's 1:12) over
  /// more than sampling noise (8 ft — mirrors the server's noise gate).
  bool get isSteepClimb =>
      climbFt >= 8 &&
      distanceM > 0 &&
      (climbFt / 3.28084) / distanceM > 0.08;

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
      case 'board':
        return Icons.departure_board;
      case 'ride':
        return Icons.directions_bus;
      case 'alight':
        return Icons.pin_drop;
      case 'rent':
        return Icons.pedal_bike;
      case 'dock':
        return Icons.lock_outline;
      default:
        return Icons.straight;
    }
  }
}

/// A stretch of the route that lacks the infrastructure the mode needs, as an
/// index range into [NavRoute.points].
class WarnRange {
  final String kind; // no_sidewalk | no_bike_lane
  final int start;
  final int end;
  final double distanceM;

  const WarnRange({
    required this.kind,
    required this.start,
    required this.end,
    required this.distanceM,
  });

  factory WarnRange.fromJson(Map<String, dynamic> j) => WarnRange(
        kind: (j['kind'] ?? 'unknown').toString(),
        start: (j['start'] as num?)?.toInt() ?? 0,
        end: (j['end'] as num?)?.toInt() ?? 0,
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
      );
}

/// One kind of gap, totalled across the route — what the banner says.
class RouteWarning {
  final String kind;
  final double distanceM;
  final String label; // "0.4 mi with no sidewalk"
  final String message; // full sentence for the banner / detail sheet

  const RouteWarning({
    required this.kind,
    required this.distanceM,
    required this.label,
    required this.message,
  });

  factory RouteWarning.fromJson(Map<String, dynamic> j) => RouteWarning(
        kind: (j['kind'] ?? 'unknown').toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        label: (j['label'] ?? '').toString(),
        message: (j['message'] ?? '').toString(),
      );

  IconData get icon => warnIcons[kind] ?? Icons.warning_amber_rounded;
}

/// Per-warning presentation. Keyed by the server's `kind`, so a kind the app
/// has not been taught about falls back to something honest rather than
/// borrowing another warning's wording -- a `steep` grade used to be announced
/// as a missing bike lane.
const warnIcons = <String, IconData>{
  'no_sidewalk': Icons.no_transfer_outlined,
  'no_bike_lane': Icons.directions_bike_outlined,
  'steep': Icons.trending_up,
};

/// Short phrase for a per-step warning; `{d}` worth of distance is prepended
/// by the caller.
const warnStepPhrases = <String, String>{
  'no_sidewalk': 'with no sidewalk',
  'no_bike_lane': 'with no bike lane',
  'steep': 'of steep grade',
};

/// Sentence for the maneuver card, mid-turn.
const warnStepSentences = <String, String>{
  'no_sidewalk': 'No sidewalk mapped on this stretch',
  'no_bike_lane': 'No bike lane on this stretch',
  'steep': 'Steep grade on this stretch',
};

String warnStepPhrase(String? kind) =>
    warnStepPhrases[kind] ?? 'flagged by the router';

String warnStepSentence(String? kind) =>
    warnStepSentences[kind] ?? 'This stretch is flagged by the router';

/// A different way to make the same trip, priced by the router.
class RouteAlternative {
  final String plan; // bike | walk | roll | bcycle | bike-transit | ...
  final String label;
  final String iconMode;
  final double distanceM;
  final double durationMin;
  final List<RouteWarning> warnings;

  const RouteAlternative({
    required this.plan,
    required this.label,
    required this.iconMode,
    required this.distanceM,
    required this.durationMin,
    required this.warnings,
  });

  factory RouteAlternative.fromJson(Map<String, dynamic> j) =>
      RouteAlternative(
        plan: (j['plan'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
        iconMode: (j['icon_mode'] ?? '').toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        durationMin: (j['duration_min'] as num?)?.toDouble() ?? 0.0,
        warnings: ((j['warnings'] as List?) ?? [])
            .map((w) => RouteWarning.fromJson(Map<String, dynamic>.from(w)))
            .toList(),
      );
}

/// A computed route: the line, its steps and the cumulative distance table
/// used to place the rider along it.
class NavRoute {
  final List<LatLng> points;
  final List<RouteStep> steps;
  final List<double> cumulative; // meters from start to points[i]
  final double distanceM;
  final double durationMin;
  /// Total ascent over the trip, in feet. 0 when the router had no elevation.
  final int climbFt;

  /// [distance_from_start_m, elevation_ft] samples for the preview sparkline,
  /// or null when the router had no elevation for this trip.
  final List<List<double>>? elevationProfile;
  /// The rider said they are on an e-bike, so the pace and hill cost reflect it.
  final bool ebike;
  final String mode; // bike | walk | roll | transit | bcycle
  final String? transitRoute; // Greenlink short name, transit only
  final String? boardStop;
  final String? routeColor; // official route color, transit only

  /// Router plan key (`bike`, `bike-transit`, `bcycle`, …) and its label.
  final String plan;
  final String planLabel;

  /// Stretches missing the infrastructure this mode needs, plus the per-kind
  /// totals the banner reads from.
  final List<WarnRange> warnRanges;
  final List<RouteWarning> warnings;

  /// Set when the mode's own network couldn't get there and the route fell back
  /// to plain streets; [fallbackNote] is the sentence to show.
  final String? fallback;
  final String? fallbackNote;

  /// Other itineraries the router costed for the same trip.
  final List<RouteAlternative> alternatives;

  /// Bike-share specifics, when [plan] is `bcycle`.
  final String? rentStation;
  final String? dockStation;
  final String? rentStationUri;

  NavRoute._({
    required this.points,
    required this.steps,
    required this.cumulative,
    required this.distanceM,
    required this.durationMin,
    required this.climbFt,
    required this.elevationProfile,
    required this.ebike,
    required this.mode,
    required this.transitRoute,
    required this.boardStop,
    required this.routeColor,
    required this.plan,
    required this.planLabel,
    required this.warnRanges,
    required this.warnings,
    required this.fallback,
    required this.fallbackNote,
    required this.alternatives,
    required this.rentStation,
    required this.dockStation,
    required this.rentStationUri,
  });

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
    final mode = (props['mode'] ?? 'bike').toString();
    return NavRoute._(
      points: coords,
      steps: steps,
      cumulative: cumulative,
      distanceM: (props['distance_m'] as num?)?.toDouble() ??
          (cumulative.isEmpty ? 0.0 : cumulative.last),
      durationMin: (props['duration_min'] as num?)?.toDouble() ?? 0.0,
      climbFt: (props['climb_ft'] as num?)?.round() ?? 0,
      elevationProfile: (props['elevation_profile'] is List &&
              (props['elevation_profile'] as List).length >= 2)
          ? [
              for (final p in props['elevation_profile'] as List)
                [(p[0] as num).toDouble(), (p[1] as num).toDouble()],
            ]
          : null,
      ebike: props['ebike'] == true,
      mode: mode,
      transitRoute: props['route']?.toString(),
      boardStop: props['board_stop']?.toString(),
      routeColor: props['route_color']?.toString(),
      plan: (props['plan'] ?? mode).toString(),
      planLabel: (props['plan_label'] ?? '').toString(),
      warnRanges: ((props['warn_ranges'] as List?) ?? [])
          .map((r) => WarnRange.fromJson(Map<String, dynamic>.from(r)))
          .toList(),
      warnings: ((props['warnings'] as List?) ?? [])
          .map((w) => RouteWarning.fromJson(Map<String, dynamic>.from(w)))
          .toList(),
      fallback: props['fallback']?.toString(),
      fallbackNote: props['fallback_note']?.toString(),
      alternatives: ((props['alternatives'] as List?) ?? [])
          .map((a) => RouteAlternative.fromJson(Map<String, dynamic>.from(a)))
          .toList(),
      rentStation: props['rent_station']?.toString(),
      dockStation: props['dock_station']?.toString(),
      rentStationUri: props['rent_station_uri']?.toString(),
    );
  }

  bool get isEmpty => points.length < 2;

  LatLng get destination => points.last;

  bool get isTransit => mode == 'transit';

  /// GeoJSON for the "watch out here" overlay: one LineString per gap, drawn
  /// dashed on top of the route so a rider can see before starting exactly
  /// which blocks have no sidewalk or no bike lane.
  Map<String, dynamic> warnCollection() => {
        'type': 'FeatureCollection',
        'features': [
          for (final r in warnRanges)
            if (r.end > r.start && r.start >= 0 && r.end < points.length)
              {
                'type': 'Feature',
                'geometry': {
                  'type': 'LineString',
                  'coordinates': [
                    for (final p in points.sublist(r.start, r.end + 1))
                      [p.longitude, p.latitude],
                  ],
                },
                'properties': {'kind': r.kind},
              },
        ],
      };

  /// GeoJSON for the maneuver markers: one rotated chevron per turn, so the
  /// upcoming turns are visible on the map itself and not only in the card.
  /// [fromStep] skips maneuvers already behind the rider.
  Map<String, dynamic> stepCollection({int fromStep = 0}) => {
        'type': 'FeatureCollection',
        'features': [
          for (var i = fromStep; i < steps.length; i++)
            if (_isTurn(steps[i]) && steps[i].startIndex < points.length)
              {
                'type': 'Feature',
                'geometry': {
                  'type': 'Point',
                  'coordinates': [
                    points[steps[i].startIndex].longitude,
                    points[steps[i].startIndex].latitude,
                  ],
                },
                'properties': {
                  'bearing': _bearingAt(steps[i].startIndex),
                  'first': i == fromStep,
                },
              },
        ],
      };

  static bool _isTurn(RouteStep s) => const {
        'left',
        'right',
        'slight-left',
        'slight-right',
        'sharp-left',
        'sharp-right',
        'uturn',
      }.contains(s.maneuver);

  /// Heading of the route as it leaves [index] — the chevron's rotation.
  double _bearingAt(int index) {
    if (points.length < 2) return 0.0;
    final i = index.clamp(0, points.length - 2);
    return bearingBetween(points[i], points[i + 1]);
  }
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
