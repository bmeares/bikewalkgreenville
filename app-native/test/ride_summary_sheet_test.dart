import 'package:bwg_app_native/rides.dart';
import 'package:bwg_app_native/widgets/ride_summary_sheet.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

void main() {
  testWidgets('Save turns the summary into Share a stretch / Export GPX', (
    tester,
  ) async {
    final ride = Ride(
      id: 'live',
      name: 'Ride',
      startedAt: DateTime(2026, 9, 23, 8),
      endedAt: DateTime(2026, 9, 23, 9),
      points: const [LatLng(34.85, -82.4), LatLng(34.86, -82.4)],
      activeDuration: const Duration(minutes: 10),
    );
    String? savedName;
    Ride? exported;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: RideSummarySheet(
            ride: ride,
            onSave: (name) async {
              savedName = name;
              return (
                ride: Ride.fromJson({...ride.toJson(), 'name': name}),
                error: null,
              );
            },
            onDiscard: () async => true,
            onExport: (r) async => exported = r,
          ),
        ),
      ),
    );
    expect(find.text('Ride on Sep 23'), findsOneWidget);
    expect(find.text('10 min'), findsOneWidget);
    expect(find.text('Share a stretch'), findsNothing);
    expect(find.text('Export GPX'), findsNothing);
    await tester.enterText(find.byType(TextField), 'Morning loop');
    await tester.tap(find.text('Save'));
    await tester.pumpAndSettle();
    expect(savedName, 'Morning loop');
    expect(find.text('Morning loop'), findsOneWidget);
    expect(find.text('Save'), findsNothing);
    expect(find.text('Share a stretch'), findsOneWidget);
    expect(find.text('Done'), findsOneWidget);
    await tester.tap(find.text('Export GPX'));
    await tester.pump();
    expect(exported?.name, 'Morning loop');
  });

  testWidgets('Share a stretch is disabled while navigating', (tester) async {
    final ride = Ride(
      id: 'saved',
      name: 'Ride',
      startedAt: DateTime(2026, 9, 23, 8),
      endedAt: DateTime(2026, 9, 23, 9),
      points: const [LatLng(34.85, -82.4), LatLng(34.86, -82.4)],
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(body: RideSummarySheet(ride: ride, canShare: false)),
      ),
    );
    final chip = tester.widget<ActionChip>(
      find.widgetWithText(ActionChip, 'Share a stretch'),
    );
    expect(chip.onPressed, isNull);
    expect(find.textContaining('Finish navigating first'), findsOneWidget);
  });
}
