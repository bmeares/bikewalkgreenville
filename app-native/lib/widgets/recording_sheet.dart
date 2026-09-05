import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../nav.dart';
import '../rides.dart';

class RecordingSheet extends StatelessWidget {
  const RecordingSheet({
    super.key,
    required this.onResume,
    required this.onPause,
    required this.onSaved,
    required this.onDiscarded,
  });

  final Future<void> Function() onResume;
  final Future<void> Function() onPause;
  final void Function(Ride) onSaved;
  final VoidCallback onDiscarded;

  @override
  Widget build(BuildContext context) {
    final recorder = context.watch<RideRecorder>();
    return SafeArea(
      child: SingleChildScrollView(
        padding: const EdgeInsets.all(16),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Text(
              recorder.recovered
                  ? 'Recovered ride'
                  : recorder.paused
                  ? 'Paused'
                  : 'Recording',
              style: Theme.of(context).textTheme.titleLarge,
            ),
            const SizedBox(height: 8),
            Text(
              '${formatDistance(recorder.liveDistanceM)} · '
              '${formatDuration(recorder.liveDuration.inSeconds / 60)}',
            ),
            if (recorder.error != null)
              Padding(
                padding: const EdgeInsets.only(top: 8),
                child: Semantics(
                  liveRegion: true,
                  child: Text(recorder.error!),
                ),
              ),
            const SizedBox(height: 12),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                FilledButton.icon(
                  onPressed: recorder.busy
                      ? null
                      : recorder.paused
                      ? onResume
                      : onPause,
                  icon: Icon(recorder.paused ? Icons.play_arrow : Icons.pause),
                  label: Text(recorder.paused ? 'Resume' : 'Pause'),
                ),
                OutlinedButton.icon(
                  onPressed: recorder.busy
                      ? null
                      : () async {
                          final ride = await recorder.stop();
                          if (context.mounted && ride != null) onSaved(ride);
                        },
                  icon: const Icon(Icons.check),
                  label: const Text('Save'),
                ),
                TextButton(
                  onPressed: recorder.busy
                      ? null
                      : () async {
                          final discard = await showDialog<bool>(
                            context: context,
                            builder: (ctx) => AlertDialog(
                              title: const Text('Discard ride?'),
                              actions: [
                                TextButton(
                                  onPressed: () => Navigator.pop(ctx, false),
                                  child: const Text('Keep'),
                                ),
                                TextButton(
                                  onPressed: () => Navigator.pop(ctx, true),
                                  child: const Text('Discard'),
                                ),
                              ],
                            ),
                          );
                          if (discard == true &&
                              await recorder.discard() &&
                              context.mounted) {
                            onDiscarded();
                          }
                        },
                  child: const Text('Discard'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
