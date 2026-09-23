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
import '../widgets/app_sheet.dart';
import '../widgets/travel_modes.dart';
import '../widgets/safety_notice.dart';
import '../nav.dart';
import '../nav_notifier.dart';
import '../theme.dart';
import '../widgets/elevation_profile.dart';
import '../widgets/map_cards.dart';
import '../widgets/alternative_chip.dart';
import '../widgets/recording_sheet.dart';
import '../widgets/record_icon.dart';
import '../widgets/welcome_tour.dart';
import '../auth.dart';
import 'add_point_sheet.dart';
import 'directions_sheet.dart';
import 'report_sheet.dart';
import 'community_screen.dart';
import 'area_draw_sheet.dart';
import 'route_draw_sheet.dart';
import '../geometry_draft.dart';
import '../widgets/ride_summary_sheet.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';
import 'package:flutter/foundation.dart';
import 'rides_screen.dart';
import 'tools_screen.dart';
import '../rides.dart';
import '../group_ride.dart';
import 'group_ride_sheet.dart';

class MapScreen extends StatefulWidget {
  const MapScreen({super.key});

  @override
  State<MapScreen> createState() => _MapScreenState();
}

class _MapScreenState extends State<MapScreen> {
  MapLibreMapController? _map;
  bool _styleReady = false;
  bool _locationEnabled = false;

  // Web addImage fixes pixelRatio at 1; only iOS decodes PNGs at screen scale.
  // Avoid oversized sprites and atlas churn when zooming dense downtown POIs.
  double get _imageDpr => !kIsWeb && defaultTargetPlatform == TargetPlatform.iOS
      ? MediaQuery.of(context).devicePixelRatio
      : 1.0;

  /// Basemap style currently on the widget. Changing it (dark mode) makes
  /// MapLibre reload the whole style, which drops every source and layer we
  /// added — [build] detects the swap and resets the bookkeeping so
  /// [_onStyleLoaded] rebuilds them.
  String? _activeStyle;
  bool? _activeContrast;

  // Search state.
  final _searchCtl = TextEditingController();
  final _searchFocus = FocusNode();
  Timer? _searchDebounce;
  List<dynamic> _results = [];
  int _searchSeq = 0;

  /// A search request is in flight — spinner in the field's suffix.
  bool _searching = false;

  /// "Searching…", "3 results", "No results" — a live region announces it.
  String _searchStatus = '';

  /// Destination picked from search — drives the bottom place card.
  Map<String, dynamic>? _place;

  // Trip planning: both ends of the trip, so a route doesn't have to start
  // where the rider is standing.
  TripEndpoint _from = TripEndpoint.myLocation;
  TripEndpoint? _to;

  /// Trail preference for the CURRENT trip only (the preview chip and the
  /// planner sheet set it); null = follow Settings. Cleared with the route.
  bool? _tripTrail;
  bool? _tripCommunity;

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

  /// The router's raw Feature behind [_navRoute]: a leading group rider
  /// shares it with the group.
  Map<String, dynamic>? _navFeature;

  /// Ride shown on the map for trimming, and the kept [start, end] indices.
  Ride? _shownRide;
  int _rideStart = 0, _rideEnd = 0;

  /// Trim mode: two draggable circle annotations on the ride line.
  bool _trimming = false;
  Circle? _startHandle, _endHandle;
  bool _rideDrawing = false, _rideDrawPending = false;

  /// Draw tools (waypoint route / no-entry corners); map taps feed them.
  RouteDraft? _routeDraft;
  AreaDraft? _areaDraft;
  bool _straightNext = false;
  int _legsPending = 0;
  Future<void> _drawQueue = Future.value();
  bool _sharedTripHandled = false;
  bool _welcomeOffered = false;
  VoidCallback? _recorderListener;
  VoidCallback? _groupListener;
  int _groupRevision = -1;
  bool _groupEnded = false;
  bool _groupWasActive = false;
  final Set<String> _groupImages = {};
  bool _recoveryOffered = false;
  int? _lastRideRevision;
  bool? _recordingAwake;
  LatLng? _destination;
  bool _navigating = false;
  NavProgress? _progress;
  StreamSubscription<Position>? _posSub;

  // GPS watchdog: navigation is only as alive as its position stream, and the
  // stream can die silently (platform channel error, provider stall, onDone).
  // Every fix stamps [_lastFixAt]; the watchdog resubscribes when fixes stop.
  Timer? _navWatchdog;
  DateTime _lastFixAt = DateTime.fromMillisecondsSinceEpoch(0);
  bool _gpsDryToastShown = false;

  /// Last raw fix while navigating — feeds the live speed / GPS-health line
  /// in the trip bar, the visible proof the screen is tracking, not frozen.
  Position? _lastNavFix;
  FlutterTts? _tts;
  bool _voice = true;
  int _spokenStep = -1;
  bool _spokenImminent = false;
  final _rerouteGovernor = RerouteGovernor();
  bool _rerouting = false;

  // Follow camera: on until the user pans away, then a Re-center chip brings
  // it back. `_progAnimUntil` marks our own camera animations so a user
  // gesture can be told apart from the follow camera moving itself.
  bool _followNav = true;
  DateTime _lastCamAt = DateTime.fromMillisecondsSinceEpoch(0);
  DateTime _progAnimUntil = DateTime.now().add(const Duration(seconds: 5));
  LatLng? _lastNavPos;
  double? _lastNavBearing;

  /// Current map rotation, degrees. Drives the rail's compass, which only
  /// appears once the map is actually turned off north.
  double _bearing = 0;

  // Persistent notification with the upcoming turn.
  final _navNotifier = NavNotifier();
  int _notifiedStep = -1;
  DateTime _lastNotifyAt = DateTime.fromMillisecondsSinceEpoch(0);

  /// Puck bitmaps already registered with the style (by image name).
  final Set<String> _puckImages = {};
  String _puckImage = 'puck-arrow';

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
    _searchCtl.dispose();
    _navWatchdog?.cancel();
    _posSub?.cancel();
    if (_recorderListener != null) {
      context.read<RideRecorder>().removeListener(_recorderListener!);
    }
    if (_groupListener != null) {
      context.read<GroupRideClient>().removeListener(_groupListener!);
    }
    _tts?.stop();
    _navNotifier.cancel();
    WakelockPlus.disable();
    super.dispose();
  }

  // ---------------------------------------------------------------- layers

  /// The app's own line layers (highlight, route, ride, draft, group route),
  /// built in one place so style load and the High contrast toggle agree.
  /// High contrast widens them 1.6x and swaps the pale amber highlight for
  /// deep orange on light bases (amber is 1.5:1 on the light basemap); a dark
  /// base swaps the ride purple (2.6:1 on black) for a light orchid (8.8:1).
  Map<String, LineLayerProperties> _ownLineStyles() {
    final state = context.read<AppState>();
    final hc = state.highContrast;
    final k = hc ? 1.6 : 1.0;
    final ride = _darkBase() ? rideLineOnDarkHex : rideLineHex;
    LineLayerProperties line(dynamic color, double width, {double? opacity,
        List<double>? dash, String cap = 'round'}) => LineLayerProperties(
      lineColor: color,
      lineWidth: width * k,
      lineOpacity: opacity == null ? null : (hc ? math.max(opacity, 0.9) : opacity),
      lineDasharray: dash,
      lineCap: cap,
      lineJoin: 'round',
    );
    return {
      'lyr-highlight-line': line(
        hc && !_darkBase() ? '#E65100' : '#FFC107', 12.0,
        opacity: hc ? 0.7 : 0.55,
      ),
      'lyr-route-casing': line('#ffffff', 8.0),
      'lyr-route': line(['coalesce', ['get', 'color'], '#1565C0'], 5.0),
      'lyr-route-hills': line([
        'match',
        ['get', 'sev'],
        for (final e in hillColors.entries) ...[e.key, e.value],
        hillColors['mod']!,
      ], 5.0),
      'lyr-route-warn': line('#D32F2F', 5.0, dash: [2.0, 1.6], cap: 'butt'),
      'lyr-ride-rest': line('#9E9E9E', 4.0, opacity: 0.8),
      'lyr-ride-gap': line(ride, 3.0, opacity: 0.85, dash: [1.5, 1.5], cap: 'butt'),
      'lyr-ride': line(ride, 5.0, opacity: 0.85),
      'lyr-draft-line': line(['get', 'color'], 4.0),
      'lyr-group-route': line(groupLeaderHex, 5.0, opacity: 0.7, dash: [2.0, 1.0]),
    };
  }

  /// Satellite imagery or the dark basemap: colors must read on near-black.
  bool _darkBase() =>
      context.read<AppState>().mapBase == MapBase.satellite ||
      Theme.of(context).brightness == Brightness.dark;

  /// High contrast toggled without a style reload: re-apply [_ownLineStyles].
  Future<void> _restyleOwnLines() async {
    final map = _map;
    if (map == null || !mounted) return;
    for (final e in _ownLineStyles().entries) {
      try {
        await map.setLayerProperties(e.key, e.value);
      } catch (_) {}
    }
  }

  Future<void> _onStyleLoaded() async {
    final map = _map!;
    final ratio = _imageDpr;
    final own = _ownLineStyles();

    // Tap highlight: one source, two layers — the line layer renders when the
    // tapped feature is a line, the circle layer when it's a point. The
    // geometry-type filters matter: without them the circle layer draws a
    // ring on EVERY VERTEX of a tapped line (the SRT turned into dot soup).
    await map.addSource(
      'highlight',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addLineLayer(
      'highlight',
      'lyr-highlight-line',
      own['lyr-highlight-line']!,
      filter: [
        '==',
        ['geometry-type'],
        'LineString',
      ],
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
      filter: [
        '==',
        ['geometry-type'],
        'Point',
      ],
      enableInteraction: false,
    );

    // Route line (casing + fill) and the selection pin, above everything.
    // Thematic layers are added lazily (see _ensureLayer): line layers slot
    // in below the highlight, symbol pins below the route casing, so draw
    // order stays lines → highlight → pins → route → pin.
    await map.addSource(
      'route',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addLineLayer(
      'route',
      'lyr-route-casing',
      own['lyr-route-casing']!,
      enableInteraction: false,
    );
    await map.addLineLayer(
      'route',
      'lyr-route',
      own['lyr-route']!,
      enableInteraction: false,
    );

    // Hills, drawn over the route line and colored by severity (amber →
    // red), so "how hard is this trip" is visible on the map itself and not
    // only in the elevation graph.
    await map.addSource(
      'route-hills',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addLineLayer(
      'route-hills',
      'lyr-route-hills',
      own['lyr-route-hills']!,
      enableInteraction: false,
    );

    // Gaps in the network, drawn dashed ON TOP of the route: the stretches with
    // no sidewalk (walk/roll) or no bike lane (bike). The route still goes
    // there — this is the disclosure, not a detour.
    await map.addSource(
      'route-warn',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addLineLayer(
      'route-warn',
      'lyr-route-warn',
      own['lyr-route-warn']!,
      enableInteraction: false,
    );

    // Upcoming turns, on the map itself: an arrowhead at each maneuver rotated
    // to the heading the rider leaves it on.
    await map.addImage(
      'turn-marker',
      await renderTurnMarker(color: brandDark, devicePixelRatio: ratio),
    );
    await map.addSource(
      'route-steps',
      GeojsonSourceProperties(data: _emptyCollection),
    );
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
    await map.addSource(
      'ride',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    // Trimming: the discarded part grey, the kept stretch purple, GPS gaps
    // inside the kept range dashed.
    await map.addLineLayer(
      'ride',
      'lyr-ride-rest',
      own['lyr-ride-rest']!,
      filter: ['==', ['get', 'part'], 'rest'],
      enableInteraction: false,
    );
    await map.addLineLayer(
      'ride',
      'lyr-ride-gap',
      own['lyr-ride-gap']!,
      filter: ['==', ['get', 'part'], 'gap'],
      enableInteraction: false,
    );
    await map.addLineLayer(
      'ride',
      'lyr-ride',
      own['lyr-ride']!,
      filter: ['==', ['get', 'part'], 'kept'],
      enableInteraction: false,
    );
    // Draw tools: the draft route/area and its waypoint markers.
    await map.addSource(
      'draft',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addFillLayer(
      'draft',
      'lyr-draft-fill',
      const FillLayerProperties(fillColor: '#C62828', fillOpacity: 0.22),
      filter: ['==', ['geometry-type'], 'Polygon'],
      enableInteraction: false,
    );
    await map.addLineLayer(
      'draft',
      'lyr-draft-line',
      own['lyr-draft-line']!,
      filter: ['!=', ['geometry-type'], 'Point'],
      enableInteraction: false,
    );
    await map.addCircleLayer(
      'draft',
      'lyr-draft-pt',
      const CircleLayerProperties(
        circleColor: '#ffffff',
        circleRadius: 6.0,
        circleStrokeColor: ['get', 'color'],
        circleStrokeWidth: 3.0,
      ),
      filter: ['==', ['geometry-type'], 'Point'],
      enableInteraction: false,
    );
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

    // Group ride: the leader's shared route (tap → follow it), then everyone
    // else as dots with name labels (bitmaps: satellite has no glyphs).
    _groupImages.clear();
    await map.addSource(
      'group-route',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addLineLayer(
      'group-route',
      'lyr-group-route',
      own['lyr-group-route']!,
    );
    await map.addSource(
      'group-members',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addCircleLayer(
      'group-members',
      'lyr-group-members',
      const CircleLayerProperties(
        circleColor: [
          'case',
          ['get', 'leader'],
          groupLeaderHex,
          groupRideHex,
        ],
        circleRadius: [
          'case',
          ['get', 'leader'],
          10.0,
          7.0,
        ],
        circleStrokeColor: '#ffffff',
        circleStrokeWidth: 3.0,
      ),
      enableInteraction: false,
    );
    await map.addSymbolLayer(
      'group-members',
      'lyr-group-names',
      const SymbolLayerProperties(
        iconImage: ['get', 'img'],
        iconAnchor: 'top',
        iconOffset: [0, 12],
        iconAllowOverlap: true,
        iconIgnorePlacement: true,
      ),
      enableInteraction: false,
    );

    // The rider themselves, during navigation: an arrow (or mode icon —
    // Settings) rotated to the heading, above everything else. The native
    // blue dot is hidden while this is on screen.
    await map.addSource(
      'puck',
      GeojsonSourceProperties(data: _emptyCollection),
    );
    await map.addSymbolLayer(
      'puck',
      'lyr-puck',
      const SymbolLayerProperties(
        iconImage: ['get', 'icon'],
        iconRotate: ['get', 'bearing'],
        iconRotationAlignment: 'map',
        // Billboard: keep the marker facing the camera instead of lying flat
        // on the tilted ground plane — at the 60° isometric nav tilt a
        // map-pitched icon foreshortens into an unreadable sliver.
        iconPitchAlignment: 'viewport',
        iconAllowOverlap: true,
        iconIgnorePlacement: true,
        iconSize: 1.0,
      ),
      enableInteraction: false,
    );

    _styleReady = true;
    _applyVisibility();
    _refreshLive();

    // After a style reload (dark-mode swap) the freshly re-added sources are
    // empty — put the trip back on the map.
    final route = _navRoute;
    if (route != null) {
      await map.setGeoJsonSource('route', route.routeCollection());
      await map.setGeoJsonSource('route-hills', route.hillCollection());
      await map.setGeoJsonSource('route-warn', route.warnCollection());
      await map.setGeoJsonSource(
        'route-steps',
        route.stepCollection(fromStep: _progress?.stepIndex ?? 0),
      );
      if (_destination != null) await _setPin(_destination!);
    }
    if (_navigating) {
      await _ensurePuckImage();
      final here = _lastNavPos;
      if (here != null) await _updatePuck(here, _lastNavBearing ?? 0);
    }
    await _drawRide();
    await _drawDraft();
    // A style reload wipes annotations; put the trim handles back.
    if (_trimming) await _placeHandles();
    if (!mounted) return;
    if (_recorderListener == null) {
      _recorderListener = () {
        final recorder = context.read<RideRecorder>();
        if (_lastRideRevision != recorder.traceRevision) {
          _lastRideRevision = recorder.traceRevision;
          _drawRide();
        }
        _offerRideRecovery();
        _syncWakelock();
      };
      context.read<RideRecorder>().addListener(_recorderListener!);
    }
    if (_groupListener == null) {
      _groupListener = () {
        final group = context.read<GroupRideClient>();
        if (group.ended && !_groupEnded && mounted) {
          toast(context, 'The ride has ended.');
        }
        _groupEnded = group.ended;
        // Started / joined a ride mid-navigation: the leader's route goes up
        // now rather than waiting for the next reroute.
        if (group.active && !_groupWasActive && _navigating) {
          unawaited(_shareGroupRoute());
        }
        _groupWasActive = group.active;
        if (_groupRevision != group.revision) {
          _groupRevision = group.revision;
          _drawGroup();
        }
      };
      context.read<GroupRideClient>().addListener(_groupListener!);
    }
    await _drawGroup();
    _offerRideRecovery();
    await _openSharedTrip();
    if (!_welcomeOffered && mounted) {
      _welcomeOffered = true;
      await maybeShowWelcomeTour(context);
    }
  }

  // ---------------------------------------------------------------- rides

  /// Keep the screen on while navigating OR actively recording (all four
  /// combinations): one place decides, called on every change of either.
  void _syncWakelock() {
    if (!mounted) return;
    final recorder = context.read<RideRecorder>();
    final awake = _navigating || (recorder.recording && !recorder.paused);
    if (_recordingAwake == awake) return;
    _recordingAwake = awake;
    if (awake) {
      WakelockPlus.enable();
    } else {
      WakelockPlus.disable();
    }
  }

  /// The live trace while recording, else the shown ride split into kept /
  /// discarded / gap parts.
  Future<void> _drawRide() async {
    final map = _map;
    if (map == null || !mounted || !_styleReady) return;
    // Drag frames arrive faster than the platform channel; coalesce them.
    if (_rideDrawing) {
      _rideDrawPending = true;
      return;
    }
    _rideDrawing = true;
    final recorder = context.read<RideRecorder>();
    try {
      do {
        _rideDrawPending = false;
        final ride = _shownRide;
        final Map<String, dynamic> data;
        if (recorder.recording && ride == null) {
          data = {
            'type': 'FeatureCollection',
            'features': [
              {
                'type': 'Feature',
                'geometry': recorder.liveRide.lineString(),
                'properties': {'part': 'kept'},
              },
            ],
          };
        } else if (ride != null) {
          data = ride.trimCollection(_rideStart, _rideEnd);
        } else {
          data = _emptyCollection;
        }
        await map.setGeoJsonSource('ride', data);
      } while (_rideDrawPending && mounted);
    } finally {
      _rideDrawing = false;
    }
  }

  Future<bool> _locationForRecording() async {
    final permission = await Geolocator.requestPermission();
    if (permission == LocationPermission.denied ||
        permission == LocationPermission.deniedForever) {
      if (mounted) toast(context, 'Location permission is needed to record.');
      return false;
    }
    return mounted;
  }

  Future<void> _toggleRecording() async {
    final recorder = context.read<RideRecorder>();
    if (recorder.busy) return;
    if (!recorder.loaded && !await recorder.retry()) {
      if (mounted) toast(context, recorder.error ?? 'Could not load rides. Retry.');
      return;
    }
    if (!recorder.recording) {
      if (!await _locationForRecording()) return;
      await _clearRide();
      await recorder.start();
      if (!mounted) return;
      _syncWakelock();
      if (recorder.error != null) toast(context, recorder.error!);
      if (recorder.recording) _openRecordingSheet();
      return;
    }
    _openRecordingSheet();
  }

  void _offerRideRecovery() {
    if (!mounted || _recoveryOffered || !context.read<RideRecorder>().recovered) return;
    _recoveryOffered = true;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (mounted) _openRideSummary();
    });
  }

  void _openRecordingSheet() {
    showAppSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => RecordingSheet(
        onResume: () async {
          final recorder = context.read<RideRecorder>();
          if (!await _locationForRecording()) return;
          await recorder.resume();
          _syncWakelock();
        },
        onPause: () async {
          await context.read<RideRecorder>().pause();
          _syncWakelock();
        },
        onStop: () async {
          Navigator.pop(ctx);
          await context.read<RideRecorder>().pause();
          _syncWakelock();
          if (mounted) await _openRideSummary();
        },
      ),
    );
  }

  /// Stop → summary. With [saved], the sheet opens on an existing ride (from
  /// My rides) at its after-save state.
  Future<void> _openRideSummary({Ride? saved}) async {
    final recorder = context.read<RideRecorder>();
    if (saved == null && !recorder.recording) return;
    final ride = saved ?? recorder.liveRide;
    await _setShownRide(ride);
    if (!mounted) return;
    final result = await showAppSheet<RideSummaryResult>(
      context: context,
      showDragHandle: true,
      isScrollControlled: true,
      builder: (_) => RideSummarySheet(
        ride: ride,
        canShare: !_navigating,
        onSave: saved != null
            ? null
            : (name) async {
                final done = await recorder.stop(name: name);
                if (done != null && mounted) await _setShownRide(done);
                return (ride: done, error: done == null ? recorder.error : null);
              },
        onDiscard: saved != null
            ? null
            : () async {
                final ok = await recorder.discard();
                _syncWakelock();
                return ok;
              },
      ),
    );
    if (!mounted) return;
    if (result == RideSummaryResult.share && _shownRide != null) {
      await _startTrim();
    } else {
      await _clearRide();
    }
  }

  bool get _toolOpen =>
      _routeDraft != null || _areaDraft != null || _trimming;

  Future<void> _showRide(Ride ride) => _openRideSummary(saved: ride);

  Future<void> _setShownRide(Ride ride) async {
    setState(() {
      _shownRide = ride;
      _rideStart = 0;
      _rideEnd = math.max(0, ride.points.length - 1);
    });
    await _drawRide();
    // Navigating owns the camera.
    if (!_navigating) await _fitGeometry(ride.lineString());
  }

  // ------------------------------------------------------------- trimming

  Future<void> _startTrim() async {
    final ride = _shownRide;
    if (ride == null || ride.points.length < 2) return;
    setState(() => _trimming = true);
    await _placeHandles();
  }

  /// Start / end handles as draggable circle annotations. maplibre_gl moves
  /// the circle itself; [_onHandleDrag] snaps it to the nearest ride point.
  Future<void> _placeHandles() async {
    final map = _map;
    final ride = _shownRide;
    if (map == null || ride == null || !_styleReady) return;
    await _removeHandles();
    // Hit area = radius + stroke: a 24 dp halo makes a 48 dp target around
    // the 22 dp dot, so a thumb can grab it without covering it.
    CircleOptions handle(LatLng at, String color) => CircleOptions(
      geometry: at,
      circleRadius: 11,
      circleColor: color,
      circleStrokeColor: '#ffffff',
      circleStrokeWidth: 13,
      circleStrokeOpacity: 0.55,
      draggable: true,
    );
    try {
      _startHandle = await map.addCircle(
        handle(ride.points[_rideStart], '#2E7D32'),
      );
      _endHandle = await map.addCircle(
        handle(ride.points[_rideEnd], '#C62828'),
      );
    } catch (_) {
      // Annotation manager not ready (style reloading); onStyleLoaded retries.
    }
  }

  Future<void> _removeHandles() async {
    final map = _map;
    final handles = [?_startHandle, ?_endHandle];
    _startHandle = _endHandle = null;
    if (map == null || handles.isEmpty) return;
    try {
      await map.removeCircles(handles);
    } catch (_) {}
  }

  void _onHandleDrag(
    math.Point<double> point,
    LatLng origin,
    LatLng current,
    LatLng delta,
    String id,
    Annotation? annotation,
    DragEventType eventType,
  ) {
    final ride = _shownRide;
    if (!mounted || !_trimming || ride == null) return;
    final isStart = id == _startHandle?.id;
    if (!isStart && id != _endHandle?.id) return;
    final i = ride.nearestIndexNear(current, isStart ? _rideStart : _rideEnd);
    final range = isStart
        ? ride.trimRange(math.min(i, _rideEnd - 1), _rideEnd)
        : ride.trimRange(_rideStart, math.max(i, _rideStart + 1));
    if (range.start != _rideStart || range.end != _rideEnd) {
      setState(() {
        _rideStart = range.start;
        _rideEnd = range.end;
      });
      _drawRide();
    }
    if (eventType == DragEventType.end) {
      final circle = isStart ? _startHandle : _endHandle;
      final snapped = ride.points[isStart ? _rideStart : _rideEnd];
      // Let the annotation manager finish its own drag update first.
      if (circle != null) {
        Future<void>.delayed(const Duration(milliseconds: 50), () async {
          // Trim may have ended (handles removed) within those 50 ms.
          if (circle != _startHandle && circle != _endHandle) return;
          try {
            await _map?.updateCircle(circle, CircleOptions(geometry: snapped));
          } catch (_) {}
        });
      }
    }
  }

  /// Longest run of the ride inside the current viewport — "only include the
  /// part I can see" is how you carve one stretch out of a long ride.
  Future<void> _trimRideToView() async {
    final ride = _shownRide;
    final map = _map;
    if (ride == null || map == null) return;
    final bounds = await map.getVisibleRegion();
    bool inside(LatLng p) =>
        p.latitude >= bounds.southwest.latitude &&
        p.latitude <= bounds.northeast.latitude &&
        p.longitude >= bounds.southwest.longitude &&
        p.longitude <= bounds.northeast.longitude;
    final kept = ride.longestStretchWhere(inside);
    if (kept == null) {
      if (mounted) toast(context, 'None of this ride is on screen.');
      return;
    }
    await _setTrim(kept.start, kept.end);
  }

  Future<void> _setTrim(int start, int end) async {
    setState(() {
      _rideStart = start;
      _rideEnd = end;
    });
    await _drawRide();
    if (_trimming) await _placeHandles();
  }

  Future<void> _clearRide() async {
    await _removeHandles();
    if (!mounted) return;
    setState(() {
      _shownRide = null;
      _trimming = false;
    });
    await _drawRide();
  }

  /// Kept stretch → ≤200 vertices → name/comment sheet → publish.
  Future<void> _shareStretch() async {
    final ride = _shownRide;
    if (ride == null) return;
    final kept = ride.points.sublist(_rideStart, _rideEnd + 1);
    if (kept.length < 2) return;
    final published = await _publishGeometry(
      fitToVertexLimit(kept),
      name: ride.name,
      category: 'route-suggestion',
    );
    if (published && mounted) await _clearRide();
  }

  /// Shared publish step for every drawn/recorded geometry: auth gate, then
  /// the existing name / comment / category sheet and submit endpoint.
  Future<bool> _publishGeometry(
    List<LatLng> points, {
    bool polygon = false,
    String? name,
    String? comment,
    String? category,
    String? replaces,
  }) async {
    if (!await AuthGate.require(context) || !mounted) return false;
    final coords = [
      for (final p in points) [p.longitude, p.latitude],
    ];
    final saved = await showAppSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      builder: (_) => AddPointSheet(
        latLng: points[points.length ~/ 2],
        geometry: polygon
            ? {
                'type': 'Polygon',
                'coordinates': [
                  [...coords, coords.first],
                ],
              }
            : {'type': 'LineString', 'coordinates': coords},
        initialCategory: polygon ? 'no-entry' : category ?? 'route-suggestion',
        initialName: name,
        initialComment: comment,
        replaces: replaces,
      ),
    );
    if (saved == null || !mounted) return false;
    await _refreshCommunity();
    if (mounted) {
      toast(
        context,
        submittedMessage(
          saved,
          published: 'Published. Community history includes rollback.',
        ),
      );
    }
    return true;
  }

  Widget _trimPanel() {
    final ride = _shownRide!;
    final gap = ride.spansGap(_rideStart, _rideEnd);
    return Material(
      elevation: 6,
      borderRadius: BorderRadius.circular(18),
      color: Theme.of(context).colorScheme.surface,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 6, 10),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Semantics(
                    liveRegion: true,
                    child: Text(
                      'Keeping ${formatDistance(ride.keptDistanceM(_rideStart, _rideEnd))} '
                      'of ${formatDistance(ride.distanceM)}'
                      '${gap ? ' · includes a GPS gap' : ''}',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                  ),
                ),
                IconButton(
                  tooltip: 'Close',
                  icon: const Icon(Icons.close),
                  onPressed: _clearRide,
                ),
              ],
            ),
            const Text(
              'Drag the green and red handles along the ride.',
              style: TextStyle(fontSize: 12),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ActionChip(
                  avatar: const Icon(Icons.crop_free, size: 18),
                  label: const Text('Keep what is on screen'),
                  onPressed: _trimRideToView,
                ),
                ActionChip(
                  avatar: const Icon(Icons.undo, size: 18),
                  label: const Text('Whole ride'),
                  onPressed: () => _setTrim(0, ride.points.length - 1),
                ),
                FilledButton.icon(
                  style: FilledButton.styleFrom(backgroundColor: brandGreenStrong),
                  icon: const Icon(Icons.groups_outlined),
                  label: const Text('Share'),
                  onPressed: _shareStretch,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  // ---------------------------------------------------------- draw tools

  Future<void> _startRouteDraw(LatLng first) async {
    final state = context.read<AppState>();
    await _clearRide();
    setState(() {
      _areaDraft = null;
      _straightNext = false;
      _routeDraft = RouteDraft(
        modes: state.apiModes,
        stress: state.stressApiName,
      );
    });
    await _addDraftPoint(first);
  }

  Future<void> _startAreaDraw(LatLng first) async {
    await _clearRide();
    setState(() {
      _routeDraft = null;
      _areaDraft = AreaDraft();
    });
    await _addDraftPoint(first);
  }

  /// Map tap while a draw tool is open. Legs are fetched in tap order.
  Future<void> _addDraftPoint(LatLng p) {
    final route = _routeDraft, area = _areaDraft;
    if (area != null) {
      if (!area.add(p)) toast(context, 'Use at most 200 corners.');
      setState(() {});
      return _drawDraft();
    }
    if (route == null) return Future.value();
    final straight = _straightNext;
    setState(() => _legsPending++);
    return _drawQueue = _drawQueue.then((_) async {
      final fellBack = await route.add(p, straight: straight);
      if (!mounted || _routeDraft != route) return;
      setState(() => _legsPending--);
      if (fellBack) toast(context, 'No route found; drew a straight line.');
      await _drawDraft();
    });
  }

  Future<void> _drawDraft() async {
    final map = _map;
    if (map == null || !_styleReady) return;
    final route = _routeDraft, area = _areaDraft;
    // The waypoint markers replace the dropped pin the tool started from.
    if (route != null || area != null) {
      await map.setGeoJsonSource('pin', _emptyCollection);
    }
    final color = area != null ? '#C62828' : '#6F9920';
    final pts = route?.waypoints ?? area?.corners ?? const <LatLng>[];
    List<double> c(LatLng p) => [p.longitude, p.latitude];
    final line = route?.line ?? const <LatLng>[];
    await map.setGeoJsonSource('draft', {
      'type': 'FeatureCollection',
      'features': [
        if (area != null && area.corners.length >= 3)
          {'type': 'Feature', 'geometry': area.geometry, 'properties': {'color': color}},
        if (area != null && area.corners.length == 2)
          {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': area.corners.map(c).toList()},
            'properties': {'color': color},
          },
        if (line.length >= 2)
          {
            'type': 'Feature',
            'geometry': {'type': 'LineString', 'coordinates': line.map(c).toList()},
            'properties': {'color': color},
          },
        for (final p in pts)
          {
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': c(p)},
            'properties': {'color': color},
          },
      ],
    });
  }

  Future<void> _cancelDraw() async {
    setState(() {
      _routeDraft = null;
      _areaDraft = null;
      _legsPending = 0;
    });
    await _drawDraft();
  }

  Future<void> _publishDraw() async {
    await _drawQueue;
    if (!mounted) return;
    final route = _routeDraft, area = _areaDraft;
    final bool ok;
    if (area != null && area.canPublish) {
      ok = await _publishGeometry(
        area.corners,
        polygon: true,
        name: area.name,
        comment: area.comment,
        replaces: area.replaces,
      );
    } else if (route != null && route.canPublish) {
      ok = await _publishGeometry(
        fitToVertexLimit(route.line),
        name: route.name,
        comment: route.comment,
        category: route.category,
        replaces: route.replaces,
      );
    } else {
      return;
    }
    if (ok && mounted) await _cancelDraw();
  }

  Widget _drawBar() {
    final area = _areaDraft;
    if (area != null) {
      return AreaDrawBar(
        draft: area,
        onUndo: () {
          setState(area.undo);
          _drawDraft();
        },
        onPublish: _publishDraw,
        onCancel: _cancelDraw,
      );
    }
    final route = _routeDraft!;
    return RouteDrawBar(
      draft: route,
      straight: _straightNext,
      busy: _legsPending > 0,
      onUndo: () {
        setState(route.undo);
        _drawDraft();
      },
      onStraight: (v) => setState(() => _straightNext = v),
      onPublish: _publishDraw,
      onCancel: _cancelDraw,
    );
  }

  /// Web build opened from a shared link: `/bwg-app/?from=lat,lon&to=lat,lon`.
  Future<void> _openSharedTrip() async {
    if (!kIsWeb || _sharedTripHandled) return;
    _sharedTripHandled = true;
    final q = Uri.base.queryParameters;
    LatLng? parse(String? v) {
      final parts = (v ?? '').split(',');
      if (parts.length != 2) return null;
      final lat = double.tryParse(parts[0]), lon = double.tryParse(parts[1]);
      return lat == null || lon == null ? null : LatLng(lat, lon);
    }

    // `?ride=CODE`: a group ride share link opens the join sheet.
    final rideCode = rideCodeFromUri(Uri.base);
    if (rideCode != null) {
      if (!context.read<GroupRideClient>().active) {
        unawaited(_openGroupRide(code: rideCode));
      }
      return;
    }
    final from = parse(q['from']), to = parse(q['to']);
    if (to == null) return;
    final state = context.read<AppState>();
    final stress = BikeStress.values.where((s) => s.name == q['stress']);
    if (stress.isNotEmpty) state.setStress(stress.first);
    setState(() {
      _from = from == null
          ? TripEndpoint.myLocation
          : TripEndpoint(label: 'Shared start', latLng: from);
      _to = TripEndpoint(label: 'Shared destination', latLng: to);
      _destination = to;
    });
    await _planTrip();
  }

  /// Copy a link that replans this trip in the web app.
  Future<void> _shareTrip() async {
    final route = _navRoute;
    if (route == null || route.points.length < 2) return;
    final a = route.points.first, b = route.points.last;
    String f(double v) => v.toStringAsFixed(5);
    final url =
        'https://bwg.mrsm.io/bwg-app/?from=${f(a.latitude)},${f(a.longitude)}'
        '&to=${f(b.latitude)},${f(b.longitude)}'
        '&stress=${context.read<AppState>().stressApiName}';
    await Clipboard.setData(ClipboardData(text: url));
    if (mounted) toast(context, 'Trip link copied.');
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
      if (def.isLabel) {
        // Text labels as bitmaps: fetched inline (a handful of features),
        // one image per feature name, ink picked for the base underneath
        // (satellite imagery reads dark). Re-rendered on every style swap
        // since the swap clears images anyway.
        final geojson = await api.layerGeoJson(def.path);
        if (!mounted) return;
        final darkBase =
            context.read<AppState>().mapBase == MapBase.satellite ||
            Theme.of(context).brightness == Brightness.dark;
        final dpr = _imageDpr;
        final features = List<Map<String, dynamic>>.from(
          geojson['features'] ?? [],
        );
        for (final f in features) {
          final props = Map<String, dynamic>.from(f['properties'] ?? {});
          final name = (props['name'] ?? '').toString();
          if (name.isEmpty) continue;
          final key = 'lbl-${def.id}-$name';
          props['__img'] = key;
          f['properties'] = props;
          await map.addImage(
            key,
            await renderLabel(
              text: name,
              devicePixelRatio: dpr,
              darkBase: darkBase,
            ),
          );
        }
        await map.addSource(def.id, GeojsonSourceProperties(data: geojson));
        await map.addSymbolLayer(
          def.id,
          'lyr-${def.id}',
          const SymbolLayerProperties(
            iconImage: ['get', '__img'],
            iconSize: 1.0,
            iconAllowOverlap: true,
          ),
          minzoom: def.minZoom > 0 ? def.minZoom : null,
          belowLayerId: 'lyr-route-casing',
        );
        return;
      }
      final url = await api.layerUrl(def.path);
      await map.addSource(def.id, GeojsonSourceProperties(data: url));
      if (def.isPoint) {
        if (!mounted) return;
        await map.addImage(
          'pin-${def.id}',
          await renderPin(
            icon: def.icon,
            color: hexColor(def.color),
            devicePixelRatio: _imageDpr,
            scale: 1.0,
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
          filter: def.filter,
          belowLayerId: 'lyr-route-casing',
        );
      } else if (def.isHeatmap) {
        // Severity-weighted density: a death outweighs a stack of
        // no-injury crashes, mirroring the router's danger scoring.
        await map.addHeatmapLayer(
          def.id,
          'lyr-${def.id}',
          HeatmapLayerProperties(
            heatmapWeight: [
              'case',
              [
                '>',
                [
                  'coalesce',
                  ['get', 'killed'],
                  0,
                ],
                0,
              ],
              1.0,
              [
                '>',
                [
                  'coalesce',
                  ['get', 'injured'],
                  0,
                ],
                0,
              ],
              0.35,
              0.15,
            ],
            heatmapRadius: [
              'interpolate',
              ['linear'],
              ['zoom'],
              10.0,
              10.0,
              13.0,
              18.0,
              16.0,
              32.0,
            ],
            heatmapIntensity: [
              'interpolate',
              ['linear'],
              ['zoom'],
              10.0,
              1.0,
              16.0,
              2.5,
            ],
            heatmapColor: [
              'interpolate',
              ['linear'],
              ['heatmap-density'],
              0.0,
              'rgba(0,0,0,0)',
              0.25,
              'rgba(255,235,59,0.35)',
              0.5,
              'rgba(255,152,0,0.55)',
              0.75,
              'rgba(244,67,54,0.7)',
              1.0,
              'rgba(183,28,28,0.85)',
            ],
            heatmapOpacity: 0.8,
          ),
          belowLayerId: 'lyr-highlight-line',
        );
      } else if (def.isCircle) {
        // Some dot colors are tuned for a dark base (streetlight amber) and
        // vanish on the light map — swap to the layer's light-base color.
        final darkBase =
            (mounted &&
                context.read<AppState>().mapBase == MapBase.satellite) ||
            (mounted && Theme.of(context).brightness == Brightness.dark);
        await map.addCircleLayer(
          def.id,
          'lyr-${def.id}',
          CircleLayerProperties(
            circleColor: (!darkBase ? def.lightBaseColor : null) ?? def.color,
            circleRadius: [
              'interpolate',
              ['linear'],
              ['zoom'],
              12.0,
              1.5,
              16.0,
              4.0,
            ],
            circleOpacity: 0.7,
            circleStrokeWidth: 0.0,
          ),
          minzoom: def.minZoom > 0 ? def.minZoom : null,
          filter: def.filter,
          belowLayerId: 'lyr-highlight-line',
          // 40k dots with nothing to say — never swallow feature taps.
          enableInteraction: false,
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
          // Without the geometry-type filter MapLibre closes every
          // LineString in the shared community source into a red polygon.
          filter: def.filter,
          belowLayerId: 'lyr-highlight-line',
        );
      } else {
        // Low vision support: high contrast bolds every thematic line, and
        // satellite imagery (busy, dark) gets a milder boost so hairlines
        // like sidewalks don't vanish into rooftops.
        final appState = mounted ? context.read<AppState>() : null;
        final boost = (appState?.highContrast ?? false)
            ? 1.7
            : (appState?.mapBase == MapBase.satellite ? 1.35 : 1.0);
        final opacity = boost > 1.0
            ? math.max(def.opacity, boost >= 1.7 ? 0.85 : 0.75)
            : def.opacity;
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
                // Community purple sinks into the dark base (2.6:1).
                : _layerColorExpr(def,
                    mounted && _darkBase() && def.color == rideLineHex
                        ? rideLineOnDarkHex : null),
            lineWidth: def.width * boost,
            lineOpacity: opacity,
            lineCap: 'round',
            lineJoin: 'round',
            // Dotted: unofficial connectors (shortcuts) read differently
            // from mapped streets.
            lineDasharray: def.dashed ? [0.5, 2.0] : null,
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
  dynamic _layerColorExpr(LayerDef def, [String? fallback]) {
    if (def.matchProp != null && def.matchColors != null) {
      return [
        'match',
        ['get', def.matchProp!],
        for (final e in def.matchColors!.entries) ...[e.key, e.value],
        fallback ?? def.color,
      ];
    }
    return [
      'coalesce',
      ['get', 'color'],
      fallback ?? def.color,
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

  /// Drop and re-add every thematic LINE layer so a new width/opacity boost
  /// (the high-contrast toggle) takes effect without a style reload.
  Future<void> _restyleLineLayers() async {
    final map = _map;
    if (map == null) return;
    for (final def in layerDefs.where(
      (d) => !d.isPoint && !d.isFill && !d.isHeatmap && !d.isCircle,
    )) {
      if (!_addedLayers.contains(def.id)) continue;
      try {
        await map.removeLayer('lyr-${def.id}');
        await map.removeSource(def.id);
      } catch (_) {}
      _addedLayers.remove(def.id);
    }
    if (mounted) _applyVisibility();
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

  bool _featureQueryPending = false;
  /// "Edit this contribution" on a community line or area: reopen it in the
  /// waypoint tool (vertices → straight legs) or the corner tool. The
  /// published revision replaces the old one via `replaces`.
  Future<void> _editDrawn({
    bool polygon = false,
    Map<String, dynamic>? geometry,
    Map<String, dynamic>? properties,
  }) async {
    _dismissSearch();
    final interaction = _interactionSeq;
    final revision = properties?['id'];
    if (revision != null) {
      // Rendered features may be clipped to map tiles. Edit the complete,
      // authoritative revision instead of replacing it with a visible fragment.
      try {
        final collection = await api.layerGeoJson(
          '/map-layers/community.geojson',
        );
        Map<String, dynamic>? original;
        for (final item in (collection['features'] as List? ?? [])) {
          if ((item['properties'] as Map?)?['id'] == revision) {
            original = Map<String, dynamic>.from(item as Map);
            break;
          }
        }
        if (original == null) {
          throw ApiError(
            'This contribution changed. Refresh the map before editing.',
          );
        }
        geometry = Map<String, dynamic>.from(original['geometry'] as Map);
        properties = Map<String, dynamic>.from(original['properties'] as Map);
        polygon = geometry['type'] == 'Polygon';
      } catch (e) {
        if (mounted) toast(context, e.toString());
        return;
      }
    }
    if (!mounted ||
        interaction != _interactionSeq ||
        ModalRoute.of(context)?.isCurrent != true) {
      return;
    }
    final vertices = geometryLatLngs(geometry);
    if (vertices.length < 2) return;
    final state = context.read<AppState>();
    await _clearRide();
    if (!mounted) return;
    final name = properties?['name']?.toString();
    final comment = properties?['comment']?.toString();
    final replaces = properties?['id']?.toString();
    setState(() {
      _straightNext = false;
      if (polygon) {
        _routeDraft = null;
        _areaDraft = AreaDraft(
          corners: vertices,
          name: name,
          comment: comment,
          replaces: replaces,
        );
      } else {
        _areaDraft = null;
        _routeDraft = RouteDraft.seeded(
          vertices,
          modes: state.apiModes,
          stress: state.stressApiName,
          name: name,
          comment: comment,
          category: properties?['category']?.toString(),
          replaces: replaces,
        );
      }
    });
    await _drawDraft();
    await _fitGeometry(geometry);
  }

  /// Fly to a GeoJSON geometry picked from the Community edits list.
  Future<void> _fitGeometry(dynamic geometry) async {
    final points = geometryLatLngs(geometry);
    if (points.isEmpty) return;
    if (points.length == 1) {
      await _map?.animateCamera(CameraUpdate.newLatLngZoom(points.first, 17));
      return;
    }
    var minLat = 90.0, maxLat = -90.0, minLon = 180.0, maxLon = -180.0;
    for (final p in points) {
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
        left: 60,
        right: 60,
        top: 120,
        bottom: 160,
      ),
    );
  }

  Future<void> _refreshCommunity() async {
    final data = await api.layerGeoJson('/map-layers/community.geojson');
    for (final def in layerDefs.where((d) => d.id.startsWith('community'))) {
      if (mounted) context.read<AppState>().toggleLayer(def.id, true);
      await _ensureLayer(def);
      await _map?.setGeoJsonSource(def.id, data);
    }
  }

  int _interactionSeq = 0;
  DateTime _lastMapTap = DateTime.fromMillisecondsSinceEpoch(0);
  bool _mapCanInteract() {
    final now = DateTime.now();
    if (now.difference(_lastMapTap).inMilliseconds < 250) return false;
    _lastMapTap = now;
    ++_interactionSeq;
    if (!mounted || _navigating) return false;
    if (_searchFocus.hasFocus || _results.isNotEmpty) {
      _dismissSearch();
      return false;
    }
    // HTML platform-view events may arrive through Flutter's modal barrier.
    // That click dismisses the open sheet; it must never open another one.
    if (ModalRoute.of(context)?.isCurrent != true) {
      Navigator.of(context).maybePop();
      return false;
    }
    return true;
  }

  // ------------------------------------------------------------ interactions

  /// Taps on interactive layers arrive here (this maplibre_gl fork suppresses
  /// onMapClick for feature taps); we query the tapped layer for the feature's
  /// properties.
  Future<void> _onFeatureTap(
    math.Point<double> point,
    LatLng latLng,
    String id,
    String layerId,
    Annotation? annotation,
  ) async {
    // Trim handles are annotations: dragging them is the interaction.
    if (annotation != null) return;
    if (!_mapCanInteract()) return;
    if (_routeDraft != null || _areaDraft != null) {
      await _addDraftPoint(latLng);
      return;
    }
    if (_trimming) return;
    if (layerId == 'lyr-group-route') {
      await _offerFollowLeader();
      return;
    }
    final interaction = _interactionSeq;
    _featureQueryPending = true;
    try {
      HapticFeedback.selectionClick();
      final map = _map!;
      var features = await map.queryRenderedFeaturesInRect(
        Rect.fromCenter(
          center: Offset(point.x, point.y),
          width: 24,
          height: 24,
        ),
        [layerId],
        null,
      );
      if (features.isEmpty && mounted) {
        // Some platforms expect device pixels here.
        final ratio = MediaQuery.of(context).devicePixelRatio;
        features = await map.queryRenderedFeaturesInRect(
          Rect.fromCenter(
            center: Offset(point.x * ratio, point.y * ratio),
            width: 24 * ratio,
            height: 24 * ratio,
          ),
          [layerId],
          null,
        );
      }
      if (!mounted || interaction != _interactionSeq || features.isEmpty) {
        return;
      }
      final f = Map<String, dynamic>.from(features.first as Map);
      f['layer'] = {'id': layerId};
      await _setHighlight(f, latLng);
      if (mounted &&
          interaction == _interactionSeq &&
          ModalRoute.of(context)?.isCurrent == true) {
        _showFeatureSheet(f, latLng);
      }
    } finally {
      if (interaction == _interactionSeq) _featureQueryPending = false;
    }
  }

  /// Outlines the tapped feature so it's obvious the tap registered.
  Future<void> _setHighlight(Map<String, dynamic> feature, LatLng at) async {
    final geometry =
        feature['geometry'] ??
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

  void _clearHighlight() =>
      _map?.setGeoJsonSource('highlight', _emptyCollection);

  /// Plain taps land here (feature taps go to [_onFeatureTap] instead).
  /// Tapping anywhere now drops a pin and offers the same actions long-press
  /// always did — nobody discovers long-press on their own.
  Future<void> _onMapClick(math.Point<double> point, LatLng latLng) async {
    if (!_mapCanInteract()) return;
    if (_featureQueryPending) {
      _featureQueryPending = false;
      _clearHighlight();
      return; // This tap cancels a pending feature lookup.
    }
    // First tap with the keyboard up just dismisses it.
    if (_searchFocus.hasFocus) {
      _searchFocus.unfocus();
      return;
    }
    // Draw tools own the taps: each one is a waypoint / corner.
    if (_routeDraft != null || _areaDraft != null) {
      await _addDraftPoint(latLng);
      return;
    }
    // Trimming: the handles are dragged; plain taps do nothing.
    if (_trimming) return;
    // A tap while picking a trip endpoint means "there", not "what's here?".
    if (_pickField != null) {
      await _applyPick(latLng);
      return;
    }
    // Tapping the drawn route asks about the route: which street is this
    // stretch, and whose road is it.
    final route = _navRoute;
    if (route != null && !route.isEmpty) {
      final p = NavProgress.of(route, latLng);
      if (p != null && p.offRouteM < 30) {
        _showRouteSegmentInfo(route, p);
        return;
      }
    }
    await _showPlaceActions(latLng);
  }

  /// What the tapped stretch of the route is: the street's name, how far the
  /// route rides it, its step instruction, why it's shaded red/orange (hill
  /// grade, missing bike lane or sidewalk), and the road-ownership lookup.
  void _showRouteSegmentInfo(NavRoute route, NavProgress p) {
    final step = route.steps.isEmpty
        ? null
        : route.steps[p.stepIndex.clamp(0, route.steps.length - 1)];
    final name = step?.name ?? 'Unnamed path';
    final notes = route.segmentNotes(p.traveledM);
    showAppSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              leading: Icon(Icons.route, color: brandOnSurface(ctx)),
              title: Text(
                name,
                style: const TextStyle(fontWeight: FontWeight.w600),
              ),
              subtitle: step == null
                  ? null
                  : Text(
                      '${formatDistance(step.distanceM)} of this route'
                      '${step.warn != null ? ' · has gaps (dashed red)' : ''}',
                    ),
            ),
            for (final note in notes)
              ListTile(
                dense: true,
                leading: Icon(
                  Icons.warning_amber_rounded,
                  size: 20,
                  color: warnAccent(ctx),
                ),
                title: Text(note, style: const TextStyle(fontSize: 13.5)),
              ),
            if (step != null && step.instruction.isNotEmpty)
              ListTile(
                dense: true,
                leading: const Icon(Icons.turn_right, size: 20),
                title: Text(
                  step.instruction,
                  style: const TextStyle(fontSize: 13.5),
                ),
              ),
            ListTile(
              leading: const Icon(Icons.badge_outlined),
              title: const Text('Who owns this road?'),
              subtitle: const Text('Owner and contact for this stretch'),
              onTap: () {
                Navigator.pop(ctx);
                _showRoadInfo(p.snapped);
              },
            ),
          ],
        ),
      ),
    );
  }

  Future<void> _onMapLongClick(math.Point<double> point, LatLng latLng) async {
    if (_mapCanInteract()) await _showPlaceActions(latLng);
  }

  Future<void> _showPlaceActions(LatLng latLng) async {
    final interaction = _interactionSeq;
    await _setPin(latLng);
    if (!mounted ||
        interaction != _interactionSeq ||
        ModalRoute.of(context)?.isCurrent != true) {
      return;
    }
    final state = context.read<AppState>();
    // Only what you can do AT this spot. History and rides live in the menu.
    // Chips wrap, so large text stacks them instead of overflowing.
    Widget chip(IconData icon, String label, VoidCallback onTap, {Color? color}) =>
        ActionChip(
          avatar: Icon(icon, size: 18, color: color),
          label: Text(label),
          onPressed: onTap,
        );
    showAppSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: FilledButton.icon(
                      icon: Icon(state.iconFor(state.mode)),
                      label: Text(state.directionsVerb),
                      onPressed: () {
                        Navigator.pop(ctx);
                        _routeTo(latLng);
                      },
                    ),
                  ),
                  IconButton(
                    tooltip: 'Plan a trip from here',
                    icon: const Icon(Icons.tune),
                    onPressed: () {
                      Navigator.pop(ctx);
                      _openDirections(
                        to: TripEndpoint(label: 'Dropped pin', latLng: latLng),
                      );
                    },
                  ),
                ],
              ),
              const SizedBox(height: 12),
              Wrap(
                spacing: 8,
                runSpacing: 8,
                children: [
                  chip(Icons.report_problem, 'Report', () {
                    Navigator.pop(ctx);
                    _openReportSheet(latLng);
                  }, color: const Color(0xFFF9A825)),
                  chip(Icons.add_location_alt, 'Add place', () {
                    Navigator.pop(ctx);
                    _openAddPointSheet(latLng);
                  }, color: brandGreen),
                  chip(Icons.draw_outlined, 'Draw route', () {
                    Navigator.pop(ctx);
                    _startRouteDraw(latLng);
                  }, color: brandGreen),
                  chip(Icons.block, 'No-entry area', () {
                    Navigator.pop(ctx);
                    _startAreaDraw(latLng);
                  }, color: const Color(0xFFC62828)),
                  chip(Icons.badge_outlined, 'Who owns this road?', () {
                    Navigator.pop(ctx);
                    _showRoadInfo(latLng);
                  }),
                ],
              ),
            ],
          ),
        ),
      ),
    );
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
        },
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
      _setPin(
        LatLng((r['lat'] as num).toDouble(), (r['lon'] as num).toDouble()),
      );
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

    String title =
        (props['name'] ??
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
        'short_id',
        'rental_uri',
        'bikes',
        'ebikes',
        'docks',
        'is_renting',
        'is_returning',
        'last_reported',
      },
      // Garage internals: the availability line already says it better.
      if (isGarage) ...{'capacity', 'occupied', 'percent_occupied', 'as_of'},
    };
    final rows = props.entries
        .where(
          (e) =>
              !skip.contains(e.key) &&
              e.value != null &&
              e.value.toString().trim().isNotEmpty,
        )
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

    showAppSheet(
      context: context,
      builder: (ctx) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Row(
                children: [
                  Icon(
                    def?.icon ?? Icons.place,
                    color: isBcycle ? hexColor(bcycleRed) : brandGreen,
                  ),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Semantics(
                      header: true,
                      child: Text(
                        title,
                        style: Theme.of(ctx).textTheme.titleMedium,
                      ),
                    ),
                  ),
                ],
              ),
              TextButton.icon(
                icon: const Icon(Icons.edit_location_alt_outlined),
                label: const Text('Edit community information'),
                onPressed: () async {
                  Navigator.pop(ctx);
                  if (!await AuthGate.require(context) || !mounted) return;
                  final community = def?.id.startsWith('community') == true;
                  final geometry = feature['geometry'];
                  if (community &&
                      geometry is Map &&
                      const [
                        'LineString',
                        'Polygon',
                      ].contains(geometry['type'])) {
                    await _editDrawn(
                      polygon: geometry['type'] == 'Polygon',
                      geometry: Map<String, dynamic>.from(geometry),
                      properties: Map<String, dynamic>.from(props),
                    );
                    return;
                  }
                  final updated = await showAppSheet<Map<String, dynamic>>(
                    context: context,
                    isScrollControlled: true,
                    builder: (_) => AddPointSheet(
                      latLng: target,
                      initialCategory: community
                          ? props['category']?.toString()
                          : 'map-correction',
                      initialName: title,
                      initialComment: community
                          ? props['comment']?.toString()
                          : 'Correction to $title: ',
                      replaces: community ? props['id']?.toString() : null,
                    ),
                  );
                  if (updated == null || !mounted) return;
                  toast(
                    context,
                    submittedMessage(updated, published: 'Thanks — updated.'),
                  );
                  await _refreshCommunity();
                },
              ),
              if (def?.id.startsWith('community') == true &&
                  props['id'] != null)
                VoteButtons(
                  id: props['id'].toString(),
                  up: ((props['upvotes'] ?? props['confirmations']) as num?)
                          ?.toInt() ??
                      0,
                  down: (props['downvotes'] as num?)?.toInt() ?? 0,
                  mine: state.myVote(props['id'].toString()),
                  onVoted: (mine) {
                    state.setMyVote(props['id'].toString(), mine);
                    _refreshCommunity();
                  },
                ),
              // Delete lives where the thing is: tapping a contribution is how
              // people find it, not the history list.
              // Admins remove anything; riders their own (remembered on this
              // device — the layer carries no author; the server re-checks).
              if (def?.id.startsWith('community') == true &&
                  props['id'] != null &&
                  (context.read<AuthState>().isAdmin ||
                      state.isMyContribution(props['id'].toString())))
                TextButton.icon(
                  icon: const Icon(Icons.delete_outline),
                  label: const Text('Remove this contribution'),
                  onPressed: () async {
                    Navigator.pop(ctx);
                    final reason = await askReason(
                      context,
                      title: 'Remove this contribution?',
                      body:
                          'This takes it off the community map and out of routing. '
                          'The original and your reason stay in public history under Community edits.',
                      action: 'Remove',
                    );
                    if (reason == null || !mounted) return;
                    try {
                      final done = await withAuth(context, () async {
                        await api.rollbackContribution(
                          props['id'].toString(),
                          reason,
                        );
                        return true;
                      });
                      if (done == null || !mounted) return;
                      toast(context, 'Removed. History keeps it.');
                      await _refreshCommunity();
                    } catch (_) {
                      if (mounted) {
                        toast(context, 'Could not remove. Refresh and retry.');
                      }
                    }
                  },
                ),
              if (def?.id == 'reports')
                TextButton.icon(
                  icon: const Icon(Icons.visibility_off_outlined),
                  label: const Text('Dismiss this report'),
                  onPressed: () async {
                    Navigator.pop(ctx);
                    final reason = await askReason(
                      context,
                      title: 'Dismiss this report?',
                      body:
                          'This removes it from the map. The report and your reason stay in public history under Community edits.',
                      action: 'Dismiss',
                    );
                    if (reason == null || !mounted) return;
                    try {
                      final done = await withAuth(context, () async {
                        await api.dismissReport(props['id'].toString(), reason);
                        return true;
                      });
                      if (done == null || !mounted) return;
                      toast(context, 'Report dismissed. History keeps it.');
                      await _refreshReports();
                    } catch (_) {
                      if (mounted) {
                        toast(context, 'Could not dismiss. Refresh and retry.');
                      }
                    }
                  },
                ),
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
              // Wrap: at large text the three actions stack instead of
              // overflowing.
              Wrap(
                spacing: 4,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: brandGreenStrong,
                      foregroundColor: Colors.white,
                    ),
                    icon: Icon(state.iconFor(state.mode), size: 18),
                    label: const Text('Directions'),
                    onPressed: () {
                      Navigator.pop(ctx);
                      _routeTo(target, label: title);
                    },
                  ),
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
                      _openReportSheet(
                        latLng,
                        spotName: def?.id == 'bike-parking' ? title : null,
                      );
                    },
                  ),
                ],
              ),
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
      return Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Text(
          'Live availability unavailable right now.',
          style: TextStyle(color: Theme.of(ctx).colorScheme.onSurfaceVariant),
        ),
      );
    }
    // Text colors that pass 4.5:1 on either theme (brandGreen was 3.4:1).
    final color = !renting || (bikes ?? 0) == 0
        ? Theme.of(ctx).colorScheme.error
        : brandOnSurface(ctx);
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
        if (await launchUrlString(
          uri,
          mode: LaunchMode.externalNonBrowserApplication,
        )) {
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
      showAppSheet(
        context: context,
        builder: (ctx) => RoadInfoSheet(info: info),
      );
    } on Exception catch (e) {
      if (mounted) toast(context, e.toString());
    }
  }

  // ------------------------------------------------------------ group rides

  /// Everyone but me (the native dot is me) plus the leader's shared route.
  Future<void> _drawGroup() async {
    final map = _map;
    if (map == null || !mounted || !_styleReady) return;
    final group = context.read<GroupRideClient>();
    final darkBase =
        context.read<AppState>().mapBase == MapBase.satellite ||
        Theme.of(context).brightness == Brightness.dark;
    final features = <Map<String, dynamic>>[];
    for (final m in group.active ? group.members : const <GroupMember>[]) {
      final at = m.position;
      if (at == null || m.id == group.memberId) continue;
      final img = 'grp-${darkBase ? 'd' : 'l'}-${m.name}';
      if (_groupImages.add(img)) {
        try {
          await map.addImage(
            img,
            await renderLabel(
              text: m.name,
              devicePixelRatio: _imageDpr,
              darkBase: darkBase,
            ),
          );
        } catch (_) {
          _groupImages.remove(img);
        }
      }
      features.add({
        'type': 'Feature',
        'geometry': {
          'type': 'Point',
          'coordinates': [at.longitude, at.latitude],
        },
        'properties': {'name': m.name, 'leader': m.isLeader, 'img': img},
      });
    }
    if (!mounted) return;
    await map.setGeoJsonSource('group-members', {
      'type': 'FeatureCollection',
      'features': features,
    });
    final route = group.active ? group.leaderRoute : null;
    await map.setGeoJsonSource('group-route', {
      'type': 'FeatureCollection',
      'features': [?route],
    });
  }

  /// Group ride sheet (menu tile, rail button, `?ride=` link).
  Future<void> _openGroupRide({String? code}) async {
    final action = await showAppSheet<GroupSheetAction>(
      context: context,
      builder: (_) =>
          GroupRideSheet(locate: _bestOrigin, initialCode: code),
    );
    if (action == null || !mounted) return;
    switch (action.kind) {
      case 'catch-up':
        final m = action.member;
        final at = m?.position;
        if (m == null || at == null) return;
        await _planTrip(
          from: TripEndpoint.myLocation,
          to: TripEndpoint(label: m.name, latLng: at),
        );
      case 'follow':
        await _followLeader();
      case 'fit':
        await _fitGroup();
    }
  }

  /// Leader navigating: share the planned line (and every reroute).
  Future<void> _shareGroupRoute() async {
    final feature = _navFeature;
    final group = context.read<GroupRideClient>();
    if (feature == null || !_navigating || !group.isLeader) return;
    await group.setRoute(feature);
  }

  Future<void> _offerFollowLeader() async {
    final group = context.read<GroupRideClient>();
    if (group.isLeader || group.leaderRoute == null) return;
    final go = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text("Leader's route"),
        content: Text(
          'Navigate along the route ${group.leader?.name ?? 'the leader'} '
          'is riding?',
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx, false),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, true),
            child: const Text('Follow the leader'),
          ),
        ],
      ),
    );
    if (go == true && mounted) await _followLeader();
  }

  /// Turn-by-turn along the leader's shared Feature. Reroutes head for the
  /// leader's destination (ponytail: not back onto their exact line).
  Future<void> _followLeader() async {
    final feature = context.read<GroupRideClient>().leaderRoute;
    if (feature == null) return;
    final route = NavRoute.fromFeature(feature);
    if (route.isEmpty) return;
    ++_planSeq;
    await _stopNav();
    await _map?.setGeoJsonSource('route', route.routeCollection());
    await _map?.setGeoJsonSource('route-hills', route.hillCollection());
    await _map?.setGeoJsonSource('route-warn', route.warnCollection());
    await _map?.setGeoJsonSource('route-steps', route.stepCollection());
    await _setPin(route.destination);
    if (!mounted) return;
    setState(() {
      _routing = true;
      _navRoute = route;
      _navFeature = null; // theirs, not ours to re-share
      _destination = route.destination;
      _from = TripEndpoint.myLocation;
      _to = TripEndpoint(
        label: "Leader's destination",
        latLng: route.destination,
      );
      _place = null;
    });
    await _startNav();
  }

  /// Frame me and everyone in the ride.
  Future<void> _fitGroup() async {
    final group = context.read<GroupRideClient>();
    final pts = [
      for (final m in group.members) ?m.position,
      ?group.position,
    ];
    if (pts.isEmpty) return;
    if (pts.length == 1) {
      await _map?.animateCamera(CameraUpdate.newLatLngZoom(pts.first, 16.0));
      return;
    }
    var s = pts.first.latitude, n = s, w = pts.first.longitude, e = w;
    for (final p in pts) {
      s = math.min(s, p.latitude);
      n = math.max(n, p.latitude);
      w = math.min(w, p.longitude);
      e = math.max(e, p.longitude);
    }
    await _map?.animateCamera(
      CameraUpdate.newLatLngBounds(
        LatLngBounds(southwest: LatLng(s, w), northeast: LatLng(n, e)),
        left: 60,
        right: 60,
        top: 160,
        bottom: 200,
      ),
    );
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
    int alt = 0,
    bool silent = false,
  }) async {
    final state = context.read<AppState>();
    final startPoint = from ?? _from;
    final endPoint = to ?? _to;
    if (endPoint == null) return;

    final seq = ++_planSeq;
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
    if (!mounted || seq != _planSeq) return;
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
        alt: alt,
        // The trail preference shapes EVERY stress level: direct riders who
        // prefer the trail deserve the tunnel-and-trail line too (Bennett's
        // McHan → Legacy Park report). Off prices the SRT like a calm street.
        trail: _tripTrail ?? state.preferTrail,
        community: _tripCommunity ?? state.preferCommunity,
      );
      // A newer plan superseded this one while it was in flight.
      if (!mounted || seq != _planSeq) return;
      final route = NavRoute.fromFeature(feature);
      // Per-leg colors: a multi-modal itinerary draws each leg in its mode's
      // color (bus legs in the official Greenlink route color).
      await _map?.setGeoJsonSource('route', route.routeCollection());
      await _map?.setGeoJsonSource('route-hills', route.hillCollection());
      await _map?.setGeoJsonSource('route-warn', route.warnCollection());
      await _map?.setGeoJsonSource('route-steps', route.stepCollection());
      await _setPin(dest);
      if (!mounted || seq != _planSeq) return;
      _navFeature = feature;
      // A leader mid-ride (incl. every reroute) keeps the group's line fresh.
      if (_navigating) unawaited(_shareGroupRoute());
      setState(() {
        _routing = true;
        _navRoute = route;
        _destination = dest;
        _from = startPoint;
        _to = endPoint;
        _place = null;
      });
      if (!_navigating) await _fitRoute(route);
      // The rider asked for a different route and there isn't one — say so
      // rather than silently redrawing the same line.
      if (mounted && alt > 0 && !route.altDistinct) {
        toast(
          context,
          'No genuinely different route found — '
          'this is the practical way.',
        );
      }
    } on Exception catch (e) {
      if (!mounted || seq != _planSeq) return;
      setState(() => _routing = _navRoute != null);
      final msg = e.toString();
      if (msg.contains('No bus stops') &&
          state.modes.contains(TravelMode.transit)) {
        // The backend says how far it looked; say it verbatim and offer the
        // obvious way out.
        ScaffoldMessenger.of(context)
          ..clearSnackBars()
          ..showSnackBar(
            SnackBar(
              content: Text(msg),
              duration: const Duration(seconds: 10),
              action: SnackBarAction(
                label: 'Route without the bus',
                onPressed: () {
                  final rest = {...state.modes}..remove(TravelMode.transit);
                  state.setModes(rest.isEmpty ? {TravelMode.pedestrian} : rest);
                  _planTrip(from: startPoint, to: endPoint, silent: silent);
                },
              ),
            ),
          );
      } else {
        toast(context, msg);
      }
    } finally {
      // Only the newest request may clear the spinner.
      if (mounted && seq == _planSeq && _planning) {
        setState(() => _planning = false);
      }
    }
  }

  // ------------------------------------------------------------ saved routes

  /// Bookmark the previewed trip to the account (`POST /bwg/routes`).
  Future<void> _saveCurrentRoute() async {
    final route = _navRoute;
    final dest = _destination;
    if (route == null || dest == null || route.points.isEmpty) return;
    if (!await AuthGate.require(context) || !mounted) return;
    final state = context.read<AppState>();
    // ponytail: "my location" is saved as the coordinates it resolved to;
    // add a from_here flag server-side if riders want it to follow them.
    final origin = _from.latLng ?? route.points.first;
    final fromLabel = _from.isMyLocation ? 'My location' : _from.label;
    final toLabel = _to?.label ?? 'Destination';
    final ctl = TextEditingController(text: '$fromLabel → $toLabel');
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Save route'),
        content: TextField(
          controller: ctl,
          autofocus: true,
          maxLength: 80,
          decoration: const InputDecoration(labelText: 'Name'),
          onSubmitted: (v) => Navigator.pop(ctx, v),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, ctl.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    Future<void>.delayed(const Duration(seconds: 1), ctl.dispose);
    if (name == null || name.trim().isEmpty || !mounted) return;
    final body = <String, dynamic>{
      'name': name.trim(),
      'from_lat': origin.latitude,
      'from_lon': origin.longitude,
      'to_lat': dest.latitude,
      'to_lon': dest.longitude,
      'modes': {
        ...state.apiModes,
        if (state.roll) 'roll',
        if (state.useEbike) 'ebike',
        if (state.useBcycle) 'bcycle',
      }.join(','),
      'stress': state.stressApiName,
      'distance_m': route.distanceM,
      'duration_min': route.durationMin,
      if (route.points.length <= 10000)
        'geometry': {
          'type': 'LineString',
          'coordinates': [
            for (final p in route.points) [p.longitude, p.latitude],
          ],
        },
    };
    try {
      final saved = await withAuth(context, () => auth.saveRoute(body));
      if (saved == null || !mounted) return;
      ScaffoldMessenger.of(context)
        ..clearSnackBars()
        ..showSnackBar(
          SnackBar(
            content: const Text('Route saved'),
            action: SnackBarAction(
              label: 'Undo',
              onPressed: () => auth
                  .deleteSavedRoute(saved['id'].toString())
                  .catchError((_) {}),
            ),
          ),
        );
    } catch (e) {
      if (mounted) toast(context, e.toString());
    }
  }

  /// Re-plan a saved route from its stored endpoints, modes and stress.
  Future<void> _openSavedRoute(Map<String, dynamic> r) async {
    final state = context.read<AppState>();
    final modes = '${r['modes'] ?? 'bike'}'.split(',');
    final travel = {
      if (modes.any(const {'bike', 'ebike', 'bcycle'}.contains))
        TravelMode.cyclist,
      if (modes.any(const {'walk', 'roll'}.contains)) TravelMode.pedestrian,
      if (modes.contains('transit')) TravelMode.transit,
    };
    state.setModes(travel);
    state.setRoll(modes.contains('roll'));
    state.setUseEbike(modes.contains('ebike'));
    state.setUseBcycle(modes.contains('bcycle'));
    for (final level in BikeStress.values) {
      if (level.name == r['stress']) state.setStress(level);
    }
    final from = LatLng(
      (r['from_lat'] as num).toDouble(),
      (r['from_lon'] as num).toDouble(),
    );
    final to = LatLng(
      (r['to_lat'] as num).toDouble(),
      (r['to_lon'] as num).toDouble(),
    );
    // Names default to "A → B"; reuse the halves as endpoint labels.
    final name = r['name']?.toString() ?? 'Saved route';
    final halves = name.split(' → ');
    _searchFocus.unfocus();
    await _planTrip(
      from: TripEndpoint(
        label: halves.length == 2 ? halves.first : 'Start',
        latLng: from,
      ),
      to: TripEndpoint(label: halves.length == 2 ? halves.last : name, latLng: to),
    );
  }

  // ------------------------------------------------------------- trip planner

  /// Open the planner, then act on what it returns: route the trip, or drop
  /// into map-pick mode for whichever end the user wants to tap out.
  Future<void> _openDirections({TripEndpoint? to}) async {
    // The planner opens with the destination the rider is already looking
    // at — the active trip's, else the searched place. Making someone
    // re-type the place they just searched was the reported friction.
    final place = _place;
    final fromPlace = place == null
        ? null
        : TripEndpoint(
            label: (place['label'] ?? 'Searched place').toString(),
            latLng: LatLng(
              (place['lat'] as num).toDouble(),
              (place['lon'] as num).toDouble(),
            ),
          );
    final state = context.read<AppState>();
    final result = await showAppSheet<DirectionsResult>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (_) => DirectionsSheet(
        from: _from,
        to: to ?? _to ?? fromPlace ?? const TripEndpoint(label: ''),
        trail: _tripTrail ?? state.preferTrail,
        community: _tripCommunity ?? state.preferCommunity,
      ),
    );
    if (result == null || !mounted) return;
    setState(() {
      _from = result.from ?? _from;
      _to = (result.to?.isEmpty ?? true) ? _to : result.to;
      // Trail preference for THIS trip; the durable default lives in
      // Settings and is untouched here.
      if (result.trail != null) _tripTrail = result.trail;
      if (result.community != null) _tripCommunity = result.community;
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
    ++_planSeq; // Discard any in-flight route response.
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
      _navFeature = null;
      _destination = null;
      _progress = null;
      _place = null;
      _to = null;
      _from = TripEndpoint.myLocation;
      _tripTrail = null; // per-trip override dies with the trip
      _tripCommunity = null;
    });
    await _map?.animateCamera(CameraUpdate.tiltTo(0.0));
  }

  // ------------------------------------------------------------- navigation

  /// Enter turn-by-turn mode: follow camera, spoken maneuvers, off-route
  /// recalculation — the ride actually being guided, not just drawn.
  Future<void> _startNav() async {
    final route = _navRoute;
    if (route == null || route.isEmpty) return;
    if (!await confirmRouteSafety(context) || !mounted) return;
    final pos = await _currentPosition();
    if (pos == null) {
      if (mounted) toast(context, 'Location is required to navigate.');
      return;
    }
    await _initTts();
    await _ensurePuckImage();
    if (!mounted) return;
    setState(() {
      _navigating = true;
      _followNav = true;
      _spokenStep = -1;
      _spokenImminent = false;
      _notifiedStep = -1;
      _lastNotifyAt = DateTime.fromMillisecondsSinceEpoch(0);
      _lastCamAt = DateTime.fromMillisecondsSinceEpoch(0);
      // Ignore camera chatter from the initial fly-in.
      _progAnimUntil = DateTime.now().add(const Duration(seconds: 3));
    });
    _syncWakelock();
    _rerouteGovernor.reset();
    unawaited(_shareGroupRoute());
    // Strip the thematic overlays so the street layout underneath is legible.
    _applyVisibility();
    await _subscribeNavPositions();
    // The watchdog is what makes navigation survive the real world: if fixes
    // stop for any reason (stream error, provider stall, silent onDone), it
    // resubscribes instead of leaving the rider staring at a frozen map.
    _lastFixAt = DateTime.now();
    _gpsDryToastShown = false;
    _navWatchdog?.cancel();
    _navWatchdog = Timer.periodic(const Duration(seconds: 5), (_) {
      if (!_navigating) return;
      // Repaint so the trip bar's GPS line can go stale visibly even when no
      // fix arrives to trigger a rebuild.
      if (mounted) setState(() {});
      if (DateTime.now().difference(_lastFixAt) < const Duration(seconds: 10)) {
        return;
      }
      if (!_gpsDryToastShown && mounted) {
        _gpsDryToastShown = true;
        toast(context, 'Waiting for GPS…');
      }
      _subscribeNavPositions();
    });
    _onNavPosition(pos);
  }

  /// (Re)open the navigation position stream. Extracted so the watchdog can
  /// bring a dead stream back mid-ride.
  Future<void> _subscribeNavPositions() async {
    await _posSub?.cancel();
    // distanceFilter 0: fixes keep coming even at a standstill — a filter of
    // 5 m meant a stopped rider got NO fixes, so off-route detection and the
    // follow camera both froze exactly when someone pulled over.
    // intervalDuration 1 s: geolocator's Android default is FIVE seconds
    // (LocationOptions.java falls back to 5000 ms when no timeInterval is
    // sent, which is what the generic LocationSettings does) — a fix every
    // 5 s reads as "navigation is frozen" on a bike. The follow camera's
    // ~1.1 s glide is tuned for 1 Hz fixes.
    _posSub =
        Geolocator.getPositionStream(
          locationSettings: AndroidSettings(
            accuracy: LocationAccuracy.bestForNavigation,
            distanceFilter: 0,
            intervalDuration: const Duration(seconds: 1),
          ),
        ).listen(
          _onNavPosition,
          // Never let an error tear the stream down silently mid-ride; the
          // watchdog resubscribes once fixes go quiet.
          onError: (_) {},
          cancelOnError: false,
        );
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
          devicePixelRatio: _imageDpr,
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
        },
      ],
    });
  }

  Future<void> _stopNav({bool arrived = false}) async {
    ++_planSeq;
    _navWatchdog?.cancel();
    _navWatchdog = null;
    await _posSub?.cancel();
    _posSub = null;
    await _tts?.stop();
    await _navNotifier.cancel();
    await _map?.setGeoJsonSource('puck', _emptyCollection);
    if (!mounted || !_navigating) return;
    setState(() {
      _navigating = false;
      _progress = null;
      _lastNavFix = null;
    });
    // Leader stopped navigating: followers shouldn't chase a stale line.
    unawaited(context.read<GroupRideClient>().clearRoute());
    _syncWakelock();
    _applyVisibility();
    final here = _map?.cameraPosition?.target;
    if (here != null) {
      await _map?.animateCamera(
        CameraUpdate.newCameraPosition(
          CameraPosition(target: here, zoom: 15.0, bearing: 0, tilt: 0),
        ),
      );
    }
    if (arrived && mounted) toast(context, 'You have arrived.');
  }

  Future<void> _onNavPosition(Position pos) async {
    _lastFixAt = DateTime.now();
    _gpsDryToastShown = false;
    final route = _navRoute;
    if (!_navigating || route == null) return;
    final here = LatLng(pos.latitude, pos.longitude);
    final progress = NavProgress.of(route, here);
    if (progress == null || !mounted) return;
    final advanced = progress.stepIndex != _progress?.stepIndex;
    setState(() {
      _progress = progress;
      _lastNavFix = pos;
    });
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

    if (progress.remainingM < 25 &&
        progress.offRouteM < 25 &&
        _destination != null &&
        Geolocator.distanceBetween(
              here.latitude,
              here.longitude,
              _destination!.latitude,
              _destination!.longitude,
            ) <
            35) {
      await _speak('You have arrived.');
      await _stopNav(arrived: true);
      await _clearRoute();
      return;
    }

    _updateNavNotification(route, progress, advanced);
    if (progress.offRouteM <= RerouteGovernor.offRouteM && !_rerouting) {
      _announce(route, progress);
    }
    await _maybeReroute(progress);
  }

  /// One programmatic camera move. Deliberately does NOT touch
  /// [_progAnimUntil]: routine follow animations stay within the distance
  /// threshold [_onCameraMove] checks, and extending the suppression window
  /// on every fix would blind gesture detection for the whole trip.
  void _moveNavCamera(LatLng target, double bearing) {
    _lastCamAt = DateTime.now();
    _map?.animateCamera(
      CameraUpdate.newCameraPosition(
        CameraPosition(
          target: target,
          zoom: 17.5,
          bearing: bearing,
          tilt: 60.0,
        ),
      ),
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
    // Feeds the rail's compass. Repaint only on a visible change, and never
    // mid-navigation (the camera rotates with every GPS fix there, and the
    // compass is hidden anyway).
    if ((pos.bearing - _bearing).abs() > 2) {
      _bearing = pos.bearing;
      if (!_navigating) setState(() {});
    }
    if (!_navigating || !_followNav) return;
    if (DateTime.now().isBefore(_progAnimUntil)) return;
    final here = _lastNavPos;
    if (here == null) return;
    if (metersBetween(pos.target, here) > 120) {
      setState(() => _followNav = false);
    }
  }

  /// Lay the map back north-up. The compass button disappears afterwards
  /// because there is nothing left to correct.
  void _resetBearing() {
    _map?.animateCamera(CameraUpdate.bearingTo(0));
    setState(() => _bearing = 0);
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
    NavRoute route,
    NavProgress progress,
    bool advanced,
  ) {
    if (route.steps.isEmpty) return;
    final nextIndex = math.min(progress.stepIndex + 1, route.steps.length - 1);
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
      _speak(spokenText('In ${formatDistance(d)}, ${step.instruction}'));
    } else if (nextIndex == _spokenStep && !_spokenImminent && d < 45) {
      _spokenImminent = true;
      _speak(spokenText(step.instruction));
    }
  }

  /// Always route from the actual fix, preserving destination and itinerary.
  Future<void> _maybeReroute(NavProgress p) async {
    final here = _lastNavPos;
    if (_rerouting || !_navigating || _destination == null || here == null) {
      return;
    }
    // An inaccurate fix must not send a rider onto a parallel street.
    if ((_lastNavFix?.accuracy ?? 0) > RerouteGovernor.offRouteM) return;
    final decision = _rerouteGovernor.onFix(p.offRouteM, DateTime.now());
    if (decision == RerouteDecision.none) return;
    _rerouting = true;
    final previous = _navRoute;
    try {
      unawaited(_speak('Updating the route from your current location.'));
      if (mounted) setState(() {});
      await _planTrip(
        from: TripEndpoint(label: 'Current position', latLng: here),
        to: _to ?? TripEndpoint(label: 'Destination', latLng: _destination!),
        plan: previous?.plan,
        silent: true,
      );
      if (!_navigating || !mounted) return;
      if (!identical(previous, _navRoute)) {
        _spokenStep = -1;
        _spokenImminent = false;
        _notifiedStep = -1;
        final updated = NavProgress.of(_navRoute!, _lastNavPos ?? here);
        if (updated != null) {
          setState(() => _progress = updated);
          _updateNavNotification(_navRoute!, updated, true);
          _announce(_navRoute!, updated);
        }
      } else {
        unawaited(_speak('Unable to update the route yet. I will try again.'));
      }
    } finally {
      _rerouting = false;
      if (mounted) setState(() {});
    }
  }

  Future<void> _initTts() async {
    if (_tts != null) return;
    final tts = FlutterTts();
    try {
      await tts.setLanguage('en-US');
      await tts.setSpeechRate(0.5);
      await tts.setVolume(1.0);
      // Android's default pick is often the robotic legacy voice. Prefer a
      // Google "network" voice (their natural-sounding tier; the engine
      // caches it after first use), else any modern en-us-x voice.
      final voices = await tts.getVoices;
      if (voices is List) {
        final names = <String>[
          for (final v in voices)
            if (v is Map &&
                (v['locale']?.toString().toLowerCase() ?? '').startsWith(
                  'en-us',
                ))
              v['name']?.toString() ?? '',
        ];
        final pick = names.firstWhere(
          (n) => n.contains('network'),
          orElse: () =>
              names.firstWhere((n) => n.contains('en-us-x'), orElse: () => ''),
        );
        if (pick.isNotEmpty) {
          await tts.setVoice({'name': pick, 'locale': 'en-US'});
        }
      }
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
        locationSettings: const LocationSettings(
          accuracy: LocationAccuracy.high,
        ),
      );
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
      CameraUpdate.newLatLngZoom(LatLng(pos.latitude, pos.longitude), 15.5),
    );
  }

  // ----------------------------------------------------------------- search

  void _dismissSearch({bool clearText = false}) {
    ++_searchSeq;
    _searchDebounce?.cancel();
    _searchFocus.unfocus();
    if (clearText) _searchCtl.clear();
    setState(() {
      _results = [];
      _searching = false;
      _searchStatus = '';
    });
  }

  void _onSearchChanged(String q) {
    _searchDebounce?.cancel();
    final seq = ++_searchSeq;
    if (q.trim().length < 2) {
      setState(() {
        _results = [];
        _searching = false;
        _searchStatus = '';
      });
      return;
    }
    _searchDebounce = Timer(const Duration(milliseconds: 400), () async {
      if (!mounted || seq != _searchSeq) return;
      setState(() {
        _searching = true;
        _searchStatus = 'Searching…';
      });
      try {
        final results = await api.search(q.trim());
        if (mounted && seq == _searchSeq && _searchFocus.hasFocus) {
          setState(() {
            _results = results;
            final n = math.min(results.length, 6); // the dropdown shows 6
            _searchStatus = n == 0
                ? 'No results'
                : '$n result${n == 1 ? '' : 's'}';
          });
        }
      } catch (_) {
        if (mounted && seq == _searchSeq) {
          setState(() => _searchStatus = 'Search failed. Check your connection.');
        }
      } finally {
        // Only the newest request owns the spinner.
        if (mounted && seq == _searchSeq) setState(() => _searching = false);
      }
    });
  }

  /// The search state as a live region. "No results" and errors are shown
  /// (sighted riders need them too); "Searching…" and "N results" are heard
  /// only — the spinner and the list already show them.
  Widget _searchStatusLine() {
    final shown = !_searching && _results.isEmpty;
    return Semantics(
      key: const ValueKey('search-status'),
      container: true,
      liveRegion: true,
      label: _searchStatus,
      excludeSemantics: true,
      child: shown
          ? Padding(
              padding: const EdgeInsets.fromLTRB(12, 4, 12, 0),
              child: Material(
                elevation: 3,
                borderRadius: BorderRadius.circular(12),
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
                  child: Row(children: [Expanded(child: Text(_searchStatus))]),
                ),
              ),
            )
          // 1 dp, not zero: zero-size nodes are dropped as invisible.
          : const SizedBox.square(dimension: 1),
    );
  }

  Future<void> _selectResult(Map<String, dynamic> r) async {
    _dismissSearch();
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

  /// Save or remove a place; removal is undoable from the snackbar.
  void _toggleSaved(Map<String, dynamic> place) {
    final state = context.read<AppState>();
    final removed = state.toggleSaved(place);
    if (removed == null) return;
    ScaffoldMessenger.of(context)
      ..hideCurrentSnackBar()
      ..showSnackBar(
        SnackBar(
          content: Text('Removed ${removed['label']}'),
          action: SnackBarAction(
            label: 'Undo',
            onPressed: () => state.restoreSaved(removed),
          ),
        ),
      );
  }

  Future<void> _renameSaved(Map<String, dynamic> place) async {
    final ctl = TextEditingController(text: place['label']?.toString() ?? '');
    final name = await showDialog<String>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Rename saved place'),
        content: TextField(
          controller: ctl,
          autofocus: true,
          decoration: const InputDecoration(labelText: 'Name'),
          onSubmitted: (v) => Navigator.pop(ctx, v),
        ),
        actions: [
          TextButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Cancel'),
          ),
          FilledButton(
            onPressed: () => Navigator.pop(ctx, ctl.text),
            child: const Text('Save'),
          ),
        ],
      ),
    );
    if (name != null && mounted) {
      context.read<AppState>().renameSaved(place, name);
    }
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
    return PlaceCard(
      label: label,
      sublabel: sublabel,
      verb: state.directionsVerb,
      modeIcon: state.iconFor(state.mode),
      saved: state.isSaved(r),
      onNavigate: () => _routeTo(target, label: label),
      onToggleSaved: () => _toggleSaved(r),
      onPlan: () => _openDirections(
        to: TripEndpoint(label: label, latLng: target),
      ),
      onClose: _clearPlace,
    );
  }

  // ---------------------------------------------------------------- reports

  Future<void> _openReportSheet(LatLng latLng, {String? spotName}) async {
    // Ask before the form, not after the rider has typed it all out.
    if (!await AuthGate.require(context) || !mounted) {
      _clearPinIfIdle();
      return;
    }
    final submitted = await showAppSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      builder: (_) => ReportSheet(latLng: latLng, spotName: spotName),
    );
    if (submitted != null && mounted) {
      final road = submitted['road_name'];
      toast(
        context,
        submittedMessage(
          submitted,
          published: road != null
              ? 'Thanks! Your report near $road is on the map.'
              : 'Thanks! Your report is on the map.',
        ),
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
    if (!await AuthGate.require(context) || !mounted) {
      _clearPinIfIdle();
      return;
    }
    final submitted = await showAppSheet<Map<String, dynamic>>(
      context: context,
      isScrollControlled: true,
      builder: (_) => AddPointSheet(latLng: latLng),
    );
    if (submitted != null && mounted) {
      toast(
        context,
        submittedMessage(
          submitted,
          published:
              'Published to the community map. Changes can be rolled back.',
        ),
      );
      await _refreshCommunity();
    }
    _clearPinIfIdle();
  }

  Future<void> _reportAtMyLocation() async {
    final pos = await _currentPosition();
    if (!mounted) return;
    if (pos == null) {
      toast(
        context,
        'Location unavailable — long-press the map to report a spot.',
      );
      return;
    }
    _openReportSheet(LatLng(pos.latitude, pos.longitude));
  }

  // ------------------------------------------------------------------ build

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    final account = context.watch<AuthState>();
    // The rider picks the base (layers sheet); `auto` follows the app theme
    // (Theme.of resolves ThemeMode.system for us).
    final styleUrl = switch (state.mapBase) {
      MapBase.light => basemapStyleUrl,
      MapBase.dark => basemapStyleDarkUrl,
      MapBase.satellite => satelliteStyleJson,
      MapBase.auto =>
        Theme.of(context).brightness == Brightness.dark
            ? basemapStyleDarkUrl
            : basemapStyleUrl,
    };
    if (_activeStyle != null && _activeStyle != styleUrl) {
      // The style reload wipes our sources/layers/images; forget them so
      // _onStyleLoaded (which refires after the reload) re-adds everything.
      _styleReady = false;
      _addedLayers.clear();
      _puckImages.clear();
    }
    _activeStyle = styleUrl;
    // High contrast bolds the thematic lines (see _ensureLayer). Toggling it
    // does NOT reload the style, so re-add the line layers by hand.
    if (_activeContrast != null &&
        _activeContrast != state.highContrast &&
        _styleReady) {
      _restyleLineLayers();
      _restyleOwnLines();
    }
    _activeContrast = state.highContrast;
    return Scaffold(
      // The map is fullscreen chrome — never let the keyboard resize it
      // (resizing left a white band behind after backgrounding the app with
      // the keyboard up).
      resizeToAvoidBottomInset: false,
      body: Stack(
        children: [
          // One labeled node for the map: TalkBack/VoiceOver would otherwise
          // wander into the native view's tiles. Everything to do on the map
          // (search, rail, cards, sheets) sits above it and stays reachable.
          Semantics(
            label: 'Map of Greenville',
            hint: 'Use search or the buttons at the bottom right to explore',
            container: true,
            excludeSemantics: true,
            child: MapLibreMap(
            styleString: styleUrl,
            initialCameraPosition: const CameraPosition(
              target: LatLng(homeLat, homeLon),
              zoom: homeZoom,
            ),
            onMapCreated: (c) {
              _map = c;
              c.onFeatureTapped.add(_onFeatureTap);
              c.onFeatureDrag.add(_onHandleDrag);
            },
            onStyleLoadedCallback: _onStyleLoaded,
            // The native compass drew itself under the status bar and vanished
            // the moment it was tapped (it fades out facing north). Ours lives
            // in the control rail, inside the safe area, where it can be seen
            // and hit.
            compassEnabled: false,
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
              8,
              MediaQuery.of(context).padding.bottom + 8,
            ),
          )),

          // Top chrome: search + mode switch (hidden while navigating).
          if (!_navigating)
            SafeArea(
              child: PointerInterceptor(
                intercepting: kIsWeb,
                // The dropdowns below count as "inside" the search field.
                // Without this, on web the pointer-DOWN on a result is a tap
                // outside the field: EditableText unfocuses it, the focus
                // listener rebuilds, the dropdown (gated on hasFocus) is
                // gone before pointer-up, and the tap never lands.
                child: TextFieldTapRegion(
                child: Column(
                  mainAxisSize: MainAxisSize.min,
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
                              child: Image.asset('assets/logo.png', width: 28,
                                  excludeFromSemantics: true),
                            ),
                            suffixIcon: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                if (_searching)
                                  const Padding(
                                    key: ValueKey('search-progress'),
                                    padding: EdgeInsets.all(4),
                                    child: SizedBox.square(
                                      dimension: 18,
                                      child: CircularProgressIndicator(
                                        strokeWidth: 2,
                                        semanticsLabel: 'Searching',
                                      ),
                                    ),
                                  ),
                                if (_searchCtl.text.isNotEmpty ||
                                    _searchFocus.hasFocus ||
                                    _results.isNotEmpty)
                                  IconButton(
                                    key: const ValueKey('search-clear'),
                                    icon: const Icon(Icons.clear),
                                    tooltip: 'Clear search',
                                    onPressed: () {
                                      _dismissSearch(clearText: true);
                                      _clearPlace();
                                    },
                                  ),
                                IconButton(
                                  key: const ValueKey('search-directions'),
                                  icon: const Icon(Icons.directions),
                                  tooltip: 'Plan a trip',
                                  color: brandGreen,
                                  onPressed: () => _openDirections(),
                                ),
                                IconButton(
                                  key: const ValueKey('search-menu'),
                                  icon: const Icon(Icons.menu),
                                  tooltip: 'Dashboards & more',
                                  onPressed: () async {
                                    final picked = await Navigator.push(
                                    context,
                                    MaterialPageRoute(
                                      builder: (_) => const ToolsScreen(),
                                    ),
                                    );
                                    if (picked is Ride && mounted) {
                                      await _showRide(picked);
                                    } else if (picked is SavedRoutePick &&
                                        mounted) {
                                      await _openSavedRoute(picked.route);
                                    } else if (picked == 'group-ride' &&
                                        context.mounted) {
                                      await _openGroupRide();
                                    }
                                  },
                                ),
                              ],
                            ),
                            border: InputBorder.none,
                            contentPadding: const EdgeInsets.symmetric(
                              horizontal: 8,
                              vertical: 14,
                            ),
                          ),
                        ),
                      ),
                    ),
                    if (_searchFocus.hasFocus && _searchStatus.isNotEmpty)
                      _searchStatusLine(),
                    if (_searchFocus.hasFocus && _results.isNotEmpty)
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        child: Material(
                          elevation: 3,
                          borderRadius: BorderRadius.circular(12),
                          child: Column(
                            children: [
                              for (final (i, r) in _results.take(6).indexed)
                                ListTile(
                                  key: ValueKey(
                                    'result-$i-${r['label']}-${r['lat']},${r['lon']}',
                                  ),
                                  dense: true,
                                  leading: const Icon(Icons.place_outlined),
                                  title: Text(r['label']?.toString() ?? ''),
                                  subtitle: Text(
                                    r['sublabel']?.toString() ?? '',
                                  ),
                                  onTap: () => _selectResult(
                                    Map<String, dynamic>.from(r as Map),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      )
                    // Places picked before, one tap from the still-focused field —
                    // the "bring my route back" path after clearing navigation.
                    else if (_searchFocus.hasFocus &&
                        (state.recentSearches.isNotEmpty ||
                            state.savedPlaces.isNotEmpty ||
                            account.savedRoutes.isNotEmpty))
                      Padding(
                        padding: const EdgeInsets.symmetric(horizontal: 12),
                        child: Material(
                          elevation: 3,
                          borderRadius: BorderRadius.circular(12),
                          clipBehavior: Clip.antiAlias,
                          // Bounded and scrollable: every saved place stays
                          // reachable, and with a keyboard up plus large text
                          // the list can never cover the whole map.
                          child: ConstrainedBox(
                            constraints: BoxConstraints(
                              maxHeight:
                                  MediaQuery.of(context).size.height * 0.35,
                            ),
                            child: ListView(
                              shrinkWrap: true,
                              padding: EdgeInsets.zero,
                            children: [
                                // Saved first: tapping one goes straight to the
                                // route preview (focus, tap — two taps).
                                for (final r in state.savedPlaces)
                                  ListTile(
                                    key: ValueKey(
                                      'saved-${r['lat']},${r['lon']}',
                                    ),
                                    dense: true,
                                    leading: const Icon(Icons.bookmark),
                                    title: Text(r['label']?.toString() ?? ''),
                                    subtitle:
                                        (r['sublabel']?.toString() ?? '')
                                            .isEmpty
                                        ? null
                                        : Text(r['sublabel'].toString()),
                                    onTap: () async {
                                      final row = Map<String, dynamic>.from(r);
                                      await _selectResult(row);
                                      if (!mounted) return;
                                      await _routeTo(
                                        LatLng(
                                          (row['lat'] as num).toDouble(),
                                          (row['lon'] as num).toDouble(),
                                        ),
                                        label: row['label']?.toString(),
                                      );
                                    },
                                    trailing: PopupMenuButton<String>(
                                      tooltip: 'Saved place options',
                                      onSelected: (v) => v == 'rename'
                                          ? _renameSaved(
                                              Map<String, dynamic>.from(r),
                                            )
                                          : _toggleSaved(
                                              Map<String, dynamic>.from(r),
                                            ),
                                      itemBuilder: (_) => const [
                                        PopupMenuItem(
                                          value: 'rename',
                                          child: Text('Rename'),
                                        ),
                                        PopupMenuItem(
                                          value: 'remove',
                                          child: Text('Remove'),
                                        ),
                                      ],
                                    ),
                                  ),
                                // Saved routes (account): the first few, then
                                // the full list in My rides & routes.
                                for (final r in account.savedRoutes.take(3))
                                  ListTile(
                                    key: ValueKey('saved-route-${r['id']}'),
                                    dense: true,
                                    leading: const Icon(Icons.bookmark_added_outlined),
                                    title: Text(r['name']?.toString() ?? 'Saved route'),
                                    subtitle: r['distance_m'] is num
                                        ? Text(formatDistance(
                                            (r['distance_m'] as num).toDouble()))
                                        : null,
                                    onTap: () => _openSavedRoute(r),
                                  ),
                                if (account.savedRoutes.length > 3)
                                  ListTile(
                                    key: const ValueKey('saved-routes-all'),
                                    dense: true,
                                    leading: const Icon(Icons.more_horiz),
                                    title: Text(
                                      'See all ${account.savedRoutes.length} saved routes',
                                    ),
                                    onTap: () async {
                                      _searchFocus.unfocus();
                                      final picked = await Navigator.push(
                                        context,
                                        MaterialPageRoute(
                                          builder: (_) =>
                                              const RidesScreen(initialTab: 1),
                                        ),
                                      );
                                      if (!mounted) return;
                                      if (picked is SavedRoutePick) {
                                        await _openSavedRoute(picked.route);
                                      } else if (picked is Ride) {
                                        await _showRide(picked);
                                      }
                                    },
                                  ),
                                for (final r
                                    in state.recentSearches
                                        .where((r) => !state.isSaved(r))
                                        .take(5))
                                ListTile(
                                  key: ValueKey(
                                    'recent-${r['label']}-${r['lat']},${r['lon']}',
                                  ),
                                  dense: true,
                                  leading: const Icon(Icons.history),
                                  title: Text(r['label']?.toString() ?? ''),
                                  subtitle:
                                        (r['sublabel']?.toString() ?? '')
                                            .isEmpty
                                      ? null
                                      : Text(r['sublabel'].toString()),
                                  onTap: () => _selectResult(
                                    Map<String, dynamic>.from(r),
                                  ),
                                ),
                            ],
                          ),
                        ),
                      ),
                      ),
                    if (_pickField != null) ...[
                      const SizedBox(height: 8),
                      _pickBanner(),
                    ],
                  ],
                ),
                ),
              ),
            ),

          if (_navigating) _navChrome(),

          // One bottom overlay: the control rail above whichever card is active
          // (route preview, place card, or the nav trip bar), the whole thing
          // lifted clear of the system navigation bar. Phones with 3-button
          // nav have a ~48 dp inset here; gesture phones ~16 dp. Everything
          // that is not the current decision hides behind a rail button — a
          // map you cannot see is a map you cannot ride by.
          Positioned(
            left: 12,
            right: 12,
            bottom: MediaQuery.of(context).padding.bottom + (kIsWeb ? 52 : 36),
            child: PointerInterceptor(
              intercepting: kIsWeb,
              // pointer_interceptor's web shield is a centered Stack, so the
              // column must claim the full width or the rail/Report button
              // drift to the middle of the screen on web.
              child: SizedBox(
                width: double.infinity,
                child: Column(
                mainAxisSize: MainAxisSize.min,
                crossAxisAlignment: CrossAxisAlignment.end,
                children: [
                  _controlRail(),
                  if (!_navigating && _navRoute == null && !_toolOpen) ...[
                    const SizedBox(height: 10),
                    _reportFab(),
                  ],
                  if (_navigating && !_followNav) ...[
                    const SizedBox(height: 10),
                    Align(
                      alignment: Alignment.center,
                      child: FloatingActionButton.extended(
                        heroTag: 'recenter',
                        backgroundColor: brandGreenStrong,
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
                  if (!_navigating &&
                      (_routeDraft != null || _areaDraft != null)) ...[
                    const SizedBox(height: 10),
                    _drawBar(),
                  ] else if (!_navigating && _trimming && _shownRide != null) ...[
                    const SizedBox(height: 10),
                    _trimPanel(),
                  ] else if (_navigating && _navRoute != null) ...[
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
            ),
          ),
        ],
      ),
    );
  }

  /// One narrow control rail instead of a stack of separate round buttons:
  /// compass, layers, hazards and locate read as a single object clinging to
  /// the edge, so the map keeps its middle for panning and pinch-zoom. Buttons
  /// that have nothing to say (compass facing north, a route with no hazards)
  /// are simply absent.
  ///
  /// "Clear route" is gone on purpose — the ✕ on the route summary bar right
  /// below already does exactly that, and two ways to cancel one route was
  /// half the crowding.
  Widget _controlRail() {
    final hazards = _navRoute == null ? const [] : _hazards(_navRoute!);
    return Material(
      elevation: 3,
      color: Theme.of(context).colorScheme.surface,
      borderRadius: BorderRadius.circular(26),
      child: Column(
        mainAxisSize: MainAxisSize.min,
        children: [
          if (!_navigating && _bearing.abs() > 2)
            _railButton(
              tooltip: 'Face north',
              onTap: _resetBearing,
              // The needle keeps pointing north while the map turns under it.
              icon: Transform.rotate(
                angle: -_bearing * math.pi / 180,
                child: const Icon(Icons.explore_outlined),
              ),
            ),
          if (!_navigating)
            Builder(
              builder: (ctx) {
                final state = ctx.watch<AppState>();
                return _railButton(
                  tooltip: 'Travel modes',
                  onTap: _openModesSheet,
                  icon: Icon(state.iconFor(state.mode), color: brandGreen),
                );
              },
            ),
          if (!_navigating)
            _railButton(
              tooltip: 'Map layers',
              onTap: _openLayersSheet,
              icon: const Icon(Icons.layers_outlined),
            ),
          if (hazards.isNotEmpty)
            _railButton(
              tooltip: 'What to expect on this route',
              onTap: _openHazardsSheet,
              icon: Badge(
                label: Text('${hazards.length}'),
                // warnFg/warnBg: the numeral on warnAccent was 3.6:1.
                backgroundColor: warnFg(context),
                textColor: warnBg(context),
                child: Icon(
                  Icons.warning_amber_rounded,
                  color: warnAccent(context),
                ),
              ),
            ),
          Builder(
            builder: (ctx) {
              final recorder = ctx.watch<RideRecorder>();
              final recording = recorder.recording;
              return _railButton(
                tooltip: recorder.error ?? (recording ? (recorder.paused ? 'Resume ride' : 'Recording controls') : 'Record a ride'),
                onTap: _toggleRecording,
                icon: recorder.error != null
                    ? const Icon(Icons.error_outline, color: recordRed)
                    : RecordIcon(
                        state: !recording
                            ? RecordState.idle
                            : (recorder.paused
                                  ? RecordState.paused
                                  : RecordState.recording),
                      ),
              );
            },
          ),
          Builder(
            builder: (ctx) {
              final group = ctx.watch<GroupRideClient>();
              if (!group.active) return const SizedBox.shrink();
              final n = group.members.length;
              return _railButton(
                tooltip: 'Group ride, $n ${n == 1 ? 'rider' : 'riders'}',
                onTap: _openGroupRide,
                icon: Badge(
                  label: Text('$n'),
                  isLabelVisible: n > 0,
                  backgroundColor: groupRideBadge,
                  textColor: Colors.white,
                  child: const Icon(Icons.groups, color: groupRideColor),
                ),
              );
            },
          ),
          _railButton(
            tooltip: 'My location',
            onTap: _locateMe,
            icon: const Icon(Icons.my_location),
          ),
        ],
      ),
    );
  }

  /// Bike / Walk / Transit and their variants, in a sheet instead of a bar
  /// under the search field: at large text the bar overflowed the screen.
  void _openModesSheet() {
    showAppSheet(
      context: context,
      showDragHandle: true,
      builder: (_) => SafeArea(
        child: Padding(
          padding: const EdgeInsets.fromLTRB(12, 0, 12, 12),
          child: TravelModes(
            onChanged: () {
              if (_navRoute != null && !_navigating && _to != null) {
                _planTrip();
              }
            },
          ),
        ),
      ),
    );
  }

  Widget _railButton({
    required Widget icon,
    required String tooltip,
    required VoidCallback onTap,
  }) => IconButton(
    icon: icon,
    tooltip: tooltip,
    onPressed: onTap,
    // Standard density: compact shrank the hit area to 40 dp (48 minimum).
  );

  /// The one prominent action on an otherwise empty map. Only offered when no
  /// route is drawn — with a trip on screen the bottom belongs to the trip.
  Widget _reportFab() => FloatingActionButton.extended(
    heroTag: 'report',
    backgroundColor: brandGreenStrong,
    foregroundColor: Colors.white,
    icon: const Icon(Icons.add_location_alt_outlined),
    label: const Text('Report'),
    onPressed: _reportAtMyLocation,
  );

  /// Immediate feedback that the router is working on it ("Bike here" used to
  /// do nothing visible for a second or two).
  Widget _planningChip() => Align(
    alignment: Alignment.center,
    child: Semantics(liveRegion: true, child: Material(
      elevation: 4,
      borderRadius: BorderRadius.circular(24),
      color: Theme.of(context).colorScheme.surface,
      child: const Padding(
        padding: EdgeInsets.symmetric(horizontal: 18, vertical: 12),
        child: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            SizedBox(
              width: 18,
              height: 18,
              child: CircularProgressIndicator(
                strokeWidth: 2.5,
                color: brandGreen,
              ),
            ),
            SizedBox(width: 12),
            Text(
              'Finding your route…',
              semanticsLabel: 'Finding your route',
              style: TextStyle(fontWeight: FontWeight.w600),
            ),
          ],
        ),
      ),
    )),
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
        child: Row(
          children: [
            const Icon(Icons.touch_app, color: Colors.white, size: 20),
            const SizedBox(width: 10),
            Expanded(
              child: Semantics(liveRegion: true, child: Text(
                _pickField == 'from'
                    ? 'Tap the map to set your start point'
                    : 'Tap the map to set your destination',
                style: const TextStyle(color: Colors.white),
              )),
            ),
            IconButton(
              tooltip: 'Stop picking on the map',
              icon: const Icon(Icons.close, color: Colors.white, size: 20),
              onPressed: () => setState(() => _pickField = null),
            ),
          ],
        ),
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
    final base = route.isTransit && route.transitRoute != null
        ? 'Greenlink Route ${route.transitRoute}'
              '${route.boardStop != null ? ' · board at ${route.boardStop}' : ''}'
        : (route.plan == 'bcycle' && route.rentStation != null
              ? 'BCycle from ${route.rentStation}'
              : (route.planDisplayLabel.isNotEmpty
                    ? route.planDisplayLabel
                    : 'Route'));
    final subtitle = route.alt > 0
        ? '$base · alternate route ${route.alt}'
        : base;
    final icon = route.planIcon;
    // The caveats, the hills and the elevation graph used to stack here as
    // three more cards; they live behind the rail's hazards button now. What
    // stays is what you decide with: the alternatives and the trip itself.
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (route.alternatives.isNotEmpty || _canAlt(route))
          _alternativesRow(route),
        if (!route.isTransit && route.plan != 'bcycle') _tripPrefsRow(),
        RoutePreviewCard(
          color: color,
          icon: icon,
          // Distance and ETA only — the climb lives in the hazards sheet
          // with the elevation graph.
          title: '${formatDistance(route.distanceM)} · '
              '${formatDuration(route.durationMin)}',
          subtitle: subtitle,
          onSave: _saveCurrentRoute,
          onShare: _shareTrip,
          onClear: _clearRoute,
          onSteps: route.steps.isEmpty ? null : _openStepsSheet,
          onStart: route.steps.isEmpty ? null : _startNav,
        ),
      ],
    );
  }

  /// One-tap trip preferences, right on the preview — the answer to "how do
  /// I make it quieter?" without hunting through sheets. Every chip replans
  /// immediately; sized for a gloved thumb on a bike. Stress writes the
  /// durable preference (same control as Settings and the planner). The
  /// trail preference lives one tap away under "More" (the planner) and
  /// applies to every stress level.
  Widget _tripPrefsRow() {
    final state = context.watch<AppState>();
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      child: SizedBox(
        height: 38,
        child: ListView(
          scrollDirection: Axis.horizontal,
          children: [
            if (state.showsBikeOptions)
              for (final level in BikeStress.values)
                Padding(
                  padding: const EdgeInsets.only(right: 8),
                  child: ChoiceChip(
                    label: Text(bikeStressLabels[level]!),
                    selected: state.stress == level,
                    showCheckmark: false,
                    avatar: Icon(switch (level) {
                      BikeStress.quiet => Icons.self_improvement,
                      BikeStress.balanced => Icons.balance,
                      BikeStress.direct => Icons.straighten,
                    }, size: 18),
                    onSelected: (_) {
                      state.setStress(level);
                      _planTrip(silent: true);
                    },
                  ),
                ),
            ActionChip(
              avatar: const Icon(Icons.tune, size: 18),
              label: const Text('More'),
              onPressed: () => _openDirections(),
            ),
          ],
        ),
      ),
    );
  }

  /// The honest bit, as data: what this route is missing, what it climbs, and
  /// why it looks like this. Empty means the trip has nothing worth bracing
  /// for — and then the hazards button never appears at all.
  List<({IconData icon, String text})> _hazards(NavRoute route) => [
    if (route.fallbackNote != null)
      (icon: Icons.info_outline, text: route.fallbackNote!),
    for (final w in route.visibleWarnings()) (icon: w.icon, text: w.message),
    if (route.communityNames.isNotEmpty)
      (
        icon: Icons.groups_outlined,
        text:
            'Rides community routes drawn by other riders: '
            '${route.communityNames.join(', ')}',
      ),
    if (route.hillSummary() case final hill?)
      (icon: Icons.trending_up, text: hill),
  ];

  /// The hazards view: everything the old banner shouted from the map, plus
  /// the elevation graph, opened deliberately instead of occupying the
  /// viewport on every route.
  void _openHazardsSheet() {
    final route = _navRoute;
    if (route == null) return;
    final hazards = _hazards(route);
    final hasGaps = route.visibleWarnings().isNotEmpty;
    showAppSheet(
      context: context,
      showDragHandle: true,
      builder: (ctx) => SafeArea(
        child: ListView(
          shrinkWrap: true,
          children: [
            ListTile(
              leading: Icon(
                Icons.warning_amber_rounded,
                color: warnAccent(ctx),
              ),
              title: const Text(
                'What to expect on this route',
                style: TextStyle(fontWeight: FontWeight.w600),
              ),
              subtitle: Text(
                '${formatDistance(route.distanceM)} · '
                '${formatDuration(route.durationMin)}'
                '${route.climbFt >= 50 ? ' · ↑ ${route.climbFt} ft' : ''}',
              ),
            ),
            for (final h in hazards)
              ListTile(
                dense: true,
                leading: Icon(h.icon, size: 20, color: warnAccent(ctx)),
                title: Text(h.text, style: const TextStyle(fontSize: 13.5)),
              ),
            if (hasGaps)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
                child: Text(
                  'Those stretches are dashed red on the map.',
                  style: TextStyle(
                    fontSize: 12,
                    color: Theme.of(ctx).colorScheme.onSurfaceVariant,
                    fontStyle: FontStyle.italic,
                  ),
                ),
              ),
            // The terrain, at a glance: where the trip climbs and where it
            // bites (steep stretches in red). Only worth the pixels once the
            // climb is enough to feel in your legs.
            if (route.elevationProfile != null && route.climbFt >= 30)
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 8),
                child: ElevationProfile(profile: route.elevationProfile!),
              ),
            if (route.steps.isNotEmpty)
              ListTile(
                leading: const Icon(Icons.list_alt),
                title: const Text('See every turn'),
                onTap: () {
                  Navigator.pop(ctx);
                  _openStepsSheet();
                },
              ),
          ],
        ),
      ),
    );
  }

  /// Alternate routes only make sense for plain plans: a transit or BCycle
  /// itinerary's shape is fixed by the stop/dock locations, not street choice.
  bool _canAlt(NavRoute route) =>
      const {'bike', 'walk', 'roll'}.contains(route.plan);

  /// Every itinerary the router costed, as one row of choices: the plan on
  /// screen first (filled green, so it reads as selected), the alternatives
  /// beside it, and "Different route" to ask for another way with the same
  /// plan. Making the whole set visible is what lets someone pick the
  /// bike-to-the-bus trip even when pure bike is a few minutes faster.
  Widget _alternativesRow(NavRoute route) {
    final ebike = context.read<AppState>().useEbike;
    return Padding(
      padding: const EdgeInsets.only(bottom: 6),
      // No fixed height: the row takes the chips' own height, so large text
      // grows it instead of clipping the labels.
      child: SingleChildScrollView(
        scrollDirection: Axis.horizontal,
        child: Row(
          children: [
            Padding(
              padding: const EdgeInsets.only(right: 8),
              child: ActionChip(
                avatar: Icon(route.planIcon, size: 18, color: Colors.white),
                label: Text(
                  '${route.planDisplayLabel.isNotEmpty ? route.planDisplayLabel : 'Route'}'
                  ' · ${formatDuration(route.durationMin)}',
                  style: const TextStyle(
                    color: Colors.white,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                backgroundColor: brandGreenStrong,
                onPressed: _openStepsSheet,
              ),
            ),
            for (final alt in route.alternatives)
              Padding(
                padding: const EdgeInsets.only(right: 8),
                child: AlternativeChip(
                  alt: alt,
                  selected: route,
                  ebike: ebike,
                  onPressed: () => _planTrip(plan: alt.plan),
                ),
              ),
            if (_canAlt(route))
              ActionChip(
                avatar: const Icon(Icons.alt_route, size: 18),
                label: Text(
                  route.alt > 0 ? 'Another route' : 'Different route',
                ),
                backgroundColor: Theme.of(context).colorScheme.surface,
                // Cycle through up to 3 alternates, then back to the base.
                onPressed: () => _planTrip(
                  plan: route.plan,
                  alt: route.alt >= 3 ? 0 : route.alt + 1,
                ),
              ),
          ],
        ),
      ),
    );
  }

  /// Turn-by-turn top chrome: maneuver card + the upcoming-turns strip.
  /// (The trip bar renders in the shared bottom overlay — see build().)
  Widget _navChrome() {
    final route = _navRoute;
    final progress = _progress;
    if (route == null || route.steps.isEmpty) return const SizedBox.shrink();
    final nextIndex = math.min(
      (progress?.stepIndex ?? -1) + 1,
      route.steps.length - 1,
    );
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
              child: Semantics(onTapHint: 'list every turn', child: InkWell(
                borderRadius: BorderRadius.circular(18),
                onTap: _openStepsSheet,
                child: Padding(
                  padding: const EdgeInsets.fromLTRB(14, 12, 6, 12),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Row(
                        children: [
                          // Sized to be read at arm's length from a handlebar
                          // mount in sunlight — glanceable, not squintable.
                          Icon(step.icon, color: Colors.white, size: 56),
                          const SizedBox(width: 12),
                          Expanded(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(
                                  formatDistance(toManeuver),
                                  style: const TextStyle(
                                    color: Colors.white,
                                    fontSize: 34,
                                    fontWeight: FontWeight.w800,
                                    height: 1.1,
                                  ),
                                ),
                                // Live region on the instruction only: it
                                // changes once per step, while the distance
                                // above ticks every fix and stays silent.
                                Semantics(
                                  liveRegion: true,
                                  container: true,
                                  child: Text(
                                    step.instruction,
                                    maxLines: 2,
                                    overflow: TextOverflow.ellipsis,
                                    style: const TextStyle(
                                      color: Colors.white,
                                      fontSize: 20,
                                      fontWeight: FontWeight.w600,
                                      height: 1.15,
                                    ),
                                  ),
                                ),
                              ],
                            ),
                          ),
                          IconButton(
                            tooltip: _voice ? 'Mute voice' : 'Unmute voice',
                            icon: Icon(
                              _voice ? Icons.volume_up : Icons.volume_off,
                              color: Colors.white70,
                            ),
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
                              const Icon(
                                Icons.warning_amber_rounded,
                                color: Color(0xFFFFB74D),
                                size: 18,
                              ),
                              const SizedBox(width: 6),
                              Expanded(
                                child: Text(
                                  warnStepSentence(step.warn),
                                  style: const TextStyle(
                                    color: Color(0xFFFFB74D),
                                    fontSize: 14,
                                  ),
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
                              const Icon(
                                Icons.trending_up,
                                color: Color(0xFFFFB74D),
                                size: 18,
                              ),
                              const SizedBox(width: 6),
                              Expanded(
                                child: Text(
                                  'Steep climb ahead — ${step.climbFt} ft up',
                                  style: const TextStyle(
                                    color: Color(0xFFFFB74D),
                                    fontSize: 14,
                                  ),
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
                              // white70, 15px: the "then" preview has to survive
                              // sunlight too, just visibly quieter than the turn.
                              const Text(
                                'then ',
                                style: TextStyle(
                                  color: Colors.white70,
                                  fontSize: 15,
                                ),
                              ),
                              Icon(after.icon, color: Colors.white70, size: 22),
                              const SizedBox(width: 6),
                              Expanded(
                                child: Text(
                                  after.instruction,
                                  overflow: TextOverflow.ellipsis,
                                  style: const TextStyle(
                                    color: Colors.white70,
                                    fontSize: 15,
                                  ),
                                ),
                              ),
                            ],
                          ),
                        ),
                    ],
                  ),
                ),
              )),
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
      color: Theme.of(context).colorScheme.surface,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 10, 10, 10),
        // Wrap, not Row + Spacer: at large text the buttons drop below the
        // ETA instead of overflowing the card.
        child: Wrap(
          alignment: WrapAlignment.spaceBetween,
          crossAxisAlignment: WrapCrossAlignment.center,
          spacing: 8,
          runSpacing: 4,
          children: [
            Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              mainAxisSize: MainAxisSize.min,
              children: [
                Text(
                  formatDuration(etaMin),
                  style: const TextStyle(
                    fontSize: 24,
                    fontWeight: FontWeight.w800,
                  ),
                ),
                Text(
                  '${formatDistance(remaining)} left',
                  style: TextStyle(
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                    fontSize: 15,
                  ),
                ),
                _gpsStatusLine(),
                _recordingLine(),
              ],
            ),
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                TextButton.icon(
                  icon: const Icon(Icons.list_alt),
                  label: const Text('Steps'),
                  onPressed: _openStepsSheet,
                ),
                const SizedBox(width: 4),
                FilledButton.icon(
                  style: FilledButton.styleFrom(
                    backgroundColor: Colors.red.shade700,
                    foregroundColor: Colors.white,
                  ),
                  icon: const Icon(Icons.close),
                  label: const Text('End'),
                  onPressed: () => _stopNav(),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  /// Small red dot + elapsed time in the nav card while a ride records.
  Widget _recordingLine() {
    final recorder = context.watch<RideRecorder>();
    if (!recorder.recording) return const SizedBox.shrink();
    final elapsed = formatDuration(recorder.liveDuration.inSeconds / 60);
    final label = recorder.paused ? 'Recording paused' : 'Recording';
    return Semantics(
      label: '$label, $elapsed',
      excludeSemantics: true,
      child: Row(
        mainAxisSize: MainAxisSize.min,
        children: [
          Icon(
            Icons.circle,
            size: 10,
            color: recorder.paused ? Colors.grey : recordRed,
          ),
          const SizedBox(width: 6),
          Text(elapsed, style: const TextStyle(fontSize: 12)),
        ],
      ),
    );
  }

  /// Live "am I actually being tracked?" line: current speed + GPS accuracy
  /// while fixes flow, a red warning once they stop. This is the motion
  /// feedback that tells a rider the screen is navigating, not frozen.
  Widget _gpsStatusLine() {
    final style = TextStyle(
      color: Theme.of(context).colorScheme.onSurfaceVariant,
      fontSize: 12,
    );
    final fix = _lastNavFix;
    final age = DateTime.now().difference(_lastFixAt);
    if (fix == null || age > const Duration(seconds: 8)) {
      // colorScheme.error: Colors.red was 3.7:1 on the light card.
      return Semantics(
        liveRegion: true,
        child: Text(
          'GPS lost — searching…',
          style: TextStyle(color: Theme.of(context).colorScheme.error, fontSize: 12),
        ),
      );
    }
    final mph = fix.speed * 2.23694;
    final accFt = fix.accuracy * 3.28084;
    final speed = mph < 0.7 ? 'stopped' : '${mph.round()} mph';
    return Text('$speed · GPS ±${accFt.round()} ft', style: style);
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
        height: 40,
        child: ListView(
          scrollDirection: Axis.horizontal,
          children: [
            for (final i in ahead.take(8))
              Padding(
                padding: const EdgeInsets.only(right: 6),
                child: Material(
                  color: Theme.of(context).colorScheme.surface,
                  borderRadius: BorderRadius.circular(20),
                  elevation: 2,
                  child: Padding(
                    padding: const EdgeInsets.symmetric(horizontal: 12),
                    child: Row(
                      children: [
                        Icon(
                          route.steps[i].icon,
                          size: 22,
                          color:
                              route.steps[i].warn != null ||
                                  route.steps[i].isSteepClimb
                              ? warnRed
                              : brandOnSurface(context),
                        ),
                        const SizedBox(width: 5),
                        Text(
                          route.steps[i].name ??
                              (route.steps[i].maneuver == 'arrive'
                                  ? 'Arrive'
                                  : 'Continue'),
                          style: TextStyle(
                            fontSize: 14,
                            fontWeight: FontWeight.w600,
                            color: Theme.of(context).colorScheme.onSurface,
                          ),
                        ),
                        if (route.steps[i].distanceM > 0) ...[
                          const SizedBox(width: 6),
                          Text(
                            formatDistance(route.steps[i].distanceM),
                            style: TextStyle(
                              fontSize: 13,
                              color: Theme.of(
                                context,
                              ).colorScheme.onSurfaceVariant,
                            ),
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
    showAppSheet(
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
                    '${route.planDisplayLabel.isNotEmpty ? ' · ${route.planDisplayLabel}' : ''}'
                    '${route.alt > 0 ? ' · alternate ${route.alt}' : ''}',
                    style: Theme.of(ctx).textTheme.titleMedium,
                  ),
                  // Route-wide caveats live in the hazards sheet now; what
                  // stays here is per-step, in the red subtitle lines below.
                ],
              ),
            ),
            // Maneuver icons are tinted by the mode of travel for that step
            // (bike blue, walk teal, bus purple, BCycle red — the same colors
            // as the route line's legs), so a multi-modal itinerary reads as
            // "these turns are on the bike, these are the bus". Missing
            // infrastructure stays disclosed in the red subtitle line.
            for (final (i, stepMode) in route.stepModes().indexed)
              ListTile(
                dense: true,
                leading: Icon(
                  route.steps[i].icon,
                  color: i < current
                      ? Theme.of(ctx).disabledColor
                      : hexColor(
                          routeLegColors[stepMode] ?? routeLegColors['bike']!,
                        ),
                ),
                title: Text(
                  route.steps[i].instruction,
                  style: TextStyle(
                    color: i < current ? Theme.of(ctx).disabledColor : null,
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
          // colorScheme.error, not warnRed: 3.8:1 on the dark sheet.
          style: TextStyle(fontSize: 12, color: Theme.of(context).colorScheme.error),
        ),
      if (step.climbFt >= 20 || step.isSteepClimb)
        Text(
          step.isSteepClimb
              ? '↑ ${step.climbFt} ft — steep climb'
              : '↑ ${step.climbFt} ft of climb',
          style: TextStyle(
            fontSize: 12,
            color: step.isSteepClimb
                ? Theme.of(context).colorScheme.error
                : Theme.of(context).colorScheme.onSurfaceVariant,
            fontWeight: step.isSteepClimb ? FontWeight.w600 : null,
          ),
        ),
    ];
    if (lines.isEmpty) return null;
    if (lines.length == 1) return lines.first;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: lines,
    );
  }

  void _openLayersSheet() {
    final zoom = _map?.cameraPosition?.zoom ?? homeZoom;
    showAppSheet(
      context: context,
      builder: (ctx) => Consumer<AppState>(
        builder: (ctx, state, _) => SafeArea(
          child: ListView(
            shrinkWrap: true,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
                child: Text(
                  semanticsLabel: 'Map layers for '
                      '${TravelMode.values.where(state.modes.contains).map(state.labelFor).join(' and ')}',
                  'Map layers — '
                  '${TravelMode.values.where(state.modes.contains).map(state.labelFor).join(' + ')}',
                  style: Theme.of(ctx).textTheme.titleMedium,
                ),
              ),
              // Base map: Standard follows the app theme (Settings →
              // Appearance is the ONLY light/dark switch — a dark basemap
              // under light chrome read as a glitch, so mismatches are not
              // offered), Satellite is imagery either way.
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 4, 16, 8),
                child: SegmentedButton<MapBase>(
                  segments: const [
                    ButtonSegment(
                      value: MapBase.auto,
                      label: Text('Standard'),
                      icon: Icon(Icons.map_outlined),
                    ),
                    ButtonSegment(
                      value: MapBase.satellite,
                      label: Text('Satellite'),
                      icon: Icon(Icons.satellite_alt),
                    ),
                  ],
                  selected: {
                    state.mapBase == MapBase.satellite
                        ? MapBase.satellite
                        : MapBase.auto,
                  },
                  showSelectedIcon: false,
                  onSelectionChanged: (s) => state.setMapBase(s.first),
                ),
              ),
              for (final group in LayerGroup.values)
                if (state.relevantLayers.any((d) => d.group == group)) ...[
                Padding(
                  padding: const EdgeInsets.fromLTRB(16, 12, 16, 0),
                  child: Semantics(
                    header: true,
                    child: Text(
                      layerGroupLabels[group]!,
                      style: Theme.of(ctx).textTheme.labelLarge?.copyWith(
                        color: brandOnSurface(ctx),
                      ),
                    ),
                  ),
                ),
              for (final def in state.relevantLayers.where(
                (d) => d.group == group,
              ))
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
              // The stress legend only makes sense while its layer has a
              // toggle here (it's an opt-in advocacy layer now).
              if (group == LayerGroup.biking &&
                  state.relevantLayers.any((d) => d.id == 'bike-stress'))
                _stressLegend(),
              ],
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
            Row(
              mainAxisSize: MainAxisSize.min,
              children: [
                Container(
                  width: 14,
                  height: 4,
                  color: Color(
                    int.parse(
                      'ff${stressColors[e.key]!.substring(1)}',
                      radix: 16,
                    ),
                  ),
                ),
                const SizedBox(width: 4),
                Text(e.value, style: const TextStyle(fontSize: 12)),
              ],
            ),
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
            Text(
              '${info['name'] ?? 'Road'} · ${info['type'] ?? ''}',
              style: Theme.of(context).textTheme.titleMedium,
            ),
            const SizedBox(height: 4),
            Text(
              'Maintained by $owner',
              style: Theme.of(context).textTheme.bodyLarge,
            ),
            const SizedBox(height: 8),
            if (info['email'] != null)
              ContactRow(
                icon: Icons.email_outlined,
                value: info['email'],
                uri: 'mailto:${info['email']}',
              ),
            if (info['phone'] != null)
              ContactRow(
                icon: Icons.phone_outlined,
                value: info['phone'],
                uri: 'tel:${info['phone']}',
              ),
            if (info['online_form'] != null)
              ContactRow(
                icon: Icons.open_in_new,
                value: 'Report an issue online',
                uri: info['online_form'],
              ),
            if (info['email'] == null && info['online_form'] != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Text(
                  'This office has no public email — use the online form above '
                  'to contact them directly.',
                  style: TextStyle(
                    fontSize: 12,
                    color: Theme.of(context).colorScheme.onSurfaceVariant,
                  ),
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
  const ContactRow({
    super.key,
    required this.icon,
    required this.value,
    required this.uri,
  });

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

/// Thumbs up / down on a community contribution with live counts. Voting
/// needs an account ([AuthGate]); the server's reply is the new truth.
class VoteButtons extends StatefulWidget {
  final String id;
  final int up, down;
  final String? mine;
  final void Function(String? mine)? onVoted;
  const VoteButtons({
    super.key,
    required this.id,
    required this.up,
    required this.down,
    this.mine,
    this.onVoted,
  });

  @override
  State<VoteButtons> createState() => _VoteButtonsState();
}

class _VoteButtonsState extends State<VoteButtons> {
  late int _up = widget.up, _down = widget.down;
  late String? _mine = widget.mine;
  bool _busy = false;

  Future<void> _vote(bool up) async {
    if (_busy) return;
    setState(() => _busy = true);
    try {
      final r = await withAuth(context, () => api.vote(widget.id, up));
      if (r == null || !mounted) return;
      setState(() {
        _up = (r['up'] as num?)?.toInt() ?? _up;
        _down = (r['down'] as num?)?.toInt() ?? _down;
        _mine = r['mine']?.toString();
      });
      widget.onVoted?.call(_mine);
    } catch (e) {
      if (mounted) toast(context, e.toString());
    } finally {
      if (mounted) setState(() => _busy = false);
    }
  }

  Widget _button(bool up) {
    final on = _mine == (up ? 'up' : 'down');
    final count = up ? _up : _down;
    final label = up ? 'Confirm this exists' : 'Report this is wrong or gone';
    // excludeSemantics drops the button's own tap action, so the node
    // declares it (without it TalkBack's double-tap did nothing).
    return Semantics(
      button: true,
      selected: on,
      enabled: !_busy,
      label: '$label, $count',
      onTap: _busy ? null : () => _vote(up),
      excludeSemantics: true,
      child: TextButton.icon(
        key: ValueKey(up ? 'vote-up' : 'vote-down'),
        style: TextButton.styleFrom(minimumSize: const Size(48, 48)),
        onPressed: _busy ? null : () => _vote(up),
        icon: Icon(
          up
              ? (on ? Icons.thumb_up_alt : Icons.thumb_up_alt_outlined)
              : (on ? Icons.thumb_down_alt : Icons.thumb_down_alt_outlined),
        ),
        label: Text('$count'),
      ),
    );
  }

  @override
  Widget build(BuildContext context) => Row(
    mainAxisSize: MainAxisSize.min,
    children: [_button(true), _button(false)],
  );
}
