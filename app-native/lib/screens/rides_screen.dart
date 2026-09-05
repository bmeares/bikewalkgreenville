import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../nav.dart';
import '../rides.dart';

/// Rides recorded on this device. Tap one to see it on the map, trim it, and
/// share a stretch as a community route.
class RidesScreen extends StatelessWidget {
  const RidesScreen({super.key});

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
    return Scaffold(
      appBar: AppBar(title: const Text('My rides')),
      body: ListView(
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
                onSelected: (v) async {
                  if (v == 'delete') {
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
                  PopupMenuItem(value: 'rename', child: Text('Rename')),
                  PopupMenuItem(value: 'delete', child: Text('Delete')),
                ],
              ),
            ),
        ],
      ),
    );
  }
}
