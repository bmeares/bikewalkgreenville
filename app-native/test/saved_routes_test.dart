import 'package:bwg_app_native/app_state.dart';
import 'package:bwg_app_native/theme.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

Future<AppState> _loaded(Map<String, Object> prefs) async {
  SharedPreferences.setMockInitialValues(prefs);
  final state = AppState();
  await state.load();
  return state;
}

void main() {
  test(
    'sign-in merge: union of places, remote scalars only over defaults',
    () async {
      final state = await _loaded({
        'saved_places': [
          '{"label":"Home","sublabel":"","lat":34.85,"lon":-82.4}',
        ],
        'stress': 'quiet', // chosen on this device: must survive
        'welcome_seen': true,
      });
      state.mergeRemoteSettings({
        'saved_places': [
          {
            'label': 'Casa',
            'sublabel': '',
            'lat': 34.85,
            'lon': -82.4,
          }, // same place
          {'label': 'Work', 'sublabel': 'Main St', 'lat': 34.86, 'lon': -82.39},
          {'label': 'Broken'}, // no coordinates: ignored
        ],
        'stress': 'direct',
        'prefer_trail': false,
        'modes': ['pedestrian'],
        'ebike': true,
        'advocacy_layers': ['ownership'],
        'disclaimer_accepted_version': 2,
      });
      expect(state.savedPlaces.map((p) => p['label']), ['Home', 'Work']);
      expect(state.stress, BikeStress.quiet);
      expect(state.preferTrail, isFalse);
      expect(state.modes.contains(TravelMode.pedestrian), isTrue);
      expect(state.useEbike, isTrue);
      expect(state.advocacyEnabled('ownership'), isTrue);
      expect(state.disclaimerAcceptedVersion, 2);
    },
  );

  test('later refreshes: the account list wins (no resurrection)', () async {
    final state = await _loaded({
      'saved_places': [
        '{"label":"Home","sublabel":"","lat":34.85,"lon":-82.4}',
        '{"label":"Deleted elsewhere","sublabel":"","lat":34.9,"lon":-82.3}',
      ],
    });
    state.mergeRemoteSettings({
      'saved_places': [
        {'label': 'Home', 'sublabel': '', 'lat': 34.85, 'lon': -82.4},
      ],
    }, union: false);
    expect(state.savedPlaces.map((p) => p['label']), ['Home']);
  });

  test('my votes follow each vote reply and survive a restart', () async {
    final state = await _loaded({});
    state.setMyVote('c1', 'up');
    state.setMyVote('c2', 'down');
    state.setMyVote('c1', null); // vote withdrawn
    await pumpEventQueue();
    final again = AppState();
    await again.load();
    expect(again.myVote('c1'), isNull);
    expect(again.myVote('c2'), 'down');
  });

  test('a local disclaimer newer than the account is kept', () async {
    final state = await _loaded({'disclaimer_accepted_version': 3});
    state.mergeRemoteSettings({'disclaimer_accepted_version': 2});
    expect(state.disclaimerAcceptedVersion, 3);
  });

  test('synced blob carries preferences, not device-only state', () async {
    final state = await _loaded({'welcome_seen': true});
    final blob = state.syncedSettings;
    expect(
      blob.keys,
      containsAll([
        'saved_places',
        'stress',
        'modes',
        'prefer_trail',
        'prefer_community',
        'advocacy_layers',
        'disclaimer_accepted_version',
      ]),
    );
    expect(blob.keys, isNot(contains('voter_id')));
    expect(blob.keys, isNot(contains('welcome_seen')));
    expect(blob.keys, isNot(contains('notified_event_uids')));
  });
}
