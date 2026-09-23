import 'dart:math' as math;
import 'package:maplibre_gl/maplibre_gl.dart';

import 'api.dart';
import 'nav.dart';

/// Tap-the-corners polygon for a no-entry area. GeoJSON closes the ring only
/// on export.
class AreaDraft {
  static const maxPoints = 200;
  final List<LatLng> corners;
  final String? name, comment, replaces;

  AreaDraft({
    Iterable<LatLng> corners = const [],
    this.name,
    this.comment,
    this.replaces,
  }) : corners = List.of(corners) {
    if (this.corners.length > 1 && this.corners.first == this.corners.last) {
      this.corners.removeLast();
    }
  }

  bool get canPublish => corners.length >= 3;
  bool add(LatLng p) {
    if (corners.length >= maxPoints) return false;
    corners.add(p);
    return true;
  }

  void undo() {
    if (corners.isNotEmpty) corners.removeLast();
  }

  Map<String, dynamic> get geometry {
    final coords = corners.map((p) => [p.longitude, p.latitude]).toList();
    return {
      'type': 'Polygon',
      'coordinates': [
        [...coords, if (coords.isNotEmpty) coords.first],
      ],
    };
  }
}

/// A route drawn by tapping waypoints. Each leg between consecutive
/// waypoints is routed along the network via `/route`, or drawn straight
/// when the rider asks for it (paths the map does not know) or the router
/// fails.
class RouteDraft {
  final List<LatLng> waypoints = [];

  /// legs[i] joins waypoints[i] to waypoints[i + 1], endpoints included.
  final List<List<LatLng>> legs = [];
  final String? name, comment, category, replaces;
  final Set<String> modes;
  final String? stress;

  RouteDraft({
    this.modes = const {'bike'},
    this.stress,
    this.name,
    this.comment,
    this.category,
    this.replaces,
  });

  /// Reopen an existing line: its vertices become waypoints with straight legs.
  RouteDraft.seeded(
    List<LatLng> vertices, {
    this.modes = const {'bike'},
    this.stress,
    this.name,
    this.comment,
    this.category,
    this.replaces,
  }) {
    for (final p in vertices) {
      if (waypoints.isNotEmpty) legs.add([waypoints.last, p]);
      waypoints.add(p);
    }
  }

  bool get canPublish => line.length >= 2;

  /// Append a waypoint. Returns true when the leg fell back to a straight
  /// line because the router failed (so the caller can say so).
  Future<bool> add(LatLng p, {bool straight = false}) async {
    if (waypoints.isEmpty) {
      waypoints.add(p);
      return false;
    }
    final from = waypoints.last;
    var leg = [from, p];
    var fellBack = false;
    if (!straight) {
      try {
        final routed = NavRoute.fromFeature(
          await api.route(
            from.latitude,
            from.longitude,
            p.latitude,
            p.longitude,
            modes: modes,
            stress: stress,
          ),
        ).points;
        if (routed.length >= 2) {
          // Pin the leg to the tapped points so undo/redo stays exact.
          leg = [from, ...routed, p];
        } else {
          fellBack = true;
        }
      } catch (_) {
        fellBack = true;
      }
    }
    waypoints.add(p);
    legs.add(leg);
    return fellBack;
  }

  void undo() {
    if (waypoints.isEmpty) return;
    waypoints.removeLast();
    if (legs.isNotEmpty) legs.removeLast();
  }

  /// All legs joined, without repeating the shared waypoint between legs.
  List<LatLng> get line {
    if (legs.isEmpty) return List.of(waypoints);
    final out = <LatLng>[];
    for (final leg in legs) {
      for (final p in leg) {
        if (out.isEmpty || out.last != p) out.add(p);
      }
    }
    return out;
  }
}

/// Ramer–Douglas–Peucker in local meters. Keeps both stroke endpoints.
List<LatLng> simplifyStroke(List<LatLng> points, double toleranceM) {
  if (points.length < 3) return List.of(points);
  final a = points.first, b = points.last;
  final mx = 111320 * math.cos(a.latitude * math.pi / 180);
  final dx = (b.longitude - a.longitude) * mx;
  final dy = (b.latitude - a.latitude) * 111320;
  final l2 = dx * dx + dy * dy;
  var farthest = -1.0, index = 0;
  for (var i = 1; i < points.length - 1; i++) {
    final x = (points[i].longitude - a.longitude) * mx;
    final y = (points[i].latitude - a.latitude) * 111320;
    final t = l2 == 0 ? 0.0 : ((x * dx + y * dy) / l2).clamp(0.0, 1.0);
    final distance = math.sqrt(
      math.pow(x - t * dx, 2) + math.pow(y - t * dy, 2),
    );
    if (distance > farthest) {
      farthest = distance;
      index = i;
    }
  }
  if (farthest <= toleranceM) return [a, b];
  return [
    ...simplifyStroke(points.sublist(0, index + 1), toleranceM)..removeLast(),
    ...simplifyStroke(points.sublist(index), toleranceM),
  ];
}
