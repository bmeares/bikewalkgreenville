import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';
import 'package:bwg_app_native/app_state.dart';
import 'package:bwg_app_native/widgets/travel_modes.dart';
import 'package:bwg_app_native/theme.dart';

void main() {
  testWidgets('mode selection and variants are independent and fit a phone', (tester) async {
    SharedPreferences.setMockInitialValues({});
    tester.view.physicalSize = const Size(390, 844);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);
    final state = AppState();
    await tester.pumpWidget(ChangeNotifierProvider.value(value: state,
      child: const MaterialApp(home: Scaffold(body: TravelModes()))));
    await tester.tap(find.byTooltip('Choose Bike or E-bike'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('E-bike'));
    await tester.pumpAndSettle();
    expect(state.useEbike, isTrue);
    expect(state.modes, {TravelMode.cyclist});
    await tester.tap(find.text('Walk'));
    await tester.pumpAndSettle();
    expect(state.modes, {TravelMode.cyclist, TravelMode.pedestrian});
    await tester.tap(find.byTooltip('Choose Walk or Roll'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Roll / wheelchair'));
    await tester.pumpAndSettle();
    expect(state.roll, isTrue);
    expect(state.useEbike, isTrue);
    expect(tester.takeException(), isNull);
  });
}
