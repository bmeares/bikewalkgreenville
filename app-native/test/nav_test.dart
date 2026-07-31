import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

import 'package:bwg_app_native/nav.dart';

/// A ~440 m dogleg: east along one block, then north along the next.
Map<String, dynamic> _feature() => {
      'type': 'Feature',
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          [-82.4000, 34.8500],
          [-82.3980, 34.8500],
          [-82.3980, 34.8520],
        ],
      },
      'properties': {
        'distance_m': 405.0,
        'duration_min': 3.0,
        'steps': [
          {
            'maneuver': 'depart',
            'instruction': 'Head east on Main St',
            'name': 'Main St',
            'distance_m': 183.0,
            'start_index': 0,
          },
          {
            'maneuver': 'left',
            'instruction': 'Turn left onto Elm St',
            'name': 'Elm St',
            'distance_m': 222.0,
            'start_index': 1,
          },
          {
            'maneuver': 'arrive',
            'instruction': 'Arrive at your destination',
            'distance_m': 0.0,
            'start_index': 2,
          },
        ],
      },
    };

void main() {
  test('route parses geometry and steps', () {
    final route = NavRoute.fromFeature(_feature());
    expect(route.points.length, 3);
    expect(route.steps.length, 3);
    expect(route.steps.first.maneuver, 'depart');
    expect(route.cumulative.last, greaterThan(300));
  });

  test('progress snaps to the leg in progress and counts down the turn', () {
    final route = NavRoute.fromFeature(_feature());
    // Halfway along the first (east-west) leg.
    final p = NavProgress.of(route, const LatLng(34.8500, -82.3990))!;
    expect(p.segmentIndex, 0);
    expect(p.stepIndex, 0);
    expect(p.offRouteM, lessThan(5));
    // The turn is at the end of the first leg: roughly half the leg away.
    expect(p.distanceToManeuverM, greaterThan(50));
    expect(p.distanceToManeuverM, lessThan(150));
    expect(p.remainingM, lessThan(route.distanceM));
  });

  test('progress advances to the second step after the turn', () {
    final route = NavRoute.fromFeature(_feature());
    final p = NavProgress.of(route, const LatLng(34.8510, -82.3980))!;
    expect(p.segmentIndex, 1);
    expect(p.stepIndex, 1);
  });

  test('off-route distance is reported in meters', () {
    final route = NavRoute.fromFeature(_feature());
    // ~110 m north of the first leg.
    final p = NavProgress.of(route, const LatLng(34.8510, -82.3990))!;
    expect(p.offRouteM, greaterThan(80));
    expect(p.offRouteM, lessThan(140));
  });

  test('transit metadata and maneuvers parse', () {
    final f = _feature();
    final props = f['properties'] as Map<String, dynamic>;
    props['mode'] = 'transit';
    props['route'] = '503';
    props['board_stop'] = 'W Washington St + S Main St';
    props['route_color'] = '#7B1FA2';
    final route = NavRoute.fromFeature(f);
    expect(route.mode, 'transit');
    expect(route.transitRoute, '503');
    expect(route.boardStop, 'W Washington St + S Main St');
    expect(route.routeColor, '#7B1FA2');

    RouteStep step(String maneuver) => RouteStep.fromJson({
          'maneuver': maneuver,
          'instruction': 'x',
          'distance_m': 0.0,
          'start_index': 0,
        });
    expect(step('board').icon, Icons.departure_board);
    expect(step('ride').icon, Icons.directions_bus);
    expect(step('alight').icon, Icons.pin_drop);
  });

  test('bike is the default mode', () {
    final route = NavRoute.fromFeature(_feature());
    expect(route.mode, 'bike');
    expect(route.transitRoute, isNull);
  });

  test('distances format imperially', () {
    expect(formatDistance(30), '100 ft');
    expect(formatDistance(1609.344), '1.0 mi');
    expect(formatDuration(0.5), '<1 min');
    expect(formatDuration(90), '1 hr 30 min');
  });
}
