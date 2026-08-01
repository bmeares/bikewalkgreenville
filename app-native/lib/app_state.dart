import 'package:flutter/foundation.dart' show setEquals;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'theme.dart';

/// Single source of truth for map UI state and travel preferences.
///
/// Modes are a SET, not a single choice: someone who has a bike and a bus pass
/// picks both, and the router costs bike-only and bike-to-the-bus itineraries
/// against each other. `roll` and `useBcycle` are sub-options of walking and
/// biking respectively.
class AppState extends ChangeNotifier {
  static const _kModes = 'modes';
  static const _kRoll = 'roll';
  static const _kBcycle = 'bcycle';

  Set<TravelMode> _modes = {TravelMode.cyclist};
  bool _roll = false;
  bool _useBcycle = false;

  Set<TravelMode> get modes => _modes;
  bool get roll => _roll;
  bool get useBcycle => _useBcycle;

  /// The mode whose icon and verb the one-tap UI should use. With several
  /// selected there is no single answer, so callers check [isMultiModal] first.
  TravelMode get mode => _modes.contains(TravelMode.cyclist)
      ? TravelMode.cyclist
      : (_modes.contains(TravelMode.pedestrian)
          ? TravelMode.pedestrian
          : TravelMode.transit);

  bool get isMultiModal => _modes.length > 1;

  /// "Bike here" / "Go here" — the button label on a destination card.
  String get directionsVerb =>
      isMultiModal ? 'Go here' : (modeVerbs[mode] ?? 'Directions');

  /// Walking is relabelled when the rider rolls; everything downstream (the
  /// router's weighting, the sidewalk warnings) follows the same flag.
  String labelFor(TravelMode m) =>
      (m == TravelMode.pedestrian && _roll) ? 'Roll' : modeLabels[m]!;

  IconData iconFor(TravelMode m) => (m == TravelMode.pedestrian && _roll)
      ? Icons.accessible_forward
      : modeIcons[m]!;

  /// `modes=` query value for `/map-layers/route`.
  Set<String> get apiModes =>
      _modes.map((m) => modeApiNames[m]!).toSet();

  /// Per-layer manual overrides (from the layers sheet); otherwise a layer is
  /// visible when a selected mode includes it and its defaultOn is true.
  final Map<String, bool> _overrides = {};

  bool layerVisible(LayerDef def) =>
      def.modes.any(_modes.contains) && (_overrides[def.id] ?? def.defaultOn);

  /// Layers offered in the layers sheet for the current selection.
  Iterable<LayerDef> get relevantLayers =>
      layerDefs.where((d) => d.modes.any(_modes.contains));

  Future<void> load() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      final saved = prefs.getStringList(_kModes);
      if (saved != null && saved.isNotEmpty) {
        final parsed = saved
            .map((s) => TravelMode.values.where((m) => m.name == s))
            .expand((e) => e)
            .toSet();
        if (parsed.isNotEmpty) _modes = parsed;
      }
      _roll = prefs.getBool(_kRoll) ?? false;
      _useBcycle = prefs.getBool(_kBcycle) ?? false;
      notifyListeners();
    } catch (_) {
      // Preferences are a convenience; defaults are perfectly usable.
    }
  }

  Future<void> _save() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_kModes, _modes.map((m) => m.name).toList());
      await prefs.setBool(_kRoll, _roll);
      await prefs.setBool(_kBcycle, _useBcycle);
    } catch (_) {}
  }

  /// Replace the selection. Empty selections are ignored — with nothing chosen
  /// there is nothing to route and no layers to show.
  void setModes(Set<TravelMode> next) {
    if (next.isEmpty || setEquals(next, _modes)) return;
    _modes = {...next};
    notifyListeners();
    _save();
  }

  void toggleMode(TravelMode m) {
    final next = {..._modes};
    if (!next.remove(m)) next.add(m);
    setModes(next);
  }

  /// Back-compat single-mode setter (feature sheets that route one way).
  void setMode(TravelMode m) => setModes({m});

  void setRoll(bool value) {
    if (value == _roll) return;
    _roll = value;
    // Rolling is a way of being a pedestrian: turn that mode on with it.
    if (value) _modes = {..._modes, TravelMode.pedestrian};
    notifyListeners();
    _save();
  }

  void setUseBcycle(bool value) {
    if (value == _useBcycle) return;
    _useBcycle = value;
    if (value) _modes = {..._modes, TravelMode.cyclist};
    notifyListeners();
    _save();
  }

  void toggleLayer(String id, bool on) {
    _overrides[id] = on;
    notifyListeners();
  }

  bool overrideFor(LayerDef def) => _overrides[def.id] ?? def.defaultOn;
}
