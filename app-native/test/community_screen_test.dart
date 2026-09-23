import 'package:bwg_app_native/api.dart';
import 'package:bwg_app_native/auth.dart';
import 'package:bwg_app_native/screens/community_screen.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';

import 'auth_test.dart' show MemoryStore;

class _HistoryApi extends Api {
  bool admin = false;
  @override
  Future<List<Map<String, dynamic>>> communityHistory() async => [
    {
      'id': 'a',
      'type': 'add',
      'name': 'My rack',
      'active': true,
      'mine': true,
      'ts': '2',
    },
    {
      'id': 'b',
      'type': 'add',
      'name': 'Their rack',
      'active': true,
      'mine': false,
      'ts': '1',
    },
  ];
  @override
  Future<List<Map<String, dynamic>>> walkAuditHistory() async => [];
  @override
  Future<Map<String, dynamic>> me() async => {
    'email': 'r@example.com',
    'is_admin': admin,
    'settings': {},
  };
  @override
  Future<List<Map<String, dynamic>>> savedRoutes() async => [];
}

void main() {
  test('geometryLatLngs flattens point, line and polygon', () {
    expect(
      geometryLatLngs({
        'type': 'Point',
        'coordinates': [-82.4, 34.85],
      }).single.latitude,
      34.85,
    );
    expect(
      geometryLatLngs({
        'type': 'LineString',
        'coordinates': [
          [-82.4, 34.85],
          [-82.39, 34.86],
        ],
      }).length,
      2,
    );
    expect(
      geometryLatLngs({
        'type': 'Polygon',
        'coordinates': [
          [
            [-82.4, 34.85],
            [-82.39, 34.85],
            [-82.39, 34.86],
            [-82.4, 34.85],
          ],
        ],
      }).length,
      4,
    );
    expect(geometryLatLngs(null), isEmpty);
    expect(geometryLatLngs({'type': 'Point', 'coordinates': []}), isEmpty);
  });

  for (final admin in [false, true]) {
    testWidgets(
      'undo shows on ${admin ? 'every row for admins' : 'only my rows'}',
      (tester) async {
        api = _HistoryApi()..admin = admin;
        final account = AuthState(
          store: MemoryStore(
            '{"token":"t","email":"r@example.com","is_admin":$admin}',
          ),
        );
        await account.load();
        await tester.pumpWidget(
          ChangeNotifierProvider.value(
            value: account,
            child: const MaterialApp(home: CommunityScreen()),
          ),
        );
        await tester.pumpAndSettle();
        expect(find.text('Their rack'), findsOneWidget);
        expect(
          find.byTooltip('Roll back contribution'),
          findsNWidgets(admin ? 2 : 1),
        );
      },
    );
  }
}
