import 'package:flutter/material.dart';

import '../theme.dart';
import 'safety_notice.dart';

/// Route summary before the trip starts (the map's bottom card). Two rows so
/// large text grows the card instead of squeezing the summary to nothing:
/// summary + Start + close, then the disclaimer beside the secondary actions.
class RoutePreviewCard extends StatelessWidget {
  const RoutePreviewCard({
    super.key,
    required this.color,
    required this.icon,
    required this.title,
    required this.subtitle,
    required this.onSave,
    required this.onShare,
    required this.onClear,
    this.onSteps,
    this.onStart,
  });

  /// Card color; white text must read on it (all route colors are ≥ 4.5:1).
  final Color color;
  final IconData icon;

  /// Distance · duration.
  final String title;
  final String subtitle;
  final VoidCallback onSave, onShare, onClear;

  /// Null when the route has no turn list (no Steps, no Start).
  final VoidCallback? onSteps, onStart;

  @override
  Widget build(BuildContext context) {
    const white = TextStyle(color: Colors.white);
    return Material(
      elevation: 4,
      borderRadius: BorderRadius.circular(16),
      color: color,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(14, 4, 4, 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                Icon(icon, color: Colors.white, size: 20),
                const SizedBox(width: 10),
                Expanded(
                  // Announced when a route (re)plans, so a screen reader
                  // hears the result of "Navigate here".
                  child: Semantics(
                    liveRegion: true,
                    child: Column(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        Text(
                          title,
                          style: white.copyWith(
                            fontWeight: FontWeight.w600,
                            fontSize: 16,
                          ),
                        ),
                        Text(
                          subtitle,
                          maxLines: 2,
                          overflow: TextOverflow.ellipsis,
                          // Full white: white70 was 3.7:1 on the route blue.
                          style: white.copyWith(fontSize: 12),
                        ),
                      ],
                    ),
                  ),
                ),
                if (onStart != null)
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: Colors.white,
                      foregroundColor: color,
                    ),
                    icon: const Icon(Icons.navigation, size: 18),
                    label: const Text('Start'),
                    onPressed: onStart,
                  ),
                IconButton(
                  tooltip: 'Clear route',
                  icon: const Icon(Icons.close, color: Colors.white, size: 20),
                  onPressed: onClear,
                ),
              ],
            ),
            Row(
              children: [
                // Agreed once (confirmRouteSafety); this line every time.
                const Expanded(child: DisclaimerLine()),
                IconButton(
                  tooltip: 'Save this route',
                  icon: const Icon(
                    Icons.bookmark_add_outlined,
                    color: Colors.white,
                  ),
                  onPressed: onSave,
                ),
                IconButton(
                  tooltip: 'Copy a link to this trip',
                  icon: const Icon(Icons.share_outlined, color: Colors.white),
                  onPressed: onShare,
                ),
                if (onSteps != null)
                  IconButton(
                    tooltip: 'Upcoming turns',
                    icon: const Icon(Icons.list_alt, color: Colors.white),
                    onPressed: onSteps,
                  ),
              ],
            ),
          ],
        ),
      ),
    );
  }
}

/// Bottom card for a searched place: its name on top, the actions below so
/// "Navigate here" never squeezes the name out at large text sizes.
class PlaceCard extends StatelessWidget {
  const PlaceCard({
    super.key,
    required this.label,
    required this.sublabel,
    required this.verb,
    required this.modeIcon,
    required this.saved,
    required this.onNavigate,
    required this.onToggleSaved,
    required this.onPlan,
    required this.onClose,
  });

  final String label, sublabel, verb;
  final IconData modeIcon;
  final bool saved;
  final VoidCallback onNavigate, onToggleSaved, onPlan, onClose;

  @override
  Widget build(BuildContext context) {
    return Material(
      elevation: 6,
      borderRadius: BorderRadius.circular(18),
      // brandGreenStrong: white on brandGreen was 3.4:1.
      color: brandGreenStrong,
      child: Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 4, 8),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Row(
              children: [
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        label,
                        maxLines: 2,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                          color: Colors.white,
                          fontSize: 17,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      if (sublabel.isNotEmpty)
                        Text(
                          sublabel,
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                          style: const TextStyle(
                            color: Colors.white,
                            fontSize: 13,
                          ),
                        ),
                    ],
                  ),
                ),
                IconButton(
                  tooltip: 'Close',
                  icon: const Icon(Icons.close, color: Colors.white),
                  onPressed: onClose,
                ),
              ],
            ),
            Align(
              alignment: Alignment.centerRight,
              child: Wrap(
                alignment: WrapAlignment.end,
                crossAxisAlignment: WrapCrossAlignment.center,
                children: [
                  IconButton(
                    tooltip: saved ? 'Remove from saved' : 'Save',
                    icon: Icon(
                      saved ? Icons.bookmark : Icons.bookmark_border,
                      color: Colors.white,
                    ),
                    onPressed: onToggleSaved,
                  ),
                  IconButton(
                    tooltip: 'Change start point or modes',
                    icon: const Icon(Icons.tune, color: Colors.white),
                    onPressed: onPlan,
                  ),
                  const SizedBox(width: 4),
                  // Tap to route straight away; the tune button opens the
                  // planner when the trip doesn't start where you stand.
                  FilledButton.icon(
                    style: FilledButton.styleFrom(
                      backgroundColor: Colors.white,
                      foregroundColor: brandDark,
                      padding: const EdgeInsets.symmetric(
                        horizontal: 16,
                        vertical: 12,
                      ),
                    ),
                    icon: Icon(modeIcon, size: 20),
                    label: Text(
                      verb,
                      style: const TextStyle(
                        fontSize: 15,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    onPressed: onNavigate,
                  ),
                  const SizedBox(width: 8),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}
