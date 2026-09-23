import 'package:bwg_app_native/api.dart';
import 'package:bwg_app_native/app_state.dart';
import 'package:bwg_app_native/auth.dart';
import 'package:bwg_app_native/screens/map_screen.dart';
import 'package:bwg_app_native/theme.dart';
import 'package:bwg_app_native/widgets/events_card.dart';
import 'package:bwg_app_native/widgets/safety_notice.dart';
import 'package:bwg_app_native/widgets/welcome_tour.dart';
import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:shared_preferences/shared_preferences.dart';

class _FakeApi extends Api {
  final votes = <(String, bool)>[];
  List<Map<String, dynamic>> eventList = [];

  @override
  Future<Map<String, dynamic>> vote(String id, bool up) async {
    votes.add((id, up));
    return {'up': up ? 4 : 3, 'down': up ? 0 : 1, 'mine': up ? 'up' : 'down'};
  }

  @override
  Future<List<Map<String, dynamic>>> events({int days = 60}) async =>
      eventList;
}

Future<AppState> _loadedState([Map<String, Object> prefs = const {}]) async {
  SharedPreferences.setMockInitialValues(prefs);
  final state = AppState();
  await state.load();
  return state;
}

Widget _host(AppState state, Widget Function(BuildContext) body) =>
    ChangeNotifierProvider.value(
      value: state,
      child: MaterialApp(
        home: Scaffold(body: Builder(builder: body)),
      ),
    );

void main() {
  late _FakeApi fake;
  setUp(() {
    fake = _FakeApi();
    api = fake;
    AuthGate.require = (_) async => true;
  });

  test('every layer belongs to a layers-sheet group', () {
    for (final d in layerDefs.where((d) => !d.fixed)) {
      expect(layerGroupLabels[d.group], isNotNull, reason: d.id);
    }
    expect(layerGroupLabels.keys.toSet(), LayerGroup.values.toSet());
  });

  testWidgets('disclaimer: version < 2 shows the agree sheet, then skips',
      (tester) async {
    final state = await _loadedState({'disclaimer_accepted_version': 1});
    bool? result;
    await tester.pumpWidget(_host(state, (ctx) => TextButton(
          onPressed: () async => result = await confirmRouteSafety(ctx),
          child: const Text('go'),
        )));
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    expect(find.text(disclaimerTitle), findsOneWidget);
    // I Agree stays disabled until the box is checked.
    await tester.ensureVisible(find.text('I Agree'));
    await tester.pumpAndSettle();
    expect(
      tester.widget<FilledButton>(find.byType(FilledButton)).onPressed,
      isNull,
    );
    await tester.tap(find.text('I Agree'));
    await tester.pumpAndSettle();
    expect(result, isNull);
    await tester.tap(find.text(disclaimerCheckbox));
    await tester.pump();
    await tester.tap(find.text('I Agree'));
    await tester.pumpAndSettle();
    expect(result, isTrue);
    expect(state.disclaimerAcceptedVersion, disclaimerVersion);
    expect(state.disclaimerAcceptedAt, isNotNull);

    result = null;
    await tester.tap(find.text('go'));
    await tester.pumpAndSettle();
    expect(find.text(disclaimerTitle), findsNothing);
    expect(result, isTrue);
  });

  testWidgets('welcome tour shows once', (tester) async {
    final state = await _loadedState();
    await tester.pumpWidget(_host(state, (ctx) => TextButton(
          onPressed: () => maybeShowWelcomeTour(ctx),
          child: const Text('launch'),
        )));
    await tester.tap(find.text('launch'));
    await tester.pumpAndSettle();
    expect(find.text('Map & layers'), findsOneWidget);
    await tester.tap(find.text('Skip'));
    await tester.pumpAndSettle();
    expect(state.welcomeSeen, isTrue);

    await tester.tap(find.text('launch'));
    await tester.pumpAndSettle();
    expect(find.text('Map & layers'), findsNothing);
  });

  testWidgets('events card lists the next 3 events', (tester) async {
    final state = await _loadedState();
    fake.eventList = [
      for (var i = 1; i <= 5; i++)
        {
          'uid': 'e$i',
          'start': '2099-10-0${i}T13:30:00Z',
          'title': 'Ride $i',
          'location': 'Falls Park',
          'all_day': false,
        },
    ];
    await tester.pumpWidget(_host(state, (_) => const SingleChildScrollView(
          child: EventsCard(),
        )));
    await tester.pumpAndSettle();
    expect(find.text('Ride 1'), findsOneWidget);
    expect(find.text('Ride 3'), findsOneWidget);
    expect(find.text('Ride 4'), findsNothing);
    expect(find.text('Open full calendar'), findsOneWidget);
  });

  testWidgets('vote buttons call the API and show the new counts',
      (tester) async {
    final state = await _loadedState();
    String? voted;
    await tester.pumpWidget(_host(state, (_) => VoteButtons(
          id: 'abc',
          up: 3,
          down: 0,
          onVoted: (m) => voted = m,
        )));
    expect(find.bySemanticsLabel('Confirm this exists, 3'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('vote-up')));
    await tester.pumpAndSettle();
    expect(fake.votes, [('abc', true)]);
    expect(voted, 'up');
    expect(find.text('4'), findsOneWidget);

    // Signed out and declined: no request.
    AuthGate.require = (_) async => false;
    await tester.tap(find.byKey(const ValueKey('vote-down')));
    await tester.pumpAndSettle();
    expect(fake.votes.length, 1);
  });

  // The web search bug: a pointer-down outside a focused TextField unfocuses
  // it (desktop + all web). The results dropdown is gated on focus, so it
  // vanished between pointer-down and pointer-up and the tap never fired.
  // TextFieldTapRegion around the dropdown (as in map_screen) fixes it.
  for (final inRegion in [false, true]) {
    testWidgets('result tap survives focus loss (tap region: $inRegion)',
        (tester) async {
      debugDefaultTargetPlatformOverride = TargetPlatform.linux;
      final focus = FocusNode();
      var picked = false;
      await tester.pumpWidget(MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(builder: (ctx, setState) {
            focus.addListener(() => setState(() {}));
            final dropdown = focus.hasFocus
                ? ListTile(
                    title: const Text('Falls Park'),
                    onTap: () => picked = true,
                  )
                : const SizedBox();
            final column = Column(children: [
              TextField(focusNode: focus),
              dropdown,
            ]);
            return inRegion ? TextFieldTapRegion(child: column) : column;
          }),
        ),
      ));
      await tester.tap(find.byType(TextField));
      await tester.pump();
      // Press, let a frame run (a real click takes ~100 ms), then release.
      final g = await tester.startGesture(
        tester.getCenter(find.text('Falls Park')),
      );
      await tester.pump(const Duration(milliseconds: 100));
      await g.up();
      await tester.pump();
      expect(picked, inRegion);
      debugDefaultTargetPlatformOverride = null;
    });
  }
}
