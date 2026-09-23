import 'package:bwg_app_native/api.dart';
import 'package:bwg_app_native/auth.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

class MemoryStore implements AuthStore {
  MemoryStore([this.value]);
  String? value;
  @override
  Future<String?> read() async => value;
  @override
  Future<void> write(String? v) async => value = v;
}

class FakeAuthApi extends Api {
  bool meUnauthorized = false;
  bool admin = false;
  final codesRequested = <String>[];
  final puts = <Map<String, dynamic>>[];

  @override
  Future<void> requestCode(String email) async => codesRequested.add(email);

  @override
  Future<Map<String, dynamic>> verifyCode(String email, String code) async {
    if (code != '123456') throw ApiError('That code is not right.', 401);
    return {
      'token': 'tok-new',
      'email': email,
      'display_name': null,
      'is_admin': false,
    };
  }

  @override
  Future<Map<String, dynamic>> me() async {
    if (meUnauthorized) {
      // What the Dio interceptor does on a 401 sent with a token.
      bearerToken = null;
      onUnauthorized?.call();
      throw AuthExpired();
    }
    return {
      'email': 'rider@example.com',
      'display_name': 'Rider',
      'is_admin': admin,
      'settings': {},
    };
  }

  @override
  Future<Map<String, dynamic>> updateMe(Map<String, dynamic> body) async {
    puts.add(body);
    return me();
  }

  @override
  Future<List<Map<String, dynamic>>> savedRoutes() async => [];
}

const _stored =
    '{"token":"tok-old","email":"rider@example.com","display_name":null,"is_admin":false}';

void main() {
  late FakeAuthApi fake;
  setUp(() {
    fake = FakeAuthApi();
    api = fake;
  });

  test(
    'load: a stored token becomes the Bearer and /me refreshes the profile',
    () async {
      fake.admin = true;
      final state = AuthState(store: MemoryStore(_stored));
      await state.load();
      expect(state.signedIn, isTrue);
      expect(api.bearerToken, 'tok-old');
      expect(state.isAdmin, isTrue);
      expect(state.displayName, 'Rider');
    },
  );

  test('load: a 401 from /me signs out silently and clears storage', () async {
    fake.meUnauthorized = true;
    final store = MemoryStore(_stored);
    final state = AuthState(store: store);
    await state.load();
    await pumpEventQueue();
    expect(state.signedIn, isFalse);
    expect(api.bearerToken, isNull);
    expect(store.value, isNull);
  });

  test('load: unreadable storage falls back to signed out', () async {
    final state = AuthState(store: MemoryStore('not json'));
    await state.load();
    expect(state.signedIn, isFalse);
  });

  testWidgets('sign-in sheet: send code, verify, gate completes', (
    tester,
  ) async {
    auth = AuthState(store: MemoryStore());
    bool? result;
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: Builder(
            builder: (context) => TextButton(
              onPressed: () async => result = await AuthGate.require(context),
              child: const Text('gate'),
            ),
          ),
        ),
      ),
    );
    await tester.tap(find.text('gate'));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('sign-in-email')),
      'Rider@Example.com',
    );
    await tester.tap(find.text('Send code'));
    await tester.pumpAndSettle();
    expect(fake.codesRequested, ['rider@example.com']);
    expect(find.text('Resend in 30 s'), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey('sign-in-code')),
      '000000',
    );
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();
    expect(find.text('That code is wrong or expired.'), findsOneWidget);

    await tester.enterText(
      find.byKey(const ValueKey('sign-in-code')),
      '123456',
    );
    await tester.tap(find.text('Verify'));
    await tester.pumpAndSettle();
    expect(result, isTrue);
    expect(auth.signedIn, isTrue);
    expect(api.bearerToken, 'tok-new');
    expect(find.text('Verify'), findsNothing);
  });

  testWidgets('gate returns false when the sheet is dismissed', (tester) async {
    auth = AuthState(store: MemoryStore());
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: Text('map'))),
    );
    bool? result;
    AuthGate.require(tester.element(find.text('map'))).then((v) => result = v);
    await tester.pumpAndSettle();
    expect(find.text('Send code'), findsOneWidget);
    await tester.tapAt(const Offset(5, 5)); // the modal barrier
    await tester.pumpAndSettle();
    expect(result, isFalse);
    expect(auth.signedIn, isFalse);
  });

  testWidgets('withAuth re-prompts once after a dead token', (tester) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: Text('map'))),
    );
    var prompts = 0, calls = 0;
    AuthGate.require = (_) async => ++prompts > 0;
    final result = await withAuth(tester.element(find.text('map')), () async {
      if (++calls == 1) throw AuthExpired();
      return 'ok';
    });
    expect(result, 'ok');
    expect((prompts, calls), (2, 2));
  });

  test('held and photo replies change the thank-you', () {
    expect(
      submittedMessage({'status': 'published'}, published: 'Live.'),
      'Live.',
    );
    expect(
      submittedMessage({
        'status': 'held',
        'photo_status': 'pending',
      }, published: 'Live.'),
      'Thanks — your submission is awaiting review before it appears. '
      'Photos appear after BWG approves them.',
    );
  });
}
