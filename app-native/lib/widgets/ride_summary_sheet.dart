import 'package:flutter/material.dart';

import '../gpx.dart';
import '../nav.dart';
import '../rides.dart';

/// What the summary sheet was closed with.
enum RideSummaryResult { share, done, discarded }

/// Stop → summary: name the ride, see its stats, Save or Discard. After Save
/// the same sheet offers Share a stretch / Export GPX / Done. Opened already
/// saved for rides picked from My rides.
class RideSummarySheet extends StatefulWidget {
  const RideSummarySheet({
    super.key,
    required this.ride,
    this.onSave,
    this.onDiscard,
    this.onExport = shareRideGpx,
    this.canShare = true,
  });

  /// False while navigating: trimming would fight the nav camera.
  final bool canShare;

  /// Live snapshot (unsaved) or a saved ride.
  final Ride ride;

  /// Null means [ride] is already saved.
  final Future<({Ride? ride, String? error})> Function(String name)? onSave;
  final Future<bool> Function()? onDiscard;
  final Future<void> Function(Ride) onExport;

  @override
  State<RideSummarySheet> createState() => _RideSummarySheetState();
}

class _RideSummarySheetState extends State<RideSummarySheet> {
  late Ride _ride = widget.ride;
  late bool _saved = widget.onSave == null;
  late final _name = TextEditingController(
    text: _saved ? _ride.name : defaultRideName(_ride.startedAt),
  );
  bool _busy = false;
  String? _error;

  @override
  void dispose() {
    _name.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    final name = _name.text.trim();
    setState(() {
      _busy = true;
      _error = null;
    });
    final result = await widget.onSave!(
      name.isEmpty ? defaultRideName(_ride.startedAt) : name,
    );
    if (!mounted) return;
    setState(() {
      _busy = false;
      if (result.ride != null) {
        _ride = result.ride!;
        _saved = true;
      } else {
        _error = result.error ?? 'Could not save. Try again.';
      }
    });
  }

  Future<void> _discard() async {
    final ok = await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: const Text('Discard ride?'),
        content: const Text('This recording will be deleted.'),
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
    if (ok != true || !mounted) return;
    setState(() => _busy = true);
    final done = await widget.onDiscard!();
    if (!mounted) return;
    if (done) {
      Navigator.pop(context, RideSummaryResult.discarded);
    } else {
      setState(() {
        _busy = false;
        _error = 'Could not discard. Try again.';
      });
    }
  }

  Future<void> _export() async {
    try {
      await widget.onExport(_ride);
    } catch (_) {
      if (mounted) setState(() => _error = 'Could not export GPX.');
    }
  }

  @override
  Widget build(BuildContext context) {
    final ride = _ride;
    final seconds = ride.duration.inSeconds;
    final mph = seconds <= 0 ? 0.0 : ride.distanceM / seconds * 2.23694;
    final muted = TextStyle(
      color: Theme.of(context).colorScheme.onSurfaceVariant,
    );
    Widget stat(String label, String value) => Expanded(
      child: Semantics(
        label: '$label $value',
        excludeSemantics: true,
        child: Column(
          children: [
            Text(
              value,
              style: Theme.of(context).textTheme.titleMedium,
              textAlign: TextAlign.center,
            ),
            Text(label, style: muted, textAlign: TextAlign.center),
          ],
        ),
      ),
    );
    return Padding(
      padding: EdgeInsets.only(
        bottom: MediaQuery.of(context).viewInsets.bottom,
      ),
      child: SafeArea(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(16),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_saved)
                Text(ride.name, style: Theme.of(context).textTheme.titleLarge)
              else
                TextField(
                  controller: _name,
                  enabled: !_busy,
                  decoration: const InputDecoration(
                    labelText: 'Ride name',
                    border: OutlineInputBorder(),
                  ),
                ),
              const SizedBox(height: 16),
              Row(
                children: [
                  stat('Distance', formatDistance(ride.distanceM)),
                  stat('Moving time', formatDuration(seconds / 60)),
                  stat('Average', '${mph.toStringAsFixed(1)} mph'),
                ],
              ),
              if (_error != null)
                Padding(
                  padding: const EdgeInsets.only(top: 12),
                  child: Semantics(
                    liveRegion: true,
                    child: Text(
                      _error!,
                      style: TextStyle(
                        color: Theme.of(context).colorScheme.error,
                      ),
                    ),
                  ),
                ),
              const SizedBox(height: 16),
              if (!_saved)
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  alignment: WrapAlignment.end,
                  children: [
                    TextButton(
                      onPressed: _busy || widget.onDiscard == null
                          ? null
                          : _discard,
                      child: const Text('Discard'),
                    ),
                    FilledButton.icon(
                      onPressed: _busy ? null : _save,
                      icon: const Icon(Icons.check),
                      label: const Text('Save'),
                    ),
                  ],
                )
              else
                Wrap(
                  spacing: 8,
                  runSpacing: 8,
                  children: [
                    ActionChip(
                      avatar: const Icon(Icons.groups_outlined, size: 18),
                      label: const Text('Share a stretch'),
                      tooltip: widget.canShare
                          ? null
                          : 'Finish navigating first',
                      onPressed: widget.canShare
                          ? () =>
                                Navigator.pop(context, RideSummaryResult.share)
                          : null,
                    ),
                    ActionChip(
                      avatar: const Icon(Icons.ios_share, size: 18),
                      label: const Text('Export GPX'),
                      onPressed: _export,
                    ),
                    ActionChip(
                      avatar: const Icon(Icons.check, size: 18),
                      label: const Text('Done'),
                      onPressed: () =>
                          Navigator.pop(context, RideSummaryResult.done),
                    ),
                    if (!widget.canShare)
                      Text(
                        'Finish navigating first to share a stretch.',
                        style: Theme.of(context).textTheme.bodySmall,
                      ),
                  ],
                ),
            ],
          ),
        ),
      ),
    );
  }
}
