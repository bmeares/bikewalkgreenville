import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:bwg_app_native/api.dart';
import 'package:bwg_app_native/geometry_draft.dart';

class _ThrowingApi extends Api {
  int calls = 0;
  @override
  Future<Map<String, dynamic>> route(
    double fromLat,
    double fromLon,
    double toLat,
    double toLon, {
    Set<String> modes = const {'bike'},
    bool roll = false,
    bool bcycle = false,
    String? plan,
    bool ebike = false,
    String? stress,
    int alt = 0,
    bool trail = true,
    bool community = true,
  }) async {
    calls++;
    throw ApiError('router down');
  }
}

class _RoutingApi extends _ThrowingApi {
  @override
  Future<Map<String, dynamic>> route(
    double fromLat,
    double fromLon,
    double toLat,
    double toLon, {
    Set<String> modes = const {'bike'},
    bool roll = false,
    bool bcycle = false,
    String? plan,
    bool ebike = false,
    String? stress,
    int alt = 0,
    bool trail = true,
    bool community = true,
  }) async {
    calls++;
    return {
      'type': 'Feature',
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          [fromLon, fromLat],
          [fromLon, toLat],
          [toLon, toLat],
        ],
      },
      'properties': <String, dynamic>{},
    };
  }
}

void main() {
  const a = LatLng(34.85, -82.4),
      b = LatLng(34.85, -82.399),
      c = LatLng(34.851, -82.399);
  final realApi = api;
  tearDown(() => api = realApi);

  test('polygon closes only on export and reopening drops the ring close', () {
    final d = AreaDraft(corners: [a, b]);
    expect(d.canPublish, isFalse);
    d.add(c);
    expect(d.canPublish, isTrue);
    final coordinates = d.geometry['coordinates'][0] as List;
    expect(coordinates.length, 4);
    expect(coordinates.first, coordinates.last);
    expect(AreaDraft(corners: [a, b, c, a]).corners, [a, b, c]);
    d.undo();
    expect(d.corners, [a, b]);
  });

  test(
    'a waypoint leg falls back to a straight line when routing fails',
    () async {
      final fake = _ThrowingApi();
      api = fake;
      final d = RouteDraft();
      expect(await d.add(a), isFalse); // first tap only places the start
      expect(await d.add(b), isTrue);
      expect(fake.calls, 1);
      expect(d.line, [a, b]);
      d.undo();
      expect(d.waypoints, [a]);
      expect(d.line, [a]);
    },
  );

  test(
    'routed legs append the router path; straight toggle skips it',
    () async {
      final fake = _RoutingApi();
      api = fake;
      final d = RouteDraft();
      await d.add(a);
      expect(await d.add(c), isFalse);
      expect(d.line, [a, const LatLng(34.851, -82.4), c]);
      await d.add(b, straight: true);
      expect(fake.calls, 1);
      expect(d.line.last, b);
      expect(d.line.length, 4); // shared waypoint not repeated
    },
  );

  test('seeding an existing line makes straight legs through its vertices', () {
    final d = RouteDraft.seeded([a, b, c], replaces: 'x');
    expect(d.waypoints, [a, b, c]);
    expect(d.line, [a, b, c]);
    expect(d.replaces, 'x');
  });

  test('RDP simplifies collinear samples while retaining endpoints', () {
    final pts = List.generate(100, (i) => LatLng(34.85, -82.4 + i * .00001));
    expect(simplifyStroke(pts, 1.5), [pts.first, pts.last]);
    expect(simplifyStroke([a, b, c], 1.5), [a, b, c]);
  });
}
