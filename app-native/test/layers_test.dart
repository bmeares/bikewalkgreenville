import 'package:flutter_test/flutter_test.dart';

import 'package:bwg_app_native/app_state.dart';
import 'package:bwg_app_native/theme.dart';

void main() {
  test('every travel mode has at least one default-on layer', () {
    for (final mode in TravelMode.values) {
      final layers =
          layerDefs.where((d) => d.modes.contains(mode) && d.defaultOn);
      expect(layers, isNotEmpty, reason: 'mode $mode has no visible layers');
    }
  });

  test('stress colors and labels cover the same levels', () {
    expect(stressColors.keys.toSet(), stressLabels.keys.toSet());
  });

  test('layer ids are unique', () {
    final ids = layerDefs.map((d) => d.id).toList();
    expect(ids.toSet().length, ids.length);
  });

  test('advocacy layers hide until opted in, then gain a sheet toggle', () {
    final state = AppState();
    final lights = layerDefs.firstWhere((d) => d.id == 'street-lights');
    expect(state.layerVisible(lights), isFalse);
    expect(state.relevantLayers.map((d) => d.id), isNot(contains('street-lights')));
    state.setAdvocacyLayer('street-lights', true);
    // Opting in both shows the layer and adds its toggle to the sheet.
    expect(state.layerVisible(lights), isTrue);
    expect(state.relevantLayers.map((d) => d.id), contains('street-lights'));
    state.setAdvocacyLayer('street-lights', false);
    expect(state.layerVisible(lights), isFalse);
  });

  test('fixed layers are always drawn and never offered as toggles', () {
    final state = AppState();
    for (final id in ['landmarks', 'custom-paths']) {
      final def = layerDefs.firstWhere((d) => d.id == id);
      expect(def.fixed, isTrue);
      expect(state.layerVisible(def), isTrue, reason: '$id must always draw');
      expect(state.relevantLayers.map((d) => d.id), isNot(contains(id)));
    }
  });

  test('vulnerable road users splits into heat, crashes and fatalities', () {
    final vru = layerDefs
        .where((d) => d.path == '/map-layers/vulnerable-crashes.geojson')
        .toList();
    expect(vru.map((d) => d.id).toSet(),
        {'vulnerable-heat', 'vulnerable-crashes', 'vulnerable-fatalities'});
    expect(vru.every((d) => d.advocacy), isTrue);
    // The point/circle splits slice the source by the killed property.
    expect(
        vru.firstWhere((d) => d.id == 'vulnerable-fatalities').filter, isNotNull);
    expect(
        vru.firstWhere((d) => d.id == 'vulnerable-crashes').filter, isNotNull);
  });

  test('shortcuts draw dotted; streetlights carry a light-base color', () {
    expect(layerDefs.firstWhere((d) => d.id == 'custom-paths').dashed, isTrue);
    expect(layerDefs.firstWhere((d) => d.id == 'street-lights').lightBaseColor,
        isNotNull);
  });
}
