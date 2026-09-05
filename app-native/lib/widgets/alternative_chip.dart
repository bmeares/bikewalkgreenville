import 'package:flutter/material.dart';

import '../nav.dart';
import '../theme.dart';

/// One route alternative, read as its difference from the selected route
/// (`Walk · +4 min · −80 ft`). A gap glyph appears only for a warning kind the
/// selected route lacks. Assistive tech gets a single label and a tap action.
class AlternativeChip extends StatelessWidget {
  final RouteAlternative alt;
  final NavRoute selected;
  final bool ebike;
  final VoidCallback onPressed;

  const AlternativeChip({
    super.key,
    required this.alt,
    required this.selected,
    required this.ebike,
    required this.onPressed,
  });

  @override
  Widget build(BuildContext context) {
    // The alt was costed with the rider's e-bike too; the server's label
    // stays plain "Bike" on purpose.
    final isEbike = ebike && alt.plan == 'bike';
    final name = isEbike ? 'E-bike' : alt.label;
    final delta = alternativeDelta(alt, selected);
    final extra = extraWarnings(alt, selected);
    return Semantics(
      label: '$name, $delta'
          '${extra.isEmpty ? '' : ', ${extra.map((w) => w.label).join(', ')}'}',
      button: true,
      // excludeSemantics drops the chip's own node (and its tap), so the
      // action is declared here.
      onTap: onPressed,
      excludeSemantics: true,
      child: ActionChip(
        avatar: Icon(
          isEbike
              ? legModeIcons['ebike']!
              : (planIcons[alt.plan] ?? Icons.directions),
          size: 18,
        ),
        label: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            Text('$name · $delta'),
            if (extra.isNotEmpty) ...[
              const SizedBox(width: 4),
              Tooltip(
                message: extra.map((w) => w.label).join('\n'),
                child: Icon(
                  extra.first.icon,
                  size: 16,
                  color: warnAccent(context),
                ),
              ),
            ],
          ],
        ),
        backgroundColor: Theme.of(context).colorScheme.surface,
        onPressed: onPressed,
      ),
    );
  }
}
