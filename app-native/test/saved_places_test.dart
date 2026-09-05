import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:shared_preferences/shared_preferences.dart';

import 'package:bwg_app_native/app_state.dart';

void main() {
  final home = {'label': 'Home', 'sublabel': 'McHan St', 'lat': 34.83716, 'lon': -82.40463};

  test('save, rename, remove with undo keeps one row per place', () {
    final state = AppState();
    expect(state.savedPlaces, isEmpty);
    expect(state.toggleSaved(home), isNull);
    expect(state.isSaved(home), isTrue);
    state.renameSaved(home, 'Casa');
    expect(state.savedPlaces.single['label'], 'Casa');
    expect(state.isSaved({...home, 'label': 'anything'}), isTrue);
    final removed = state.toggleSaved(home)!;
    expect(state.savedPlaces, isEmpty);
    state.restoreSaved(removed);
    expect(state.savedPlaces.single['label'], 'Casa');
    state.restoreSaved(removed); // undo twice never duplicates
    expect(state.savedPlaces, hasLength(1));
  });

  test('a row without coordinates is never saved', () {
    final state = AppState();
    expect(state.toggleSaved({'label': 'Nowhere'}), isNull);
    expect(state.savedPlaces, isEmpty);
  });

  test('saved places survive a relaunch through preferences', () async {
    SharedPreferences.setMockInitialValues({
      'saved_places': [jsonEncode(home)],
    });
    final restored = AppState();
    await restored.load();
    expect(restored.savedPlaces.single['label'], 'Home');
    expect(restored.isSaved(home), isTrue);

    restored.renameSaved(home, 'Casa');
    await Future<void>.delayed(Duration.zero); // let the async save land
    final prefs = await SharedPreferences.getInstance();
    final written = prefs.getStringList('saved_places')!;
    expect(jsonDecode(written.single)['label'], 'Casa');

    final relaunched = AppState();
    await relaunched.load();
    expect(relaunched.savedPlaces.single['label'], 'Casa');
  });
}
