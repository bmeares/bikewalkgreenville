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

  test('warning ranges become dashed overlay geometry', () {
    final f = _feature();
    final props = f['properties'] as Map<String, dynamic>;
    props['mode'] = 'walk';
    props['warn_ranges'] = [
      {'kind': 'no_sidewalk', 'start': 1, 'end': 2, 'distance_m': 222.0},
      // Out of range and degenerate entries must not produce features.
      {'kind': 'no_sidewalk', 'start': 2, 'end': 2, 'distance_m': 0.0},
      {'kind': 'no_sidewalk', 'start': 5, 'end': 9, 'distance_m': 10.0},
    ];
    props['warnings'] = [
      {
        'kind': 'no_sidewalk',
        'distance_m': 222.0,
        'label': '700 ft with no sidewalk',
        'message': 'About 700 ft of this route has no sidewalk.',
      },
    ];
    final route = NavRoute.fromFeature(f);
    expect(route.warnRanges.length, 3);
    expect(route.warnings.single.kind, 'no_sidewalk');
    expect(route.warnings.single.label, '700 ft with no sidewalk');

    final collection = route.warnCollection();
    final features = collection['features'] as List;
    expect(features.length, 1, reason: 'only the in-range, non-empty span');
    final coords = features.first['geometry']['coordinates'] as List;
    expect(coords.length, 2);
    expect(coords.first, [-82.3980, 34.8500]);
  });

  test('street fallback and plan metadata parse', () {
    final f = _feature();
    final props = f['properties'] as Map<String, dynamic>;
    props['plan'] = 'bike-transit';
    props['plan_label'] = 'Bike + bus';
    props['fallback'] = 'street';
    props['fallback_note'] = 'No bikeable route was available.';
    props['alternatives'] = [
      {
        'plan': 'bike',
        'label': 'Bike',
        'icon_mode': 'bike',
        'distance_m': 4000.0,
        'duration_min': 16.0,
        'warnings': [],
      },
    ];
    final route = NavRoute.fromFeature(f);
    expect(route.plan, 'bike-transit');
    expect(route.planLabel, 'Bike + bus');
    expect(route.fallback, 'street');
    expect(route.fallbackNote, contains('No bikeable route'));
    expect(route.alternatives.single.plan, 'bike');
    expect(route.alternatives.single.durationMin, 16.0);
  });

  test('turn markers are emitted only for turns, ahead of the rider', () {
    final route = NavRoute.fromFeature(_feature());
    // depart / left / arrive -> one marker, for the left turn.
    final all = route.stepCollection()['features'] as List;
    expect(all.length, 1);
    expect(all.first['properties']['bearing'], isA<double>());
    // Once the turn is behind us it drops off the map.
    final ahead = route.stepCollection(fromStep: 2)['features'] as List;
    expect(ahead, isEmpty);
  });

  test('per-step warnings parse and bike-share maneuvers have icons', () {
    final step = RouteStep.fromJson({
      'maneuver': 'straight',
      'instruction': 'Continue on Elm St',
      'distance_m': 300.0,
      'start_index': 0,
      'warn': 'no_bike_lane',
      'warn_m': 180.0,
    });
    expect(step.warn, 'no_bike_lane');
    expect(step.warnM, 180.0);

    RouteStep m(String maneuver) => RouteStep.fromJson({
          'maneuver': maneuver,
          'instruction': 'x',
          'distance_m': 0.0,
          'start_index': 0,
        });
    expect(m('rent').icon, Icons.pedal_bike);
    expect(m('dock').icon, Icons.lock_outline);
    expect(m('straight').warn, isNull);
  });

  test('distances format imperially', () {
    expect(formatDistance(30), '100 ft');
    expect(formatDistance(1609.344), '1.0 mi');
    expect(formatDuration(0.5), '<1 min');
    expect(formatDuration(90), '1 hr 30 min');
  });

  group('warning presentation', () {
    test('every server warning kind has its own wording and icon', () {
      for (final kind in ['no_sidewalk', 'no_bike_lane', 'steep']) {
        expect(warnStepPhrases[kind], isNotNull, reason: kind);
        expect(warnStepSentences[kind], isNotNull, reason: kind);
        expect(warnIcons[kind], isNotNull, reason: kind);
      }
    });

    test('a steep grade is not announced as a missing bike lane', () {
      expect(warnStepPhrase('steep'), 'of steep grade');
      expect(warnStepSentence('steep'), 'Steep grade on this stretch');
      expect(warnStepPhrase('steep'), isNot(contains('bike lane')));
      expect(warnStepSentence('steep'), isNot(contains('bike lane')));
    });

    test('an unknown kind stays honest instead of borrowing wording', () {
      expect(warnStepPhrase('something_new'), isNot(contains('bike lane')));
      expect(warnStepPhrase('something_new'), isNot(contains('sidewalk')));
      expect(warnStepSentence(null), isNot(contains('bike lane')));
    });
  });
}
