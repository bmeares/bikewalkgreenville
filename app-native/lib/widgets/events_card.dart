import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter_local_notifications/flutter_local_notifications.dart';
import 'package:provider/provider.dart';
import 'package:url_launcher/url_launcher_string.dart';

import '../api.dart';
import '../app_state.dart';
import '../theme.dart';

const calendarUrl = 'https://bikewalkgreenville.org/calendar';

const _weekdays = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const _months = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
];

DateTime? _start(Map<String, dynamic> e) =>
    DateTime.tryParse(e['start']?.toString() ?? '')?.toLocal();

/// "Sat, Oct 4 · 9:30 AM" (no time for all-day events).
String formatEventWhen(Map<String, dynamic> e) {
  final d = _start(e);
  if (d == null) return '';
  final date = '${_weekdays[d.weekday - 1]}, ${_months[d.month - 1]} ${d.day}';
  if (e['all_day'] == true) return date;
  final h = d.hour % 12 == 0 ? 12 : d.hour % 12;
  final m = d.minute.toString().padLeft(2, '0');
  return '$date · $h:$m ${d.hour < 12 ? 'AM' : 'PM'}';
}

/// "Upcoming events" in the menu: the next three from the BWG calendar.
class EventsCard extends StatefulWidget {
  const EventsCard({super.key});
  @override
  State<EventsCard> createState() => _EventsCardState();
}

class _EventsCardState extends State<EventsCard> {
  List<Map<String, dynamic>>? _events;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final events = await api.events(days: 60);
      if (!mounted) return;
      setState(() => _events = events);
      _notifySoon(events);
    } catch (_) {
      if (mounted) setState(() => _failed = true);
    }
  }

  /// One local notification per event starting within 3 days.
  // ponytail: fires when the menu loads the list, not at a scheduled time —
  // zonedSchedule needs the timezone package; add it if exact reminders matter.
  Future<void> _notifySoon(List<Map<String, dynamic>> events) async {
    if (kIsWeb) return;
    final state = context.read<AppState>();
    final now = DateTime.now();
    final soon = [
      for (final e in events)
        if (e['uid'] != null &&
            !state.eventNotified(e['uid'].toString()) &&
            (_start(e)?.isAfter(now) ?? false) &&
            _start(e)!.difference(now) <= const Duration(days: 3))
          e,
    ];
    if (soon.isEmpty) return;
    try {
      final plugin = FlutterLocalNotificationsPlugin();
      await plugin.initialize(
        settings: const InitializationSettings(
          android: AndroidInitializationSettings('@mipmap/ic_launcher'),
          iOS: DarwinInitializationSettings(),
        ),
      );
      // Android 13+ needs POST_NOTIFICATIONS at runtime; without it `show`
      // silently drops the reminder, so only a granted one counts as sent.
      final android = plugin
          .resolvePlatformSpecificImplementation<
            AndroidFlutterLocalNotificationsPlugin
          >();
      if (android != null &&
          await android.requestNotificationsPermission() != true) {
        return;
      }
      for (final e in soon) {
        final uid = e['uid'].toString();
        await plugin.show(
          id: 2000 + uid.hashCode % 100000,
          title: 'Coming up: ${e['title'] ?? 'BWG event'}',
          body: [formatEventWhen(e), e['location'] ?? '']
              .where((s) => s.toString().isNotEmpty)
              .join(' · '),
          notificationDetails: const NotificationDetails(
            android: AndroidNotificationDetails(
              'events',
              'Upcoming events',
              channelDescription: 'Bike Walk Greenville events in the next few days',
            ),
          ),
        );
        state.markEventNotified(uid);
      }
    } catch (_) {
      // Reminders are a nicety; the card still lists the events.
    }
  }

  void _open(String url) =>
      launchUrlString(url, mode: LaunchMode.externalApplication);

  @override
  Widget build(BuildContext context) {
    final muted = TextStyle(
      color: Theme.of(context).colorScheme.onSurfaceVariant,
      fontSize: 13,
    );
    final events = _events;
    return Card(
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          const ListTile(
            leading: Icon(Icons.event, color: brandGreen, size: 32),
            title: Text('Upcoming events'),
          ),
          if (_failed || (events != null && events.isEmpty))
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text(
                _failed
                    ? 'Events could not be loaded right now.'
                    : 'No upcoming events on the calendar.',
                style: muted,
              ),
            )
          else if (events == null)
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 8),
              child: Text('Loading events…', style: muted),
            )
          else
            for (final e in events.take(3))
              ListTile(
                key: ValueKey('event-${e['uid']}-${e['start']}'),
                dense: true,
                title: Text(e['title']?.toString() ?? 'Event'),
                subtitle: Text(
                  [formatEventWhen(e), e['location']?.toString() ?? '']
                      .where((s) => s.isNotEmpty)
                      .join('\n'),
                ),
                isThreeLine: (e['location']?.toString() ?? '').isNotEmpty,
                trailing: const Icon(Icons.open_in_new, size: 18),
                onTap: () {
                  final luma = e['luma_url']?.toString() ?? '';
                  final link = luma.isNotEmpty
                      ? luma
                      : (e['html_link']?.toString() ?? '');
                  if (link.isNotEmpty) _open(link);
                },
              ),
          Align(
            alignment: Alignment.centerRight,
            child: TextButton.icon(
              onPressed: () => _open(calendarUrl),
              icon: const Icon(Icons.calendar_month),
              label: const Text('Open full calendar'),
            ),
          ),
        ],
      ),
    );
  }
}
