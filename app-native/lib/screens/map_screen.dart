import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_tts/flutter_tts.dart';
import 'package:geolocator/geolocator.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher_string.dart';
import 'package:wakelock_plus/wakelock_plus.dart';

import '../api.dart';
import '../app_state.dart';
import '../map_icons.dart';
import '../nav.dart';
import '../nav_notifier.dart';
import '../theme.dart';
import '../widgets/elevation_profile.dart';
import 'add_point_sheet.dart';
import 'directions_sheet.dart';
import 'report_sheet.dart';
import 'tools_screen.dart';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  MapLibreMapController? _map;
  bool _styleReady = false;
  bool _locationEnabled = false;

  // Search state.
  final _searchCtl = TextEditingController();
  final _searchFocus = FocusNode();
  Timer? _searchDebounce;
  List<dynamic> _results = [];

  /// Destination picked from search — drives the bottom place card.
  Map<String, dynamic>? _place;

  // Trip planning: both ends of the trip, so a route doesn't have to start
  // where the rider is standing.
  TripEndpoint _from = TripEndpoint.myLocation;
  TripEndpoint? _to;

  /// `from` / `to` while the user is picking that end by tapping the map.
  String? _pickField;

  // Route + turn-by-turn navigation state.
  bool _routing = false;

  /// A route request is in flight — drives the "Finding route…" feedback.
  bool _planning = false;

  /// Monotonic id of the newest _planTrip call; older responses that lose the
  /// race (rapid pill cycling re-plans in bursts) are discarded on arrival.
  int _planSeq = 0;
  NavRoute? _navRoute;
  LatLng? _destination;
  bool _navigating = false;
  NavProgress? _progress;
  StreamSubscription<Position>? _posSub;
  FlutterTts? _tts;
  bool _voice = true;
  int _spokenStep = -1;
  bool _spokenImminent = false;
  int _offRouteHits = 0;
  bool _rerouting = false;

  // Follow camera: on until the user pans away, then a Re-center chip brings
  // it back. `_progAnimUntil` marks our own camera animations so a user
  // gesture can be told apart from the follow camera moving itself.
  bool _followNav = true;
  DateTime _lastCamAt = DateTime.fromMillisecondsSinceEpoch(0);
  DateTime _progAnimUntil = DateTime.now().add(const Duration(seconds: 5));
  LatLng? _lastNavPos;
  double? _lastNavBearing;

  // Persistent notification with the upcoming turn.
  final _navNotifier = NavNotifier();
  int _notifiedStep = -1;
  DateTime _lastNotifyAt = DateTime.fromMillisecondsSinceEpoch(0);

  /// Puck bitmaps already registered with the style (by image name).
  final Set<String> _puckImages = {};
  String _puckImage = 'puck-arrow';

  /// Double-beep on reroute, Google Maps style (MainActivity's ToneGenerator).
  static const _tone = MethodChannel('bwg/tone');

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    context.read<AppState>().addListener(_applyVisibility);
    // Focusing the search field brings back recents — and re-runs whatever is
    // still typed there, so a cleared route is two taps to restore.
    _searchFocus.addListener(() {
      if (!mounted) return;
      setState(() {});
      final q = _searchCtl.text.trim();
      if (_searchFocus.hasFocus && q.length >= 2 && _results.isEmpty) {
        _onSearchChanged(q);
      }
    });
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchFocus.dispose();
    _posSub?.cancel();
    _tts?.stop();
    _navNotifier.cancel();
    WakelockPlus.disable();
    super.dispose();
  }

  // ---------------------------------------------------------------- layers

  Future<void> _onStyleLoaded() async {
    final map = _map!;
    final ratio = MediaQuery.of(context).devicePixelRatio;

    // Tap highlight: one source, two layers — the line layer renders when the
    // tapped feature is a line, the circle layer when it's a point. The
    // geometry-type filters matter: without them the circle layer draws a
    // ring on EVERY VERTEX of a tapped line (the SRT turned into dot soup).
    await map.addSource(
        'highlight', GeojsonSourceProperties(data: _emptyCollection));
    await map.addLineLayer(
      'highlight',
      'lyr-highlight-line',
      const LineLayerProperties(
        lineColor: '#FFC107',
        lineWidth: 12.0,
        lineOpacity: 0.55,
        lineCap: 'round',
        lineJoin: 'round',
      ),
      filter: ['==', ['geometry-type'], 'LineString'],
      enableInteraction: false,
    );
    await map.addCircleLayer(
      'highlight',
      'lyr-highlight-point',
      const CircleLayerProperties(
        circleRadius: 20.0,
        circleColor: '#FFC107',
        circleOpacity: 0.3,
        circleStrokeColor: '#FFC107',
        circleStrokeWidth: 2.5,
      ),
      filter: ['==', ['geometry-type'], 'Point'],
      enableInteraction: false,
    );

    // Route line (casing + fill) and the selection pin, above everything.
    // Thematic layers are added lazily (see _ensureLayer): line layers slot
    // in below the highlight, symbol pins below the route casing, so draw
    // order stays lines → highlight → pins → route → pin.
    await map.addSource(
        'route', GeojsonSourceProperties(data: _emptyCollection));
    await map.addLineLayer(
      'route',
      'lyr-route-casing',
      const LineLayerProperties(
          lineColor: '#ffffff', lineWidth: 8.0, lineCap: 'round', lineJoin: 'round'),
      enableInteraction: false,
    );
    await map.addLineLayer(
      'route',
      'lyr-route',
      LineLayerProperties(
          lineColor: [
            'coalesce',
            ['get', 'color'],
            '#1565C0',
          ],
          lineWidth: 5.0,
          lineCap: 'round',
          lineJoin: 'round'),
      enableInteraction: false,
    );

    // Hills, drawn over the route line and colored by severity (amber →
    // red), so "how hard is this trip" is visible on the map itself and not
    // only in the elevation graph.
    await map.addSource(
        'route-hills', GeojsonSourceProperties(data: _emptyCollection));
    await map.addLineLayer(
      'route-hills',
      'lyr-route-hills',
      LineLayerProperties(
        lineColor: [
          'match',
          ['get', 'sev'],
          for (final e in hillColors.entries) ...[e.key, e.value],
          hillColors['mod']!,
        ],
        lineWidth: 5.0,
        lineCap: 'round',
        lineJoin: 'round',
      ),
      enableInteraction: false,
    );

    // Gaps in the network, drawn dashed ON TOP of the route: the stretches with
    // no sidewalk (walk/roll) or no bike lane (bike). The route still goes
    // there — this is the disclosure, not a detour.
    await map.addSource(
        'route-warn', GeojsonSourceProperties(data: _emptyCollection));
    await map.addLineLayer(
      'route-warn',
      'lyr-route-warn',
      const LineLayerProperties(
        lineColor: '#D32F2F',
        lineWidth: 5.0,
        lineDasharray: [2.0, 1.6],
        lineCap: 'butt',
        lineJoin: 'round',
      ),
      enableInteraction: false,
    );

    // Upcoming turns, on the map itself: an arrowhead at each maneuver rotated
    // to the heading the rider leaves it on.
    await map.addImage(
      'turn-marker',
      await renderTurnMarker(color: brandDark, devicePixelRatio: ratio),
    );
    await map.addSource(
        'route-steps', GeojsonSourceProperties(data: _emptyCollection));
    await map.addSymbolLayer(
      'route-steps',
      'lyr-route-steps',
      const SymbolLayerProperties(
        iconImage: 'turn-marker',
        iconRotate: ['get', 'bearing'],
        iconRotationAlignment: 'map',
        iconAllowOverlap: true,
        iconIgnorePlacement: true,
        iconSize: [
          'interpolate',
          ['linear'],
          ['zoom'],
          12.0,
          0.6,
          16.0,
          1.0,
        ],
      ),
      enableInteraction: false,
    );

    await map.addSource('pin', GeojsonSourceProperties(data: _emptyCollection));
    await map.addCircleLayer(
      'pin',
      'lyr-pin',
      const CircleLayerProperties(
        circleColor: '#6F9920',
        circleRadius: 9.0,
        circleStrokeColor: '#ffffff',
        circleStrokeWidth: 3.0,
      ),
      enableInteraction: false,
    );

    // The rider themselves, during navigation: an arrow (or mode icon —
    // Settings) rotated to the heading, above everything else. The native
    // blue dot is hidden while this is on screen.
    await map.addSource(
        'puck', GeojsonSourceProperties(data: _emptyCollection));
    await map.addSymbolLayer(
      'puck',
      'lyr-puck',
      const SymbolLayerProperties(
        iconImage: ['get', 'icon'],
        iconRotate: ['get', 'bearing'],
        iconRotationAlignment: 'map',
        iconAllowOverlap: true,
        iconIgnorePlacement: true,
        iconSize: 1.0,
      ),
      enableInteraction: false,
    );

    _styleReady = true;
    _applyVisibility();
    _refreshLive();
  }

  static const _emptyCollection = {
    'type': 'FeatureCollection',
    'features': <dynamic>[],
  };

  /// Layers already added to the style. Sources are only fetched when a layer
  /// first becomes visible — the county sidewalk GeoJSON alone is megabytes,
  /// and loading every layer up front made first paint network-bound.
  final Set<String> _addedLayers = {};

  Future<void> _ensureLayer(LayerDef def) async {
    final map = _map;
    if (map == null || _addedLayers.contains(def.id)) return;
    _addedLayers.add(def.id); // claim before the first await (re-entrancy)
    try {
      final url = await api.layerUrl(def.path);
      await map.addSource(def.id, GeojsonSourceProperties(data: url));
      if (def.isPoint) {
        if (!mounted) return;
        await map.addImage(
          'pin-${def.id}',
          await renderPin(
            icon: def.icon,
            color: hexColor(def.color),
            devicePixelRatio: MediaQuery.of(context).devicePixelRatio,
            scale: def.pinScale,
          ),
        );
        await map.addSymbolLayer(
          def.id,
          'lyr-${def.id}',
          SymbolLayerProperties(
            iconImage: 'pin-${def.id}',
            iconSize: [
              'interpolate',
              ['linear'],
              ['zoom'],
              11.0,
              0.45 * def.pinScale,
              14.0,
              0.7 * def.pinScale,
              17.0,
              0.95 * def.pinScale,
            ],
            iconAnchor: 'bottom',
            // Reports are the point of the app — never declutter them.
            iconAllowOverlap: def.id == 'reports',
          ),
          minzoom: def.minZoom > 0 ? def.minZoom : null,
          belowLayerId: 'lyr-route-casing',
        );
      } else if (def.isFill) {
        // Polygon layers (parking land use) sit under every line so streets
        // and trails stay legible on top of them.
        await map.addFillLayer(
          def.id,
          'lyr-${def.id}',
          FillLayerProperties(
            fillColor: _layerColorExpr(def),
            fillOpacity: 0.45,
            fillOutlineColor: def.color,
          ),
          belowLayerId: 'lyr-highlight-line',
        );
      } else {
        await map.addLineLayer(
          def.id,
          'lyr-${def.id}',
          LineLayerProperties(
            lineColor: def.colorByStress
                ? [
                    'match',
                    ['get', 'stress_level'],
                    for (final e in stressColors.entries) ...[e.key, e.value],
                    '#9e9e9e',
                  ]
                : _layerColorExpr(def),
            lineWidth: def.width,
            lineOpacity: def.opacity,
            lineCap: 'round',
            lineJoin: 'round',
          ),
          belowLayerId: 'lyr-highlight-line',
        );
      }
    } catch (_) {
      _addedLayers.remove(def.id); // retry on the next visibility pass
    }
  }

  /// Data-driven color: a per-layer property match (lots vs garages), else a
  /// per-feature `color` (GTFS routes), else the layer's own color.
  dynamic _layerColorExpr(LayerDef def) {
    if (def.matchProp != null && def.matchColors != null) {
      return [
        'match',
        ['get', def.matchProp!],
        for (final e in def.matchColors!.entries) ...[e.key, e.value],
        def.color,
      ];
    }
    return [
      'coalesce',
      ['get', 'color'],
      def.color,
    ];
  }

  void _applyVisibility() {
    if (!_styleReady || _map == null) return;
    final state = context.read<AppState>();
    for (final def in layerDefs) {
      // While navigating, every thematic overlay comes off: what a rider needs
      // mid-turn is the street grid and its labels, not the stress colouring.
      final visible = _navigating ? false : state.layerVisible(def);
      if (visible && !_addedLayers.contains(def.id)) {
        _ensureLayer(def);
        continue; // added visible; nothing to toggle yet
      }
      if (_addedLayers.contains(def.id)) {
        _map!.setLayerVisibility('lyr-${def.id}', visible);
      }
    }
  }

  /// Re-pull a layer whose contents go stale (bike-share availability).
  Future<void> _refreshLive() async {
    if (!_styleReady || _map == null) return;
    final state = context.read<AppState>();
    for (final def in layerDefs.where((d) => d.live)) {
      if (!state.layerVisible(def) || !_addedLayers.contains(def.id)) continue;
      try {
        await _map!.setGeoJsonSource(def.id, await api.layerGeoJson(def.path));
      } catch (_) {
        // Keep whatever the map already has.
      }
    }
  }

  // ------------------------------------------------------------ interactions

  /// Taps on interactive layers arrive here (this maplibre_gl fork suppresses
  /// onMapClick for feature taps); we query the tapped layer for the feature's
  /// properties.
  Future<void> _onFeatureTap(math.Point<double> point, LatLng latLng,
      String id, String layerId, Annotation? annotation) async {
    HapticFeedback.selectionClick();
    final map = _map!;
    var features = await map.queryRenderedFeaturesInRect(
        Rect.fromCenter(
            center: Offset(point.x, point.y), width: 24, height: 24),
        [layerId],
        null);
    if (features.isEmpty && mounted) {
      // Some platforms expect device pixels here.
      final ratio = MediaQuery.of(context).devicePixelRatio;
      features = await map.queryRenderedFeaturesInRect(
          Rect.fromCenter(
              center: Offset(point.x * ratio, point.y * ratio),
              width: 24 * ratio,
              height: 24 * ratio),
          [layerId],
          null);
    }
    if (!mounted || features.isEmpty) return;
    final f = Map<String, dynamic>.from(features.first as Map);
    f['layer'] = {'id': layerId};
    await _setHighlight(f, latLng);
    if (mounted) _showFeatureSheet(f, latLng);
  }

  /// Outlines the tapped feature so it's obvious the tap registered.
  Future<void> _setHighlight(Map<String, dynamic> feature, LatLng at) async {
    final geometry = feature['geometry'] ??
        {
          'type': 'Point',
          'coordinates': [at.longitude, at.latitude],
        };
    await _map?.setGeoJsonSource('highlight', {
      'type': 'FeatureCollection',
      'features': [
        {'type': 'Feature', 'geometry': geometry, 'properties': {}},
      ],
    });
  }

  void _clearHighlight() => _map?.setGeoJsonSource('highlight', _emptyCollection);

  /// Plain taps land here (feature taps go to [_onFeatureTap] instead).
  /// Tapping anywhere now drops a pin and offers the same actions long-press
  /// always did — nobody discovers long-press on their own.
  Future<void> _onMapClick(math.Point<double> point, LatLng latLng) async {
    if (_navigating) return;
    // First tap with the keyboard up just dismisses it.
    if (_searchFocus.hasFocus) {
      _searchFocus.unfocus();
      return;
    }
    // A tap while picking a trip endpoint means "there", not "what's here?".
    if (_pickField != null) {
      await _applyPick(latLng);
      return;
    }
    await _showPlaceActions(latLng);
  }

  Future<void> _onMapLongClick(math.Point<double> point, LatLng latLng) =>
      _showPlaceActions(latLng);

  Future<void> _showPlaceActions(LatLng latLng) async {
    await _setPin(latLng);
    if (!mounted) return;
    final state = context.read<AppState>();
    showModalBottomSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: Icon(state.iconFor(state.mode), color: brandGreen),
              title: Text(state.directionsVerb),
              subtitle: const Text('Turn-by-turn directions to this spot'),
              onTap: () {
                Navigator.pop(ctx);
                _routeTo(latLng);
              },
            ),
            ListTile(
              leading: const Icon(Icons.alt_route, color: brandGreen),
              title: const Text('Plan a trip from here'),
              subtitle: const Text('Pick a start point and travel modes'),
              onTap: () {
                Navigator.pop(ctx);
                _openDirections(
                  to: TripEndpoint(label: 'Dropped pin', latLng: latLng),
                );
              },
            ),
            ListTile(
              leading: const Icon(Icons.report_problem, color: Color(0xFFF9A825)),
              title: const Text('Report an issue here'),
              subtitle: const Text('Sidewalks, crossings, bike lanes…'),
              onTap: () {
                Navigator.pop(ctx);
                _openReportSheet(latLng);
              },
            ),
            ListTile(
              leading: const Icon(Icons.add_location_alt, color: brandGreen),
              title: const Text('Add a missing place here'),
              subtitle: const Text('Bike parking, repair station, fountain…'),
              onTap: () {
                Navigator.pop(ctx);
                _openAddPointSheet(latLng);
              },
            ),
            ListTile(
              leading: const Icon(Icons.badge_outlined),
              title: const Text('Who owns this road?'),
              onTap: () {
                Navigator.pop(ctx);
                _showRoadInfo(latLng);
              },
            ),
          ],
        ),
      ),
    ).whenComplete(() => _clearPinIfIdle());
  }

  Future<void> _setPin(LatLng latLng) async {
    await _map?.setGeoJsonSource('pin', {
      'type': 'FeatureCollection',
      'features': [
        {
          'type': 'Feature',
          'geometry': {
            'type': 'Point',
            'coordinates': [latLng.longitude, latLng.latitude],
          },
          'properties': {},
        }
      ],
    });
  }

  /// Restore the pin to whatever still matters (route destination, searched
  /// place) or clear it.
  void _clearPinIfIdle() {
    if (_routing && _destination != null) {
      _setPin(_destination!);
      return;
    }
    final r = _place;
    if (r != null) {
      _setPin(LatLng(
        (r['lat'] as num).toDouble(),
        (r['lon'] as num).toDouble(),
      ));
      return;
    }
    _map?.setGeoJsonSource('pin', _emptyCollection);
  }

  // ----------------------------------------------------------- feature info

  void _showFeatureSheet(Map<String, dynamic> feature, LatLng latLng) {
    final props = Map<String, dynamic>.from(feature['properties'] ?? {});
    final layerId = (feature['layer'] is Map)
        ? ((feature['layer'] as Map)['id'] ?? '').toString()
        : '';
    final def = layerDefs
        .where((d) => 'lyr-${d.id}' == layerId)
        .cast<LayerDef?>()
        .firstWhere((_) => true, orElse: () => null);

    String title = (props['name'] ??
            props['label'] ??
            props['street_name'] ??
            props['full_name'] ??
            props['NAME'] ??
            props['STREET_NAM'] ??
            def?.label ??
            'Feature')
        .toString();
    if (def?.id == 'bike-stress') {
      final lvl = stressLabels[props['stress_level']] ?? '';
      if (lvl.isNotEmpty) title = '$title — $lvl';
    }

    final isBcycle = def?.id == 'bcycle';
    final isGarage = def?.id == 'parking-garages';
    final skip = {
      'name', 'label', 'street_name', 'geojson', 'color', 'id',
      // BCycle internals: the availability line already says it better.
      if (isBcycle) ...{
        'short_id', 'rental_uri', 'bikes', 'ebikes', 'docks',
        'is_renting', 'is_returning', 'last_reported',
      },
      // Garage internals: the availability line already says it better.
      if (isGarage) ...{'capacity', 'occupied', 'percent_occupied', 'as_of'},
    };
    final rows = props.entries
        .where((e) =>
            !skip.contains(e.key) &&
            e.value != null &&
            e.value.toString().trim().isNotEmpty)
        .take(8)
        .toList();

    // Point features (bus stops, bike parking, repair stations) navigate to
    // their exact coordinate, not the finger's.
    var target = latLng;
    final geom = feature['geometry'];
    if (geom is Map && geom['type'] == 'Point' && geom['coordinates'] is List) {
      final c = geom['coordinates'] as List;
      target = LatLng((c[1] as num).toDouble(), (c[0] as num).toDouble());
    }
    final state = context.read<AppState>();

    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(children: [
                Icon(def?.icon ?? Icons.place,
                    color: isBcycle ? hexColor(bcycleRed) : brandGreen),
                const SizedBox(width: 8),
                Expanded(
                    child: Text(title,
                        style: Theme.of(ctx).textTheme.titleMedium)),
              ]),
              const SizedBox(height: 8),
              if (isBcycle) _bcycleAvailability(ctx, props),
              for (final e in rows)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Text('${_prettyKey(e.key)}: ${e.value}'),
                ),
              const SizedBox(height: 8),
              if (isBcycle)
                Padding(
                  padding: const EdgeInsets.only(bottom: 8),
                  child: OutlinedButton.icon(
                    icon: const Icon(Icons.open_in_new, size: 18),
                    label: const Text('Open the BCycle app to unlock'),
                    onPressed: () =>
                        _openBcycleApp(props['rental_uri']?.toString()),
                  ),
                ),
              Row(children: [
                FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: brandGreen,
                    foregroundColor: Colors.white,
                  ),
                  icon: Icon(state.iconFor(state.mode), size: 18),
                  label: const Text('Directions'),
                  onPressed: () {
                    Navigator.pop(ctx);
                    _routeTo(target, label: title);
                  },
                ),
                const Spacer(),
                IconButton(
                  tooltip: 'Who owns this road?',
                  icon: const Icon(Icons.badge_outlined),
                  onPressed: () {
                    Navigator.pop(ctx);
                    _showRoadInfo(latLng);
                  },
                ),
                TextButton.icon(
                  icon: const Icon(Icons.report_problem_outlined),
                  label: const Text('Report'),
                  onPressed: () {
                    Navigator.pop(ctx);
                    _openReportSheet(latLng,
                        spotName: def?.id == 'bike-parking' ? title : null);
                  },
                ),
              ]),
            ],
          ),
        ),
      ),
    ).whenComplete(_clearHighlight);
  }

  String _prettyKey(String k) =>
      k.replaceAll('_', ' ').replaceAll('-', ' ').trim();

  /// Live dock counts, the one thing a rider needs before walking to a station.
  Widget _bcycleAvailability(BuildContext ctx, Map<String, dynamic> props) {
    final bikes = (props['bikes'] as num?)?.toInt();
    final ebikes = (props['ebikes'] as num?)?.toInt() ?? 0;
    final docks = (props['docks'] as num?)?.toInt();
    final renting = props['is_renting'] != false;
    if (bikes == null && docks == null) {
      return const Padding(
        padding: EdgeInsets.only(bottom: 8),
        child: Text('Live availability unavailable right now.',
            style: TextStyle(color: Colors.black54)),
      );
    }
    final color = !renting || (bikes ?? 0) == 0 ? warnRed : brandGreen;
    return Padding(
      padding: const EdgeInsets.only(bottom: 8),
      child: Row(
        children: [
          Icon(Icons.pedal_bike, color: color, size: 20),
          const SizedBox(width: 6),
          Expanded(
            child: Text(
              !renting
                  ? 'Not renting right now'
                  : '${bikes ?? 0} bike${bikes == 1 ? '' : 's'} available'
                      '${ebikes > 0 ? ' ($ebikes electric)' : ''}'
                      '${docks != null ? ' · $docks open dock${docks == 1 ? '' : 's'}' : ''}',
              style: TextStyle(color: color, fontWeight: FontWeight.w600),
            ),
          ),
        ],
      ),
    );
  }

  /// Hand off to the BCycle app: the station's own deep link when GBFS gave us
  /// one, then the app's discovery scheme, then the Play Store listing.
  ///
  /// Every candidate is tried app-first (`externalNonBrowserApplication`) so a
  /// https deep link opens the installed app instead of a browser tab; only
  /// the Play Store fallback is allowed to land anywhere else. The manifest
  /// carries `<queries>` for the `bcycle` scheme + package — without those,
  /// Android 11+ package visibility makes every launch silently unresolvable.
  Future<void> _openBcycleApp(String? stationUri) async {
    final candidates = <String>[
      if (stationUri != null && stationUri.isNotEmpty) stationUri,
      'bcycle://',
    ];
    for (final uri in candidates) {
      try {
        if (await launchUrlString(uri,
            mode: LaunchMode.externalNonBrowserApplication)) {
          return;
        }
      } catch (_) {
        // Try the next fallback.
      }
    }
    try {
      if (await launchUrlString(
        'https://play.google.com/store/apps/details?id=com.bcycle',
        mode: LaunchMode.externalApplication,
      )) {
        return;
      }
    } catch (_) {}
    if (mounted) toast(context, 'Could not open the BCycle app.');
  }

  Future<void> _showRoadInfo(LatLng latLng) async {
    try {
      final info = await api.roadInfo(latLng.latitude, latLng.longitude);
      if (!mounted) return;
      showModalBottomSheet(
        context: context,
        builder: (ctx) => RoadInfoSheet(info: info),
      );
    } on Exception catch (e) {
      if (mounted) toast(context, e.toString());
    }
  }

  // ---------------------------------------------------------------- routing

  /// One-tap "directions to here": keeps whatever start is currently set
  /// (your location unless the planner says otherwise).
  Future<void> _routeTo(LatLng dest, {String? label}) => _planTrip(
        to: TripEndpoint(label: label ?? 'Dropped pin', latLng: dest),
      );

  /// Route between the two trip endpoints with the rider's selected modes.
  ///
  /// [plan] pins a specific itinerary (the alternatives chips); otherwise the
  /// router picks the fastest of everything the modes allow.
  Future<void> _planTrip({
    TripEndpoint? from,
    TripEndpoint? to,
    String? plan,
    bool silent = false,
  }) async {
    final state = context.read<AppState>();
    final startPoint = from ?? _from;
    final endPoint = to ?? _to;
    if (endPoint == null) return;

    final origin = startPoint.isMyLocation
        ? await _bestOrigin()
        : startPoint.latLng;
    final dest = endPoint.isMyLocation ? await _bestOrigin() : endPoint.latLng;
    if (origin == null || dest == null) {
      if (mounted) {
        toast(context, 'Turn on location, or pick a start point on the map.');
      }
      return;
    }
    final seq = ++_planSeq;
    if (!silent) {
      setState(() {
        _routing = true;
        _planning = true;
      });
    }
    try {
      final feature = await api.route(
        origin.latitude,
        origin.longitude,
        dest.latitude,
        dest.longitude,
        modes: state.apiModes,
        roll: state.roll,
        bcycle: state.useBcycle,
        ebike: state.useEbike,
        stress: state.stressApiName,
        plan: plan,
      );
      // A newer plan superseded this one while it was in flight.
      if (seq != _planSeq) return;
      final route = NavRoute.fromFeature(feature);
      // Per-leg colors: a multi-modal itinerary draws each leg in its mode's
      // color (bus legs in the official Greenlink route color).
      await _map?.setGeoJsonSource('route', route.routeCollection());
      await _map?.setGeoJsonSource('route-hills', route.hillCollection());
      await _map?.setGeoJsonSource('route-warn', route.warnCollection());
      await _map?.setGeoJsonSource('route-steps', route.stepCollection());
      await _setPin(dest);
      if (!mounted) return;
      setState(() {
        _routing = true;
        _navRoute = route;
        _destination = dest;
        _from = startPoint;
        _to = endPoint;
        _place = null;
      });
      if (!_navigating) await _fitRoute(route);
    } on Exception catch (e) {
      if (!mounted || seq != _planSeq) return;
      setState(() => _routing = _navRoute != null);
      toast(context, e.toString());
    } finally {
      // Only the newest request may clear the spinner.
      if (mounted && seq == _planSeq && _planning) {
        setState(() => _planning = false);
      }
    }
  }

  // ------------------------------------------------------------- trip planner

  /// Open the planner, then act on what it returns: route the trip, or drop
  /// into map-pick mode for whichever end the user wants to tap out.
  Future<void> _openDirections({TripEndpoint? to}) async {
    final result = await showModalBottomSheet<DirectionsResult>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => DirectionsSheet(
        from: _from,
        to: to ?? _to ?? const TripEndpoint(label: ''),
      ),
    );
    if (result == null || !mounted) return;
    setState(() {
      _from = result.from ?? _from;
      _to = (result.to?.isEmpty ?? true) ? _to : result.to;
    });
    if (result.isPick) {
      setState(() => _pickField = result.pickField);
      toast(
        context,
        result.pickField == 'from'
            ? 'Tap the map to set your start point.'
            : 'Tap the map to set your destination.',
      );
      return;
    }
    await _planTrip();
  }

  /// Map tap while picking a trip endpoint.
  Future<void> _applyPick(LatLng latLng) async {
    final field = _pickField;
    if (field == null) return;
    final endpoint = TripEndpoint(
      label: field == 'from' ? 'Point on the map' : 'Dropped pin',
      latLng: latLng,
    );
    setState(() {
      _pickField = null;
      if (field == 'from') {
        _from = endpoint;
      } else {
        _to = endpoint;
      }
    });
    await _setPin(latLng);
    await _openDirections();
  }

  Future<void> _fitRoute(NavRoute route) async {
    if (route.isEmpty) return;
    var minLat = 90.0, maxLat = -90.0, minLon = 180.0, maxLon = -180.0;
    for (final p in route.points) {
      minLat = math.min(minLat, p.latitude);
      maxLat = math.max(maxLat, p.latitude);
      minLon = math.min(minLon, p.longitude);
      maxLon = math.max(maxLon, p.longitude);
    }
    await _map?.animateCamera(
      CameraUpdate.newLatLngBounds(
        LatLngBounds(
          southwest: LatLng(minLat, minLon),
          northeast: LatLng(maxLat, maxLon),
        ),
        left: 40,
        right: 40,
        top: 180,
        bottom: 220,
      ),
    );
    // Isometric preview: tip the camera after the fit so the route reads in
    // 3D, the way the follow camera will show it once navigation starts.
    await _map?.animateCamera(CameraUpdate.tiltTo(35.0));
  }

  Future<void> _clearRoute() async {
    await _stopNav();
    await _map?.setGeoJsonSource('route', _emptyCollection);
    await _map?.setGeoJsonSource('route-hills', _emptyCollection);
    await _map?.setGeoJsonSource('route-warn', _emptyCollection);
    await _map?.setGeoJsonSource('route-steps', _emptyCollection);
    await _map?.setGeoJsonSource('pin', _emptyCollection);
    if (!mounted) return;
    setState(() {
      _routing = false;
      _navRoute = null;
      _destination = null;
      _progress = null;
      _place = null;
      _to = null;
      _from = TripEndpoint.myLocation;
    });
    await _map?.animateCamera(CameraUpdate.tiltTo(0.0));
  }

  // ------------------------------------------------------------- navigation

  /// Enter turn-by-turn mode: follow camera, spoken maneuvers, off-route
  /// recalculation — the ride actually being guided, not just drawn.
  Future<void> _startNav() async {
    final route = _navRoute;
    if (route == null || route.isEmpty) return;
    final pos = await _currentPosition();
    if (pos == null) {
      if (mounted) toast(context, 'Location is required to navigate.');
      return;
    }
    await _initTts();
    await WakelockPlus.enable();
    await _ensurePuckImage();
    if (!mounted) return;
    setState(() {
      _navigating = true;
      _followNav = true;
      _spokenStep = -1;
      _spokenImminent = false;
      _offRouteHits = 0;
      _notifiedStep = -1;
      _lastNotifyAt = DateTime.fromMillisecondsSinceEpoch(0);
      _lastCamAt = DateTime.fromMillisecondsSinceEpoch(0);
      // Ignore camera chatter from the initial fly-in.
      _progAnimUntil = DateTime.now().add(const Duration(seconds: 3));
    });
    // Strip the thematic overlays so the street layout underneath is legible.
    _applyVisibility();
    await _posSub?.cancel();
    // distanceFilter 0: fixes keep coming (~1/s) even at a standstill — a
    // filter of 5 m meant a stopped rider got NO fixes, so off-route
    // detection and the follow camera both froze exactly when someone pulled
    // over to look at the phone.
    _posSub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        distanceFilter: 0,
      ),
    ).listen(_onNavPosition);
    _onNavPosition(pos);
  }

  /// Register the rider marker bitmap the current settings call for; the puck
  /// layer picks it by name per feature.
  Future<void> _ensurePuckImage() async {
    final map = _map;
    if (map == null || !mounted) return;
    final state = context.read<AppState>();
    final byMode = state.puckStyle == PuckStyle.mode;
    _puckImage = byMode ? 'puck-${state.labelFor(state.mode)}' : 'puck-arrow';
    if (_puckImages.contains(_puckImage)) return;
    try {
      await map.addImage(
        _puckImage,
        await renderPuck(
          color: byMode ? brandGreen : const Color(0xFF1A73E8),
          devicePixelRatio: MediaQuery.of(context).devicePixelRatio,
          icon: byMode ? state.iconFor(state.mode) : null,
        ),
      );
      _puckImages.add(_puckImage);
    } catch (_) {
      _puckImage = 'puck-arrow'; // native dot still shows if this also fails
    }
  }

  Future<void> _updatePuck(LatLng at, double bearing) async {
    await _map?.setGeoJsonSource('puck', {
      'type': 'FeatureCollection',
      'features': [
        {
          'type': 'Feature',
          'geometry': {
            'type': 'Point',
            'coordinates': [at.longitude, at.latitude],
          },
          'properties': {'icon': _puckImage, 'bearing': bearing},
        }
      ],
    });
  }

  Future<void> _stopNav({bool arrived = false}) async {
    await _posSub?.cancel();
    _posSub = null;
    await _tts?.stop();
    await _navNotifier.cancel();
    await WakelockPlus.disable();
    await _map?.setGeoJsonSource('puck', _emptyCollection);
    if (!mounted || !_navigating) return;
    setState(() {
      _navigating = false;
      _progress = null;
    });
    _applyVisibility();
    final here = _map?.cameraPosition?.target;
    if (here != null) {
      await _map?.animateCamera(CameraUpdate.newCameraPosition(
        CameraPosition(target: here, zoom: 15.0, bearing: 0, tilt: 0),
      ));
    }
    if (arrived && mounted) toast(context, 'You have arrived.');
  }

  Future<void> _onNavPosition(Position pos) async {
    final route = _navRoute;
    if (!_navigating || route == null) return;
    final here = LatLng(pos.latitude, pos.longitude);
    final progress = NavProgress.of(route, here);
    if (progress == null || !mounted) return;
    final advanced = progress.stepIndex != _progress?.stepIndex;
    setState(() => _progress = progress);
    _lastNavPos = here;
    // Course-up: GPS heading while moving, else the route's own bearing —
    // which is what puts the next turn at the top of the screen from the
    // moment Start is tapped, before the rider is even rolling.
    _lastNavBearing = (pos.heading >= 0 && pos.speed > 0.8)
        ? pos.heading
        : progress.courseBearing;
    // The rider's own marker rides the route, snapped onto the line so GPS
    // scatter doesn't drag the arrow through front yards.
    await _updatePuck(
      progress.offRouteM < 30 ? progress.snapped : here,
      _lastNavBearing!,
    );
    // Drop the arrows for turns already made, so what's on the map is only
    // what's still ahead.
    if (advanced) {
      await _map?.setGeoJsonSource(
        'route-steps',
        route.stepCollection(fromStep: progress.stepIndex),
      );
    }

    // Follow camera, throttled: a fix must not queue up a backlog of
    // animations — that is exactly the jitter that made nav mode unusable.
    // Fixes arrive ~1/s; a ~1 s animation started at most every ~600 ms keeps
    // the camera gliding continuously instead of hopping fix to fix.
    if (_followNav &&
        DateTime.now().difference(_lastCamAt) >
            const Duration(milliseconds: 600)) {
      _moveNavCamera(here, _lastNavBearing!);
    }

    if (progress.remainingM < 25) {
      await _speak('You have arrived.');
      await _stopNav(arrived: true);
      await _clearRoute();
      return;
    }

    _updateNavNotification(route, progress, advanced);
    _announce(route, progress);
    await _maybeReroute(progress);
  }

  /// One programmatic camera move. Deliberately does NOT touch
  /// [_progAnimUntil]: routine follow animations stay within the distance
  /// threshold [_onCameraMove] checks, and extending the suppression window
  /// on every fix would blind gesture detection for the whole trip.
  void _moveNavCamera(LatLng target, double bearing) {
    _lastCamAt = DateTime.now();
    _map?.animateCamera(
      CameraUpdate.newCameraPosition(CameraPosition(
        target: target,
        zoom: 17.5,
        bearing: bearing,
        tilt: 60.0,
      )),
      // Slightly longer than the ~1 s between GPS fixes, so consecutive
      // animations blend into one continuous glide.
      duration: const Duration(milliseconds: 1100),
    );
  }

  /// The user panning away to look at something stops the follow camera; the
  /// Re-center chip brings it back.
  ///
  /// Detection is by DISTANCE, not timing: our own follow animations keep the
  /// camera within metres of the rider, so a camera target far from the last
  /// fix can only be a user drag. (A time-window heuristic doesn't work here —
  /// follow animations fire continuously, so their suppression windows overlap
  /// and a real drag would almost never be seen.) `_progAnimUntil` only covers
  /// the two legitimately-far programmatic moves: the fly-in at nav start and
  /// the Re-center animation itself.
  void _onCameraMove(CameraPosition pos) {
    if (!_navigating || !_followNav) return;
    if (DateTime.now().isBefore(_progAnimUntil)) return;
    final here = _lastNavPos;
    if (here == null) return;
    if (metersBetween(pos.target, here) > 120) {
      setState(() => _followNav = false);
    }
  }

  void _recenterNav() {
    setState(() => _followNav = true);
    // The camera may be far away right now — that animation is ours.
    _progAnimUntil = DateTime.now().add(const Duration(milliseconds: 1500));
    final here = _lastNavPos;
    if (here != null) {
      _moveNavCamera(here, _lastNavBearing ?? _progress?.courseBearing ?? 0);
    }
  }

  /// The upcoming turn, pinned in the notification shade.
  void _updateNavNotification(
      NavRoute route, NavProgress progress, bool advanced) {
    if (route.steps.isEmpty) return;
    final nextIndex =
        math.min(progress.stepIndex + 1, route.steps.length - 1);
    final now = DateTime.now();
    if (!advanced &&
        nextIndex == _notifiedStep &&
        now.difference(_lastNotifyAt) < const Duration(seconds: 15)) {
      return;
    }
    _notifiedStep = nextIndex;
    _lastNotifyAt = now;
    final step = route.steps[nextIndex];
    final etaMin = route.durationMin <= 0 || route.distanceM <= 0
        ? 0.0
        : route.durationMin * (progress.remainingM / route.distanceM);
    _navNotifier.update(
      instruction:
          'In ${formatDistance(progress.distanceToManeuverM)}: ${step.instruction}',
      detail:
          '${formatDistance(progress.remainingM)} left · ${formatDuration(etaMin)}',
    );
  }

  /// Two prompts per maneuver, the way every nav app does it: a heads-up at
  /// ~200 m and the bare instruction right before the turn.
  void _announce(NavRoute route, NavProgress p) {
    if (route.steps.isEmpty) return;
    final nextIndex = math.min(p.stepIndex + 1, route.steps.length - 1);
    final step = route.steps[nextIndex];
    final d = p.distanceToManeuverM;
    if (nextIndex != _spokenStep && d < 230) {
      _spokenStep = nextIndex;
      _spokenImminent = false;
      _speak('In ${formatDistance(d)}, ${step.instruction}');
    } else if (nextIndex == _spokenStep && !_spokenImminent && d < 45) {
      _spokenImminent = true;
      _speak(step.instruction);
    }
  }

  Future<void> _maybeReroute(NavProgress p) async {
    if (p.offRouteM > 45) {
      _offRouteHits++;
    } else {
      _offRouteHits = 0;
    }
    if (_offRouteHits < 3 || _rerouting || _destination == null) return;
    _rerouting = true;
    _offRouteHits = 0;
    // The Google-style cue: a tone first, words second.
    try {
      await _tone.invokeMethod('reroute');
    } catch (_) {}
    await _speak('Rerouting.');
    if (mounted) toast(context, 'Off route — recalculating…');
    // Recompute from where the rider actually is, keeping the same itinerary
    // (no silent switch from "bike + bus" to "walk" mid-trip).
    await _planTrip(
      from: TripEndpoint(label: 'Current position', latLng: p.snapped),
      to: TripEndpoint(label: 'Destination', latLng: _destination!),
      plan: _navRoute?.plan,
      silent: true,
    );
    _spokenStep = -1;
    _spokenImminent = false;
    _rerouting = false;
  }

  Future<void> _initTts() async {
    if (_tts != null) return;
    final tts = FlutterTts();
    try {
      await tts.setLanguage('en-US');
      await tts.setSpeechRate(0.5);
      await tts.setVolume(1.0);
    } catch (_) {
      // Voice is a nicety; the banner still guides the ride.
    }
    _tts = tts;
  }

  Future<void> _speak(String text) async {
    if (!_voice) return;
    try {
      await _tts?.stop();
      await _tts?.speak(text);
    } catch (_) {}
  }

  Future<LatLng?> _bestOrigin() async {
    final pos = await _currentPosition();
    if (pos != null) return LatLng(pos.latitude, pos.longitude);
    return null;
  }

  // --------------------------------------------------------------- location

  Future<Position?> _currentPosition() async {
    try {
      var permission = await Geolocator.checkPermission();
      if (permission == LocationPermission.denied) {
        permission = await Geolocator.requestPermission();
      }
      if (permission == LocationPermission.denied ||
          permission == LocationPermission.deniedForever) {
        return null;
      }
      if (!_locationEnabled && mounted) setState(() => _locationEnabled = true);
      return await Geolocator.getCurrentPosition(
          locationSettings:
              const LocationSettings(accuracy: LocationAccuracy.high));
    } catch (_) {
      return null;
    }
  }

  Future<void> _locateMe() async {
    final pos = await _currentPosition();
    if (pos == null) {
      if (mounted) toast(context, 'Location unavailable.');
      return;
    }
    _map?.animateCamera(
        CameraUpdate.newLatLngZoom(LatLng(pos.latitude, pos.longitude), 15.5));
  }

  // ----------------------------------------------------------------- search

  void _onSearchChanged(String q) {
    _searchDebounce?.cancel();
    if (q.trim().length < 2) {
      setState(() => _results = []);
      return;
    }
    _searchDebounce = Timer(const Duration(milliseconds: 400), () async {
      try {
        final results = await api.search(q.trim());
        if (mounted) setState(() => _results = results);
      } catch (_) {}
    });
  }

  Future<void> _selectResult(Map<String, dynamic> r) async {
    FocusScope.of(context).unfocus();
    final lat = (r['lat'] as num?)?.toDouble();
    final lon = (r['lon'] as num?)?.toDouble();
    // Searching somewhere new retires the old trip — otherwise the previous
    // blue route line stays on screen with its endpoint pointing nowhere.
    if (_navRoute != null) await _clearRoute();
    if (!mounted) return;
    setState(() {
      _results = [];
      _searchCtl.text = r['label']?.toString() ?? '';
      _place = (lat == null || lon == null) ? null : r;
    });
    if (lat == null || lon == null) return;
    context.read<AppState>().addRecentSearch(r);
    final target = LatLng(lat, lon);
    await _setPin(target);
    _map?.animateCamera(CameraUpdate.newLatLngZoom(target, 15.5));
  }

  void _clearPlace() {
    setState(() => _place = null);
    _clearPinIfIdle();
  }

  /// Bottom card for the searched destination — big enough to actually hit.
  Widget _placeCard() {
    final r = _place!;
    final state = context.watch<AppState>();
    final target = LatLng(
      (r['lat'] as num).toDouble(),
      (r['lon'] as num).toDouble(),
    );
    final label = (r['label'] ?? 'Destination').toString();
    final sublabel = (r['sublabel'] ?? '').toString();
    return Material(
        elevation: 6,
        borderRadius: BorderRadius.circular(18),
        color: brandGreen,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 14, 8, 14),
          child: Row(
            children: [
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      label,
                      maxLines: 2,
                      overflow: TextOverflow.ellipsis,
                      style: const TextStyle(
                        color: Colors.white,
                        fontSize: 17,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    if (sublabel.isNotEmpty)
                      Text(
                        sublabel,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            color: Colors.white70, fontSize: 13),
                      ),
                  ],
                ),
              ),
              const SizedBox(width: 10),
              // Tap the label to route straight away; the ⋮ opens the planner
              // when the trip doesn't start where you're standing.
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: Colors.white,
                  foregroundColor: brandDark,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                ),
                icon: Icon(state.iconFor(state.mode), size: 20),
                label: Text(
                  state.directionsVerb,
                  style: const TextStyle(
                      fontSize: 15, fontWeight: FontWeight.w600),
                ),
                onPressed: () => _routeTo(target, label: label),
              ),
              IconButton(
                tooltip: 'Change start point or modes',
                icon: const Icon(Icons.tune, color: Colors.white70),
                onPressed: () => _openDirections(
                  to: TripEndpoint(label: label, latLng: target),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70),
                onPressed: _clearPlace,
              ),
            ],
          ),
        ),
    );
  }

  // ---------------------------------------------------------------- reports

  Future<void> _openReportSheet(LatLng latLng, {String? spotName}) async {
    final submitted = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      builder: (_) => ReportSheet(latLng: latLng, spotName: spotName),
    );
    if (submitted != null && mounted) {
      final road = submitted['road_name'];
      toast(
        context,
        road != null
            ? 'Thanks! Your report near $road is on the map.'
            : 'Thanks! Your report is on the map.',
      );
      await _refreshReports();
    }
    _clearPinIfIdle();
  }

  /// Re-pull the reports layer (and force it visible) so a just-submitted pin
  /// shows up without a restart.
  Future<void> _refreshReports() async {
    const id = 'reports';
    final def = layerDefs.firstWhere((d) => d.id == id);
    if (mounted) context.read<AppState>().toggleLayer(id, true);
    await _ensureLayer(def);
    try {
      final data = await api.layerGeoJson(def.path);
      await _map?.setGeoJsonSource(id, data);
    } catch (_) {
      // Non-fatal: the pin will appear on the next app start.
    }
  }

  /// "This exists on the ground but not on the map."
  Future<void> _openAddPointSheet(LatLng latLng) async {
    final submitted = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (_) => AddPointSheet(latLng: latLng),
    );
    if (submitted == true && mounted) {
      toast(context,
          'Thanks! We\'ll review it and add it to the map.');
    }
    _clearPinIfIdle();
  }

  Future<void> _reportAtMyLocation() async {
    final pos = await _currentPosition();
    if (!mounted) return;
    if (pos == null) {
      toast(context,
          'Location unavailable — long-press the map to report a spot.');
      return;
    }
    _openReportSheet(LatLng(pos.latitude, pos.longitude));
  }

  // ------------------------------------------------------------------ build

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Scaffold(
      // The map is fullscreen chrome — never let the keyboard resize it
      // (resizing left a white band behind after backgrounding the app with
      // the keyboard up).
      resizeToAvoidBottomInset: false,
      body: Stack(
        children: [
          MapLibreMap(
            styleString: basemapStyleUrl,
            initialCameraPosition: const CameraPosition(
              target: LatLng(homeLat, homeLon),
              zoom: homeZoom,
            ),
            onMapCreated: (c) {
              _map = c;
              c.onFeatureTapped.add(_onFeatureTap);
            },
            onStyleLoadedCallback: _onStyleLoaded,
            onMapClick: _onMapClick,
            onMapLongClick: _onMapLongClick,
            onCameraMove: _onCameraMove,
            // The native dot hides while navigating — the rotated arrow puck
            // (lyr-puck) is the rider then.
            myLocationEnabled: _locationEnabled && !_navigating,
            trackCameraPosition: true,
            attributionButtonPosition: AttributionButtonPosition.bottomLeft,
            // Lift the (i) clear of the system navigation bar (3-button nav
            // phones put ~48 dp of buttons at the bottom edge).
            attributionButtonMargins: math.Point(
                8, MediaQuery.of(context).padding.bottom + 8),
          ),

          // Top chrome: search + mode switch (hidden while navigating).
          if (!_navigating)
            SafeArea(
            child: Column(
              children: [
                Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
                  child: Material(
                    elevation: 3,
                    borderRadius: BorderRadius.circular(28),
                    child: TextField(
                      controller: _searchCtl,
                      focusNode: _searchFocus,
                      onChanged: (q) {
                        setState(() {}); // clear button visibility
                        _onSearchChanged(q);
                      },
                      decoration: InputDecoration(
                        hintText: 'Search streets, stops, bike parking…',
                        prefixIcon: Padding(
                          padding: const EdgeInsets.only(left: 8),
                          child: Image.asset('assets/logo.png', width: 28),
                        ),
                        suffixIcon: Row(
                          mainAxisSize: MainAxisSize.min,
                          children: [
                            if (_searchCtl.text.isNotEmpty)
                              IconButton(
                                icon: const Icon(Icons.clear),
                                tooltip: 'Clear search',
                                onPressed: () {
                                  _searchCtl.clear();
                                  setState(() => _results = []);
                                  _clearPlace();
                                },
                              ),
                            IconButton(
                              icon: const Icon(Icons.directions),
                              tooltip: 'Plan a trip',
                              color: brandGreen,
                              onPressed: () => _openDirections(),
                            ),
                            IconButton(
                              icon: const Icon(Icons.menu),
                              tooltip: 'Dashboards & more',
                              onPressed: () => Navigator.push(
                                context,
                                MaterialPageRoute(
                                    builder: (_) => const ToolsScreen()),
                              ),
                            ),
                          ],
                        ),
                        border: InputBorder.none,
                        contentPadding:
                            const EdgeInsets.symmetric(horizontal: 8, vertical: 14),
                      ),
                    ),
                  ),
                ),
                if (_results.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Material(
                      elevation: 3,
                      borderRadius: BorderRadius.circular(12),
                      child: Column(
                        children: [
                          for (final r in _results.take(6))
                            ListTile(
                              dense: true,
                              leading: const Icon(Icons.place_outlined),
                              title: Text(r['label']?.toString() ?? ''),
                              subtitle: Text(r['sublabel']?.toString() ?? ''),
                              onTap: () => _selectResult(
                                  Map<String, dynamic>.from(r as Map)),
                            ),
                        ],
                      ),
                    ),
                  )
                // Places picked before, one tap from the still-focused field —
                // the "bring my route back" path after clearing navigation.
                else if (_searchFocus.hasFocus &&
                    state.recentSearches.isNotEmpty)
                  Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Material(
                      elevation: 3,
                      borderRadius: BorderRadius.circular(12),
                      child: Column(
                        children: [
                          for (final r in state.recentSearches.take(5))
                            ListTile(
                              dense: true,
                              leading: const Icon(Icons.history),
                              title: Text(r['label']?.toString() ?? ''),
                              subtitle: (r['sublabel']?.toString() ?? '')
                                      .isEmpty
                                  ? null
                                  : Text(r['sublabel'].toString()),
                              onTap: () => _selectResult(
                                  Map<String, dynamic>.from(r)),
                            ),
                        ],
                      ),
                    ),
                  ),
                const SizedBox(height: 8),
                // Multi-select on purpose: bike AND bus means "cost me a
                // bike-to-the-bus trip", not "pick one". Tapping a selected
                // pill cycles its variant (Bike → E-bike, Walk → Roll) before
                // it deselects — see AppState.cyclePill.
                SegmentedButton<TravelMode>(
                  multiSelectionEnabled: true,
                  // The empty selection is never applied — it's how the
                  // widget reports "the last selected pill was tapped", which
                  // cyclePill turns into a variant cycle instead.
                  emptySelectionAllowed: true,
                  showSelectedIcon: false,
                  segments: [
                    for (final m in TravelMode.values)
                      ButtonSegment(
                        value: m,
                        icon: Icon(state.iconFor(m)),
                        label: Text(state.labelFor(m)),
                      ),
                  ],
                  selected: state.modes,
                  onSelectionChanged: (s) {
                    // The widget hands back the would-be selection; the pill
                    // the user actually touched is the symmetric difference.
                    final tapped = {
                      ...state.modes.difference(s),
                      ...s.difference(state.modes),
                    };
                    if (tapped.length != 1) {
                      state.setModes(s);
                    } else {
                      state.cyclePill(tapped.first);
                    }
                    // A drawn route follows the mode switch.
                    if (_navRoute != null && !_navigating && _to != null) {
                      _planTrip();
                    }
                  },
                  style: ButtonStyle(
                    backgroundColor: WidgetStateProperty.resolveWith(
                      (states) => states.contains(WidgetState.selected)
                          ? brandGreen.withValues(alpha: 0.9)
                          : Colors.white.withValues(alpha: 0.92),
                    ),
                    foregroundColor: WidgetStateProperty.resolveWith(
                      (states) => states.contains(WidgetState.selected)
                          ? Colors.white
                          : Colors.black87,
                    ),
                  ),
                ),
                if (_pickField != null) _pickBanner(),
              ],
            ),
          ),

          if (_navigating) _navChrome(),

          // One bottom overlay: FABs stacked above whichever card is active
          // (route preview, place card, or the nav trip bar), the whole thing
          // lifted clear of the system navigation bar. Phones with 3-button
          // nav have a ~48 dp inset here; gesture phones ~16 dp.
          Positioned(
            left: 12,
            right: 12,
            bottom: MediaQuery.of(context).padding.bottom + 12,
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                _fabColumn(),
                if (_navigating && !_followNav) ...[
                  const SizedBox(height: 10),
                  Align(
                    alignment: Alignment.center,
                    child: FloatingActionButton.extended(
                      heroTag: 'recenter',
                      backgroundColor: brandGreen,
                      foregroundColor: Colors.white,
                      icon: const Icon(Icons.navigation),
                      label: const Text('Re-center'),
                      onPressed: _recenterNav,
                    ),
                  ),
                ],
                if (_planning && !_navigating) ...[
                  const SizedBox(height: 10),
                  _planningChip(),
                ],
                if (_navigating && _navRoute != null) ...[
                  const SizedBox(height: 10),
                  _navTripBar(),
                ] else if (_navRoute != null && !_planning) ...[
                  const SizedBox(height: 10),
                  _routePreview(),
                ] else if (_place != null && !_planning) ...[
                  const SizedBox(height: 10),
                  _placeCard(),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }

  /// FABs, right-aligned above the bottom card (recenter only while
  /// navigating).
  Widget _fabColumn() => Column(
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.end,
        children: [
          if (!_navigating) ...[
            FloatingActionButton.small(
              heroTag: 'layers',
              onPressed: _openLayersSheet,
              child: const Icon(Icons.layers),
            ),
            const SizedBox(height: 10),
          ],
          FloatingActionButton.small(
            heroTag: 'locate',
            onPressed: _locateMe,
            child: const Icon(Icons.my_location),
          ),
          if (!_navigating && _navRoute == null) ...[
            const SizedBox(height: 10),
            FloatingActionButton.extended(
              heroTag: 'report',
              backgroundColor: brandGreen,
              foregroundColor: Colors.white,
              icon: const Icon(Icons.report_problem_outlined),
              label: const Text('Report'),
              onPressed: _reportAtMyLocation,
            ),
          ],
        ],
      );

  /// Immediate feedback that the router is working on it ("Bike here" used to
  /// do nothing visible for a second or two).
  Widget _planningChip() => Align(
        alignment: Alignment.center,
        child: Material(
          elevation: 4,
          borderRadius: BorderRadius.circular(24),
          color: Colors.white,
          child: const Padding(
            padding: EdgeInsets.symmetric(horizontal: 18, vertical: 12),
            child: Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                SizedBox(
                  width: 18,
                  height: 18,
                  child: CircularProgressIndicator(
                      strokeWidth: 2.5, color: brandGreen),
                ),
                SizedBox(width: 12),
                Text('Finding your route…',
                    style: TextStyle(fontWeight: FontWeight.w600)),
              ],
            ),
          ),
        ),
      );

  /// "Tap the map" banner while a trip endpoint is being picked.
  Widget _pickBanner() => Padding(
        padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
        child: Material(
          elevation: 3,
          borderRadius: BorderRadius.circular(14),
          color: brandDark,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
            child: Row(children: [
              const Icon(Icons.touch_app, color: Colors.white, size: 20),
              const SizedBox(width: 10),
              Expanded(
                child: Text(
                  _pickField == 'from'
                      ? 'Tap the map to set your start point'
                      : 'Tap the map to set your destination',
                  style: const TextStyle(color: Colors.white),
                ),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70, size: 20),
                onPressed: () => setState(() => _pickField = null),
              ),
            ]),
          ),
        ),
      );

  /// Route summary before the trip starts: distance, time, the trip's caveats,
  /// the other itineraries, upcoming turns, Start.
  Widget _routePreview() {
    final route = _navRoute!;
    final color = route.isTransit
        ? const Color(0xFF7B1FA2)
        : (route.plan == 'bcycle'
            ? hexColor(bcycleRed)
            : const Color(0xFF1565C0));
    final subtitle = route.isTransit && route.transitRoute != null
        ? 'Greenlink Route ${route.transitRoute}'
            '${route.boardStop != null ? ' · board at ${route.boardStop}' : ''}'
        : (route.plan == 'bcycle' && route.rentStation != null
            ? 'BCycle from ${route.rentStation}'
            : (route.planLabel.isNotEmpty ? route.planLabel : 'Route'));
    final icon = planIcons[route.plan] ?? Icons.directions;
    // Tiny infrastructure gaps stay quiet (threshold in Settings); the hill
    // disclosure is computed client-side from the elevation profile.
    final warnings =
        route.visibleWarnings(context.watch<AppState>().warnFt);
    final hillNote = route.hillSummary();
    // Bottom-anchored: the summary + Start sit closest to the thumb, the
    // caveats and alternatives stack above them.
    return Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        mainAxisSize: MainAxisSize.min,
        children: [
          if (route.alternatives.isNotEmpty) _alternativesRow(route),
          if (route.fallbackNote != null ||
              warnings.isNotEmpty ||
              hillNote != null)
            _warningBanner(route, warnings, hillNote),
          // The terrain, at a glance: where the trip climbs and where it
          // bites (steep stretches in red). Only worth the pixels once the
          // climb is enough to feel in your legs.
          if (route.elevationProfile != null && route.climbFt >= 30)
            Padding(
              padding: const EdgeInsets.only(bottom: 6),
              child: Material(
                elevation: 2,
                borderRadius: BorderRadius.circular(14),
                color: Colors.white,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(12, 8, 12, 8),
                  child: ElevationProfile(profile: route.elevationProfile!),
                ),
              ),
            ),
          Material(
            elevation: 4,
            borderRadius: BorderRadius.circular(16),
            color: color,
            child: Padding(
              padding: const EdgeInsets.fromLTRB(14, 8, 6, 8),
              child: Row(
                children: [
                  Icon(icon, color: Colors.white, size: 20),
                  const SizedBox(width: 10),
                  Expanded(
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          '${formatDistance(route.distanceM)} · '
                          '${formatDuration(route.durationMin)}'
                          // Climb is only worth the pixels once it is enough
                          // to feel in your legs.
                          '${route.climbFt >= 50 ? ' · ↑ ${route.climbFt} ft' : ''}',
                          style: const TextStyle(
                              color: Colors.white,
                              fontWeight: FontWeight.w600,
                              fontSize: 16),
                        ),
                        Text(subtitle,
                            maxLines: 2,
                            overflow: TextOverflow.ellipsis,
                            style: const TextStyle(
                                color: Colors.white70, fontSize: 12)),
                      ],
                    ),
                  ),
                  if (route.steps.isNotEmpty)
                    IconButton(
                      tooltip: 'Upcoming turns',
                      icon: const Icon(Icons.list_alt, color: Colors.white),
                      onPressed: _openStepsSheet,
                    ),
                  if (route.steps.isNotEmpty)
                    FilledButton.icon(
                      style: FilledButton.styleFrom(
                        backgroundColor: Colors.white,
                        foregroundColor: color,
                        visualDensity: VisualDensity.compact,
                      ),
                      icon: const Icon(Icons.navigation, size: 18),
                      label: const Text('Start'),
                      onPressed: _startNav,
                    ),
                  IconButton(
                    icon:
                        const Icon(Icons.close, color: Colors.white, size: 20),
                    onPressed: _clearRoute,
                  ),
                ],
              ),
            ),
          ),
        ],
    );
  }

  /// The honest bit: what this route is missing, and why it looks like this.
  Widget _warningBanner(
      NavRoute route, List<RouteWarning> warnings, String? hillNote) {
    final lines = <String>[
      if (route.fallbackNote != null) route.fallbackNote!,
      for (final w in warnings) w.message,
      ?hillNote,
    ];
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: Material(
        elevation: 2,
        borderRadius: BorderRadius.circular(14),
        color: const Color(0xFFFFF3E0),
        child: InkWell(
          borderRadius: BorderRadius.circular(14),
          onTap: _openStepsSheet,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 10, 12, 10),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.warning_amber_rounded,
                    color: Color(0xFFE65100), size: 20),
                const SizedBox(width: 10),
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      for (final line in lines)
                        Padding(
                          padding: const EdgeInsets.only(bottom: 2),
                          child: Text(line,
                              style: const TextStyle(
                                  fontSize: 12.5, color: Color(0xFF6D3B00))),
                        ),
                      if (warnings.isNotEmpty)
                        const Text(
                          'Those stretches are dashed red on the map.',
                          style: TextStyle(
                              fontSize: 11.5,
                              color: Color(0xFF8D5A1B),
                              fontStyle: FontStyle.italic),
                        ),
                    ],
                  ),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }

  /// The other itineraries the router costed — one tap swaps to them.
  Widget _alternativesRow(NavRoute route) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: SizedBox(
          height: 38,
          child: ListView(
            scrollDirection: Axis.horizontal,
            children: [
              for (final alt in route.alternatives)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ActionChip(
                    avatar: Icon(planIcons[alt.plan] ?? Icons.directions,
                        size: 18),
                    label: Text(
                        '${alt.label} · ${formatDuration(alt.durationMin)}'),
                    backgroundColor: Colors.white,
                    onPressed: () => _planTrip(plan: alt.plan),
                  ),
                ),
            ],
          ),
        ),
      );

  /// Turn-by-turn top chrome: maneuver card + the upcoming-turns strip.
  /// (The trip bar renders in the shared bottom overlay — see build().)
  Widget _navChrome() {
    final route = _navRoute;
    final progress = _progress;
    if (route == null || route.steps.isEmpty) return const SizedBox.shrink();
    final nextIndex =
        math.min((progress?.stepIndex ?? -1) + 1, route.steps.length - 1);
    final step = route.steps[nextIndex];
    final after = nextIndex + 1 < route.steps.length
        ? route.steps[nextIndex + 1]
        : null;
    final toManeuver = progress?.distanceToManeuverM ?? step.distanceM;

    return SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
        Padding(
          padding: const EdgeInsets.fromLTRB(10, 8, 10, 0),
          child: Material(
            elevation: 6,
            borderRadius: BorderRadius.circular(18),
            color: const Color(0xFF13322A),
            // Tapping the card lists every upcoming turn.
            child: InkWell(
              borderRadius: BorderRadius.circular(18),
              onTap: _openStepsSheet,
              child: Padding(
              padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Row(
                    children: [
                      Icon(step.icon, color: Colors.white, size: 42),
                      const SizedBox(width: 12),
                      Expanded(
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              formatDistance(toManeuver),
                              style: const TextStyle(
                                color: Colors.white,
                                fontSize: 26,
                                fontWeight: FontWeight.w700,
                                height: 1.1,
                              ),
                            ),
                            Text(
                              step.instruction,
                              style: const TextStyle(
                                  color: Colors.white, fontSize: 16),
                            ),
                          ],
                        ),
                      ),
                      IconButton(
                        tooltip: _voice ? 'Mute voice' : 'Unmute voice',
                        icon: Icon(_voice ? Icons.volume_up : Icons.volume_off,
                            color: Colors.white70),
                        onPressed: () {
                          setState(() => _voice = !_voice);
                          if (!_voice) _tts?.stop();
                        },
                      ),
                    ],
                  ),
                  if (step.warn != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 6, left: 2),
                      child: Row(
                        children: [
                          const Icon(Icons.warning_amber_rounded,
                              color: Color(0xFFFFB74D), size: 16),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              warnStepSentence(step.warn),
                              style: const TextStyle(
                                  color: Color(0xFFFFB74D), fontSize: 12.5),
                            ),
                          ),
                        ],
                      ),
                    )
                  else if (step.isSteepClimb)
                    Padding(
                      padding: const EdgeInsets.only(top: 6, left: 2),
                      child: Row(
                        children: [
                          const Icon(Icons.trending_up,
                              color: Color(0xFFFFB74D), size: 16),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              'Steep climb ahead — ${step.climbFt} ft up',
                              style: const TextStyle(
                                  color: Color(0xFFFFB74D), fontSize: 12.5),
                            ),
                          ),
                        ],
                      ),
                    ),
                  if (after != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 6, left: 4),
                      child: Row(
                        children: [
                          const Text('then ',
                              style: TextStyle(color: Colors.white54)),
                          Icon(after.icon, color: Colors.white54, size: 18),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Text(
                              after.instruction,
                              overflow: TextOverflow.ellipsis,
                              style: const TextStyle(color: Colors.white54),
                            ),
                          ),
                        ],
                      ),
                    ),
                ],
              ),
              ),
            ),
          ),
        ),
        // Everything still to come, in order — the "upcoming turns" list Google
        // keeps a swipe away, here always on screen.
        _upcomingStrip(route, nextIndex),
          ],
        ),
      );
  }

  /// ETA + distance left + Steps/End, rendered inside the bottom overlay so it
  /// clears the system navigation bar.
  Widget _navTripBar() {
    final route = _navRoute!;
    final remaining = _progress?.remainingM ?? route.distanceM;
    final etaMin = route.durationMin <= 0 || route.distanceM <= 0
        ? 0.0
        : route.durationMin * (remaining / route.distanceM);
    return Material(
      elevation: 6,
      borderRadius: BorderRadius.circular(18),
      color: Colors.white,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 10, 10),
        child: Row(
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  formatDuration(etaMin),
                  style: const TextStyle(
                      fontSize: 20, fontWeight: FontWeight.w700),
                ),
                Text('${formatDistance(remaining)} left',
                    style: const TextStyle(color: Colors.black54)),
              ],
            ),
            const Spacer(),
            TextButton.icon(
              icon: const Icon(Icons.list_alt),
              label: const Text('Steps'),
              onPressed: _openStepsSheet,
            ),
            const SizedBox(width: 4),
            FilledButton.icon(
              style: FilledButton.styleFrom(
                  backgroundColor: Colors.red.shade700),
              icon: const Icon(Icons.close),
              label: const Text('End'),
              onPressed: () => _stopNav(),
            ),
          ],
        ),
      ),
    );
  }

  /// Horizontal ribbon of the turns still ahead. Scrollable, so a long trip is
  /// all there without a sheet, and each chip shows how far to that maneuver.
  Widget _upcomingStrip(NavRoute route, int nextIndex) {
    final ahead = <int>[
      for (var i = nextIndex + 1; i < route.steps.length; i++) i,
    ];
    if (ahead.isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.fromLTRB(10, 6, 10, 0),
      child: SizedBox(
        height: 34,
        child: ListView(
          scrollDirection: Axis.horizontal,
          children: [
            for (final i in ahead.take(8))
              Padding(
                padding: const EdgeInsets.only(right: 6),
                child: Material(
                  color: Colors.white.withValues(alpha: 0.92),
                  borderRadius: BorderRadius.circular(17),
                  elevation: 2,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 10),
                    child: Row(
                      children: [
                        Icon(route.steps[i].icon,
                            size: 18,
                            color: route.steps[i].warn != null ||
                                    route.steps[i].isSteepClimb
                                ? warnRed
                                : brandDark),
                        const SizedBox(width: 5),
                        Text(
                          route.steps[i].name ??
                              (route.steps[i].maneuver == 'arrive'
                                  ? 'Arrive'
                                  : 'Continue'),
                          style: const TextStyle(fontSize: 12.5),
                        ),
                        if (route.steps[i].distanceM > 0) ...[
                          const SizedBox(width: 6),
                          Text(
                            formatDistance(route.steps[i].distanceM),
                            style: const TextStyle(
                                fontSize: 11.5, color: Colors.black54),
                          ),
                        ],
                      ],
                    ),
                  ),
                ),
              ),
          ],
        ),
      ),
    );
  }

  void _openStepsSheet() {
    final route = _navRoute;
    if (route == null) return;
    final current = _progress?.stepIndex ?? 0;
    final warnings =
        route.visibleWarnings(context.read<AppState>().warnFt);
    final hillNote = route.hillSummary();
    showModalBottomSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    '${formatDistance(route.distanceM)} · '
                    '${formatDuration(route.durationMin)}'
                    '${route.planLabel.isNotEmpty ? ' · ${route.planLabel}' : ''}',
                    style: Theme.of(ctx).textTheme.titleMedium,
                  ),
                  if (route.fallbackNote != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Text(route.fallbackNote!,
                          style: const TextStyle(
                              fontSize: 12.5, color: Color(0xFF6D3B00))),
                    ),
                  for (final w in warnings)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Row(children: [
                        const Icon(Icons.warning_amber_rounded,
                            size: 16, color: Color(0xFFE65100)),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(w.message,
                              style: const TextStyle(
                                  fontSize: 12.5, color: Color(0xFF6D3B00))),
                        ),
                      ]),
                    ),
                  if (hillNote != null)
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: Row(children: [
                        const Icon(Icons.trending_up,
                            size: 16, color: Color(0xFFE65100)),
                        const SizedBox(width: 6),
                        Expanded(
                          child: Text(hillNote,
                              style: const TextStyle(
                                  fontSize: 12.5, color: Color(0xFF6D3B00))),
                        ),
                      ]),
                    ),
                ],
              ),
            ),
            for (var i = 0; i < route.steps.length; i++)
              ListTile(
                dense: true,
                leading: Icon(route.steps[i].icon,
                    color: i < current
                        ? Colors.black26
                        : (route.steps[i].warn != null ? warnRed : brandGreen)),
                title: Text(
                  route.steps[i].instruction,
                  style: TextStyle(
                    color: i < current ? Colors.black45 : null,
                    fontWeight: i == current ? FontWeight.w600 : null,
                  ),
                ),
                subtitle: _stepSubtitle(route.steps[i]),
                trailing: route.steps[i].distanceM > 0
                    ? Text(formatDistance(route.steps[i].distanceM))
                    : null,
              ),
          ],
        ),
      ),
    );
  }

  /// What a step wants you to know beyond the instruction: missing
  /// infrastructure first (worse), then a climb worth bracing for.
  Widget? _stepSubtitle(RouteStep step) {
    final lines = <Widget>[
      if (step.warn != null)
        Text(
          '${formatDistance(step.warnM)} ${warnStepPhrase(step.warn)}',
          style: const TextStyle(fontSize: 12, color: warnRed),
        ),
      if (step.climbFt >= 20 || step.isSteepClimb)
        Text(
          step.isSteepClimb
              ? '↑ ${step.climbFt} ft — steep climb'
              : '↑ ${step.climbFt} ft of climb',
          style: TextStyle(
            fontSize: 12,
            color: step.isSteepClimb ? warnRed : Colors.black54,
            fontWeight: step.isSteepClimb ? FontWeight.w600 : null,
          ),
        ),
    ];
    if (lines.isEmpty) return null;
    if (lines.length == 1) return lines.first;
    return Column(
        crossAxisAlignment: CrossAxisAlignment.start, children: lines);
  }

  void _openLayersSheet() {
    final zoom = _map?.cameraPosition?.zoom ?? homeZoom;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => Consumer<AppState>(
        builder: (ctx, state, _) => SafeArea(
          child: ListView(
            shrinkWrap: true,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                child: Text(
                    'Map layers — '
                    '${TravelMode.values.where(state.modes.contains).map(state.labelFor).join(' + ')}',
                    style: Theme.of(ctx).textTheme.titleMedium),
              ),
              for (final def in state.relevantLayers)
                SwitchListTile(
                  dense: true,
                  secondary: def.colorByStress
                      ? const Icon(Icons.speed)
                      : Icon(def.icon, color: hexColor(def.color)),
                  title: Text(def.label),
                  subtitle: def.minZoom > zoom
                      ? const Text('Zoom in to see these')
                      : null,
                  value: state.overrideFor(def),
                  onChanged: (v) {
                    state.toggleLayer(def.id, v);
                    if (v && def.live) _refreshLive();
                  },
                ),
              if (state.modes.contains(TravelMode.cyclist)) _stressLegend(),
            ],
          ),
        ),
      ),
    );
  }

  Widget _stressLegend() {
    return Padding(
      padding: const EdgeInsets.fromLTRB(16, 4, 16, 12),
      child: Wrap(
        spacing: 12,
        runSpacing: 4,
        children: [
          for (final e in stressLabels.entries)
            Row(mainAxisSize: MainAxisSize.min, children: [
              Container(
                width: 14,
                height: 4,
                color: Color(int.parse(
                    'ff${stressColors[e.key]!.substring(1)}',
                    radix: 16)),
              ),
              const SizedBox(width: 4),
              Text(e.value, style: const TextStyle(fontSize: 12)),
            ]),
        ],
      ),
    );
  }
}

/// Contact card for the office responsible for a road ("Who Owns The Roads").
class RoadInfoSheet extends StatelessWidget {
  final Map<String, dynamic> info;
  const RoadInfoSheet({super.key, required this.info});

  @override
  Widget build(BuildContext context) {
    final owner = info['owner'] ?? 'Unknown';
    return SafeArea(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text('${info['name'] ?? 'Road'} · ${info['type'] ?? ''}',
                style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 4),
            Text('Maintained by $owner',
                style: Theme.of(context).textTheme.bodyLarge),
            const SizedBox(height: 8),
            if (info['email'] != null)
              ContactRow(icon: Icons.email_outlined, value: info['email'],
                  uri: 'mailto:${info['email']}'),
            if (info['phone'] != null)
              ContactRow(icon: Icons.phone_outlined, value: info['phone'],
                  uri: 'tel:${info['phone']}'),
            if (info['online_form'] != null)
              ContactRow(icon: Icons.open_in_new, value: 'Report an issue online',
                  uri: info['online_form']),
            if (info['email'] == null && info['online_form'] != null)
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  'This office has no public email — use the online form above '
                  'to contact them directly.',
                  style: TextStyle(fontSize: 12, color: Colors.black54),
                ),
              ),
          ],
        ),
      ),
    );
  }
}

class ContactRow extends StatelessWidget {
  final IconData icon;
  final dynamic value;
  final String uri;
  const ContactRow(
      {super.key, required this.icon, required this.value, required this.uri});

  @override
  Widget build(BuildContext context) {
    return ListTile(
      dense: true,
      contentPadding: EdgeInsets.zero,
      leading: Icon(icon, color: brandGreen),
      title: Text(value.toString()),
      onTap: () => launchUrlString(uri),
    );
  }
}
