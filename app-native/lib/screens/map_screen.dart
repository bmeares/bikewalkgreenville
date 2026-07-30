import 'dart:async';
import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher_string.dart';

import '../api.dart';
import '../app_state.dart';
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
  Timer? _searchDebounce;
  List<dynamic> _results = [];

  // Route state.
  bool _routing = false;
  Map<String, dynamic>? _routeProps;

  AppState get app => context.read<AppState>();

  @override
  void initState() {
    super.initState();
    context.read<AppState>().addListener(_applyVisibility);
  }

  @override
  void dispose() {
    _searchDebounce?.cancel();
    super.dispose();
  }

  // ---------------------------------------------------------------- layers

  Future<void> _onStyleLoaded() async {
    final map = _map!;
    for (final def in layerDefs) {
      final url = await api.layerUrl(def.path);
      await map.addSource(def.id, GeojsonSourceProperties(data: url));
      if (def.isPoint) {
        await map.addCircleLayer(
          def.id,
          'lyr-${def.id}',
          CircleLayerProperties(
            circleColor: def.color,
            circleRadius: 6.0,
            circleOpacity: 0.9,
            circleStrokeColor: '#ffffff',
            circleStrokeWidth: 1.5,
          ),
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
      const LineLayerProperties(
          lineColor: '#1565C0', lineWidth: 5.0, lineCap: 'round', lineJoin: 'round'),
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
    _showFeatureSheet(f, latLng);
  }

  Future<void> _onMapLongClick(math.Point<double> point, LatLng latLng) async {
    await _setPin(latLng);
    if (!mounted) return;
    showModalBottomSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: const Icon(Icons.report_problem, color: Color(0xFFF9A825)),
              title: const Text('Report an issue here'),
              subtitle: const Text('Walk audit: sidewalks, crossings, bike lanes…'),
              onTap: () {
                Navigator.pop(ctx);
                _openReportSheet(latLng);
              },
            ),
            ListTile(
              leading: const Icon(Icons.directions_bike, color: brandGreen),
              title: const Text('Bike here (low-stress route)'),
              onTap: () {
                Navigator.pop(ctx);
                _routeTo(latLng);
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

  void _clearPinIfIdle() {
    if (!_routing) _map?.setGeoJsonSource('pin', _emptyCollection);
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
                TextButton.icon(
                  icon: const Icon(Icons.badge_outlined),
                  label: const Text('Who owns this road?'),
                  onPressed: () {
                    Navigator.pop(ctx);
                    _showRoadInfo(latLng);
                  },
                ),
                const Spacer(),
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
    );
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

  Future<void> _routeTo(LatLng dest) async {
    final origin = await _bestOrigin();
    if (origin == null) {
      if (mounted) {
        toast(context, 'Turn on location (or long-press a start point) first.');
      }
      return;
    }
    setState(() => _routing = true);
    try {
      final feature = await api.route(
          origin.latitude, origin.longitude, dest.latitude, dest.longitude);
      await _map?.setGeoJsonSource('route', {
        'type': 'FeatureCollection',
        'features': [feature],
      });
      await _setPin(dest);
      setState(() =>
          _routeProps = Map<String, dynamic>.from(feature['properties'] ?? {}));
    } on Exception catch (e) {
      setState(() => _routing = false);
      if (mounted) toast(context, e.toString());
    }
  }

  Future<void> _clearRoute() async {
    await _map?.setGeoJsonSource('route', _emptyCollection);
    await _map?.setGeoJsonSource('pin', _emptyCollection);
    setState(() {
      _routing = false;
      _routeProps = null;
    });
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
    setState(() {
      _results = [];
      _searchCtl.text = r['label']?.toString() ?? '';
    });
    final lat = (r['lat'] as num?)?.toDouble();
    final lon = (r['lon'] as num?)?.toDouble();
    if (lat == null || lon == null) return;
    final target = LatLng(lat, lon);
    await _setPin(target);
    _map?.animateCamera(CameraUpdate.newLatLngZoom(target, 15.5));
    if (!mounted) return;
    ScaffoldMessenger.of(context)
      ..clearSnackBars()
      ..showSnackBar(SnackBar(
        content: Text(r['label']?.toString() ?? 'Selected'),
        duration: const Duration(seconds: 6),
        action: SnackBarAction(
          label: 'Bike here',
          onPressed: () => _routeTo(target),
        ),
      ));
  }

  // ---------------------------------------------------------------- reports

  Future<void> _openReportSheet(LatLng latLng, {String? spotName}) async {
    final submitted = await showModalBottomSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      builder: (_) => ReportSheet(latLng: latLng, spotName: spotName),
    );
    if (submitted != null && mounted) {
      final owner = submitted['owner'];
      final road = submitted['road_name'];
      toast(
        context,
        owner != null
            ? 'Thanks! Your report near $road will be forwarded to $owner.'
            : 'Thanks! Your report was submitted.',
      );
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
            onMapLongClick: _onMapLongClick,
            myLocationEnabled: _locationEnabled,
            trackCameraPosition: true,
            attributionButtonPosition: AttributionButtonPosition.bottomLeft,
          ),

          // Top chrome: search + mode switch.
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
                      onChanged: _onSearchChanged,
                      decoration: InputDecoration(
                        hintText: 'Search streets, stops, bike parking…',
                        prefixIcon: Padding(
                          padding: const EdgeInsets.only(left: 8),
                          child: Image.asset('assets/logo.png', width: 28),
                        ),
                        suffixIcon: IconButton(
                          icon: const Icon(Icons.menu),
                          tooltip: 'Dashboards & more',
                          onPressed: () => Navigator.push(
                            context,
                            MaterialPageRoute(
                                builder: (_) => const ToolsScreen()),
                          ),
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
                  onSelectionChanged: (s) => state.setMode(s.first),
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
                if (_routeProps != null) _routeBanner(),
              ],
            ),
          ),

          // FABs.
          Positioned(
            right: 12,
            bottom: 28,
            child: Column(
              children: [
                FloatingActionButton.small(
                  heroTag: 'layers',
                  onPressed: _openLayersSheet,
                  child: const Icon(Icons.layers),
                ),
                const SizedBox(height: 10),
                FloatingActionButton.small(
                  heroTag: 'locate',
                  onPressed: _locateMe,
                  child: const Icon(Icons.my_location),
                ),
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
            ),
          ),
        ],
      ),
    );
  }

  Widget _routeBanner() {
    final props = _routeProps ?? {};
    final miles = props['distance_mi'] ?? props['miles'];
    final label = miles != null
        ? 'Low-stress bike route — $miles mi'
        : 'Low-stress bike route';
    return Padding(
      padding: const EdgeInsets.only(top: 8),
      child: Material(
        elevation: 3,
        borderRadius: BorderRadius.circular(20),
        color: const Color(0xFF1565C0),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 4),
          child: Row(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Icon(Icons.directions_bike, color: Colors.white, size: 18),
              const SizedBox(width: 8),
              Text(label, style: const TextStyle(color: Colors.white)),
              IconButton(
                icon: const Icon(Icons.close, color: Colors.white, size: 18),
                onPressed: _clearRoute,
              ),
            ],
          ),
        ),
      ),
    );
  }

  void _openLayersSheet() {
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
                      : Icon(def.icon,
                          color: Color(int.parse(
                              'ff${def.color.substring(1)}',
                              radix: 16))),
                  title: Text(def.label),
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
                  'This office has no public email — reports from the app are '
                  'relayed by BWG with the online form linked above.',
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
