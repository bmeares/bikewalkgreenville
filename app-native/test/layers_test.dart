import 'package:flutter_test/flutter_test.dart';

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
}
