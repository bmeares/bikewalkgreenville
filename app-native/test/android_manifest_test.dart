import 'dart:io';

import 'package:flutter_test/flutter_test.dart';

/// Turn-by-turn navigation lives or dies by geolocator's position STREAM, and
/// on Android that stream is served by `GeolocatorLocationService`. The plugin
/// binds it at startup; `StreamHandlerImpl.onListen` then bails out with a
/// bare `return` — no `events.error`, no `endOfStream` — if the bind never
/// happened. So a manifest that removes the service does not fail loudly: it
/// makes `getPositionStream` emit nothing, forever, in silence.
///
/// `getCurrentPosition` runs through a different handler and keeps working,
/// which is exactly what made this read as "navigation froze mid-ride" instead
/// of "location is broken" — Start found the rider, and then nothing ever
/// moved again.
void main() {
  final manifest =
      File('android/app/src/main/AndroidManifest.xml').readAsStringSync();

  test('the geolocator location service survives manifest merging', () {
    final removed = RegExp(
      r'<service[^>]*GeolocatorLocationService[^>]*tools:node\s*=\s*"remove"',
    ).hasMatch(manifest);
    expect(removed, isFalse,
        reason: 'Removing GeolocatorLocationService silently kills '
            'getPositionStream, and with it every turn-by-turn trip.');
  });

  test('the service is bound only, never a foreground service', () {
    // The service reaches startForeground() only via enableBackgroundMode(),
    // which fires only when AndroidSettings carries a
    // foregroundNotificationConfig. map_screen.dart passes none, so the
    // service stays a plain bound service — no FOREGROUND_SERVICE_LOCATION,
    // and no Play Console justification to write.
    for (final permission in const [
      'android.permission.FOREGROUND_SERVICE',
      'android.permission.FOREGROUND_SERVICE_LOCATION',
    ]) {
      expect(
        RegExp('<uses-permission[^>]*$permission[^>]*'
                r'tools:node\s*=\s*"remove"')
            .hasMatch(manifest),
        isTrue,
        reason: 'If navigation ever needs background fixes, add the '
            'permission AND a foregroundNotificationConfig together — the '
            'permission alone buys a Play review with nothing behind it.',
      );
    }
  });
}
