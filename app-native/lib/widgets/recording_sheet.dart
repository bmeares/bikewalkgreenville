import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../nav.dart';
import '../rides.dart';

class RecordingSheet extends StatelessWidget {
  const RecordingSheet({
    super.key,
    required this.onResume,
    required this.onPause,
    required this.onStop,
  });

  final Future<void> Function() onResume;
  final Future<void> Function() onPause;

  /// Stop opens the ride summary (Save / Discard live there).
  final VoidCallback onStop;

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
            // Live region: "Recording" / "Paused" is announced on each change
            // (the elapsed time below ticks every second and stays silent).
            Semantics(
              liveRegion: true,
              header: true,
              child: Text(
                recorder.recovered
                    ? 'Recovered ride'
                    : recorder.paused
                    ? 'Paused'
                    : 'Recording',
                style: Theme.of(context).textTheme.titleLarge,
              ),
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
                  onPressed: recorder.busy ? null : onStop,
                  icon: const Icon(Icons.stop),
                  label: const Text('Stop'),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
