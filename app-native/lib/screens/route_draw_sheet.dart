import 'package:flutter/material.dart';

import '../geometry_draft.dart';
import '../theme.dart';

/// Compact bottom bar for drawing a route by tapping waypoints on the map.
/// The map owns the taps; this only shows state and the actions.
class RouteDrawBar extends StatelessWidget {
  const RouteDrawBar({
    super.key,
    required this.draft,
    required this.straight,
    required this.busy,
    required this.onUndo,
    required this.onStraight,
    required this.onPublish,
    required this.onCancel,
  });

  final RouteDraft draft;

  /// Next leg is drawn straight instead of routed.
  final bool straight;

  /// A leg is being fetched.
  final bool busy;
  final VoidCallback onUndo, onPublish, onCancel;
  final ValueChanged<bool> onStraight;

  @override
  Widget build(BuildContext context) {
    final n = draft.waypoints.length;
    return Material(
      elevation: 6,
      borderRadius: BorderRadius.circular(18),
      color: Theme.of(context).colorScheme.surface,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 10, 14, 10),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Expanded(
                  child: Semantics(
                    liveRegion: true,
                    child: Text(
                      n == 0
                          ? 'Tap the map to add waypoints'
                          : '$n waypoint${n == 1 ? '' : 's'} · tap to add more',
                      style: Theme.of(context).textTheme.titleSmall,
                    ),
                  ),
                ),
                if (busy)
                  const SizedBox(
                    width: 18,
                    height: 18,
                    child: CircularProgressIndicator(strokeWidth: 2),
                  ),
              ],
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ActionChip(
                  avatar: const Icon(Icons.undo, size: 18),
                  label: const Text('Undo'),
                  onPressed: n == 0 || busy ? null : onUndo,
                ),
                FilterChip(
                  avatar: straight
                      ? null
                      : const Icon(Icons.straight, size: 18),
                  label: const Text('Straight line'),
                  tooltip:
                      'Draw the next leg straight, for paths not on the map',
                  selected: straight,
                  onSelected: onStraight,
                ),
                ActionChip(
                  avatar: const Icon(
                    Icons.publish,
                    size: 18,
                    color: brandGreen,
                  ),
                  label: const Text('Publish'),
                  onPressed: draft.canPublish && !busy ? onPublish : null,
                ),
                ActionChip(
                  avatar: const Icon(Icons.close, size: 18),
                  label: const Text('Cancel'),
                  onPressed: onCancel,
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}
