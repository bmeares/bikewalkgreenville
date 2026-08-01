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
