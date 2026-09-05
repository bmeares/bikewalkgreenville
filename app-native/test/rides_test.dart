import 'dart:async';
import 'dart:convert';

import 'package:flutter/widgets.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:geolocator/geolocator.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:bwg_app_native/rides.dart';

void main() {
  TestWidgetsFlutterBinding.ensureInitialized();
  setUp(() => SharedPreferences.setMockInitialValues({}));

  test(
    'a queued lifecycle resume cannot restart recording in the background',
    () async {
      final cancelled = Completer<void>();
      final stream = StreamController<Position>(
        sync: true,
        onCancel: () => cancelled.future,
      );
      var subscriptions = 0;
      final recorder = RideRecorder(
        positions: () {
          subscriptions++;
          return stream.stream;
        },
      );
      await recorder.start();
      recorder.didChangeAppLifecycleState(AppLifecycleState.paused);
      recorder.didChangeAppLifecycleState(AppLifecycleState.resumed);
      recorder.didChangeAppLifecycleState(AppLifecycleState.paused);
      cancelled.complete();
      await Future<void>.delayed(Duration.zero);
      expect(recorder.paused, isTrue);
      expect(subscriptions, 1);
      await recorder.discard();
      recorder.dispose();
      await stream.close();
    },
  );

  test('concurrent load and retry restore a checkpoint only once', () async {
    final draft = Ride(
      id: 'draft',
      name: 'Ride',
      startedAt: DateTime.utc(2026),
      endedAt: DateTime.utc(2026, 1, 1, 1),
      points: const [LatLng(34.85, -82.4), LatLng(34.851, -82.4)],
    );
    SharedPreferences.setMockInitialValues({
      'ride_in_progress': jsonEncode(draft.toJson()),
    });
    final gate = Completer<SharedPreferences>();
    var loads = 0;
    final recorder = RideRecorder(
      preferences: () {
        loads++;
        return gate.future;
      },
    );
    final loading = recorder.load();
    final retrying = recorder.retry();
    gate.complete(await SharedPreferences.getInstance());
    await loading;
    await retrying;
    expect(loads, 1);
    expect(recorder.live, hasLength(2));
    await recorder.discard();
    recorder.dispose();
  });

  test(
    'resume waits for pause cancellation instead of overlapping GPS streams',
    () async {
      final cancelled = Completer<void>();
      final stream = StreamController<Position>(
        sync: true,
        onCancel: () => cancelled.future,
      );
      final recorder = RideRecorder(positions: () => stream.stream);
      await recorder.start();
      final pausing = recorder.pause();
      expect(recorder.busy, isTrue);
      expect(await recorder.resume(), isFalse);
      cancelled.complete();
      await pausing;
      expect(recorder.busy, isFalse);
      await recorder.discard();
      recorder.dispose();
      await stream.close();
    },
  );

  test('a long GPS trace thins to the 200-vertex server limit', () {
    // A wiggly 1 Hz trace: 3000 fixes along a line with GPS jitter.
    final points = [
      for (var i = 0; i < 3000; i++)
        LatLng(34.85 + i * 0.00003, -82.40 + (i.isEven ? 0.00001 : -0.00001)),
    ];
    final reduced = fitToVertexLimit(points);
    expect(reduced.length, lessThanOrEqualTo(200));
    expect(reduced.length, greaterThanOrEqualTo(2));
    expect(reduced.first, points.first);
    expect(reduced.last, points.last);
  });

  test('rides round-trip through JSON and slice by index', () {
    final ride = Ride(
      id: 'r1',
      name: 'Test',
      startedAt: DateTime.utc(2026, 9, 5, 12),
      endedAt: DateTime.utc(2026, 9, 5, 12, 30),
      points: const [
        LatLng(34.85, -82.4),
        LatLng(34.851, -82.4),
        LatLng(34.852, -82.4),
      ],
    );
    final copy = Ride.fromJson(ride.toJson());
    expect(copy.points, ride.points);
    expect(copy.distanceM, closeTo(222, 5));
    expect(ride.lineString(1, 2)['coordinates'], [
      [-82.4, 34.851],
      [-82.4, 34.852],
    ]);
  });

  test(
    'segments preserve gaps in distance, geometry, slices and legacy JSON',
    () {
      final ride = Ride(
        id: 'segmented',
        name: 'Test',
        startedAt: DateTime.utc(2026),
        endedAt: DateTime.utc(2026, 1, 1, 1),
        activeDuration: const Duration(minutes: 5),
        points: const [
          LatLng(34.85, -82.4),
          LatLng(34.851, -82.4),
          LatLng(35.85, -82.4),
          LatLng(35.851, -82.4),
        ],
        segmentStarts: const [0, 2],
      );
      final copy = Ride.fromJson(ride.toJson());
      expect(copy.distanceM, closeTo(222, 5));
      expect(copy.duration, const Duration(minutes: 5));
      expect(copy.lineString()['type'], 'MultiLineString');
      expect(copy.lineString()['coordinates'], hasLength(2));
      expect(copy.lineString(2, 3)['type'], 'LineString');
      expect(copy.segments(1, 2).map((s) => s.length), [1, 1]);
      final legacy = ride.toJson()
        ..remove('segment_starts')
        ..remove('active_ms');
      expect(Ride.fromJson(legacy).duration, const Duration(hours: 1));
      expect(Ride.fromJson(legacy).lineString()['type'], 'LineString');
    },
  );

  test(
    'pause excludes time and distance, recovery starts paused and saves once',
    () async {
      final stream = StreamController<Position>.broadcast(sync: true);
      var now = DateTime.utc(2026, 9, 5, 12);
      final recorder = RideRecorder(
        positions: () => stream.stream,
        now: () => now,
      );
      expect(await recorder.start(), isTrue);
      stream.add(fix(34.85, now));
      now = now.add(const Duration(seconds: 10));
      stream.add(fix(34.851, now));
      await recorder.pause();
      now = now.add(const Duration(minutes: 5));
      expect(recorder.liveDuration, const Duration(seconds: 10));
      await recorder.resume();
      stream.add(fix(35.85, now));
      now = now.add(const Duration(seconds: 10));
      stream.add(fix(35.851, now));
      await recorder.pause();
      final originalId = recorder.liveRide.id;
      recorder.dispose();
      await Future<void>.delayed(Duration.zero);

      final restored = RideRecorder(
        positions: () => stream.stream,
        now: () => now,
      );
      await restored.load();
      expect(restored.recovered, isTrue);
      expect(restored.paused, isTrue);
      expect(restored.liveRide.id, originalId);
      expect(restored.liveDuration, const Duration(seconds: 20));
      expect(restored.liveDistanceM, closeTo(222, 5));
      expect(restored.liveRide.segmentStarts, [0, 2]);
      final ride = await restored.stop();
      expect(ride!.id, originalId);
      expect(await restored.stop(), isNull);
      expect(restored.rides, hasLength(1));
      expect(
        (await SharedPreferences.getInstance()).getString('ride_in_progress'),
        isNull,
      );
      restored.dispose();
      await stream.close();
    },
  );

  test(
    'an already saved checkpoint is not recovered after interrupted cleanup',
    () async {
      final ride = Ride(
        id: 'saved',
        name: 'Test',
        startedAt: DateTime.utc(2026),
        endedAt: DateTime.utc(2026, 1, 1, 1),
        points: const [LatLng(34.85, -82.4), LatLng(34.851, -82.4)],
      );
      SharedPreferences.setMockInitialValues({
        'rides': [jsonEncode(ride.toJson())],
        'ride_in_progress': jsonEncode(ride.toJson()),
      });
      final recorder = RideRecorder();
      await recorder.load();
      expect(recorder.recording, isFalse);
      expect(recorder.recovered, isFalse);
      expect(recorder.rides, hasLength(1));
      recorder.dispose();
    },
  );

  test(
    'failed save retains paused trace and can retry without duplication',
    () async {
      final stream = StreamController<Position>.broadcast(sync: true);
      var fail = false;
      final recorder = RideRecorder(
        positions: () => stream.stream,
        preferences: () async {
          if (fail) throw StateError('Storage unavailable');
          return SharedPreferences.getInstance();
        },
      );
      await recorder.start();
      final now = DateTime.utc(2026);
      stream.add(fix(34.85, now));
      stream.add(fix(34.851, now.add(const Duration(seconds: 1))));
      await recorder.checkpoint();
      fail = true;
      expect(await recorder.stop(), isNull);
      expect(recorder.paused, isTrue);
      expect(recorder.error, contains('Could not save'));
      expect(recorder.live, hasLength(2));
      expect(recorder.rides, isEmpty);
      fail = false;
      expect(await recorder.stop(), isNotNull);
      expect(recorder.rides, hasLength(1));
      expect(recorder.error, isNull);
      recorder.dispose();
      await stream.close();
    },
  );

  test(
    'GPS gaps split the trace while isolated inaccurate fixes are ignored',
    () async {
      final stream = StreamController<Position>.broadcast(sync: true);
      final recorder = RideRecorder(positions: () => stream.stream);
      await recorder.start();
      final now = DateTime.utc(2026);
      stream.add(fix(34.85, now));
      stream.add(fix(34.851, now.add(const Duration(seconds: 1))));
      stream.add(fix(35.85, now.add(const Duration(seconds: 60))));
      stream.add(fix(35.851, now.add(const Duration(seconds: 61))));
      stream.add(
        fix(36.85, now.add(const Duration(seconds: 62)), accuracy: 100),
      );
      stream.add(fix(35.852, now.add(const Duration(seconds: 63))));
      expect(recorder.liveRide.segmentStarts, [0, 2]);
      expect(recorder.liveDistanceM, closeTo(333, 5));
      await recorder.discard();
      recorder.dispose();
      await stream.close();
    },
  );

  test(
    'only lifecycle pauses resume when the app returns to the foreground',
    () async {
      final stream = StreamController<Position>.broadcast(sync: true);
      final recorder = RideRecorder(positions: () => stream.stream);
      await recorder.start();
      stream.add(fix(34.85, DateTime.utc(2026)));
      recorder.didChangeAppLifecycleState(AppLifecycleState.paused);
      expect(recorder.paused, isTrue);
      await recorder.checkpoint();
      final draft = jsonDecode(
        (await SharedPreferences.getInstance()).getString('ride_in_progress')!,
      );
      expect(draft['points'], hasLength(1));
      recorder.didChangeAppLifecycleState(AppLifecycleState.resumed);
      await Future<void>.delayed(Duration.zero);
      expect(recorder.paused, isFalse);
      stream.add(fix(35.85, DateTime.utc(2026, 1, 1, 0, 1)));
      expect(recorder.liveRide.segmentStarts, [0, 1]);
      await recorder.pause();
      recorder.didChangeAppLifecycleState(AppLifecycleState.paused);
      recorder.didChangeAppLifecycleState(AppLifecycleState.resumed);
      await Future<void>.delayed(Duration.zero);
      expect(recorder.paused, isTrue);
      await recorder.discard();
      recorder.dispose();
      await stream.close();
    },
  );

  test('periodic checkpoints persist before recording stops', () async {
    final stream = StreamController<Position>.broadcast(sync: true);
    late ManualTimer ticker;
    final recorder = RideRecorder(
      positions: () => stream.stream,
      periodicTimer: (_, callback) => ticker = ManualTimer(callback),
    );
    await recorder.start();
    stream.add(fix(34.85, DateTime.utc(2026)));
    for (var i = 0; i < 29; i++) {
      ticker.fire();
    }
    await Future<void>.delayed(Duration.zero);
    final prefs = await SharedPreferences.getInstance();
    expect(jsonDecode(prefs.getString('ride_in_progress')!)['points'], isEmpty);
    ticker.fire();
    await Future<void>.delayed(Duration.zero);
    final draft = jsonDecode(
      (await SharedPreferences.getInstance()).getString('ride_in_progress')!,
    );
    expect(draft['points'], hasLength(1));
    await recorder.discard();
    recorder.dispose();
    await stream.close();
  });

  test('trim to viewport chooses one continuous segment', () {
    final ride = Ride(
      id: 'segmented',
      name: 'Ride',
      startedAt: DateTime.utc(2026),
      endedAt: DateTime.utc(2026, 1, 1, 1),
      points: const [
        LatLng(34.85, -82.4),
        LatLng(34.851, -82.4),
        LatLng(35.85, -82.4),
        LatLng(35.851, -82.4),
        LatLng(35.852, -82.4),
      ],
      segmentStarts: const [0, 2],
    );
    expect(ride.longestStretchWhere((_) => true), (start: 2, end: 4));
    expect(ride.longestStretchWhere((p) => p.latitude < 35), (
      start: 0,
      end: 1,
    ));
    expect(ride.longestStretchWhere((_) => false), isNull);
  });

  test(
    'ride deletion and undo persist the original order and are idempotent',
    () async {
      final rides = [
        for (final id in ['one', 'two'])
          Ride(
            id: id,
            name: id,
            startedAt: DateTime.utc(2026),
            endedAt: DateTime.utc(2026, 1, 1, 1),
            points: const [LatLng(34.85, -82.4), LatLng(34.851, -82.4)],
          ),
      ];
      SharedPreferences.setMockInitialValues({
        'rides': rides.map((r) => jsonEncode(r.toJson())).toList(),
      });
      final recorder = RideRecorder();
      await recorder.load();
      expect(await recorder.delete('one'), isTrue);
      await recorder.restore(rides.first, 0);
      await recorder.restore(rides.first, 0);
      final reloaded = RideRecorder();
      await reloaded.load();
      expect(reloaded.rides.map((r) => r.id), ['one', 'two']);
      recorder.dispose();
      reloaded.dispose();
    },
  );
}

class ManualTimer implements Timer {
  ManualTimer(this.callback);
  final void Function(Timer) callback;
  @override
  bool isActive = true;
  @override
  int tick = 0;
  void fire() {
    if (!isActive) return;
    tick++;
    callback(this);
  }

  @override
  void cancel() {
    isActive = false;
  }
}

Position fix(double latitude, DateTime timestamp, {double accuracy = 5}) =>
    Position(
      latitude: latitude,
      longitude: -82.4,
      timestamp: timestamp,
      accuracy: accuracy,
      altitude: 0,
      altitudeAccuracy: 0,
      heading: 0,
      headingAccuracy: 0,
      speed: 0,
      speedAccuracy: 0,
    );
