import 'dart:async' show Completer;
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

/// How the rider's position is drawn while navigating: a Google-Maps-style
/// arrow, or the icon of the mode they're travelling (cyclist / walker /
/// wheelchair user).
enum PuckStyle { arrow, mode }

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
  static const _kSaved = 'saved_places';
  static const _kPuck = 'puck_style';
  static const _kTheme = 'theme_mode';
  static const _kMapBase = 'map_base';
  static const _kPreferTrail = 'prefer_trail';
  static const _kPreferCommunity = 'prefer_community';
  static const _kMyVotes = 'my_votes';
  static const _kHighContrast = 'high_contrast';
  static const _kLargeUi = 'large_ui';
  static const _kAdvocacy = 'advocacy_layers';
  static const _kWelcomeSeen = 'welcome_seen';
  static const _kDisclaimerVersion = 'disclaimer_accepted_version';
  static const _kDisclaimerAt = 'disclaimer_accepted_at';
  static const _kNotifiedEvents = 'notified_event_uids';
  static const _kMine = 'my_contribution_ids';
  static const _maxRecents = 8;

  Set<TravelMode> _modes = {TravelMode.cyclist};
  bool _roll = false;
  bool _useBcycle = false;
  bool _useEbike = false;
  BikeStress _stress = BikeStress.balanced;
  PuckStyle _puckStyle = PuckStyle.arrow;
  // Light by default: the free dark basemap hides too much detail to be
  // anyone's default. Dark stays a choice in Settings → Appearance.
  ThemeMode _themeMode = ThemeMode.light;
  MapBase _mapBase = MapBase.auto;
  bool _preferTrail = true;
  bool _preferCommunity = true;
  Map<String, String> _myVotes = {};
  bool _highContrast = false;
  bool _largeUi = false;
  bool _welcomeSeen = false;
  int _disclaimerVersion = 0;
  String? _disclaimerAt;
  Set<String> _notifiedEvents = {};
  Set<String> _mine = {};

  /// Experimental advocacy layers the user has opted into (Settings) —
  /// only these get a toggle in the layers sheet.
  Set<String> _advocacy = {};

  Set<TravelMode> get modes => _modes;
  bool get roll => _roll;
  bool get useBcycle => _useBcycle;
  bool get useEbike => _useEbike;
  BikeStress get stress => _stress;

  /// Gaps shorter than this (feet of missing bike lane / sidewalk) don't earn
  /// a warning banner. Adjustable in Settings.

  PuckStyle get puckStyle => _puckStyle;

  /// Light / dark / follow-the-device. Drives both the Material theme and
  /// (via [MapBase.auto]) which basemap style the map renders.
  ThemeMode get themeMode => _themeMode;

  /// The map's own base: follow the theme, force light/dark, or satellite.
  MapBase get mapBase => _mapBase;

  /// Bias routes onto the Prisma Health Swamp Rabbit Trail (the default).
  /// Off sends `trail=0` and the trail prices like any calm street.
  bool get preferTrail => _preferTrail;
  bool get preferCommunity => _preferCommunity;
  /// This device's vote ('up' / 'down') on community feature [id], as the
  /// last vote reply reported it; null when none.
  String? myVote(String id) => _myVotes[id];
  void setMyVote(String id, String? vote) {
    if (vote == 'up' || vote == 'down') {
      _myVotes[id] = vote!;
    } else {
      _myVotes.remove(id);
    }
    notifyListeners();
    _setPref((p) => p.setString(_kMyVotes, jsonEncode(_myVotes)));
  }

  /// Community contributions submitted from this device. The layer GeoJSON
  /// carries no author, so this is how the feature sheet knows to offer
  /// "Remove" to a non-admin (the server still checks ownership).
  bool isMyContribution(String id) => _mine.contains(id);
  void rememberContribution(String id) {
    if (id.isEmpty || !_mine.add(id)) return;
    // ponytail: grows by one id per submission; prune if it ever matters.
    _setPref((p) => p.setStringList(_kMine, _mine.toList()));
  }

  /// First-launch welcome tour already shown (or dismissed for good).
  bool get welcomeSeen => _welcomeSeen;
  void setWelcomeSeen(bool value) {
    _welcomeSeen = value;
    notifyListeners();
    _setPref((p) => p.setBool(_kWelcomeSeen, value));
  }

  /// Version of the safety/liability disclaimer the rider agreed to (0 = none).
  int get disclaimerAcceptedVersion => _disclaimerVersion;
  String? get disclaimerAcceptedAt => _disclaimerAt;
  void acceptDisclaimer(int version) {
    _disclaimerVersion = version;
    _disclaimerAt = DateTime.now().toUtc().toIso8601String();
    notifyListeners();
    _setPref((p) async {
      await p.setInt(_kDisclaimerVersion, version);
      await p.setString(_kDisclaimerAt, _disclaimerAt!);
    });
  }

  /// Calendar events already announced with a local notification.
  bool eventNotified(String uid) => _notifiedEvents.contains(uid);
  void markEventNotified(String uid) {
    if (!_notifiedEvents.add(uid)) return;
    // ponytail: grows by a few uids a month; prune if it ever matters.
    _setPref((p) => p.setStringList(_kNotifiedEvents, _notifiedEvents.toList()));
  }

  Future<void> _setPref(Future<void> Function(SharedPreferences) write) async {
    try {
      await write(await SharedPreferences.getInstance());
    } catch (_) {}
  }

  /// Low-vision support: stronger text/surface contrast and bolder map lines.
  bool get highContrast => _highContrast;

  /// Low-vision support: larger text and controls throughout.
  bool get largeUi => _largeUi;

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

  /// "Navigate here" — the button label on a destination card.
  String get directionsVerb => modeVerbs[mode] ?? 'Navigate here';

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
      def.modes.any(_modes.contains) &&
      (!def.advocacy || _advocacy.contains(def.id)) &&
      (def.fixed || (_overrides[def.id] ?? def.defaultOn));

  /// Layers offered in the layers sheet for the current selection. Fixed
  /// layers are always drawn (no toggle); advocacy layers only appear once
  /// opted into via Settings.
  Iterable<LayerDef> get relevantLayers => layerDefs.where((d) =>
      !d.fixed &&
      d.modes.any(_modes.contains) &&
      (!d.advocacy || _advocacy.contains(d.id)));

  bool advocacyEnabled(String id) => _advocacy.contains(id);

  /// Opting in also switches the layer on, so the effect is immediate; the
  /// layers sheet gains its toggle either way.
  void setAdvocacyLayer(String id, bool on) {
    if (on == _advocacy.contains(id)) return;
    if (on) {
      _advocacy = {..._advocacy, id};
      _overrides[id] = true;
    } else {
      _advocacy = {..._advocacy}..remove(id);
    }
    notifyListeners();
    _save();
  }

  final _loaded = Completer<void>();

  /// Completes once [load] has run (successfully or not).
  Future<void> get loaded => _loaded.future;

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
      final savedPuck = prefs.getString(_kPuck);
      _puckStyle = PuckStyle.values.firstWhere(
        (p) => p.name == savedPuck,
        orElse: () => PuckStyle.arrow,
      );
      final savedTheme = prefs.getString(_kTheme);
      _themeMode = ThemeMode.values.firstWhere(
        (t) => t.name == savedTheme,
        orElse: () => ThemeMode.light,
      );
      final savedBase = prefs.getString(_kMapBase);
      _mapBase = MapBase.values.firstWhere(
        (b) => b.name == savedBase,
        orElse: () => MapBase.auto,
      );
      // Forced light/dark bases are retired (a dark map under light chrome
      // read as a glitch); old persisted picks fold back into follow-theme.
      if (_mapBase == MapBase.light || _mapBase == MapBase.dark) {
        _mapBase = MapBase.auto;
      }
      _preferTrail = prefs.getBool(_kPreferTrail) ?? true;
      _preferCommunity = prefs.getBool(_kPreferCommunity) ?? true;
      final votes = prefs.getString(_kMyVotes);
      if (votes != null) {
        _myVotes = Map<String, String>.from(jsonDecode(votes) as Map);
      }
      _highContrast = prefs.getBool(_kHighContrast) ?? false;
      _largeUi = prefs.getBool(_kLargeUi) ?? false;
      _welcomeSeen = prefs.getBool(_kWelcomeSeen) ?? false;
      _disclaimerVersion = prefs.getInt(_kDisclaimerVersion) ?? 0;
      _disclaimerAt = prefs.getString(_kDisclaimerAt);
      _notifiedEvents =
          (prefs.getStringList(_kNotifiedEvents) ?? const []).toSet();
      _advocacy = (prefs.getStringList(_kAdvocacy) ?? const []).toSet();
      _mine = (prefs.getStringList(_kMine) ?? const []).toSet();
      _recents = [
        for (final s in prefs.getStringList(_kRecents) ?? <String>[])
          Map<String, dynamic>.from(jsonDecode(s) as Map),
      ];
      _saved = [
        for (final s in prefs.getStringList(_kSaved) ?? <String>[])
          Map<String, dynamic>.from(jsonDecode(s) as Map),
      ];
      notifyListeners();
    } catch (_) {
      // Preferences are a convenience; defaults are perfectly usable.
    } finally {
      if (!_loaded.isCompleted) _loaded.complete();
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
      await prefs.setString(_kPuck, _puckStyle.name);
      await prefs.setString(_kTheme, _themeMode.name);
      await prefs.setString(_kMapBase, _mapBase.name);
      await prefs.setBool(_kPreferTrail, _preferTrail);
      await prefs.setBool(_kPreferCommunity, _preferCommunity);
      await prefs.setBool(_kHighContrast, _highContrast);
      await prefs.setBool(_kLargeUi, _largeUi);
      await prefs.setStringList(_kAdvocacy, _advocacy.toList());
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

  void setPuckStyle(PuckStyle value) {
    if (value == _puckStyle) return;
    _puckStyle = value;
    notifyListeners();
    _save();
  }

  void setThemeMode(ThemeMode value) {
    if (value == _themeMode) return;
    _themeMode = value;
    notifyListeners();
    _save();
  }

  void setMapBase(MapBase value) {
    if (value == _mapBase) return;
    _mapBase = value;
    notifyListeners();
    _save();
  }

  void setPreferTrail(bool value) {
    if (value == _preferTrail) return;
    _preferTrail = value;
    notifyListeners();
    _save();
  }

  void setPreferCommunity(bool value) {
    if (value == _preferCommunity) return;
    _preferCommunity = value;
    notifyListeners();
    _save();
  }

  void setHighContrast(bool value) {
    if (value == _highContrast) return;
    _highContrast = value;
    notifyListeners();
    _save();
  }

  void setLargeUi(bool value) {
    if (value == _largeUi) return;
    _largeUi = value;
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

  /// Saved places: same row shape as recents (`label`, `sublabel`, `lat`,
  /// `lon`), kept until removed. A place is identified by its coordinates so a
  /// rename never duplicates it.
  List<Map<String, dynamic>> _saved = [];

  List<Map<String, dynamic>> get savedPlaces => _saved;

  static String _placeKey(Map<String, dynamic> p) =>
      '${(p['lat'] as num).toStringAsFixed(5)},${(p['lon'] as num).toStringAsFixed(5)}';

  bool isSaved(Map<String, dynamic> place) =>
      place['lat'] is num &&
      place['lon'] is num &&
      _saved.any((p) => _placeKey(p) == _placeKey(place));

  /// Save, or when already saved, remove. Returns the removed row so the
  /// caller can offer undo; null when the place was saved.
  Map<String, dynamic>? toggleSaved(Map<String, dynamic> place) {
    if (place['lat'] is! num || place['lon'] is! num) return null;
    if (isSaved(place)) return removeSaved(place);
    _saved = [
      {
        'label': place['label']?.toString() ?? 'Saved place',
        'sublabel': place['sublabel']?.toString() ?? '',
        'lat': (place['lat'] as num).toDouble(),
        'lon': (place['lon'] as num).toDouble(),
      },
      ..._saved,
    ];
    notifyListeners();
    _saveSaved();
    return null;
  }

  Map<String, dynamic>? removeSaved(Map<String, dynamic> place) {
    final index = _saved.indexWhere((p) => _placeKey(p) == _placeKey(place));
    if (index < 0) return null;
    final removed = _saved[index];
    _saved = [..._saved]..removeAt(index);
    notifyListeners();
    _saveSaved();
    return {...removed, '_index': index};
  }

  /// Undo for [removeSaved]: puts the row back where it was.
  void restoreSaved(Map<String, dynamic> removed) {
    final row = Map<String, dynamic>.from(removed)..remove('_index');
    if (isSaved(row)) return;
    final index = ((removed['_index'] as int?) ?? 0).clamp(0, _saved.length);
    _saved = [..._saved]..insert(index, row);
    notifyListeners();
    _saveSaved();
  }

  void renameSaved(Map<String, dynamic> place, String name) {
    final label = name.trim();
    if (label.isEmpty) return;
    _saved = [
      for (final p in _saved)
        _placeKey(p) == _placeKey(place) ? {...p, 'label': label} : p,
    ];
    notifyListeners();
    _saveSaved();
  }

  Future<void> _saveSaved() async {
    try {
      final prefs = await SharedPreferences.getInstance();
      await prefs.setStringList(_kSaved, _saved.map(jsonEncode).toList());
    } catch (_) {}
  }

  // ------------------------------------------------------------ account sync

  /// The rider preferences that follow a signed-in account between devices
  /// (`settings` on `/bwg/auth/me`). Device-only state — votes, the welcome
  /// tour, event notifications, theme and accessibility sizing — stays local.
  Map<String, dynamic> get syncedSettings => {
    'saved_places': _saved,
    'prefer_community': _preferCommunity,
    'prefer_trail': _preferTrail,
    'stress': _stress.name,
    'modes': _modes.map((m) => m.name).toList()..sort(),
    'roll': _roll,
    'ebike': _useEbike,
    'bcycle': _useBcycle,
    'advocacy_layers': _advocacy.toList()..sort(),
    'disclaimer_accepted_version': _disclaimerVersion,
  };

  /// Fold an account's settings into this device on sign-in.
  ///
  /// Saved places: on the first sign-in merge ([union]), a union keyed by
  /// coordinates (local rows and labels first); on later refreshes the
  /// account's list wins, so a place deleted on another device stays gone.
  /// Scalars: the remote value wins only where this device is still on the
  /// default — a choice made here is never overwritten. The disclaimer takes
  /// the higher accepted version.
  // ponytail: no per-field timestamps; last-writer-wins after the first merge.
  void mergeRemoteSettings(Map<String, dynamic> remote, {bool union = true}) {
    final places = remote['saved_places'];
    if (places is List) {
      if (!union) _saved = [];
      final keys = {for (final p in _saved) _placeKey(p)};
      for (final p in places) {
        if (p is! Map || p['lat'] is! num || p['lon'] is! num) continue;
        final row = {
          'label': p['label']?.toString() ?? 'Saved place',
          'sublabel': p['sublabel']?.toString() ?? '',
          'lat': (p['lat'] as num).toDouble(),
          'lon': (p['lon'] as num).toDouble(),
        };
        if (keys.add(_placeKey(row))) _saved = [..._saved, row];
      }
    }
    T? pick<T>(String key) => remote[key] is T ? remote[key] as T : null;
    if (_preferCommunity) _preferCommunity = pick<bool>('prefer_community') ?? true;
    if (_preferTrail) _preferTrail = pick<bool>('prefer_trail') ?? true;
    if (!_roll) _roll = pick<bool>('roll') ?? false;
    if (!_useEbike) _useEbike = pick<bool>('ebike') ?? false;
    if (!_useBcycle) _useBcycle = pick<bool>('bcycle') ?? false;
    if (_stress == BikeStress.balanced) {
      _stress = BikeStress.values.firstWhere(
        (s) => s.name == pick<String>('stress'),
        orElse: () => BikeStress.balanced,
      );
    }
    final modes = pick<List>('modes');
    if (modes != null &&
        setEquals(_modes, const {TravelMode.cyclist})) {
      final parsed = TravelMode.values
          .where((m) => modes.contains(m.name))
          .toSet();
      if (parsed.isNotEmpty) _modes = parsed;
    }
    final advocacy = pick<List>('advocacy_layers');
    if (advocacy != null && _advocacy.isEmpty) {
      _advocacy = advocacy.map((e) => e.toString()).toSet();
      for (final id in _advocacy) {
        _overrides[id] = true;
      }
    }
    final remoteDisclaimer =
        (remote['disclaimer_accepted_version'] as num?)?.toInt() ?? 0;
    if (remoteDisclaimer > _disclaimerVersion) {
      _disclaimerVersion = remoteDisclaimer;
      _setPref((p) => p.setInt(_kDisclaimerVersion, remoteDisclaimer));
    }
    notifyListeners();
    _save();
    _saveSaved();
  }
}
