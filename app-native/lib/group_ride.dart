import 'dart:async';
import 'dart:convert';

import 'package:dio/dio.dart' show DioException;
import 'package:flutter/widgets.dart';
import 'package:geolocator/geolocator.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'api.dart';
import 'nav.dart';

/// One rider in the group, as the last `/ping` reported them.
class GroupMember {
  final String id;
  final String name;
  final LatLng? position;
  final double? heading;
  final bool isLeader;

  const GroupMember({
    required this.id,
    required this.name,
    this.position,
    this.heading,
    this.isLeader = false,
  });

  factory GroupMember.fromJson(Map<String, dynamic> j) {
    final lat = (j['lat'] as num?)?.toDouble();
    final lon = (j['lon'] as num?)?.toDouble();
    return GroupMember(
      id: '${j['id']}',
      name: '${j['name'] ?? 'Rider'}',
      position: lat == null || lon == null ? null : LatLng(lat, lon),
      heading: (j['heading'] as num?)?.toDouble(),
      isLeader: j['is_leader'] == true,
    );
  }
}

/// A ride offered by `/nearby`.
class NearbyRide {
  final String code;
  final String name;
  final String leaderName;
  final double distanceM;
  final int members;

  const NearbyRide({
    required this.code,
    required this.name,
    required this.leaderName,
    required this.distanceM,
    required this.members,
  });

  factory NearbyRide.fromJson(Map<String, dynamic> j) => NearbyRide(
    code: '${j['code']}',
    name: '${j['name'] ?? 'Group ride'}',
    leaderName: '${j['leader_name'] ?? 'Leader'}',
    distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0,
    members: (j['members'] as num?)?.toInt() ?? 1,
  );
}

/// Ride codes use the backend's unambiguous alphabet (no I/L/O/Q/S/Z).
const groupCodeAlphabet = 'ABCDEFGHJKMNPRTUVWXY';
final _codeRe = RegExp('^[$groupCodeAlphabet]{4}\$');

/// A valid 4-letter ride code in [raw] (any case), else null.
String? parseRideCode(String? raw) {
  final code = (raw ?? '').trim().toUpperCase();
  return _codeRe.hasMatch(code) ? code : null;
}

/// `?ride=CODE` on a web share link (`https://bwg.mrsm.io/bwg-app/?ride=ABCD`).
String? rideCodeFromUri(Uri uri) => parseRideCode(uri.queryParameters['ride']);

/// Live group ride session: anonymous, leader + members.
///
/// While active it pings `/ping` every 5 s with the latest fix from its own
/// low-rate position stream (one push + pull per ping). Backgrounded, the
/// stream is dropped and pings slow to 15 s with the last known fix, then
/// stop after 30 min (the backend ends a ride after 30 min of leader
/// silence anyway). The session survives an app restart via preferences.
///
/// ponytail: its own geolocator stream rather than a shared one — nav and the
/// recorder each own theirs, and Android's fused provider merges concurrent
/// requests. Share one stream if a fourth consumer shows up.
class GroupRideClient extends ChangeNotifier with WidgetsBindingObserver {
  static const _kSession = 'group_ride';
  static const _kRiderName = 'group_rider_name';
  static const foregroundInterval = Duration(seconds: 5);
  static const backgroundInterval = Duration(seconds: 15);
  static const backgroundLimit = Duration(minutes: 30);

  GroupRideClient({
    Stream<Position> Function()? positions,
    DateTime Function()? now,
    Future<SharedPreferences> Function()? preferences,
    Timer Function(Duration, void Function())? timer,
    bool observeLifecycle = true,
  }) : _positions = positions ?? _devicePositions,
       _now = now ?? DateTime.now,
       _preferences = preferences ?? SharedPreferences.getInstance,
       _timerFactory = timer ?? Timer.new {
    if (observeLifecycle) WidgetsBinding.instance.addObserver(this);
    _observing = observeLifecycle;
  }

  final Stream<Position> Function() _positions;
  final DateTime Function() _now;
  final Future<SharedPreferences> Function() _preferences;
  final Timer Function(Duration, void Function()) _timerFactory;
  late final bool _observing;

  static Stream<Position> _devicePositions() => Geolocator.getPositionStream(
    locationSettings: AndroidSettings(
      accuracy: LocationAccuracy.high,
      distanceFilter: 5,
      intervalDuration: foregroundInterval,
    ),
  );

  String? code;
  String? rideName;
  String? shareUrl;
  String? memberId;
  String? _token;
  bool isLeader = false;

  /// The backend said the ride is over (or forgot it). Cleared by
  /// [dismissEnded] or by starting/joining another ride.
  bool ended = false;
  String? error;
  List<GroupMember> members = const [];
  String? leaderId;
  Map<String, dynamic>? leaderRoute;

  /// The backend's `route_rev` for [leaderRoute]; sent with each ping so the
  /// route only travels when it changed.
  int? routeRev;

  /// Member ids + positions from the last ping; [revision] only moves when
  /// this does (or the route changes).
  String _membersSig = '';

  /// Latest fix pushed with each ping (and "distance from you" in the sheet).
  LatLng? position;
  double? _heading;
  double? _speed;

  /// Last name typed into the sheet; prefills it next time.
  String lastRiderName = '';

  /// Bumped whenever [members] / [leaderRoute] change, so the map redraws
  /// only when it has to.
  int revision = 0;

  bool get active => code != null && _token != null && !ended;
  GroupMember? get leader => members.where((m) => m.id == leaderId).firstOrNull;

  StreamSubscription<Position>? _sub;
  Timer? _timer;
  DateTime? _backgroundSince;
  bool _pinging = false;
  bool _disposed = false;

  void _notify() {
    if (!_disposed) notifyListeners();
  }

  /// Restore a session saved before the app was closed.
  Future<void> load() async {
    try {
      final prefs = await _preferences();
      lastRiderName = prefs.getString(_kRiderName) ?? '';
      final raw = prefs.getString(_kSession);
      if (raw != null) {
        final j = Map<String, dynamic>.from(jsonDecode(raw) as Map);
        code = j['code'] as String?;
        _token = j['member_token'] as String?;
        isLeader = j['is_leader'] == true;
        memberId = j['member_id'] as String?;
        rideName = j['name'] as String?;
        shareUrl = j['share_url'] as String?;
        if (active) _start();
      }
    } catch (_) {
      await _clear();
    }
    _notify();
  }

  Future<void> _save() async {
    final prefs = await _preferences();
    await prefs.setString(
      _kSession,
      jsonEncode({
        'code': code,
        'member_token': _token,
        'is_leader': isLeader,
        'member_id': memberId,
        'name': rideName,
        'share_url': shareUrl,
      }),
    );
  }

  Future<void> _rememberName(String riderName) async {
    if (riderName.trim().isEmpty) return;
    lastRiderName = riderName.trim();
    final prefs = await _preferences();
    await prefs.setString(_kRiderName, lastRiderName);
  }

  /// Start a ride here; the caller leads it.
  Future<void> create(String name, String riderName, LatLng pos) async {
    final r = await api.createGroupRide(
      name.trim(),
      riderName.trim(),
      pos.latitude,
      pos.longitude,
    );
    await _begin(
      code: '${r['code']}',
      token: '${r['member_token']}',
      memberId: '${r['member_id']}',
      leader: true,
      name: name.trim().isEmpty ? 'Group ride' : name.trim(),
      pos: pos,
    );
    shareUrl = r['share_url']?.toString();
    await _save();
    await _rememberName(riderName);
    _notify();
  }

  /// Join by the 4-letter code (from the nearby list, a link, or typed).
  Future<void> joinByCode(
    String rawCode,
    String riderName,
    LatLng pos, {
    String? rideName,
  }) async {
    final c = parseRideCode(rawCode);
    if (c == null) throw ApiError('Ride codes are 4 letters.');
    final r = await api.joinGroupRide(
      c,
      riderName.trim(),
      pos.latitude,
      pos.longitude,
    );
    await _begin(
      code: c,
      token: '${r['member_token']}',
      memberId: '${r['member_id']}',
      leader: false,
      // The server's name wins; the nearby list's is a fallback for older
      // backends, then the code.
      name: _str(r['ride_name']) ?? rideName ?? 'Group ride $c',
      pos: pos,
    );
    shareUrl = _str(r['share_url']) ?? shareUrl;
    await _save();
    await _rememberName(riderName);
    _notify();
  }

  Future<void> _begin({
    required String code,
    required String token,
    required String memberId,
    required bool leader,
    required String name,
    required LatLng pos,
  }) async {
    _stop();
    this.code = code;
    _token = token;
    this.memberId = memberId;
    isLeader = leader;
    rideName = name;
    shareUrl = 'https://bwg.mrsm.io/bwg-app/?ride=$code';
    ended = false;
    error = null;
    position = pos;
    members = const [];
    leaderRoute = null;
    routeRev = null;
    _membersSig = '';
    leaderId = null;
    revision++;
    _start();
  }

  Future<List<NearbyRide>> nearby(LatLng pos) async => [
    for (final r in await api.nearbyGroupRides(pos.latitude, pos.longitude))
      NearbyRide.fromJson(r),
  ];

  Future<void> leave() async {
    final c = code, t = _token;
    await _clear();
    if (c != null && t != null) {
      try {
        await api.leaveGroupRide(c, t);
      } catch (_) {} // the ride forgets silent members in 5 min anyway
    }
    _notify();
  }

  /// Leader only: end the ride for everyone. A 404/401 means it's already
  /// over server-side, so the local session clears then too; other errors
  /// rethrow with the session intact (so the leader can retry).
  Future<void> end() async {
    final c = code, t = _token;
    if (c == null || t == null) return;
    try {
      await api.endGroupRide(c, t);
    } catch (e) {
      if (!_gone(e)) rethrow;
    }
    await _clear();
    _notify();
  }

  /// 404 (ride gone) or 401 (member token revoked), as an [ApiError] or a
  /// DioException carrying the response.
  static bool _gone(Object e) {
    final status = e is ApiError
        ? e.status
        : e is DioException
        ? e.response?.statusCode
        : null;
    return status == 404 || status == 401;
  }

  /// Leader only: share the route everyone should follow. Only the fields
  /// followers' navigation needs travel (the backend caps routes at 200 KB).
  Future<void> setRoute(Map<String, dynamic> feature) async {
    final c = code, t = _token;
    if (!active || !isLeader || c == null || t == null) return;
    final geometry = feature['geometry'];
    if (geometry is! Map || geometry['type'] != 'LineString') return;
    final props = Map<String, dynamic>.from(feature['properties'] ?? {});
    const keep = {
      'steps',
      'distance_m',
      'duration_min',
      'mode',
      'plan',
      'plan_label',
      'climb_ft',
    };
    final slim = {
      'type': 'Feature',
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          for (final p in geometry['coordinates'] as List)
            [(p as List)[0], p[1]],
        ],
      },
      'properties': {
        for (final e in props.entries)
          if (keep.contains(e.key)) e.key: e.value,
      },
    };
    try {
      await api.setGroupRoute(c, t, slim);
      leaderRoute = slim;
      revision++;
      _notify();
    } on ApiError catch (e) {
      error = e.message;
      _notify();
    }
  }

  /// Leader only: take the shared route down (nav stopped).
  Future<void> clearRoute() async {
    final c = code, t = _token;
    if (!active || !isLeader || c == null || t == null || leaderRoute == null) {
      return;
    }
    try {
      await api.setGroupRoute(c, t, null);
      leaderRoute = null;
      revision++;
      _notify();
    } catch (_) {} // the next route push (or the ride ending) replaces it
  }

  void dismissEnded() {
    ended = false;
    _notify();
  }

  static String? _str(Object? v) {
    final s = v?.toString().trim() ?? '';
    return s.isEmpty ? null : s;
  }

  /// One push + pull. Public for tests and for "refresh now".
  Future<void> ping() async {
    final c = code, t = _token, pos = position;
    if (!active || c == null || t == null || pos == null || _pinging) return;
    _pinging = true;
    try {
      final r = await api.pingGroupRide(
        c,
        t,
        pos.latitude,
        pos.longitude,
        heading: _heading,
        speed: _speed,
        routeRev: routeRev,
      );
      if (code != c) return; // left/re-joined while in flight
      if (r['ended'] == true) {
        await _finish();
        return;
      }
      leaderId = r['leader_id']?.toString();
      // A recovered session only had what was saved; the ping has the truth.
      final name = _str(r['ride_name']), url = _str(r['share_url']);
      if ((name != null && name != rideName) ||
          (url != null && url != shareUrl)) {
        rideName = name ?? rideName;
        shareUrl = url ?? shareUrl;
        unawaited(_save());
      }
      var changed = false;
      // `route` is only in the reply when our routeRev was stale (older
      // backends always send it).
      if (r.containsKey('route')) {
        final route = r['route'];
        leaderRoute = route is Map ? Map<String, dynamic>.from(route) : null;
        changed = true;
      }
      routeRev = (r['route_rev'] as num?)?.toInt();
      members = [
        for (final m in (r['members'] as List? ?? const []))
          if (m is Map) GroupMember.fromJson(Map<String, dynamic>.from(m)),
      ];
      final sig = [
        for (final m in members)
          '${m.id}@${m.position?.latitude},${m.position?.longitude}',
      ].join('|');
      if (sig != _membersSig) {
        _membersSig = sig;
        changed = true;
      }
      error = null;
      if (changed) revision++;
      _notify();
    } catch (e) {
      // 404: ride gone; 401: token revoked. Either way this session is over.
      if (_gone(e)) {
        await _finish(); // the sheet shows "The ride has ended"
      } else if (e is ApiError) {
        error = e.message;
        _notify();
      }
      // Otherwise offline for a moment: keep trying on the next tick.
    } finally {
      _pinging = false;
    }
  }

  Future<void> _finish() async {
    await _clear();
    ended = true;
    _notify();
  }

  Future<void> _clear() async {
    _stop();
    code = _token = memberId = rideName = shareUrl = leaderId = null;
    isLeader = false;
    members = const [];
    leaderRoute = null;
    routeRev = null;
    _membersSig = '';
    revision++;
    try {
      final prefs = await _preferences();
      await prefs.remove(_kSession);
    } catch (_) {}
  }

  // ------------------------------------------------------------ the loop

  void _start() {
    if (_backgroundSince == null) _listen();
    _schedule();
  }

  void _stop() {
    _timer?.cancel();
    _timer = null;
    _sub?.cancel();
    _sub = null;
  }

  void _listen() {
    _sub?.cancel();
    try {
      _sub = _positions().listen(
        (p) {
          if (!p.latitude.isFinite || !p.longitude.isFinite) return;
          final first = position == null;
          position = LatLng(p.latitude, p.longitude);
          _heading = p.heading.isFinite && p.heading >= 0 ? p.heading : null;
          _speed = p.speed.isFinite && p.speed >= 0 ? p.speed : null;
          // A restored session waits for its first fix before pinging.
          if (first) unawaited(ping());
        },
        onError: (_) {}, // keep pinging the last known fix
        cancelOnError: false,
      );
    } catch (_) {}
  }

  /// Current cadence: 5 s on screen, 15 s backgrounded, none after 30 min.
  Duration? get interval {
    final since = _backgroundSince;
    if (since == null) return foregroundInterval;
    if (_now().difference(since) >= backgroundLimit) return null;
    return backgroundInterval;
  }

  void _schedule() {
    _timer?.cancel();
    _timer = null;
    final every = interval;
    if (!active || every == null) return;
    _timer = _timerFactory(every, () async {
      await ping();
      _schedule();
    });
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.hidden ||
        state == AppLifecycleState.paused ||
        state == AppLifecycleState.detached) {
      if (_backgroundSince != null) return;
      _backgroundSince = _now();
      // The GPS is the battery cost; background pings reuse the last fix.
      _sub?.cancel();
      _sub = null;
      _schedule();
    } else if (state == AppLifecycleState.resumed) {
      if (_backgroundSince == null) return;
      _backgroundSince = null;
      if (!active) return;
      _listen();
      unawaited(ping());
      _schedule();
    }
  }

  /// Metres from me to [m], or null without both fixes.
  double? distanceTo(GroupMember m) {
    final a = position, b = m.position;
    return a == null || b == null ? null : metersBetween(a, b);
  }

  @override
  void dispose() {
    _disposed = true;
    _stop();
    if (_observing) WidgetsBinding.instance.removeObserver(this);
    super.dispose();
  }
}
