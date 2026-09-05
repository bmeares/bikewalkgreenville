import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

import 'package:bwg_app_native/nav.dart';
import 'package:bwg_app_native/theme.dart';

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

  test('segment notes explain the shading at a tapped distance', () {
    final f = _feature();
    final props = f['properties'] as Map<String, dynamic>;
    // A steep pitch over the first leg, a sidewalk gap over the second.
    props['elevation_profile'] = [
      [0.0, 800.0],
      [100.0, 830.0], // ~9% grade over 100 m -> steep (orange)
      [405.0, 830.0],
    ];
    props['mode'] = 'walk';
    props['warn_ranges'] = [
      {'kind': 'no_sidewalk', 'start': 1, 'end': 2, 'distance_m': 222.0},
    ];
    final route = NavRoute.fromFeature(f);
    final onHill = route.segmentNotes(50.0);
    expect(onHill.single, contains('% grade'));
    expect(onHill.single, contains('orange'));
    final onGap = route.segmentNotes(300.0);
    expect(onGap.single, contains('No sidewalk'));
    expect(onGap.single, contains('dashed red'));
    // The flat, sidewalked stretch between them explains nothing.
    // (hill window ends 100+30; the gap's starts at cumulative[1]-30 ≈ 153)
    expect(route.segmentNotes(140.0), isEmpty);
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

  test('elevation profile and per-step climb parse', () {
    final route = NavRoute.fromFeature({
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          [-82.40, 34.85],
          [-82.40, 34.86],
        ],
      },
      'properties': {
        'climb_ft': 120,
        'elevation_profile': [
          [0, 900],
          [500.5, 980],
          [1100, 1020],
        ],
        'steps': [
          {
            'maneuver': 'depart',
            'instruction': 'Head north',
            'distance_m': 100.0,
            'start_index': 0,
            'climb_ft': 40,
          },
        ],
      },
    });
    expect(route.elevationProfile, isNotNull);
    expect(route.elevationProfile!.length, 3);
    expect(route.elevationProfile![1][0], 500.5);
    expect(route.steps.first.climbFt, 40);
    expect(
      route.steps.first.isSteepClimb,
      isTrue,
      reason: '40 ft over 100 m is a 12% grade',
    );
  });

  test('no elevation profile stays null and flat steps are not steep', () {
    final route = NavRoute.fromFeature({
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          [-82.40, 34.85],
          [-82.40, 34.86],
        ],
      },
      'properties': {
        'steps': [
          {
            'maneuver': 'depart',
            'instruction': 'Head north',
            'distance_m': 100.0,
            'start_index': 0,
          },
        ],
      },
    });
    expect(route.elevationProfile, isNull);
    expect(route.steps.first.isSteepClimb, isFalse);
  });

  test('distances format imperially', () {
    expect(formatDistance(30), '100 ft');
    expect(formatDistance(1609.344), '1.0 mi');
    expect(formatDuration(0.5), '<1 min');
    expect(formatDuration(90), '1 hr 30 min');
  });

  group('hills from the elevation profile', () {
    NavRoute hilly(List<List<double>> profile, {String mode = 'bike'}) {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['mode'] = mode;
      props['elevation_profile'] = profile;
      return NavRoute.fromFeature(f);
    }

    test('a sustained steep stretch is flagged and drawn', () {
      // ~183 m first leg: 50 ft over 150 m ≈ 10.2% — very steep.
      final route = hilly([
        [0, 900],
        [150, 950],
        [400, 952],
      ]);
      final hills = route.hillRanges();
      expect(hills.length, 1);
      expect(hills.single.severity, 'vsteep');
      final features = route.hillCollection()['features'] as List;
      expect(features.length, 1);
      expect(features.single['properties']['sev'], 'vsteep');
      expect(route.hillSummary(), contains('steep hills'));
      expect(route.hillSummary(), contains('hard climb'));
    });

    test('a long 6% climb counts as steep even under the 7% line', () {
      // 76 ft over 370 m ≈ 6.3% — the N Main St case.
      final route = hilly([
        [0, 900],
        [370, 976],
      ]);
      expect(route.hillRanges().single.severity, 'steep');
    });

    test('contour noise stays quiet', () {
      // 4 ft over 20 m would be 6% — but under both noise gates.
      final route = hilly([
        [0, 900],
        [20, 904],
        [400, 906],
      ]);
      expect(route.hillRanges(), isEmpty);
      expect(route.hillSummary(), isNull);
    });

    test('e-bikes get the softened wording, walkers the hard one', () {
      final profile = [
        [0.0, 900.0],
        [150.0, 950.0],
      ];
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['mode'] = 'bike';
      props['ebike'] = true;
      props['elevation_profile'] = profile;
      expect(NavRoute.fromFeature(f).hillSummary(), contains('motor'));
      expect(hilly(profile, mode: 'walk').hillSummary(), contains('walk up'));
      // Rolling keeps the server's ADA warning instead of a duplicate.
      expect(hilly(profile, mode: 'roll').hillSummary(), isNull);
    });
  });

  group('warning threshold', () {
    test('tiny infrastructure gaps are dropped, steep never is', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['warnings'] = [
        {
          'kind': 'no_bike_lane',
          'distance_m': 30.0,
          'label': '',
          'message': 'tiny',
        },
        {
          'kind': 'no_bike_lane',
          'distance_m': 300.0,
          'label': '',
          'message': 'real',
        },
        {'kind': 'steep', 'distance_m': 10.0, 'label': '', 'message': 'steep'},
      ];
      final route = NavRoute.fromFeature(f);
      final visible = route.visibleWarnings(200);
      expect(visible.map((w) => w.message), ['real', 'steep']);
      // Threshold 0 shows everything.
      expect(route.visibleWarnings(0).length, 3);
    });

    test('the default threshold is 1000 ft — a 300 m gap is not a hazard', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['warnings'] = [
        // 300 m ≈ 984 ft: just under the bar, so it stays quiet.
        {
          'kind': 'no_bike_lane',
          'distance_m': 300.0,
          'label': '',
          'message': 'short',
        },
        {
          'kind': 'no_bike_lane',
          'distance_m': 400.0,
          'label': '',
          'message': 'long',
        },
        {'kind': 'steep', 'distance_m': 10.0, 'label': '', 'message': 'steep'},
      ];
      expect(warnMinFt, 1000.0);
      expect(NavRoute.fromFeature(f).visibleWarnings().map((w) => w.message), [
        'long',
        'steep',
      ]);
    });
  });

  group('per-mode route coloring', () {
    test('a plain route is one feature in its mode color', () {
      final route = NavRoute.fromFeature(_feature());
      final features = route.routeCollection()['features'] as List;
      expect(features.length, 1);
      expect(features.single['properties']['color'], routeLegColors['bike']);
    });

    test('a transit itinerary splits at board and alight', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['mode'] = 'transit';
      props['access_mode'] = 'bike';
      props['route_color'] = '#AB47BC';
      props['steps'] = <dynamic>[...props['steps'] as List]
        ..insertAll(1, [
          {
            'maneuver': 'board',
            'instruction': 'Board Greenlink Route 503',
            'distance_m': 0.0,
            'start_index': 1,
          },
          {
            'maneuver': 'alight',
            'instruction': 'Get off',
            'distance_m': 0.0,
            'start_index': 2,
          },
        ]);
      final route = NavRoute.fromFeature(f);
      final features = route.routeCollection()['features'] as List;
      expect(
        features.length,
        2,
        reason: 'alight at the last coordinate leaves no third leg',
      );
      expect(features[0]['properties']['color'], routeLegColors['bike']);
      expect(features[1]['properties']['color'], '#AB47BC');
    });

    test('a bike-share trip draws walk legs around the red ride', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['plan'] = 'bcycle';
      props['steps'] = <dynamic>[...props['steps'] as List]
        ..insertAll(1, [
          {
            'maneuver': 'rent',
            'instruction': 'Unlock a BCycle',
            'distance_m': 0.0,
            'start_index': 1,
          },
          {
            'maneuver': 'dock',
            'instruction': 'Dock the bike',
            'distance_m': 0.0,
            'start_index': 2,
          },
        ]);
      final route = NavRoute.fromFeature(f);
      final features = route.routeCollection()['features'] as List;
      expect(features.length, 2);
      expect(features[0]['properties']['color'], routeLegColors['walk']);
      expect(features[1]['properties']['color'], routeLegColors['bcycle']);
    });
  });

  group('per-step modes (the steps sheet coloring)', () {
    test('a plain route travels every step in its own mode', () {
      final route = NavRoute.fromFeature(_feature());
      expect(route.stepModes(), ['bike', 'bike', 'bike']);
    });

    test('a transit itinerary switches at board and alight', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['mode'] = 'transit';
      props['access_mode'] = 'bike';
      props['steps'] = <dynamic>[...props['steps'] as List]
        ..insertAll(1, [
          {'maneuver': 'board', 'instruction': 'Board', 'start_index': 1},
          {'maneuver': 'ride', 'instruction': 'Ride', 'start_index': 1},
          {'maneuver': 'alight', 'instruction': 'Get off', 'start_index': 2},
        ]);
      final route = NavRoute.fromFeature(f);
      // depart(bike) board(transit) ride(transit) alight(transit) then back
      // on the bike for the last turn and the arrival.
      expect(route.stepModes(), [
        'bike',
        'transit',
        'transit',
        'transit',
        'bike',
        'bike',
      ]);
    });

    test('a composite access profile still colors as a bike', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['mode'] = 'transit';
      props['access_mode'] = 'ebike:direct';
      final route = NavRoute.fromFeature(f);
      expect(route.footMode, 'bike');
      expect(route.stepModes().first, 'bike');
    });

    test('a bike-share trip walks, rides red, then walks again', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['plan'] = 'bcycle';
      props['steps'] = <dynamic>[...props['steps'] as List]
        ..insertAll(1, [
          {'maneuver': 'rent', 'instruction': 'Unlock', 'start_index': 1},
          {'maneuver': 'dock', 'instruction': 'Dock', 'start_index': 2},
        ]);
      final route = NavRoute.fromFeature(f);
      expect(route.stepModes(), ['walk', 'bcycle', 'bcycle', 'walk', 'walk']);
    });

    test('every step mode has a route-leg color', () {
      for (final m in ['bike', 'walk', 'roll', 'transit', 'bcycle']) {
        expect(routeLegColors[m], isNotNull, reason: m);
        expect(legModeIcons[m], isNotNull, reason: m);
      }
    });
  });

  group('e-bike labelling', () {
    test('an e-bike plan shows E-bike, not Bike', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['plan'] = 'bike';
      props['plan_label'] = 'Bike';
      props['ebike'] = true;
      final route = NavRoute.fromFeature(f);
      expect(route.planDisplayLabel, 'E-bike');
      expect(route.planIcon, legModeIcons['ebike']);
    });

    test('a plain bike stays a bike', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['plan'] = 'bike';
      props['plan_label'] = 'Bike';
      final route = NavRoute.fromFeature(f);
      expect(route.planDisplayLabel, 'Bike');
      expect(route.planIcon, planIcons['bike']);
    });

    test('the e-bike relabel never touches other plans', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['plan'] = 'walk';
      props['plan_label'] = 'Walk';
      props['ebike'] = true; // nonsense combination, but stay honest
      final route = NavRoute.fromFeature(f);
      expect(route.planDisplayLabel, 'Walk');
    });
  });

  group('alternate routes', () {
    test('alt and alt_distinct parse with safe defaults', () {
      final route = NavRoute.fromFeature(_feature());
      expect(route.alt, 0);
      expect(route.altDistinct, isTrue);
    });

    test('an identical alternate is disclosed', () {
      final f = _feature();
      final props = f['properties'] as Map<String, dynamic>;
      props['alt'] = 2;
      props['alt_distinct'] = false;
      final route = NavRoute.fromFeature(f);
      expect(route.alt, 2);
      expect(route.altDistinct, isFalse);
    });
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

  group('RerouteGovernor', () {
    final t0 = DateTime(2026, 1, 1, 12);
    DateTime at(int seconds) => t0.add(Duration(seconds: seconds));

    test('three off-route fixes trigger one announced reroute', () {
      final g = RerouteGovernor();
      expect(g.onFix(60, at(0)), RerouteDecision.none);
      expect(g.onFix(60, at(1)), RerouteDecision.none);
      expect(g.onFix(60, at(2)), RerouteDecision.announce);
    });

    test('on-route fixes never trigger', () {
      final g = RerouteGovernor();
      for (var i = 0; i < 20; i++) {
        expect(g.onFix(10, at(i)), RerouteDecision.none);
      }
    });

    test('heading back toward the route holds the reroute', () {
      final g = RerouteGovernor();
      // Off-route but closing distance every fix: the rider is rejoining.
      for (final (i, d) in const [90.0, 80.0, 70.0, 60.0, 55.0].indexed) {
        expect(
          g.onFix(d, at(i)),
          RerouteDecision.none,
          reason: 'fix $i at $d m, approaching',
        );
      }
    });

    test(
      'a repeat reroute in the same spell stays announced with debounce',
      () {
        final g = RerouteGovernor();
        var s = 0;
        expect(g.onFix(60, at(s++)), RerouteDecision.none);
        expect(g.onFix(60, at(s++)), RerouteDecision.none);
        expect(g.onFix(60, at(s++)), RerouteDecision.announce);
        // Still off-route (the Springer St tunnel): within the 15 s cooldown
        // nothing happens no matter how many fixes accumulate.
        for (; s < 17; s++) {
          expect(
            g.onFix(60, at(s)),
            RerouteDecision.none,
            reason: 'fix at ${s}s is inside the first cooldown',
          );
        }
        // Past the cooldown: recalculate with spoken feedback.
        expect(g.onFix(60, at(19)), RerouteDecision.announce);
      },
    );

    test('every reroute remains announced during a sustained detour', () {
      final g = RerouteGovernor();
      var t = 0;
      RerouteDecision drive() {
        // Feed off-route fixes until the governor acts.
        for (var i = 0; i < 400; i++) {
          final d = g.onFix(60, at(t++));
          if (d != RerouteDecision.none) return d;
        }
        fail('governor never acted');
      }

      expect(drive(), RerouteDecision.announce);
      expect(drive(), RerouteDecision.announce);
      expect(drive(), RerouteDecision.announce);
      expect(drive(), RerouteDecision.announce);
    });

    test('a short on-route blip does not reset the backoff', () {
      final g = RerouteGovernor();
      var t = 0;
      for (var i = 0; i < 3; i++) {
        g.onFix(60, at(t++));
      }
      expect(g.spellReroutes, 1);
      // A fresh reroute passes through the rider for a few seconds…
      for (var i = 0; i < 5; i++) {
        expect(g.onFix(5, at(t++)), RerouteDecision.none);
      }
      // Diverging again still gets spoken feedback.
      RerouteDecision d = RerouteDecision.none;
      for (var i = 0; i < 60 && d == RerouteDecision.none; i++) {
        d = g.onFix(60, at(t++));
      }
      expect(d, RerouteDecision.announce);
    });

    test('a sustained return to the route ends the spell', () {
      final g = RerouteGovernor();
      var t = 0;
      for (var i = 0; i < 3; i++) {
        g.onFix(60, at(t++));
      }
      expect(g.spellReroutes, 1);
      for (var i = 0; i < RerouteGovernor.onRouteResetFixes; i++) {
        g.onFix(5, at(t++));
      }
      // Next divergence is a brand-new spell: announced again.
      var d = RerouteDecision.none;
      for (var i = 0; i < 10 && d == RerouteDecision.none; i++) {
        d = g.onFix(60, at(t++));
      }
      expect(d, RerouteDecision.announce);
    });

    test('cooldowns stay responsive throughout a long detour', () {
      expect(RerouteGovernor.cooldownSeconds(1), 15);
      expect(RerouteGovernor.cooldownSeconds(2), 15);
      expect(RerouteGovernor.cooldownSeconds(3), 15);
      expect(RerouteGovernor.cooldownSeconds(4), 15);
      expect(RerouteGovernor.cooldownSeconds(8), 15);
    });
  });

  group('spokenText (what the voice says)', () {
    test('cardinal prefixes expand', () {
      expect(
        spokenText('Turn left onto E Washington St'),
        'Turn left onto East Washington Street',
      );
      expect(
        spokenText('Continue on N Main St'),
        'Continue on North Main Street',
      );
      expect(
        spokenText('Turn right onto SW Court St'),
        'Turn right onto Southwest Court Street',
      );
      // Titleized two-letter combos ("Ne" from the server's .title()).
      expect(
        spokenText('Head east on Ne Main St'),
        'Head east on Northeast Main Street',
      );
    });

    test('street-type suffixes expand', () {
      expect(
        spokenText('Turn left onto Pete Hollis Blvd'),
        'Turn left onto Pete Hollis Boulevard',
      );
      expect(
        spokenText('Bear right onto W Faris Rd'),
        'Bear right onto West Faris Road',
      );
      expect(
        spokenText('Continue onto Cedar Lane Rd'),
        'Continue onto Cedar Lane Road',
      );
      expect(
        spokenText('Turn right onto Laurens Hwy'),
        'Turn right onto Laurens Highway',
      );
    });

    test('St means Saint before a name, Street otherwise', () {
      expect(
        spokenText('Turn left onto St Francis Dr'),
        'Turn left onto Saint Francis Drive',
      );
      // Followed by a lowercase word: still Street.
      expect(
        spokenText('Bear left onto Springer St tunnel path'),
        'Bear left onto Springer Street tunnel path',
      );
      // Followed by another suffix abbreviation: still Street.
      expect(
        spokenText('Continue on E North St Ext'),
        'Continue on East North Street Extension',
      );
      // Trailing punctuation ends the name.
      expect(
        spokenText('Turn left onto E Washington St, then turn right'),
        'Turn left onto East Washington Street, then turn right',
      );
    });

    test('distance units expand', () {
      expect(
        spokenText('In 400 ft, turn left onto E Stone Ave'),
        'In 400 feet, turn left onto East Stone Avenue',
      );
      expect(
        spokenText('In 0.6 mi, make a U-turn onto Dunbar St'),
        'In 0.6 miles, make a U-turn onto Dunbar Street',
      );
    });

    test('ordinary words are left alone', () {
      expect(
        spokenText('Arrive at your destination'),
        'Arrive at your destination',
      );
      expect(
        spokenText('Head northeast on Swamp Rabbit Trail'),
        'Head northeast on Swamp Rabbit Trail',
      );
      expect(spokenText('You have arrived.'), 'You have arrived.');
    });
  });
}
