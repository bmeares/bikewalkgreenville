// Accessibility: tap targets, labels and text contrast on every screen and
// sheet that pumps without a live map, in light, dark and high contrast; the
// text-scale overflow checks; the WCAG ratios of the palette. See
// docs/wiki/ACCESSIBILITY.md for the manual screen-reader checklist.
import 'dart:async';
import 'dart:math' as math;

import 'package:bwg_app_native/api.dart';
import 'package:bwg_app_native/app_state.dart';
import 'package:bwg_app_native/auth.dart';
import 'package:bwg_app_native/geometry_draft.dart';
import 'package:bwg_app_native/group_ride.dart';
import 'package:bwg_app_native/rides.dart';
import 'package:bwg_app_native/screens/area_draw_sheet.dart';
import 'package:bwg_app_native/screens/group_ride_sheet.dart';
import 'package:bwg_app_native/screens/map_screen.dart';
import 'package:bwg_app_native/screens/route_draw_sheet.dart';
import 'package:bwg_app_native/screens/settings_screen.dart';
import 'package:bwg_app_native/screens/tools_screen.dart';
import 'package:bwg_app_native/theme.dart';
import 'package:bwg_app_native/widgets/map_cards.dart';
import 'package:bwg_app_native/widgets/recording_sheet.dart';
import 'package:bwg_app_native/widgets/ride_summary_sheet.dart';
import 'package:bwg_app_native/widgets/safety_notice.dart';
import 'package:bwg_app_native/widgets/sign_in_sheet.dart';
import 'package:bwg_app_native/widgets/welcome_tour.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeApi extends Api {
  @override
  Future<List<Map<String, dynamic>>> events({int days = 60}) async => [
    {
      'uid': 'e1',
      'start': '2099-10-04T13:30:00Z',
      'title': 'Saturday Social Ride',
      'location': 'Falls Park',
    },
  ];

  @override
  Future<List<Map<String, dynamic>>> nearbyGroupRides(
    double lat,
    double lon,
  ) async => [
    {
      'code': 'HJKM',
      'name': 'Saturday Roll',
      'leader_name': 'Alex',
      'distance_m': 91,
      'members': 3,
    },
  ];

  @override
  Future<Map<String, dynamic>> createGroupRide(
    String name,
    String riderName,
    double lat,
    double lon,
  ) async => {
    'code': 'ABCD',
    'ride_id': 'r1',
    'member_id': 'm1',
    'member_token': 'tok',
    'share_url': 'https://bwg.mrsm.io/bwg-app/?ride=ABCD',
  };

  @override
  Future<void> requestCode(String email) async {}

  @override
  Future<Map<String, dynamic>> vote(String id, bool up) async => {
    'up': 1,
    'down': 0,
    'mine': 'up',
  };
}

class _NoTimer implements Timer {
  @override
  void cancel() {}
  @override
  bool get isActive => false;
  @override
  int get tick => 0;
}

const _here = LatLng(34.85, -82.4);

final _ride = Ride(
  id: 'live',
  name: 'Morning ride',
  startedAt: DateTime(2026, 9, 23, 8),
  endedAt: DateTime(2026, 9, 23, 9),
  points: const [LatLng(34.85, -82.4), LatLng(34.86, -82.4)],
  activeDuration: const Duration(minutes: 42),
);

// ------------------------------------------------------------ WCAG helpers

double _luminance(Color c) {
  double ch(double v) =>
      v <= 0.04045 ? v / 12.92 : math.pow((v + 0.055) / 1.055, 2.4).toDouble();
  return 0.2126 * ch(c.r) + 0.7152 * ch(c.g) + 0.0722 * ch(c.b);
}

double contrast(Color a, Color b) {
  final la = _luminance(a), lb = _luminance(b);
  return (math.max(la, lb) + 0.05) / (math.min(la, lb) + 0.05);
}

/// OpenFreeMap basemap backgrounds (liberty land, dark background).
const _lightBase = Color(0xFFF8F4F0);
const _darkBase = Color(0xFF0E0E0E);

// ------------------------------------------------------------------ hosts

enum _Look { light, dark, lightHc, darkHc }

ThemeData _theme(_Look look) => switch (look) {
  _Look.light => buildTheme(),
  _Look.dark => buildDarkTheme(),
  _Look.lightHc => buildTheme(highContrast: true),
  _Look.darkHc => buildDarkTheme(highContrast: true),
};

late AppState _state;

Widget _host(
  Widget child, {
  _Look look = _Look.light,
  double scale = 1.0,
  List<ChangeNotifierProvider> extra = const [],
  bool scaffold = true,
}) => MultiProvider(
  providers: [
    ChangeNotifierProvider<AppState>.value(value: _state),
    ChangeNotifierProvider<AuthState>.value(value: auth),
    ...extra,
  ],
  child: MaterialApp(
    theme: _theme(look),
    builder: (context, c) => MediaQuery(
      data: MediaQuery.of(
        context,
      ).copyWith(textScaler: TextScaler.linear(scale)),
      child: c!,
    ),
    home: scaffold
        ? Scaffold(body: SingleChildScrollView(child: child))
        : child,
  ),
);

/// A 360 × 640 dp phone, the narrowest common Android width.
void _phone(WidgetTester tester) {
  tester.view.physicalSize = const Size(360, 640);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

Future<void> _meetsAll(WidgetTester tester, {bool contrast = true}) async {
  await expectLater(tester, meetsGuideline(androidTapTargetGuideline));
  await expectLater(tester, meetsGuideline(iOSTapTargetGuideline));
  await expectLater(tester, meetsGuideline(labeledTapTargetGuideline));
  if (contrast) {
    await expectLater(tester, meetsGuideline(textContrastGuideline));
  }
}

GroupRideClient _groupClient() => GroupRideClient(
  positions: () => const Stream.empty(),
  timer: (_, _) => _NoTimer(),
  observeLifecycle: false,
);

RoutePreviewCard _routeCard(Color color) => RoutePreviewCard(
  color: color,
  icon: Icons.directions_bike,
  title: '3.4 mi · 21 min',
  subtitle: 'Quiet streets · alternate route 2',
  onSave: () {},
  onShare: () {},
  onClear: () {},
  onSteps: () {},
  onStart: () {},
);

PlaceCard _placeCard() => PlaceCard(
  label: 'Swamp Rabbit Cafe & Grocery',
  sublabel: '205 Cedar Lane Rd, Greenville',
  verb: 'Navigate here',
  modeIcon: Icons.directions_bike,
  saved: false,
  onNavigate: () {},
  onToggleSaved: () {},
  onPlan: () {},
  onClose: () {},
);

void main() {
  setUp(() async {
    api = _FakeApi();
    AuthGate.require = (_) async => true;
    SharedPreferences.setMockInitialValues({});
    _state = AppState();
    await _state.load();
  });

  group('palette contrast (WCAG 2.1)', () {
    test('white text on filled brand surfaces is at least 4.5:1', () {
      for (final bg in [
        brandGreenStrong,
        brandDark,
        const Color(0xFF1565C0), // route blue
        const Color(0xFF7B1FA2), // transit / ride purple
        hexColor(bcycleRed),
        const Color(0xFFC62828), // record red
        const Color(0xFFD32F2F), // End navigation
        groupRideBadge,
        const Color(0xFF13322A), // nav card
      ]) {
        expect(
          contrast(Colors.white, bg),
          greaterThanOrEqualTo(4.5),
          reason: '$bg',
        );
      }
      // Why brandGreenStrong exists: the brand green itself fails for text.
      expect(contrast(Colors.white, brandGreen), lessThan(4.5));
    });

    test('icons and line work are at least 3:1 where they are drawn', () {
      final white = Colors.white;
      // Light surfaces / light basemap.
      for (final c in [
        brandGreen,
        const Color(0xFFC62828),
        groupRideColor,
        groupLeaderColor,
        hexColor(rideLineHex),
        const Color(0xFF1565C0),
      ]) {
        expect(
          contrast(c, white),
          greaterThanOrEqualTo(3),
          reason: '$c on white',
        );
        expect(
          contrast(c, _lightBase),
          greaterThanOrEqualTo(3),
          reason: '$c on basemap',
        );
      }
      // Dark surfaces / dark basemap: the ride line swaps to the orchid.
      for (final c in [
        brandGreen,
        const Color(0xFFC62828),
        groupRideColor,
        groupLeaderColor,
        hexColor(rideLineOnDarkHex),
        const Color(0xFFFFC107), // highlight amber
      ]) {
        expect(
          contrast(c, _darkBase),
          greaterThanOrEqualTo(3),
          reason: '$c on dark',
        );
      }
      expect(contrast(hexColor(rideLineHex), _darkBase), lessThan(3));
      // High contrast swaps the amber highlight for deep orange on light.
      expect(contrast(const Color(0xFFFFC107), _lightBase), lessThan(3));
      expect(
        contrast(const Color(0xFFE65100), _lightBase),
        greaterThanOrEqualTo(3),
      );
    });

    test('theme error and brand text colors read on both themes', () {
      for (final look in _Look.values) {
        final t = _theme(look);
        final surface = t.colorScheme.surface;
        expect(
          contrast(t.colorScheme.error, surface),
          greaterThanOrEqualTo(4.5),
          reason: '$look',
        );
        expect(
          contrast(t.colorScheme.onSurfaceVariant, surface),
          greaterThanOrEqualTo(4.5),
          reason: '$look',
        );
      }
      expect(contrast(brandDark, Colors.white), greaterThanOrEqualTo(4.5));
    });
  });

  group('guidelines', () {
    for (final look in _Look.values) {
      testWidgets('menu (tools screen) — $look', (tester) async {
        final handle = tester.ensureSemantics();
        await tester.pumpWidget(
          _host(const ToolsScreen(), look: look, scaffold: false),
        );
        await tester.pumpAndSettle();
        await _meetsAll(tester);
        handle.dispose();
      });

      testWidgets('settings — $look', (tester) async {
        final handle = tester.ensureSemantics();
        await tester.pumpWidget(
          _host(const SettingsScreen(), look: look, scaffold: false),
        );
        await tester.pumpAndSettle();
        await _meetsAll(tester);
        handle.dispose();
      });
    }

    testWidgets('sign-in sheet, both steps', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(const SignInSheet()));
      await _meetsAll(tester);
      await tester.enterText(
        find.byKey(const ValueKey('sign-in-email')),
        'a@b.org',
      );
      await tester.tap(find.text('Send code'));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('sign-in-code')), findsOneWidget);
      await _meetsAll(tester);
      await tester.pumpWidget(const SizedBox()); // stop the resend timer
      handle.dispose();
    });

    testWidgets('ride summary, before and after save', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(
        _host(
          RideSummarySheet(
            ride: _ride,
            onSave: (name) async => (ride: _ride, error: null),
            onDiscard: () async => true,
            onExport: (_) async {},
          ),
        ),
      );
      await _meetsAll(tester);
      await tester.tap(find.text('Save'));
      await tester.pumpAndSettle();
      await _meetsAll(tester);
      handle.dispose();
    });

    testWidgets('recording sheet', (tester) async {
      final handle = tester.ensureSemantics();
      final recorder = RideRecorder();
      await tester.pumpWidget(
        _host(
          RecordingSheet(
            onResume: () async {},
            onPause: () async {},
            onStop: () {},
          ),
          extra: [ChangeNotifierProvider<RideRecorder>.value(value: recorder)],
        ),
      );
      await _meetsAll(tester);
      // The state title is a live region: TalkBack announces each change.
      final title = tester.getSemantics(find.text('Recording'));
      expect(title.flagsCollection.isLiveRegion, isTrue);
      handle.dispose();
    });

    testWidgets('group ride sheet: idle and active', (tester) async {
      final handle = tester.ensureSemantics();
      final client = _groupClient();
      await tester.pumpWidget(
        _host(
          GroupRideSheet(locate: () async => _here),
          extra: [ChangeNotifierProvider<GroupRideClient>.value(value: client)],
        ),
      );
      await tester.pumpAndSettle();
      expect(find.text('Saturday Roll'), findsOneWidget);
      await _meetsAll(tester);
      // The code field is a full-size target, not a squeezed inline box.
      expect(
        tester.getSize(find.byKey(const ValueKey('group-code'))).height,
        greaterThanOrEqualTo(48),
      );

      await client.create('Community Roll', 'Alex', _here);
      await tester.pumpAndSettle();
      expect(find.text('End ride'), findsOneWidget);
      await _meetsAll(tester);
      expect(
        find.bySemanticsLabel(RegExp('Ride code A B C D')),
        findsOneWidget,
      );
      await tester.pumpWidget(const SizedBox());
      client.dispose();
      handle.dispose();
    });

    testWidgets('welcome tour', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(
        _host(const Dialog.fullscreen(child: WelcomeTour()), scaffold: false),
      );
      await _meetsAll(tester);
      expect(
        find.bySemanticsLabel('Page 1 of 4: Map & layers'),
        findsOneWidget,
      );
      handle.dispose();
    });

    testWidgets('disclaimer agree sheet and compact line', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(
        _host(
          Builder(
            builder: (ctx) => TextButton(
              onPressed: () => confirmRouteSafety(ctx),
              child: const Text('go'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('go'));
      await tester.pumpAndSettle();
      expect(find.text(disclaimerTitle), findsOneWidget);
      // The disabled I Agree is not a tap target; contrast is covered by the
      // theme tests (a disabled label is exempt from WCAG contrast).
      await _meetsAll(tester, contrast: false);
      handle.dispose();
    });

    testWidgets('draw bars and vote buttons', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(
        _host(
          Column(
            children: [
              RouteDrawBar(
                draft: RouteDraft(),
                straight: false,
                busy: false,
                onUndo: () {},
                onStraight: (_) {},
                onPublish: () {},
                onCancel: () {},
              ),
              AreaDrawBar(
                draft: AreaDraft(corners: const [_here]),
                onUndo: () {},
                onPublish: () {},
                onCancel: () {},
              ),
              const VoteButtons(id: 'x', up: 2, down: 1),
            ],
          ),
        ),
      );
      await _meetsAll(tester, contrast: false);
      // Vote nodes keep their tap action even with excludeSemantics.
      final up = tester.getSemantics(find.byKey(const ValueKey('vote-up')));
      expect(up.getSemanticsData().hasAction(SemanticsAction.tap), isTrue);
      handle.dispose();
    });

    for (final color in [
      const Color(0xFF1565C0),
      const Color(0xFF7B1FA2),
      hexColor(bcycleRed),
    ]) {
      testWidgets('route preview card on $color', (tester) async {
        final handle = tester.ensureSemantics();
        await tester.pumpWidget(
          _host(
            Padding(
              padding: const EdgeInsets.all(12),
              child: _routeCard(color),
            ),
          ),
        );
        await _meetsAll(tester);
        handle.dispose();
      });
    }

    testWidgets('place card', (tester) async {
      final handle = tester.ensureSemantics();
      await tester.pumpWidget(_host(_placeCard()));
      await _meetsAll(tester);
      handle.dispose();
    });
  });

  group('text scaling on a 360 dp phone', () {
    for (final scale in [1.0, 1.3, 2.0]) {
      testWidgets('route preview + place card at ${scale}x', (tester) async {
        _phone(tester);
        await tester.pumpWidget(
          _host(
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 12),
              child: Column(
                children: [_routeCard(const Color(0xFF1565C0)), _placeCard()],
              ),
            ),
            scale: scale,
          ),
        );
        expect(tester.takeException(), isNull);
        // Primary actions are whole, never ellipsized.
        expect(find.text('Start'), findsOneWidget);
        expect(find.text('Navigate here'), findsOneWidget);
        final start = tester.renderObject<RenderParagraph>(find.text('Start'));
        expect(start.didExceedMaxLines, isFalse);
      });

      testWidgets('sign-in sheet at ${scale}x', (tester) async {
        _phone(tester);
        await tester.pumpWidget(_host(const SignInSheet(), scale: scale));
        await tester.enterText(
          find.byKey(const ValueKey('sign-in-email')),
          'a@b.org',
        );
        await tester.ensureVisible(find.text('Send code'));
        await tester.pumpAndSettle();
        await tester.tap(find.text('Send code'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.text('Change email'), findsOneWidget);
        await tester.pumpWidget(const SizedBox());
      });

      testWidgets('ride summary at ${scale}x', (tester) async {
        _phone(tester);
        await tester.pumpWidget(
          _host(
            RideSummarySheet(
              ride: _ride,
              onSave: (name) async => (ride: _ride, error: null),
              onDiscard: () async => true,
              onExport: (_) async {},
            ),
            scale: scale,
          ),
        );
        expect(tester.takeException(), isNull);
        await tester.ensureVisible(find.text('Save'));
        await tester.tap(find.text('Save'));
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        expect(find.text('Export GPX'), findsOneWidget);
      });

      testWidgets('group ride sheet at ${scale}x', (tester) async {
        _phone(tester);
        final client = _groupClient();
        await tester.pumpWidget(
          _host(
            GroupRideSheet(locate: () async => _here),
            scale: scale,
            extra: [
              ChangeNotifierProvider<GroupRideClient>.value(value: client),
            ],
          ),
        );
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await client.create('Community Roll', 'Alex', _here);
        await tester.pumpAndSettle();
        expect(tester.takeException(), isNull);
        await tester.pumpWidget(const SizedBox());
        client.dispose();
      });
    }
  });

  group('keyboard and focus', () {
    testWidgets('sign-in Tab order: email → Send, then code → Verify', (
      tester,
    ) async {
      await tester.pumpWidget(_host(const SignInSheet()));
      await tester.tap(find.byKey(const ValueKey('sign-in-email')));
      await tester.enterText(
        find.byKey(const ValueKey('sign-in-email')),
        'a@b.org',
      );
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pump();
      expect(
        FocusManager.instance.primaryFocus?.context
            ?.findAncestorWidgetOfExactType<FilledButton>(),
        isNotNull,
      );
      await tester.tap(find.text('Send code'));
      await tester.pumpAndSettle();
      // The code field takes focus when it appears; Tab moves on to Verify.
      final code = tester.widget<EditableText>(
        find.descendant(
          of: find.byKey(const ValueKey('sign-in-code')),
          matching: find.byType(EditableText),
        ),
      );
      expect(code.focusNode.hasFocus, isTrue);
      await tester.sendKeyEvent(LogicalKeyboardKey.tab);
      await tester.pump();
      final focused = FocusManager.instance.primaryFocus?.context;
      expect(focused?.findAncestorWidgetOfExactType<FilledButton>(), isNotNull);
      expect(
        find.descendant(
          of: find.byType(FilledButton),
          matching: find.text('Verify'),
        ),
        findsOneWidget,
      );
      await tester.pumpWidget(const SizedBox());
    });

    testWidgets('Escape closes a sheet', (tester) async {
      await tester.pumpWidget(
        _host(
          Builder(
            builder: (ctx) => TextButton(
              onPressed: () => showDisclaimerSheet(ctx),
              child: const Text('open'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('open'));
      await tester.pumpAndSettle();
      expect(find.text(disclaimerTitle), findsOneWidget);
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pumpAndSettle();
      expect(find.text(disclaimerTitle), findsNothing);
    });

    testWidgets('welcome tour: Escape skips; reduce motion jumps pages', (
      tester,
    ) async {
      bool? result;
      await tester.pumpWidget(
        MaterialApp(
          // Above the Navigator, so the dialog route sees it too.
          builder: (context, child) => MediaQuery(
            data: MediaQuery.of(context).copyWith(disableAnimations: true),
            child: child!,
          ),
          home: Builder(
            builder: (ctx) => TextButton(
              onPressed: () async => result = await showDialog<bool>(
                context: ctx,
                barrierDismissible: false,
                builder: (_) => const Dialog.fullscreen(child: WelcomeTour()),
              ),
              child: const Text('tour'),
            ),
          ),
        ),
      );
      await tester.tap(find.text('tour'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Next'));
      await tester.pump(); // one frame: no slide animation to settle
      expect(find.text('Navigate'), findsOneWidget);
      await tester.sendKeyEvent(LogicalKeyboardKey.escape);
      await tester.pumpAndSettle();
      expect(find.byType(WelcomeTour), findsNothing);
      expect(result, isTrue);
    });
  });
}
