import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:bwg_app_native/app_state.dart';
import 'package:bwg_app_native/theme.dart';

void main() {
  test('bike is the default selection', () {
    final state = AppState();
    expect(state.modes, {TravelMode.cyclist});
    expect(state.apiModes, {'bike'});
    expect(state.isMultiModal, isFalse);
    expect(state.directionsVerb, 'Bike here');
  });

  test('modes are multi-select and drive the api parameter', () {
    final state = AppState();
    state.toggleMode(TravelMode.transit);
    expect(state.modes, {TravelMode.cyclist, TravelMode.transit});
    expect(state.apiModes, {'bike', 'transit'});
    expect(state.isMultiModal, isTrue);
    expect(state.directionsVerb, 'Go here');
  });

  test('the last mode cannot be turned off', () {
    final state = AppState();
    state.toggleMode(TravelMode.cyclist);
    expect(state.modes, {TravelMode.cyclist},
        reason: 'an empty selection has nothing to route or show');
  });

  test('roll relabels walking and selects it', () {
    final state = AppState();
    expect(state.labelFor(TravelMode.pedestrian), 'Walk');
    state.setRoll(true);
    expect(state.roll, isTrue);
    expect(state.modes, contains(TravelMode.pedestrian));
    expect(state.labelFor(TravelMode.pedestrian), 'Roll');
    expect(state.iconFor(TravelMode.pedestrian), Icons.accessible_forward);
  });

  test('bike share is a sub-option of biking', () {
    final state = AppState();
    state.setModes({TravelMode.pedestrian});
    state.setUseBcycle(true);
    expect(state.useBcycle, isTrue);
    expect(state.modes, contains(TravelMode.cyclist),
        reason: 'renting a bike means you are cycling');
  });

  test('layer visibility follows the union of selected modes', () {
    final state = AppState();
    final busStops = layerDefs.firstWhere((d) => d.id == 'bus-stops');
    final bcycle = layerDefs.firstWhere((d) => d.id == 'bcycle');
    expect(state.layerVisible(busStops), isFalse);
    expect(state.layerVisible(bcycle), isTrue);

    state.toggleMode(TravelMode.transit);
    expect(state.layerVisible(busStops), isTrue);
    expect(state.layerVisible(bcycle), isTrue,
        reason: 'adding a mode must not hide the other mode\'s layers');
  });

  test('every plan key the router can return has an icon', () {
    for (final key in const [
      'bike',
      'walk',
      'roll',
      'bcycle',
      'walk-transit',
      'roll-transit',
      'bike-transit',
    ]) {
      expect(planIcons[key], isNotNull, reason: 'missing icon for $key');
    }
  });
  group('pill cycle (multi-tap on a selected pill)', () {
    test('bike cycles off -> bike -> e-bike -> off', () {
      final state = AppState();
      state.setModes({TravelMode.transit});
      state.cyclePill(TravelMode.cyclist); // off -> bike
      expect(state.modes, contains(TravelMode.cyclist));
      expect(state.useEbike, isFalse);
      state.cyclePill(TravelMode.cyclist); // bike -> e-bike
      expect(state.modes, contains(TravelMode.cyclist));
      expect(state.useEbike, isTrue);
      state.cyclePill(TravelMode.cyclist); // e-bike -> off, variant reset
      expect(state.modes, isNot(contains(TravelMode.cyclist)));
      expect(state.useEbike, isFalse,
          reason: 're-selecting later must start at the base variant');
    });

    test('walk cycles off -> walk -> roll -> off', () {
      final state = AppState();
      expect(state.modes, {TravelMode.cyclist});
      state.cyclePill(TravelMode.pedestrian);
      expect(state.modes, contains(TravelMode.pedestrian));
      expect(state.roll, isFalse);
      state.cyclePill(TravelMode.pedestrian);
      expect(state.roll, isTrue);
      state.cyclePill(TravelMode.pedestrian);
      expect(state.modes, isNot(contains(TravelMode.pedestrian)));
      expect(state.roll, isFalse);
    });

    test('the last mode never deselects; its cycle wraps to the base variant',
        () {
      final state = AppState();
      expect(state.modes, {TravelMode.cyclist});
      state.cyclePill(TravelMode.cyclist); // bike -> e-bike
      expect(state.useEbike, isTrue);
      state.cyclePill(TravelMode.cyclist); // would deselect; wraps instead
      expect(state.modes, {TravelMode.cyclist},
          reason: 'an empty selection has nothing to route or show');
      expect(state.useEbike, isFalse);
    });

    test('bus is a plain toggle', () {
      final state = AppState();
      state.cyclePill(TravelMode.transit);
      expect(state.modes, contains(TravelMode.transit));
      state.cyclePill(TravelMode.transit);
      expect(state.modes, isNot(contains(TravelMode.transit)));
    });
  });

  group('recent searches', () {
    test('newest first, deduped by label, capped at 8', () {
      final state = AppState();
      for (var i = 0; i < 10; i++) {
        state.addRecentSearch(
            {'label': 'Place $i', 'lat': 34.85, 'lon': -82.39});
      }
      expect(state.recentSearches.length, 8);
      expect(state.recentSearches.first['label'], 'Place 9');
      state.addRecentSearch(
          {'label': 'Place 5', 'lat': 34.85, 'lon': -82.39});
      expect(state.recentSearches.first['label'], 'Place 5');
      expect(
        state.recentSearches.where((r) => r['label'] == 'Place 5').length,
        1,
        reason: 're-picking a place must move it up, not duplicate it',
      );
    });

    test('entries without a resolvable coordinate are ignored', () {
      final state = AppState();
      state.addRecentSearch({'label': 'Nowhere'});
      expect(state.recentSearches, isEmpty);
    });
  });

  group('bike sub-options', () {

    test('default to a plain bike at balanced tolerance', () {
      final state = AppState();
      expect(state.useEbike, isFalse);
      expect(state.stress, BikeStress.balanced);
      expect(state.stressApiName, 'balanced',
          reason: 'the server treats balanced as its historical behaviour');
    });

    test('the e-bike toggle relabels the cyclist mode', () {
      final state = AppState();
      expect(state.labelFor(TravelMode.cyclist), 'Bike');
      state.setUseEbike(true);
      expect(state.useEbike, isTrue);
      expect(state.labelFor(TravelMode.cyclist), 'E-bike');
      expect(state.iconFor(TravelMode.cyclist), Icons.electric_bike);
    });

    test('turning on the e-bike selects biking, the way roll selects walking',
        () {
      final state = AppState();
      state.setModes({TravelMode.transit});
      state.setUseEbike(true);
      expect(state.modes, contains(TravelMode.cyclist));
    });

    test('the tolerance round-trips to the api name', () {
      final state = AppState();
      state.setStress(BikeStress.quiet);
      expect(state.stressApiName, 'quiet');
      state.setStress(BikeStress.direct);
      expect(state.stressApiName, 'direct');
    });

    test('the sub-options only show when a bike is selected', () {
      final state = AppState();
      expect(state.showsBikeOptions, isTrue);
      state.setModes({TravelMode.pedestrian});
      expect(state.showsBikeOptions, isFalse);
    });

    test('every tolerance has a label and a blurb for the UI', () {
      for (final level in BikeStress.values) {
        expect(bikeStressLabels[level], isNotNull);
        expect(bikeStressBlurbs[level], isNotNull);
      }
    });
  });
}
