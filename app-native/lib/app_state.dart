import 'package:flutter/foundation.dart';

import 'theme.dart';

/// Single source of truth for map UI state.
class AppState extends ChangeNotifier {
  TravelMode _mode = TravelMode.cyclist;
  TravelMode get mode => _mode;

  /// Per-layer manual overrides (from the layers sheet); otherwise a layer is
  /// visible when the current mode includes it and its defaultOn is true.
  final Map<String, bool> _overrides = {};

  bool layerVisible(LayerDef def) =>
      def.modes.contains(_mode) && (_overrides[def.id] ?? def.defaultOn);

  void setMode(TravelMode m) {
    if (m == _mode) return;
    _mode = m;
    notifyListeners();
  }

  void toggleLayer(String id, bool on) {
    _overrides[id] = on;
    notifyListeners();
  }

  bool overrideFor(LayerDef def) => _overrides[def.id] ?? def.defaultOn;
}
