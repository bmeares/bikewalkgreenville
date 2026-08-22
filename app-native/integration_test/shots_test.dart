// Screenshot tour for App Store captures. Run via ~/asc-scripts/ios_shots.zsh.
import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:bwg_app_native/main.dart' as app;

Future<void> park(WidgetTester t, int seconds) async {
  for (var i = 0; i < seconds * 2; i++) {
    await t.pump(const Duration(milliseconds: 100));
    await Future<void>.delayed(const Duration(milliseconds: 400));
  }
}

void main() {
  final binding = IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  binding.framePolicy = LiveTestWidgetsFlutterBindingFramePolicy.fullyLive;

  testWidgets('screenshot tour', (t) async {
    app.main();
    await t.pumpAndSettle(const Duration(seconds: 3));
    await park(t, 20);
    debugPrint('SHOT map');
    await park(t, 8);

    if (find.text('Walk').evaluate().isNotEmpty) {
      await t.tap(find.text('Walk'));
      await park(t, 12);
      debugPrint('SHOT walk');
      await park(t, 8);
    }

    if (find.byIcon(Icons.menu).evaluate().isNotEmpty) {
      await t.tap(find.byIcon(Icons.menu));
      await park(t, 8);
      debugPrint('SHOT tools');
      await park(t, 8);
      if (find.byTooltip('Back').evaluate().isNotEmpty) {
        await t.tap(find.byTooltip('Back'));
        await park(t, 4);
      }
    }

    if (find.text('Report').evaluate().isNotEmpty) {
      await t.tap(find.text('Report').first);
      await park(t, 6);
      debugPrint('SHOT report');
      await park(t, 8);
    }

    debugPrint('SHOT-DONE');
  }, timeout: const Timeout(Duration(minutes: 10)));
}
