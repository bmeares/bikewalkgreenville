import 'package:flutter/material.dart';

import '../geometry_draft.dart';

/// Compact bottom bar for a no-entry area: tap the corners on the map.
class AreaDrawBar extends StatelessWidget {
  const AreaDrawBar({
    super.key,
    required this.draft,
    required this.onUndo,
    required this.onPublish,
    required this.onCancel,
  });

  final AreaDraft draft;
  final VoidCallback onUndo, onPublish, onCancel;

  @override
  Widget build(BuildContext context) {
    final n = draft.corners.length;
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
            Semantics(
              liveRegion: true,
              child: Text(
                n < 3
                    ? 'Tap the corners of the no-entry area ($n of at least 3)'
                    : '$n corners · tap to add more',
                style: Theme.of(context).textTheme.titleSmall,
              ),
            ),
            const SizedBox(height: 8),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: [
                ActionChip(
                  avatar: const Icon(Icons.undo, size: 18),
                  label: const Text('Undo'),
                  onPressed: n == 0 ? null : onUndo,
                ),
                ActionChip(
                  avatar: const Icon(
                    Icons.publish,
                    size: 18,
                    color: Color(0xFFC62828),
                  ),
                  label: const Text('Publish'),
                  onPressed: draft.canPublish ? onPublish : null,
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
