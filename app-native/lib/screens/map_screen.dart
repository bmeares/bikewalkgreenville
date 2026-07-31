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
import '../theme.dart';
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

  // Route + turn-by-turn navigation state.
  bool _routing = false;
  NavRoute? _navRoute;
  LatLng? _destination;
  TravelMode _routeMode = TravelMode.cyclist;
  bool _navigating = false;
  NavProgress? _progress;
  StreamSubscription<Position>? _posSub;
  FlutterTts? _tts;
  bool _voice = true;
  int _spokenStep = -1;
  bool _spokenImminent = false;
  int _offRouteHits = 0;
  bool _rerouting = false;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    context.read<AppState>().addListener(_applyVisibility);
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    _searchFocus.dispose();
    _posSub?.cancel();
    _tts?.stop();
    WakelockPlus.disable();
    super.dispose();
  }

  // ---------------------------------------------------------------- layers

  Future<void> _onStyleLoaded() async {
    final map = _map!;
    final ratio = MediaQuery.of(context).devicePixelRatio;

    for (final def in layerDefs) {
      final url = await api.layerUrl(def.path);
      await map.addSource(def.id, GeojsonSourceProperties(data: url));
    }

    // Lines first: pins, the tap highlight and the route all draw above them.
    for (final def in layerDefs.where((d) => !d.isPoint)) {
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
              // Per-feature color when the layer provides one (GTFS routes).
              : [
                  'coalesce',
                  ['get', 'color'],
                  def.color,
                ],
          lineWidth: def.width,
          lineOpacity: 0.85,
          lineCap: 'round',
          lineJoin: 'round',
        ),
      );
    }

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

    // Point layers render as Material-icon pins (see map_icons.dart) so each
    // layer is recognizable instead of another teal dot.
    for (final def in layerDefs.where((d) => d.isPoint)) {
      await map.addImage(
        'pin-${def.id}',
        await renderPin(
          icon: def.icon,
          color: hexColor(def.color),
          devicePixelRatio: ratio,
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
          // Reports are the point of the app — never let them get decluttered.
          iconAllowOverlap: def.id == 'reports',
        ),
        minzoom: def.minZoom > 0 ? def.minZoom : null,
      );
    }

    // Route line (casing + fill) and the selection pin, above everything.
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

    _styleReady = true;
    _applyVisibility();
  }

  static const _emptyCollection = {
    'type': 'FeatureCollection',
    'features': <dynamic>[],
  };

  void _applyVisibility() {
    if (!_styleReady || _map == null) return;
    final state = context.read<AppState>();
    for (final def in layerDefs) {
      _map!.setLayerVisibility('lyr-${def.id}', state.layerVisible(def));
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
    await _showPlaceActions(latLng);
  }

  Future<void> _onMapLongClick(math.Point<double> point, LatLng latLng) =>
      _showPlaceActions(latLng);

  Future<void> _showPlaceActions(LatLng latLng) async {
    await _setPin(latLng);
    if (!mounted) return;
    final mode = app.mode;
    showModalBottomSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: Icon(modeIcons[mode], color: brandGreen),
              title: Text(modeVerbs[mode]!),
              subtitle: const Text('Turn-by-turn directions to this spot'),
              onTap: () {
                Navigator.pop(ctx);
                _routeTo(latLng);
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

    final skip = {'name', 'label', 'street_name', 'geojson', 'color', 'id'};
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
    final mode = app.mode;

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
                Icon(def?.icon ?? Icons.place, color: brandGreen),
                const SizedBox(width: 8),
                Expanded(
                    child: Text(title,
                        style: Theme.of(ctx).textTheme.titleMedium)),
              ]),
              const SizedBox(height: 8),
              for (final e in rows)
                Padding(
                  padding: const EdgeInsets.symmetric(vertical: 2),
                  child: Text('${_prettyKey(e.key)}: ${e.value}'),
                ),
              const SizedBox(height: 8),
              Row(children: [
                FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: brandGreen,
                    foregroundColor: Colors.white,
                  ),
                  icon: Icon(modeIcons[mode], size: 18),
                  label: const Text('Directions'),
                  onPressed: () {
                    Navigator.pop(ctx);
                    _routeTo(target);
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

  Future<void> _routeTo(LatLng dest,
      {bool silent = false, TravelMode? mode}) async {
    final travelMode = mode ?? app.mode;
    final origin = await _bestOrigin();
    if (origin == null) {
      if (mounted) {
        toast(context, 'Turn on location (or tap a start point) first.');
      }
      return;
    }
    if (!silent) setState(() => _routing = true);
    try {
      final feature = await api.route(
          origin.latitude, origin.longitude, dest.latitude, dest.longitude,
          mode: modeApiNames[travelMode]!);
      final route = NavRoute.fromFeature(feature);
      // Transit routes draw in the official Greenlink route color.
      final props = Map<String, dynamic>.from(feature['properties'] ?? {});
      if (route.routeColor != null) props['color'] = route.routeColor;
      feature['properties'] = props;
      await _map?.setGeoJsonSource('route', {
        'type': 'FeatureCollection',
        'features': [feature],
      });
      await _setPin(dest);
      if (!mounted) return;
      setState(() {
        _routing = true;
        _navRoute = route;
        _destination = dest;
        _routeMode = travelMode;
        _place = null;
      });
      if (!_navigating) await _fitRoute(route);
    } on Exception catch (e) {
      if (!mounted) return;
      setState(() => _routing = _navRoute != null);
      toast(context, e.toString());
    }
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
    await _map?.setGeoJsonSource('pin', _emptyCollection);
    if (!mounted) return;
    setState(() {
      _routing = false;
      _navRoute = null;
      _destination = null;
      _progress = null;
      _place = null;
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
    if (!mounted) return;
    setState(() {
      _navigating = true;
      _spokenStep = -1;
      _spokenImminent = false;
      _offRouteHits = 0;
    });
    await _posSub?.cancel();
    _posSub = Geolocator.getPositionStream(
      locationSettings: const LocationSettings(
        accuracy: LocationAccuracy.bestForNavigation,
        distanceFilter: 5,
      ),
    ).listen(_onNavPosition);
    _onNavPosition(pos);
  }

  Future<void> _stopNav({bool arrived = false}) async {
    await _posSub?.cancel();
    _posSub = null;
    await _tts?.stop();
    await WakelockPlus.disable();
    if (!mounted || !_navigating) return;
    setState(() {
      _navigating = false;
      _progress = null;
    });
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
    setState(() => _progress = progress);

    // Course-up camera: GPS heading while moving, else the route's own bearing.
    final bearing =
        (pos.heading >= 0 && pos.speed > 0.8) ? pos.heading : progress.courseBearing;
    // Google-style isometric follow camera.
    await _map?.animateCamera(
      CameraUpdate.newCameraPosition(CameraPosition(
        target: here,
        zoom: 17.5,
        bearing: bearing,
        tilt: 60.0,
      )),
      duration: const Duration(milliseconds: 900),
    );

    if (progress.remainingM < 25) {
      await _speak('You have arrived.');
      await _stopNav(arrived: true);
      await _clearRoute();
      return;
    }

    _announce(route, progress);
    await _maybeReroute(progress);
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
    await _speak('Rerouting.');
    if (mounted) toast(context, 'Off route — recalculating…');
    await _routeTo(_destination!, silent: true, mode: _routeMode);
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
    setState(() {
      _results = [];
      _searchCtl.text = r['label']?.toString() ?? '';
      _place = (lat == null || lon == null) ? null : r;
    });
    if (lat == null || lon == null) return;
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
    final mode = context.watch<AppState>().mode;
    final target = LatLng(
      (r['lat'] as num).toDouble(),
      (r['lon'] as num).toDouble(),
    );
    final sublabel = (r['sublabel'] ?? '').toString();
    return Positioned(
      left: 12,
      right: 12,
      bottom: 24,
      child: Material(
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
                      (r['label'] ?? 'Destination').toString(),
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
              FilledButton.icon(
                style: FilledButton.styleFrom(
                  backgroundColor: Colors.white,
                  foregroundColor: brandDark,
                  padding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                ),
                icon: Icon(modeIcons[mode], size: 20),
                label: Text(
                  modeVerbs[mode]!,
                  style: const TextStyle(
                      fontSize: 15, fontWeight: FontWeight.w600),
                ),
                onPressed: () => _routeTo(target),
              ),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white70),
                onPressed: _clearPlace,
              ),
            ],
          ),
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
    try {
      final data = await api.layerGeoJson(def.path);
      await _map?.setGeoJsonSource(id, data);
    } catch (_) {
      // Non-fatal: the pin will appear on the next app start.
    }
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
            myLocationEnabled: _locationEnabled,
            trackCameraPosition: true,
            attributionButtonPosition: AttributionButtonPosition.bottomLeft,
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
                  ),
                const SizedBox(height: 8),
                SegmentedButton<TravelMode>(
                  segments: [
                    for (final m in TravelMode.values)
                      ButtonSegment(
                        value: m,
                        icon: Icon(modeIcons[m]),
                        label: Text(modeLabels[m]!),
                      ),
                  ],
                  selected: {state.mode},
                  onSelectionChanged: (s) {
                    state.setMode(s.first);
                    // A drawn route follows the mode switch.
                    if (_navRoute != null && !_navigating && _destination != null) {
                      _routeTo(_destination!, mode: s.first);
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
                if (_navRoute != null) _routePreview(),
              ],
            ),
          ),

          if (_navigating) ..._navChrome(),

          if (_place != null && _navRoute == null && !_navigating) _placeCard(),

          // FABs (trimmed to a recenter button while navigating).
          Positioned(
            right: 12,
            bottom: _navigating
                ? 130
                : (_place != null && _navRoute == null ? 120 : 28),
            child: Column(
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
                if (!_navigating) ...[
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
            ),
          ),
        ],
      ),
    );
  }

  /// Route summary before the trip starts: distance, time, upcoming turns,
  /// Start.
  Widget _routePreview() {
    final route = _navRoute!;
    final isTransit = route.mode == 'transit';
    final color =
        isTransit ? const Color(0xFF7B1FA2) : const Color(0xFF1565C0);
    final subtitle = isTransit && route.transitRoute != null
        ? 'Greenlink Route ${route.transitRoute}'
            '${route.boardStop != null ? ' · board at ${route.boardStop}' : ''}'
        : (modeRouteLabels[_routeMode] ?? 'Route');
    return Padding(
      padding: const EdgeInsets.fromLTRB(12, 8, 12, 0),
      child: Material(
        elevation: 4,
        borderRadius: BorderRadius.circular(16),
        color: color,
        child: Padding(
          padding: const EdgeInsets.fromLTRB(14, 8, 6, 8),
          child: Row(
            children: [
              Icon(modeIcons[_routeMode], color: Colors.white, size: 20),
              const SizedBox(width: 10),
              Expanded(
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '${formatDistance(route.distanceM)} · '
                      '${formatDuration(route.durationMin)}',
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
                icon: const Icon(Icons.close, color: Colors.white, size: 20),
                onPressed: _clearRoute,
              ),
            ],
          ),
        ),
      ),
    );
  }

  /// Turn-by-turn chrome: maneuver card on top, trip bar on the bottom.
  List<Widget> _navChrome() {
    final route = _navRoute;
    final progress = _progress;
    if (route == null || route.steps.isEmpty) return const [];
    final nextIndex =
        math.min((progress?.stepIndex ?? -1) + 1, route.steps.length - 1);
    final step = route.steps[nextIndex];
    final after = nextIndex + 1 < route.steps.length
        ? route.steps[nextIndex + 1]
        : null;
    final toManeuver = progress?.distanceToManeuverM ?? step.distanceM;
    final remaining = progress?.remainingM ?? route.distanceM;
    final etaMin = route.durationMin <= 0 || route.distanceM <= 0
        ? 0.0
        : route.durationMin * (remaining / route.distanceM);

    return [
      SafeArea(
        child: Padding(
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
      ),
      Positioned(
        left: 10,
        right: 10,
        bottom: 20,
        child: Material(
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
        ),
      ),
    ];
  }

  void _openStepsSheet() {
    final route = _navRoute;
    if (route == null) return;
    final current = _progress?.stepIndex ?? 0;
    showModalBottomSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                '${formatDistance(route.distanceM)} · '
                '${formatDuration(route.durationMin)}',
                style: Theme.of(ctx).textTheme.titleMedium,
              ),
            ),
            for (var i = 0; i < route.steps.length; i++)
              ListTile(
                dense: true,
                leading: Icon(route.steps[i].icon,
                    color: i < current ? Colors.black26 : brandGreen),
                title: Text(
                  route.steps[i].instruction,
                  style: TextStyle(
                    color: i < current ? Colors.black45 : null,
                    fontWeight: i == current ? FontWeight.w600 : null,
                  ),
                ),
                trailing: route.steps[i].distanceM > 0
                    ? Text(formatDistance(route.steps[i].distanceM))
                    : null,
              ),
          ],
        ),
      ),
    );
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
                child: Text('Map layers — ${modeLabels[state.mode]} mode',
                    style: Theme.of(ctx).textTheme.titleMedium),
              ),
              for (final def in layerDefs.where(
                  (d) => d.modes.contains(state.mode)))
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
                  onChanged: (v) => state.toggleLayer(def.id, v),
                ),
              if (state.mode == TravelMode.cyclist) _stressLegend(),
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
