import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../auth.dart';
import '../gpx.dart';
import '../nav.dart';
import '../rides.dart';

/// What this screen pops when a saved route is tapped; the map re-plans it.
class SavedRoutePick {
  final Map<String, dynamic> route;
  const SavedRoutePick(this.route);
}

/// Tab 1: rides recorded on this device. Tap one to see it on the map, trim
/// it, and share a stretch as a community route; ⋮ exports GPX, renames,
/// deletes. Tab 2: routes saved to the account; tap to plan it again.
class RidesScreen extends StatelessWidget {
  const RidesScreen({super.key, this.initialTab = 0});
  final int initialTab;

  Future<void> _undoRemoval(
    BuildContext context,
    RideRecorder recorder,
    Ride ride,
    int index,
  ) async {
    if (!await recorder.restore(ride, index) && context.mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text('Could not restore ride'),
          action: SnackBarAction(
            label: 'Retry',
            onPressed: () => _undoRemoval(context, recorder, ride, index),
          ),
        ),
      );
    }
  }

  @override
  Widget build(BuildContext context) {
    final recorder = context.watch<RideRecorder>();
    return DefaultTabController(
      length: 2,
      initialIndex: initialTab,
      child: Scaffold(
        appBar: AppBar(
          title: const Text('My rides & routes'),
          bottom: const TabBar(
            tabs: [
              Tab(text: 'Rides'),
              Tab(text: 'Saved routes'),
            ],
          ),
        ),
        body: TabBarView(
          children: [_ridesList(context, recorder), const SavedRoutesList()],
        ),
      ),
    );
  }

  Widget _ridesList(BuildContext context, RideRecorder recorder) {
    return ListView(
      children: [
        if (recorder.error != null)
          ListTile(
            title: Text(recorder.error!),
            trailing: recorder.loaded
                ? null
                : TextButton(
                    onPressed: recorder.retry,
                    child: const Text('Retry'),
                  ),
          ),
        if (recorder.rides.isEmpty)
          const ListTile(title: Text('No rides recorded yet.')),
        for (final ride in recorder.rides)
          ListTile(
            leading: const Icon(Icons.route_outlined),
            title: Text(ride.name),
            subtitle: Text(
              '${formatDistance(ride.distanceM)} · '
              '${formatDuration(ride.duration.inSeconds / 60)}',
            ),
            onTap: () => Navigator.pop(context, ride),
            trailing: PopupMenuButton<String>(
              tooltip: 'Ride options',
              onSelected: (v) async {
                if (v == 'gpx') {
                  try {
                    await shareRideGpx(ride);
                  } catch (_) {
                    if (context.mounted) {
                      ScaffoldMessenger.of(context).showSnackBar(
                        const SnackBar(content: Text('Could not export GPX')),
                      );
                    }
                  }
                } else if (v == 'delete') {
                  final index = recorder.rides.indexOf(ride);
                  if (await recorder.delete(ride.id) && context.mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      SnackBar(
                        content: const Text('Ride removed'),
                        action: SnackBarAction(
                          label: 'Undo',
                          onPressed: () =>
                              _undoRemoval(context, recorder, ride, index),
                        ),
                      ),
                    );
                  }
                } else if (v == 'rename') {
                  final ctl = TextEditingController(text: ride.name);
                  final name = await showDialog<String>(
                    context: context,
                    builder: (ctx) => AlertDialog(
                      title: const Text('Rename ride'),
                      content: TextField(controller: ctl, autofocus: true),
                      actions: [
                        TextButton(
                          onPressed: () => Navigator.pop(ctx),
                          child: const Text('Cancel'),
                        ),
                        FilledButton(
                          onPressed: () => Navigator.pop(ctx, ctl.text),
                          child: const Text('Save'),
                        ),
                      ],
                    ),
                  );
                  if (name != null) await recorder.rename(ride.id, name);
                }
              },
              itemBuilder: (_) => const [
                PopupMenuItem(value: 'gpx', child: Text('Export GPX')),
                PopupMenuItem(value: 'rename', child: Text('Rename')),
                PopupMenuItem(value: 'delete', child: Text('Delete')),
              ],
            ),
          ),
      ],
    );
  }
}

/// Routes bookmarked from the route preview, kept on the account.
class SavedRoutesList extends StatefulWidget {
  const SavedRoutesList({super.key});
  @override
  State<SavedRoutesList> createState() => _SavedRoutesListState();
}

class _SavedRoutesListState extends State<SavedRoutesList> {
  @override
  void initState() {
    super.initState();
    auth.refreshSavedRoutes();
  }

  Future<void> _delete(Map<String, dynamic> route) async {
    try {
      final done = await withAuth(context, () async {
        await auth.deleteSavedRoute(route['id'].toString());
        return true;
      });
      if (done == null || !mounted) return;
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(
          content: const Text('Saved route removed'),
          action: SnackBarAction(
            label: 'Undo',
            // Re-saving mints a new id; the route itself is the same.
            onPressed: () => auth
                .saveRoute(
                  Map<String, dynamic>.from(route)
                    ..remove('id')
                    ..remove('created'),
                )
                .catchError((_) => <String, dynamic>{}),
          ),
        ),
      );
    } catch (e) {
      if (mounted) {
        ScaffoldMessenger.of(
          context,
        ).showSnackBar(SnackBar(content: Text(e.toString())));
      }
    }
  }

  @override
  Widget build(BuildContext context) {
    final account = context.watch<AuthState>();
    if (!account.signedIn) {
      return ListView(
        padding: const EdgeInsets.all(16),
        children: [
          const Text(
            'Sign in to keep routes you plan often, on every device. '
            'Bookmark one from the route preview on the map.',
          ),
          const SizedBox(height: 12),
          Align(
            alignment: Alignment.centerLeft,
            child: FilledButton(
              onPressed: () async {
                if (await AuthGate.require(context)) {
                  await auth.refreshSavedRoutes();
                }
              },
              child: const Text('Sign in'),
            ),
          ),
        ],
      );
    }
    return RefreshIndicator(
      onRefresh: auth.refreshSavedRoutes,
      child: ListView(
        children: [
          if (account.savedRoutes.isEmpty)
            const ListTile(
              title: Text('No saved routes yet.'),
              subtitle: Text(
                'Plan a trip, then tap the bookmark on the route preview.',
              ),
            ),
          for (final r in account.savedRoutes)
            ListTile(
              key: ValueKey('saved-route-${r['id']}'),
              leading: const Icon(Icons.bookmark_added_outlined),
              title: Text(r['name']?.toString() ?? 'Saved route'),
              subtitle: Text(
                [
                  if (r['distance_m'] is num)
                    formatDistance((r['distance_m'] as num).toDouble()),
                  if (r['duration_min'] is num)
                    formatDuration((r['duration_min'] as num).toDouble()),
                  (r['modes']?.toString() ?? 'bike').replaceAll(',', ' + '),
                ].join(' · '),
              ),
              onTap: () => Navigator.pop(context, SavedRoutePick(r)),
              trailing: IconButton(
                tooltip: 'Delete saved route',
                icon: const Icon(Icons.delete_outline),
                onPressed: () => _delete(r),
              ),
            ),
        ],
      ),
    );
  }
}
