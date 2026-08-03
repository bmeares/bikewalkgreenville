import 'dart:convert';

import 'package:flutter/foundation.dart' show setEquals;
import 'package:flutter/material.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'theme.dart';

/// How much traffic the rider is willing to put up with on a bike. Sent to
/// `/map-layers/route` as `?stress=`; the server re-weights, it never refuses
/// a route, so every level always gets you there.
enum BikeStress { quiet, balanced, direct }

const bikeStressLabels = {
  BikeStress.quiet: 'Quiet',
  BikeStress.balanced: 'Balanced',
  BikeStress.direct: 'Direct',
};

const bikeStressBlurbs = {
  BikeStress.quiet: 'Back streets and trails, even if it is further',
  BikeStress.balanced: 'Avoids busy roads where there is a reasonable detour',
  BikeStress.direct: 'The shortest ride, traffic and all',
};

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
  static const _kEbike = 'ebike';
  static const _kStress = 'stress';
  static const _kRecents = 'recent_searches';
  static const _maxRecents = 8;

  Set<TravelMode> _modes = {TravelMode.cyclist};
  bool _roll = false;
  bool _useBcycle = false;
  bool _useEbike = false;
  BikeStress _stress = BikeStress.balanced;

  Set<TravelMode> get modes => _modes;
  bool get roll => _roll;
  bool get useBcycle => _useBcycle;
  bool get useEbike => _useEbike;
  BikeStress get stress => _stress;

  /// `stress=` query value for `/map-layers/route`.
  String get stressApiName => _stress.name;

  /// The bike sub-options only mean anything when there is a bike involved.
  bool get showsBikeOptions => _modes.contains(TravelMode.cyclist);

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
  String labelFor(TravelMode m) {
    if (m == TravelMode.pedestrian && _roll) return 'Roll';
    if (m == TravelMode.cyclist && _useEbike) return 'E-bike';
    return modeLabels[m]!;
  }

  IconData iconFor(TravelMode m) {
    if (m == TravelMode.pedestrian && _roll) return Icons.accessible_forward;
    if (m == TravelMode.cyclist && _useEbike) return Icons.electric_bike;
    return modeIcons[m]!;
  }

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
      _useEbike = prefs.getBool(_kEbike) ?? false;
      final savedStress = prefs.getString(_kStress);
      _stress = BikeStress.values.firstWhere(
        (s) => s.name == savedStress,
        orElse: () => BikeStress.balanced,
      );
      _recents = [
        for (final s in prefs.getStringList(_kRecents) ?? <String>[])
          Map<String, dynamic>.from(jsonDecode(s) as Map),
      ];
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
      await prefs.setBool(_kEbike, _useEbike);
      await prefs.setString(_kStress, _stress.name);
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

  /// One pill, tapped repeatedly, cycles: off → base → variant → off.
  ///
  /// Bike: off → Bike → E-bike → off. Walk: off → Walk → Roll → off. Bus has
  /// no variant, so it's a plain toggle. The variant resets on the way out, so
  /// re-selecting always starts at the base mode. The last remaining mode
  /// never deselects (nothing to route with an empty selection) — its cycle
  /// just wraps back to the base variant.
  void cyclePill(TravelMode m) {
    if (!_modes.contains(m)) {
      _modes = {..._modes, m};
      notifyListeners();
      _save();
      return;
    }
    final isLast = _modes.length == 1;
    switch (m) {
      case TravelMode.cyclist:
        if (!_useEbike) {
          _useEbike = true;
        } else {
          _useEbike = false;
          if (!isLast) _modes = {..._modes}..remove(m);
        }
      case TravelMode.pedestrian:
        if (!_roll) {
          _roll = true;
        } else {
          _roll = false;
          if (!isLast) _modes = {..._modes}..remove(m);
        }
      case TravelMode.transit:
        if (!isLast) _modes = {..._modes}..remove(m);
    }
    notifyListeners();
    _save();
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

  void setUseEbike(bool value) {
    if (value == _useEbike) return;
    _useEbike = value;
    // An e-bike is a bike: turn that mode on with it.
    if (value) _modes = {..._modes, TravelMode.cyclist};
    notifyListeners();
    _save();
  }

  void setStress(BikeStress value) {
    if (value == _stress) return;
    _stress = value;
    notifyListeners();
    _save();
  }

  void toggleLayer(String id, bool on) {
    _overrides[id] = on;
    notifyListeners();
  }

  bool overrideFor(LayerDef def) => _overrides[def.id] ?? def.defaultOn;

  // ---------------------------------------------------------- recent searches

  /// Places the user has actually picked from search results, newest first —
  /// so a cleared route is two taps to bring back. Each entry is the search
  /// result map (`label`, `sublabel`, `lat`, `lon`).
  List<Map<String, dynamic>> _recents = [];

  List<Map<String, dynamic>> get recentSearches => _recents;

  void addRecentSearch(Map<String, dynamic> result) {
    final lat = (result['lat'] as num?)?.toDouble();
    final lon = (result['lon'] as num?)?.toDouble();
    final label = result['label']?.toString() ?? '';
    if (lat == null || lon == null || label.isEmpty) return;
    final entry = {
      'label': label,
      'sublabel': result['sublabel']?.toString() ?? '',
      'lat': lat,
      'lon': lon,
    };
    _recents = [
      entry,
      ..._recents.where((r) => r['label'] != label),
    ].take(_maxRecents).toList();
    notifyListeners();
    _saveRecents();
  }

  Future<void> _saveRecents() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(
          _kRecents, _recents.map(jsonEncode).toList());
    } catch (_) {}
  }
}
