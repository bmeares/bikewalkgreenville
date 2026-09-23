import 'dart:async';
import 'dart:convert';

import 'package:bwg_app_native/api.dart';
import 'package:dio/dio.dart';
import 'package:bwg_app_native/auth.dart';
import 'package:bwg_app_native/group_ride.dart';
import 'package:bwg_app_native/screens/group_ride_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:geolocator/geolocator.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeApi extends Api {
  final pings = <String>[];
  final calls = <String>[];
  Map<String, dynamic> pingReply = {
    'ended': false,
    'leader_id': 'm1',
    'route': null,
    'members': [
      {'id': 'm1', 'name': 'Alex', 'lat': 34.85, 'lon': -82.4, 'is_leader': true},
      {'id': 'm2', 'name': 'Sam', 'lat': 34.851, 'lon': -82.4, 'is_leader': false},
    ],
  };
  Object? pingError;
  Object? endError;
  Object? nearbyError;
  final routeRevs = <int?>[];
  List<Map<String, dynamic>> nearbyRides = [];

  /// Merged into the join reply (the backend's ride_name / share_url).
  Map<String, dynamic> joinExtra = {};

  @override
  Future<Map<String, dynamic>> createGroupRide(
    String name,
    String riderName,
    double lat,
    double lon,
  ) async {
    calls.add('create $name $riderName');
    return {
      'code': 'ABCD',
      'ride_id': 'r1',
      'member_id': 'm1',
      'member_token': 'tok-leader',
      'share_url': 'https://bwg.mrsm.io/bwg-app/?ride=ABCD',
    };
  }

  @override
  Future<Map<String, dynamic>> joinGroupRide(
    String code,
    String riderName,
    double lat,
    double lon,
  ) async {
    calls.add('join $code');
    return {
      'ride_id': 'r1',
      'member_id': 'm2',
      'member_token': 'tok-m',
      'name': 'Sam',
      ...joinExtra,
    };
  }

  @override
  Future<Map<String, dynamic>> pingGroupRide(
    String code,
    String token,
    double lat,
    double lon, {
    double? heading,
    double? speed,
    int? routeRev,
  }) async {
    pings.add('$code $token $lat,$lon');
    routeRevs.add(routeRev);
    if (pingError != null) throw pingError!;
    return pingReply;
  }

  @override
  Future<void> setGroupRoute(String code, String token, Map<String, dynamic>? route) async =>
      calls.add('route $code ${(route?['geometry'] as Map?)?['type']}');

  @override
  Future<void> endGroupRide(String code, String token) async {
    calls.add('end $code');
    if (endError != null) throw endError!;
  }

  @override
  Future<void> leaveGroupRide(String code, String token) async => calls.add('leave $code');

  @override
  Future<List<Map<String, dynamic>>> nearbyGroupRides(double lat, double lon) async {
    calls.add('nearby');
    if (nearbyError != null) throw nearbyError!;
    return nearbyRides;
  }
}

class _FakeTimer implements Timer {
  final Duration duration;
  final void Function() callback;
  bool cancelled = false;
  _FakeTimer(this.duration, this.callback);
  @override
  void cancel() => cancelled = true;
  @override
  bool get isActive => !cancelled;
  @override
  int get tick => 0;
}

const _here = LatLng(34.85, -82.4);

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  late _FakeApi fake;
  late Api realApi;
  late List<_FakeTimer> timers;
  late StreamController<Position> gps;
  late DateTime clock;

  setUp(() {
    SharedPreferences.setMockInitialValues({});
    realApi = api;
    fake = _FakeApi();
    api = fake;
    timers = [];
    gps = StreamController<Position>.broadcast();
    clock = DateTime.utc(2026, 9, 23, 12);
  });
  tearDown(() => api = realApi);

  GroupRideClient client() => GroupRideClient(
    positions: () => gps.stream,
    now: () => clock,
    timer: (d, cb) {
      final t = _FakeTimer(d, cb);
      timers.add(t);
      return t;
    },
    observeLifecycle: false,
  );

  _FakeTimer live() => timers.lastWhere((t) => !t.cancelled);

  test('create → active → ping updates members → ended stops pinging', () async {
    final c = client();
    await c.create('Community Roll', 'Alex', _here);
    expect(c.active, isTrue);
    expect(c.isLeader, isTrue);
    expect(c.code, 'ABCD');
    expect(c.shareUrl, 'https://bwg.mrsm.io/bwg-app/?ride=ABCD');
    expect(live().duration, GroupRideClient.foregroundInterval);

    live().callback();
    await pumpEventQueue();
    expect(fake.pings.single, 'ABCD tok-leader 34.85,-82.4');
    expect(c.members.map((m) => m.name), ['Alex', 'Sam']);
    expect(c.leader?.name, 'Alex');
    expect(c.distanceTo(c.members[1])!, closeTo(111, 2));

    await c.setRoute({
      'type': 'Feature',
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          [-82.4, 34.85, 300],
          [-82.39, 34.86, 301],
        ],
      },
      'properties': {'steps': [], 'alternatives': ['big'], 'distance_m': 1400},
    });
    expect(fake.calls.last, 'route ABCD LineString');
    expect((c.leaderRoute!['properties'] as Map).containsKey('alternatives'), isFalse);

    fake.pingReply = {'ended': true, 'members': []};
    live().callback();
    await pumpEventQueue();
    expect(c.ended, isTrue);
    expect(c.active, isFalse);
    expect(timers.where((t) => !t.cancelled), isEmpty);
    final prefs = await SharedPreferences.getInstance();
    expect(prefs.getString('group_ride'), isNull);
    c.dispose();
  });

  test('join by code persists, and a restart resumes the session', () async {
    final c = client();
    await expectLater(c.joinByCode('ab', 'Sam', _here), throwsA(isA<ApiError>()));
    await c.joinByCode('abcd', 'Sam', _here, rideName: 'Community Roll');
    expect(fake.calls.last, 'join ABCD');
    expect(c.isLeader, isFalse);
    c.dispose();

    final prefs = await SharedPreferences.getInstance();
    final saved = jsonDecode(prefs.getString('group_ride')!) as Map;
    expect(saved['code'], 'ABCD');
    expect(saved['member_token'], 'tok-m');
    expect(saved['is_leader'], isFalse);
    expect(prefs.getString('group_rider_name'), 'Sam');

    final restored = client();
    await restored.load();
    expect(restored.active, isTrue);
    expect(restored.rideName, 'Community Roll');
    expect(restored.lastRiderName, 'Sam');
    // No fix yet: nothing to push. The first fix pings immediately.
    live().callback();
    await pumpEventQueue();
    expect(fake.pings, isEmpty);
    gps.add(
      Position(
        latitude: 34.86,
        longitude: -82.41,
        timestamp: clock,
        accuracy: 5,
        altitude: 0,
        altitudeAccuracy: 0,
        heading: 90,
        headingAccuracy: 0,
        speed: 4,
        speedAccuracy: 0,
      ),
    );
    await pumpEventQueue();
    expect(fake.pings.single, 'ABCD tok-m 34.86,-82.41');

    // A 404 (ride gone) clears the saved session.
    fake.pingError = ApiError('Ride not found or ended', 404);
    await restored.ping();
    expect(restored.ended, isTrue);
    expect(prefs.getString('group_ride'), isNull);
    restored.dispose();
  });

  test('joining by a typed code shows the server ride name and link', () async {
    fake.joinExtra = {
      'ride_name': 'Tuesday Taco Ride',
      'share_url': 'https://bwg.mrsm.io/bwg-app/?ride=HJKM',
    };
    final c = client();
    await c.joinByCode('hjkm', 'Sam', _here);
    expect(c.rideName, 'Tuesday Taco Ride');
    expect(c.shareUrl, 'https://bwg.mrsm.io/bwg-app/?ride=HJKM');

    // A ping carries the current name (renamed, or a recovered session).
    fake.pingReply = {...fake.pingReply, 'ride_name': 'Taco Ride (late start)'};
    await c.ping();
    expect(c.rideName, 'Taco Ride (late start)');
    final prefs = await SharedPreferences.getInstance();
    await pumpEventQueue();
    expect(
      (jsonDecode(prefs.getString('group_ride')!) as Map)['name'],
      'Taco Ride (late start)',
    );
    c.dispose();
  });

  test('backgrounded: 15 s pings on the last fix, stops after 30 min', () async {
    final c = client();
    await c.create('Roll', '', _here);
    c.didChangeAppLifecycleState(AppLifecycleState.paused);
    expect(live().duration, GroupRideClient.backgroundInterval);
    live().callback();
    await pumpEventQueue();
    expect(fake.pings.single, 'ABCD tok-leader 34.85,-82.4');

    clock = clock.add(const Duration(minutes: 31));
    live().callback();
    await pumpEventQueue();
    expect(fake.pings, hasLength(2));
    expect(timers.where((t) => !t.cancelled), isEmpty);

    c.didChangeAppLifecycleState(AppLifecycleState.resumed);
    await pumpEventQueue();
    expect(fake.pings, hasLength(3)); // pings straight away on return
    expect(live().duration, GroupRideClient.foregroundInterval);
    await c.leave();
    expect(fake.calls.last, 'leave ABCD');
    expect(c.active, isFalse);
    c.dispose();
  });

  test('?ride= parsing accepts only real codes', () {
    expect(rideCodeFromUri(Uri.parse('https://bwg.mrsm.io/bwg-app/?ride=abcd')), 'ABCD');
    expect(rideCodeFromUri(Uri.parse('https://bwg.mrsm.io/bwg-app/?ride=ABC')), isNull);
    // I/O/S/Z/L/Q are outside the backend alphabet.
    expect(rideCodeFromUri(Uri.parse('https://bwg.mrsm.io/bwg-app/?ride=IOSZ')), isNull);
    expect(rideCodeFromUri(Uri.parse('https://bwg.mrsm.io/bwg-app/?from=1,2')), isNull);
  });

  testWidgets('idle sheet lists nearby rides; code field uppercases to 4', (
    tester,
  ) async {
    fake.nearbyRides = [
      {'code': 'HJKM', 'name': 'Saturday Roll', 'leader_name': 'Alex', 'distance_m': 91, 'members': 3},
    ];
    final c = client();
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider.value(value: c),
          ChangeNotifierProvider.value(value: AuthState()),
        ],
        child: MaterialApp(
          home: Scaffold(
            body: SingleChildScrollView(
              child: GroupRideSheet(locate: () async => _here),
            ),
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    expect(find.text('Saturday Roll'), findsOneWidget);
    expect(find.textContaining('Led by Alex · 3 riders · 300 ft away'), findsOneWidget);

    await tester.enterText(find.byKey(const ValueKey('group-code')), 'hjkm7x');
    await tester.pump();
    final field = tester.widget<TextField>(find.byKey(const ValueKey('group-code')));
    expect(field.controller!.text, 'HJKM');

    await tester.tap(find.byKey(const ValueKey('nearby-HJKM')));
    await tester.pumpAndSettle();
    expect(fake.calls.last, 'join HJKM');
    expect(find.text('Leave'), findsOneWidget);

    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });

  test('route travels only when route_rev changes; revision only on change', () async {
    final c = client();
    await c.create('Roll', 'Alex', _here);
    const route = {
      'type': 'Feature',
      'geometry': {'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.39, 34.86]]},
    };
    fake.pingReply = {...fake.pingReply, 'route_rev': 1, 'route': route};
    await c.ping();
    expect(fake.routeRevs.last, isNull); // nothing known yet
    expect(c.leaderRoute, isNotNull);
    final rev = c.revision;

    // Same members, no `route` key (our rev is current): nothing to redraw.
    fake.pingReply = Map.of(fake.pingReply)..remove('route');
    await c.ping();
    expect(fake.routeRevs.last, 1);
    expect(c.leaderRoute, isNotNull);
    expect(c.revision, rev);

    // Leader cleared it: route_rev 2 with an explicit null.
    fake.pingReply = {...fake.pingReply, 'route_rev': 2, 'route': null};
    await c.ping();
    expect(c.leaderRoute, isNull);
    expect(c.revision, rev + 1);

    await c.clearRoute(); // nothing shared: no PUT
    expect(fake.calls.where((e) => e.startsWith('route')), isEmpty);
    c.dispose();
  });

  test('a 401 of any exception type ends the session; end() clears on 404', () async {
    final c = client();
    await c.joinByCode('ABCD', 'Sam', _here);
    fake.pingError = DioException(
      requestOptions: RequestOptions(path: '/group-rides/ABCD/ping'),
      response: Response(
        requestOptions: RequestOptions(path: '/group-rides/ABCD/ping'),
        statusCode: 401,
      ),
    );
    await c.ping();
    expect(c.ended, isTrue);
    expect(c.active, isFalse);

    final l = client();
    await l.create('Roll', 'Alex', _here);
    fake.endError = ApiError('Ride not found or ended', 404);
    await l.end();
    expect(l.active, isFalse);
    // Other failures keep the session so the leader can retry.
    await l.create('Roll', 'Alex', _here);
    fake.endError = ApiError('Could not save', 503);
    await expectLater(l.end(), throwsA(isA<ApiError>()));
    expect(l.active, isTrue);
    c.dispose();
    l.dispose();
  });

  test('the account token never goes to group rides or localhost', () {
    expect(sendsBearer(Uri.parse('https://bwg.mrsm.io/bwg/auth/me')), isTrue);
    expect(sendsBearer(Uri.parse('https://bwg.mrsm.io/group-rides/ABCD/ping')), isFalse);
    expect(sendsBearer(Uri.parse('http://localhost:8899/bwg/auth/me')), isFalse);
    expect(sendsBearer(Uri.parse('https://bwg.mrsm.io.evil.example/bwg/auth/me')), isFalse);
  });

  Widget sheet(GroupRideClient c) => MultiProvider(
    providers: [
      ChangeNotifierProvider.value(value: c),
      ChangeNotifierProvider.value(value: AuthState()),
    ],
    child: MaterialApp(
      home: Scaffold(
        body: SingleChildScrollView(
          child: GroupRideSheet(locate: () async => _here),
        ),
      ),
    ),
  );

  testWidgets('nearby: 429 shows inline, refresh retries, polls every 60 s', (
    tester,
  ) async {
    fake.nearbyError = ApiError('Too many lookups; try again later', 429);
    final c = client();
    await tester.pumpWidget(sheet(c));
    await tester.pumpAndSettle();
    expect(find.textContaining('Too many lookups — try again'), findsOneWidget);
    expect(find.textContaining('No rides within'), findsNothing);

    fake.nearbyError = null;
    await tester.tap(find.byKey(const ValueKey('nearby-refresh')));
    await tester.pumpAndSettle();
    expect(find.textContaining('No rides within'), findsOneWidget);
    final before = fake.calls.where((e) => e == 'nearby').length;
    await tester.pump(const Duration(seconds: 30));
    expect(fake.calls.where((e) => e == 'nearby').length, before);
    await tester.pump(const Duration(seconds: 31));
    expect(fake.calls.where((e) => e == 'nearby').length, before + 1);

    await tester.pumpWidget(const SizedBox());
    c.dispose();
  });

  testWidgets('member rows keep "Catch up" as its own button', (tester) async {
    final handle = tester.ensureSemantics();
    final c = client();
    await c.joinByCode('ABCD', 'Sam', _here);
    await c.ping();
    await tester.pumpWidget(sheet(c));
    await tester.pump();
    expect(
      tester.getSemantics(find.widgetWithText(TextButton, 'Catch up').first),
      matchesSemantics(label: 'Catch up', isButton: true, hasTapAction: true,
          isFocusable: true, hasEnabledState: true, isEnabled: true,
          hasFocusAction: true),
    );
    await tester.pumpWidget(const SizedBox());
    c.dispose();
    handle.dispose();
  });
}
