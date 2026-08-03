import 'package:flutter_local_notifications/flutter_local_notifications.dart';

/// The persistent turn-by-turn notification: while navigating, an ongoing,
/// silent notification carries the next maneuver, its distance and the trip
/// ETA — so a pocketed phone or a glance at the lock screen still answers
/// "what do I do next?".
///
/// Silent + `onlyAlertOnce` on a low-importance channel: the voice prompts are
/// the alerts; this is a status card, and it must never buzz on every update.
class NavNotifier {
  static const _id = 1001;
  final _plugin = FlutterLocalNotificationsPlugin();
  bool _ready = false;

  /// Operations are chained so a cancel() can never race an in-flight show()
  /// and lose — the ongoing notification must not outlive the trip.
  Future<void> _serial = Future.value();

  Future<void> _enqueue(Future<void> Function() op) {
    _serial = _serial.then((_) => op(), onError: (_) => op());
    return _serial;
  }

  Future<void> _init() async {
    if (_ready) return;
    await _plugin.initialize(
      settings: const InitializationSettings(
        android: AndroidInitializationSettings('@mipmap/ic_launcher'),
      ),
    );
    // Android 13+ gates notifications behind a runtime permission. Denied is
    // fine — navigation carries on with the on-screen chrome only.
    try {
      await _plugin
          .resolvePlatformSpecificImplementation<
              AndroidFlutterLocalNotificationsPlugin>()
          ?.requestNotificationsPermission();
    } catch (_) {}
    _ready = true;
  }

  Future<void> update({
    required String instruction,
    required String detail,
  }) =>
      _enqueue(() => _show(instruction, detail));

  Future<void> _show(String instruction, String detail) async {
    try {
      await _init();
      await _plugin.show(
        id: _id,
        title: instruction,
        body: detail,
        notificationDetails: const NotificationDetails(
          android: AndroidNotificationDetails(
            'navigation',
            'Turn-by-turn navigation',
            channelDescription:
                'Shows the next turn while a trip is being navigated',
            importance: Importance.low,
            priority: Priority.low,
            ongoing: true,
            autoCancel: false,
            onlyAlertOnce: true,
            playSound: false,
            enableVibration: false,
            category: AndroidNotificationCategory.navigation,
            visibility: NotificationVisibility.public,
          ),
        ),
      );
    } catch (_) {
      // A notification is a convenience; never let it break navigation.
    }
  }

  Future<void> cancel() => _enqueue(() async {
        if (!_ready) return;
        try {
          await _plugin.cancel(id: _id);
        } catch (_) {}
      });
}
