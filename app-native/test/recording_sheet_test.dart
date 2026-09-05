import 'dart:convert';

import 'package:bwg_app_native/rides.dart';
import 'package:bwg_app_native/widgets/recording_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

void main() {
  testWidgets('recovery controls fit a narrow phone at large text sizes', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(320, 568);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final ride = Ride(
      id: 'draft',
      name: 'Ride',
      startedAt: DateTime.utc(2026),
      endedAt: DateTime.utc(2026, 1, 1, 1),
      points: const [LatLng(34.85, -82.4), LatLng(34.851, -82.4)],
    );
    SharedPreferences.setMockInitialValues({
      'ride_in_progress': jsonEncode(ride.toJson()),
    });
    final recorder = RideRecorder();
    await tester.runAsync(recorder.load);
    var resumed = false;
    final semantics = tester.ensureSemantics();
    await tester.pumpWidget(
      ChangeNotifierProvider.value(
        value: recorder,
        child: MaterialApp(
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(
              context,
            ).copyWith(textScaler: const TextScaler.linear(3)),
            child: child!,
          ),
          home: Scaffold(
            body: RecordingSheet(
              onResume: () async {
                resumed = true;
              },
              onPause: () async {},
              onSaved: (_) {},
              onDiscarded: () {},
            ),
          ),
        ),
      ),
    );
    expect(find.text('Recovered ride'), findsOneWidget);
    expect(tester.takeException(), isNull);
    await tester.ensureVisible(find.text('Resume'));
    await tester.tap(find.text('Resume'));
    expect(resumed, isTrue);
    await tester.pump();
    expect(tester.takeException(), isNull);
    await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
    semantics.dispose();
    await tester.pumpWidget(const SizedBox());
    await tester.runAsync(() async {
      await recorder.discard();
      recorder.dispose();
    });
  });
}
