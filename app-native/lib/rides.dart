import 'dart:async';
import 'dart:convert';
import 'dart:math' as math;

import 'package:flutter/widgets.dart';
import 'package:geolocator/geolocator.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'geometry_draft.dart';

/// A GPS trace the rider recorded in the app. Kept on-device until the rider
/// chooses to share a stretch of it as a community route.
class Ride {
  final String id;
  String name;
  final DateTime startedAt;
  final DateTime endedAt;
  final List<LatLng> points;
  final List<int> segmentStarts;
  final Duration? activeDuration;

  Ride({
    required this.id,
    required this.name,
    required this.startedAt,
    required this.endedAt,
    required this.points,
    this.segmentStarts = const [0],
    this.activeDuration,
  });

  double get distanceM =>
      segments().fold(0.0, (sum, s) => sum + pathLengthM(s));
  Duration get duration => activeDuration ?? endedAt.difference(startedAt);

  List<({int start, int end})> get segmentRanges {
    final starts = {
      0,
      ...segmentStarts.where((i) => i >= 0 && i < points.length),
    }.toList()..sort();
    return [
      for (var i = 0; i < starts.length; i++)
        if (points.isNotEmpty)
          (
            start: starts[i],
            end: i + 1 < starts.length ? starts[i + 1] - 1 : points.length - 1,
          ),
    ];
  }

  List<List<LatLng>> segments([int start = 0, int? end]) => [
    for (final range in segmentRanges)
      if (math.max(start, range.start) <=
          math.min(end ?? points.length - 1, range.end))
        points.sublist(
          math.max(start, range.start),
          math.min(end ?? points.length - 1, range.end) + 1,
        ),
  ];

  ({int start, int end})? longestStretchWhere(bool Function(LatLng) contains) {
    ({int start, int end})? best;
    for (final range in segmentRanges) {
      int? runStart;
      for (var i = range.start; i <= range.end + 1; i++) {
        final inside = i <= range.end && contains(points[i]);
        if (inside) {
          runStart ??= i;
        } else if (runStart != null) {
          if (i - runStart >= 2 &&
              (best == null || i - 1 - runStart > best.end - best.start)) {
            best = (start: runStart, end: i - 1);
          }
          runStart = null;
        }
      }
    }
    return best;
  }

  Map<String, dynamic> toJson() => {
    'id': id,
    'name': name,
    'started_at': startedAt.toIso8601String(),
    'ended_at': endedAt.toIso8601String(),
    'segment_starts': segmentStarts,
    'active_ms': duration.inMilliseconds,
    'points': [
      for (final p in points)
        [
          double.parse(p.latitude.toStringAsFixed(6)),
          double.parse(p.longitude.toStringAsFixed(6)),
        ],
    ],
  };

  factory Ride.fromJson(Map<String, dynamic> j) => Ride(
    id: j['id'].toString(),
    name: (j['name'] ?? 'Ride').toString(),
    startedAt: DateTime.parse(j['started_at'].toString()),
    endedAt: DateTime.parse(j['ended_at'].toString()),
    segmentStarts: (j['segment_starts'] as List?)?.cast<int>() ?? const [0],
    activeDuration: j['active_ms'] is num
        ? Duration(milliseconds: (j['active_ms'] as num).toInt())
        : null,
    points: [
      for (final p in (j['points'] as List))
        LatLng((p[0] as num).toDouble(), (p[1] as num).toDouble()),
    ],
  );

  /// Legacy name retained; interrupted rides render as separate lines.
  Map<String, dynamic> lineString([int start = 0, int? end]) {
    final lines = [
      for (final segment in segments(start, end))
        if (segment.length >= 2)
          [
            for (final p in segment) [p.longitude, p.latitude],
          ],
    ];
    return {
      'type': lines.length == 1 ? 'LineString' : 'MultiLineString',
      'coordinates': lines.length == 1 ? lines.single : lines,
    };
  }
}

double distanceM(LatLng a, LatLng b) {
  final mx = 111320 * math.cos(a.latitude * math.pi / 180);
  final dx = (b.longitude - a.longitude) * mx;
  final dy = (b.latitude - a.latitude) * 111320;
  return math.sqrt(dx * dx + dy * dy);
}

double pathLengthM(List<LatLng> points) {
  var total = 0.0;
  for (var i = 1; i < points.length; i++) {
    total += distanceM(points[i - 1], points[i]);
  }
  return total;
}

/// Thin the trace until it fits the server's 200-vertex limit, coarsening the
/// tolerance until it does. Real rides at 1 Hz are thousands of fixes.
List<LatLng> fitToVertexLimit(List<LatLng> points, {int limit = 200}) {
  var tolerance = 2.0;
  var reduced = simplifyStroke(points, tolerance);
  while (reduced.length > limit && tolerance < 500) {
    tolerance *= 1.5;
    reduced = simplifyStroke(points, tolerance);
  }
  return reduced;
}

/// Records rides while the app is on screen and keeps the saved list.
///
/// ponytail: foreground only — the manifest deliberately strips the
/// FOREGROUND_SERVICE permissions (Play review), so a locked screen pauses the
/// trace. Wakelock keeps the screen on while recording. Add a foreground
/// service + Play declaration if riders want pocket recording.
class RideRecorder extends ChangeNotifier with WidgetsBindingObserver {
  static const _kRides = 'rides';
  static const _kDraft = 'ride_in_progress';
  static const _minStepM = 3.0;

  RideRecorder({
    Stream<Position> Function()? positions,
    DateTime Function()? now,
    Future<SharedPreferences> Function()? preferences,
    Timer Function(Duration, void Function(Timer))? periodicTimer,
  }) : _positions = positions ?? _devicePositions,
       _now = now ?? DateTime.now,
       _preferences = preferences ?? SharedPreferences.getInstance,
       _periodicTimer = periodicTimer ?? Timer.periodic {
    WidgetsBinding.instance.addObserver(this);
  }

  final Stream<Position> Function() _positions;
  final DateTime Function() _now;
  final Future<SharedPreferences> Function() _preferences;
  final Timer Function(Duration, void Function(Timer)) _periodicTimer;
  static Stream<Position> _devicePositions() => Geolocator.getPositionStream(
    locationSettings: AndroidSettings(
      accuracy: LocationAccuracy.bestForNavigation,
      distanceFilter: 0,
      intervalDuration: const Duration(seconds: 1),
    ),
  );

  List<Ride> rides = [];
  final List<LatLng> live = [];
  final List<int> _segmentStarts = [];
  String? _id;
  DateTime? _startedAt;
  DateTime? _activeSince;
  DateTime? _lastFix;
  Duration _elapsed = Duration.zero;
  bool _breakSegment = true;
  bool _lifecyclePaused = false;
  bool _foreground = true;
  bool _disposed = false;
  bool _loaded = false;
  Future<void>? _loading;
  Future<bool> _writes = Future.value(true);
  Timer? _timer;
  StreamSubscription<Position>? _sub;
  String? error;
  bool recovered = false;
  int traceRevision = 0;
  bool _busy = false;
  Future<void>? _pausing;
  bool get busy => _busy || _pausing != null;

  bool get recording => _startedAt != null;
  bool get loaded => _loaded;
  bool get paused => recording && _activeSince == null;
  Duration get liveDuration =>
      _elapsed +
      (_activeSince == null ? Duration.zero : _now().difference(_activeSince!));
  Ride get liveRide => Ride(
    id: _id ?? '',
    name: 'Ride',
    startedAt: _startedAt ?? _now(),
    endedAt: _now(),
    points: List.of(live),
    segmentStarts: List.of(_segmentStarts),
    activeDuration: liveDuration,
  );
  double get liveDistanceM => liveRide.distanceM;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  Future<void> load() {
    if (_loaded) return Future.value();
    return _loading ??= _load().whenComplete(() => _loading = null);
  }

  Future<void> _load() async {
    try {
      final prefs = await _preferences();
      rides = [
        for (final s in prefs.getStringList(_kRides) ?? <String>[])
          Ride.fromJson(Map<String, dynamic>.from(jsonDecode(s) as Map)),
      ];
      final draft = prefs.getString(_kDraft);
      if (draft != null) {
        final ride = Ride.fromJson(
          Map<String, dynamic>.from(jsonDecode(draft) as Map),
        );
        // A crash between saving the ride and deleting its draft must not
        // recover the same recording a second time.
        if (!rides.any((r) => r.id == ride.id)) {
          _id = ride.id;
          _startedAt = ride.startedAt;
          live.addAll(ride.points);
          _segmentStarts.addAll(ride.segmentStarts);
          _elapsed = ride.duration;
          traceRevision++;
          recovered = true;
        }
      }
      _loaded = true;
      error = null;
    } catch (_) {
      error = 'Could not load rides. Retry.';
    }
    _notify();
  }

  Future<bool> _write(Future<bool> Function(SharedPreferences) action) {
    _writes = _writes.then((_) async {
      try {
        if (!await action(await _preferences())) {
          throw StateError('Write failed');
        }
        error = null;
        _notify();
        return true;
      } catch (_) {
        error = 'Could not save. Retry.';
        _notify();
        return false;
      }
    });
    return _writes;
  }

  Future<bool> checkpoint() {
    // Whole-trace preferences are suitable for short rides. Longer recordings
    // should move to an append-only file/database before increasing frequency:
    // this rewrites every saved point and preferences provide best-effort durability.
    if (!recording) return Future.value(true);
    final snapshot = jsonEncode(liveRide.toJson());
    return _write((prefs) => prefs.setString(_kDraft, snapshot));
  }

  Future<bool> retry() async {
    if (!_loaded) {
      await load();
      return _loaded;
    }
    return checkpoint();
  }

  Future<bool> start() async {
    await load();
    if (!_loaded || recording || busy) return false;
    live.clear();
    traceRevision++;
    _segmentStarts.clear();
    _startedAt = _now();
    _id = _startedAt!.microsecondsSinceEpoch.toRadixString(36);
    _elapsed = Duration.zero;
    return resume();
  }

  Future<bool> resume() async {
    if (_disposed || !_foreground || !recording || !paused || busy) {
      return false;
    }
    _activeSince = _now();
    _breakSegment = true;
    _lastFix = null;
    recovered = false;
    _lifecyclePaused = false;
    try {
      _sub = _positions().listen(
        _onPosition,
        onError: (_) => _gpsStopped(),
        onDone: _gpsStopped,
      );
    } catch (_) {
      await _gpsStopped();
      return false;
    }
    var ticks = 0;
    _timer = _periodicTimer(const Duration(seconds: 1), (_) {
      if (++ticks % 30 == 0) unawaited(checkpoint());
      _notify();
    });
    _notify();
    await checkpoint();
    return !paused;
  }

  Future<void> _gpsStopped() async {
    if (!recording || paused) return;
    await pause();
    error = 'GPS interrupted. Resume to retry.';
    _notify();
  }

  Future<void> pause() {
    _lifecyclePaused = false;
    return _pausing ??= _pause().whenComplete(() {
      _pausing = null;
      _notify();
    });
  }

  Future<void> _pause() async {
    if (!recording) return;
    _elapsed = liveDuration;
    _activeSince = null;
    _breakSegment = true;
    _timer?.cancel();
    final sub = _sub;
    _sub = null;
    _notify();
    // Queue the snapshot before awaiting cancellation/lifecycle suspension.
    final saved = checkpoint();
    await sub?.cancel();
    await saved;
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.hidden ||
        state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      _foreground = false;
      if (recording && !paused) {
        unawaited(pause());
        _lifecyclePaused = true;
      }
    } else if (state == AppLifecycleState.resumed) {
      _foreground = true;
      unawaited(_resumeAfterLifecycle());
    }
  }

  Future<void> _resumeAfterLifecycle() async {
    await _pausing;
    if (_foreground && _lifecyclePaused && paused) await resume();
  }

  void _onPosition(Position p) {
    if (!recording || paused) return;
    if (!p.latitude.isFinite ||
        !p.longitude.isFinite ||
        !p.accuracy.isFinite ||
        p.accuracy > 30) {
      return;
    }
    if (_lastFix != null && p.timestamp.isBefore(_lastFix!)) return;
    if (_lastFix != null &&
        p.timestamp.difference(_lastFix!) > const Duration(seconds: 15)) {
      _breakSegment = true;
    }
    _lastFix = p.timestamp;
    final here = LatLng(p.latitude, p.longitude);
    if (!_breakSegment &&
        live.isNotEmpty &&
        distanceM(live.last, here) < _minStepM) {
      return;
    }
    if (_breakSegment) {
      if (_segmentStarts.isEmpty || _segmentStarts.last != live.length) {
        _segmentStarts.add(live.length);
      }
      _breakSegment = false;
    }
    live.add(here);
    traceRevision++;
    if (live.length % 50 == 0) unawaited(checkpoint());
    _notify();
  }

  /// Stop and keep the trace; null when it was too short to be a ride.
  Future<Ride?> stop({String? name}) async {
    if (!recording || busy) return null;
    _busy = true;
    await pause();
    final snapshot = liveRide;
    if (snapshot.distanceM < 50) {
      _busy = false;
      // Explicit discard remains available; a failed save must never erase it.
      error = 'Too short to save. Resume or discard.';
      _notify();
      return null;
    }
    final ride = Ride(
      id: snapshot.id,
      name: name ?? 'Ride ${_stamp(snapshot.startedAt)}',
      startedAt: snapshot.startedAt,
      endedAt: snapshot.endedAt,
      points: snapshot.points,
      segmentStarts: snapshot.segmentStarts,
      activeDuration: snapshot.duration,
    );
    final savedRides = [ride, ...rides.where((r) => r.id != ride.id)];
    final ok = await _write(
      (prefs) => prefs.setStringList(
        _kRides,
        savedRides.map((r) => jsonEncode(r.toJson())).toList(),
      ),
    );
    if (!ok) {
      _busy = false;
      _notify();
      return null;
    }
    rides = savedRides;
    await _write((prefs) => prefs.remove(_kDraft));
    _clear();
    return ride;
  }

  void _clear() {
    _startedAt = null;
    _activeSince = null;
    _id = null;
    _elapsed = Duration.zero;
    live.clear();
    traceRevision++;
    _segmentStarts.clear();
    recovered = false;
    _busy = false;
    _notify();
  }

  Future<bool> discard() async {
    if (busy) return false;
    _busy = true;
    await pause();
    if (!await _write((prefs) => prefs.remove(_kDraft))) {
      _busy = false;
      _notify();
      return false;
    }
    _clear();
    return true;
  }

  Future<bool> _replaceRides(List<Ride> next) async {
    if (busy) return false;
    _busy = true;
    _notify();
    final ok = await _write(
      (prefs) => prefs.setStringList(
        _kRides,
        next.map((r) => jsonEncode(r.toJson())).toList(),
      ),
    );
    if (ok) rides = next;
    _busy = false;
    _notify();
    return ok;
  }

  Future<bool> delete(String id) =>
      _replaceRides(rides.where((r) => r.id != id).toList());

  Future<bool> restore(Ride ride, int index) {
    if (rides.any((r) => r.id == ride.id)) return Future.value(true);
    final next = List<Ride>.of(rides)
      ..insert(index.clamp(0, rides.length), ride);
    return _replaceRides(next);
  }

  Future<bool> rename(String id, String name) async {
    if (name.trim().isEmpty) return false;
    return _replaceRides([
      for (final r in rides)
        if (r.id == id)
          Ride.fromJson({...r.toJson(), 'name': name.trim()})
        else
          r,
    ]);
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    if (recording) {
      _elapsed = liveDuration;
      _activeSince = null;
      unawaited(checkpoint());
    }
    _disposed = true;
    _timer?.cancel();
    _sub?.cancel();
    super.dispose();
  }

  static String _stamp(DateTime t) {
    const months = [
      'Jan',
      'Feb',
      'Mar',
      'Apr',
      'May',
      'Jun',
      'Jul',
      'Aug',
      'Sep',
      'Oct',
      'Nov',
      'Dec',
    ];
    final h = t.hour % 12 == 0 ? 12 : t.hour % 12;
    final m = t.minute.toString().padLeft(2, '0');
    return '${months[t.month - 1]} ${t.day}, $h:$m ${t.hour < 12 ? 'AM' : 'PM'}';
  }
}
