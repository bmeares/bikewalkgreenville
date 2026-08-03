import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:bwg_app_native/widgets/elevation_profile.dart';

void main() {
  // Regression: the route preview hosts this widget inside a shrink-wrapping
  // Column, which hands its children UNBOUNDED height. A stretch-aligned Row
  // at the widget's root asked for infinite height there, the layout threw,
  // and the whole bottom overlay (preview, Start button, FABs) vanished.
  testWidgets('renders inside an unbounded-height column', (tester) async {
    final profile = [
      for (var i = 0; i < 40; i++) [i * 100.0, 900.0 + (i % 7) * 12.0],
    ];
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Column(
            mainAxisSize: MainAxisSize.min,
            children: [ElevationProfile(profile: profile)],
          ),
        ),
      ),
    );
    expect(tester.takeException(), isNull);
    expect(find.byType(ElevationProfile), findsOneWidget);
    expect(find.textContaining('ft'), findsWidgets);
  });

  testWidgets('a flat two-point profile still renders', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              ElevationProfile(profile: const [
                [0.0, 900.0],
                [1000.0, 900.0],
              ]),
            ],
          ),
        ),
      ),
    );
    expect(tester.takeException(), isNull);
  });
}
