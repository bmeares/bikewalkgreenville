import 'dart:math' as math;
import 'package:maplibre_gl/maplibre_gl.dart';

/// Editable, unclosed vertices. GeoJSON closes polygon rings only on export.
class GeometryDraft {
  static const maxPoints = 200;
  final bool polygon;
  List<LatLng> points;
  final List<List<LatLng>> _undo = [], _redo = [];

  GeometryDraft(Iterable<LatLng> points, {this.polygon = false})
    : points = List.of(points) {
    if (polygon &&
        this.points.length > 1 &&
        this.points.first == this.points.last) {
      this.points.removeLast();
    }
  }

  bool get canPublish => points.length >= (polygon ? 3 : 2);
  bool get canUndo => _undo.isNotEmpty;
  bool get canRedo => _redo.isNotEmpty;
  void checkpoint() {
    _undo.add(List.of(points));
    if (_undo.length > 60) _undo.removeAt(0);
    _redo.clear();
  }

  void undo() {
    if (!canUndo) return;
    _redo.add(List.of(points));
    points = _undo.removeLast();
  }

  void redo() {
    if (!canRedo) return;
    _undo.add(List.of(points));
    points = _redo.removeLast();
  }

  void insert(int index, LatLng point) {
    if (points.length >= maxPoints) {
      throw StateError('Use at most 200 vertices.');
    }
    points.insert(index.clamp(0, points.length), point);
  }

  void extend(LatLng point, {bool fromStart = false}) =>
      insert(fromStart ? 0 : points.length, point);

  /// Replace one segment with a quadratic curve through a chosen control point.
  /// Samples remain ordinary editable vertices on reopening a published path.
  void curve(int segment, LatLng control) {
    final end = (segment + 1) % points.length;
    if (segment < 0 || segment >= points.length || (!polygon && end == 0)) {
      return;
    }
    const samples = 12;
    if (points.length + samples - 1 > maxPoints) {
      throw StateError('Remove some vertices before adding a curve.');
    }
    final a = points[segment], b = points[end];
    final curved = <LatLng>[];
    for (var i = 1; i < samples; i++) {
      final t = i / samples, u = 1 - t;
      curved.add(
        LatLng(
          u * u * a.latitude +
              2 * u * t * control.latitude +
              t * t * b.latitude,
          u * u * a.longitude +
              2 * u * t * control.longitude +
              t * t * b.longitude,
        ),
      );
    }
    points.insertAll(segment + 1, curved);
  }

  void addStroke(List<LatLng> stroke, {bool fromStart = false}) {
    if (stroke.isEmpty) return;
    var reduced = simplifyStroke(stroke, 1.5);
    if (points.isNotEmpty) {
      final join = fromStart ? points.first : points.last;
      // A new stroke usually starts on the endpoint. Keep one numbered handle.
      while (reduced.isNotEmpty &&
          (reduced.first.latitude - join.latitude).abs() < .000001 &&
          (reduced.first.longitude - join.longitude).abs() < .000001) {
        reduced.removeAt(0);
      }
    }
    final available = maxPoints - points.length;
    // Never silently truncate a stroke and publish a different endpoint.
    if (reduced.length > available) {
      throw StateError(
        'This stroke exceeds 200 vertices. Undo or draw a shorter section.',
      );
    }
    if (fromStart) reduced = reduced.reversed.toList();
    points.insertAll(fromStart ? 0 : points.length, reduced);
  }

  Map<String, dynamic> get geometry {
    final coords = points.map((p) => [p.longitude, p.latitude]).toList();
    return polygon
        ? {
            'type': 'Polygon',
            'coordinates': [
              [...coords, if (coords.isNotEmpty) coords.first],
            ],
          }
        : {'type': 'LineString', 'coordinates': coords};
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
