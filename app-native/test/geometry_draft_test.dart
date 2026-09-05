import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:bwg_app_native/geometry_draft.dart';

void main() {
  const a = LatLng(34.85, -82.4),
      b = LatLng(34.85, -82.399),
      c = LatLng(34.851, -82.399);
  test('extend both ends, move, delete and undo/redo retain vertex order', () {
    final d = GeometryDraft([a, b]);
    d.checkpoint();
    d.extend(c, fromStart: true);
    expect(d.points, [c, a, b]);
    d.checkpoint();
    d.points[1] = c;
    d.points.removeLast();
    expect(d.points, [c, c]);
    d.undo();
    expect(d.points, [c, a, b]);
    d.undo();
    expect(d.points, [a, b]);
    d.redo();
    expect(d.points, [c, a, b]);
  });
  test('polygon closes only on export and reopening preserves indices', () {
    final d = GeometryDraft([a, b, c], polygon: true);
    expect(d.canPublish, isTrue);
    final coordinates = d.geometry['coordinates'][0] as List;
    expect(coordinates.length, 4);
    expect(coordinates.first, coordinates.last);
    final reopened = GeometryDraft([a, b, c, a], polygon: true);
    expect(reopened.points, [a, b, c]);
  });
  test('curves preserve segment endpoints and can be undone', () {
    final d = GeometryDraft([a, b]);
    d.checkpoint();
    d.curve(0, c);
    expect(d.points.first, a);
    expect(d.points.last, b);
    expect(d.points.length, 13);
    expect(d.points[6].latitude, greaterThan(a.latitude));
    d.undo();
    expect(d.points, [a, b]);
  });
  test(
    'closing polygon segment can be curved without changing first vertex',
    () {
      final d = GeometryDraft([a, b, c], polygon: true);
      d.curve(2, const LatLng(34.851, -82.4));
      expect(d.points.first, a);
      expect(d.geometry['coordinates'][0].last, [-82.4, 34.85]);
      expect(d.points.length, 14);
    },
  );
  test('freehand simplifies collinear samples while retaining endpoints', () {
    final pts = List.generate(100, (i) => LatLng(34.85, -82.4 + i * .00001));
    final reduced = simplifyStroke(pts, 1.5);
    expect(reduced, [pts.first, pts.last]);
    final d = GeometryDraft([b]);
    d.addStroke(pts, fromStart: true);
    expect(d.points, [pts.last, pts.first, b]);
  });
  test('joining freehand strokes does not duplicate endpoint handles', () {
    final d = GeometryDraft([a, b]);
    d.addStroke([b, c]);
    expect(d.points, [a, b, c]);
    d.addStroke([a, c], fromStart: true);
    expect(d.points, [c, a, b, c]);
  });
  test('freehand preserves a meaningful bend', () {
    expect(simplifyStroke([a, b, c], 1.5), [a, b, c]);
  });
  test('limit rejection does not truncate or mutate a path', () {
    final d = GeometryDraft(List.filled(200, a));
    expect(() => d.addStroke([b, c]), throwsStateError);
    expect(d.points.length, 200);
    expect(d.points.last, a);
    expect(() => d.insert(1, b), throwsStateError);
  });
}
