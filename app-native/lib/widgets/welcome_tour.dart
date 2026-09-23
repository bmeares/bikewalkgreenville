import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';

class _Page {
  final IconData icon;
  final String title;
  final String body;
  const _Page(this.icon, this.title, this.body);
}

const _pages = [
  _Page(
    Icons.layers_outlined,
    'Map & layers',
    'Bike lanes, trails, sidewalks, bus routes and parking, all on one map. '
        'Tap the layers button to choose what you see.',
  ),
  _Page(
    Icons.directions,
    'Navigate',
    'Search a place and tap Navigate here. Bike, walk or take the bus, then '
        'pick between route choices before you start.',
  ),
  _Page(
    Icons.fiber_manual_record,
    'Record & share rides',
    'Tap the red record button to track a ride. Share a stretch as a '
        'community route so others can find it.',
  ),
  _Page(
    Icons.groups,
    'Community & group rides',
    'Add places, report problems and vote on what others mapped. Ride '
        'together and follow the leader with group rides.',
  ),
];

/// Shows the tour once: skipped when `welcome_seen` is set, which it is after
/// the tour closes with "Don't show again" checked (the default).
Future<void> maybeShowWelcomeTour(BuildContext context) async {
  final state = context.read<AppState>();
  await state.loaded;
  if (state.welcomeSeen || !context.mounted) return;
  final dontShow = await showDialog<bool>(
    context: context,
    barrierDismissible: false,
    builder: (_) => PointerInterceptor(
      intercepting: kIsWeb,
      child: const Dialog.fullscreen(child: WelcomeTour()),
    ),
  );
  if (dontShow ?? true) state.setWelcomeSeen(true);
}

/// Four-page first-launch tour. Pops with the "Don't show again" value.
class WelcomeTour extends StatefulWidget {
  const WelcomeTour({super.key});
  @override
  State<WelcomeTour> createState() => _WelcomeTourState();
}

class _WelcomeTourState extends State<WelcomeTour> {
  final _ctl = PageController();
  int _page = 0;
  bool _dontShow = true;

  @override
  void dispose() {
    _ctl.dispose();
    super.dispose();
  }

  void _close() => Navigator.pop(context, _dontShow);

  @override
  Widget build(BuildContext context) {
    final last = _page == _pages.length - 1;
    final scheme = Theme.of(context).colorScheme;
    // Escape = Skip on web/desktop (the dialog is not barrier-dismissible,
    // so the default Escape-to-dismiss doesn't apply).
    return CallbackShortcuts(
      bindings: {const SingleActivator(LogicalKeyboardKey.escape): _close},
      child: Focus(
        autofocus: true,
        child: SafeArea(
          child: Column(
            children: [
              Expanded(
                child: PageView(
                  controller: _ctl,
                  onPageChanged: (i) => setState(() => _page = i),
                  children: [
                    for (final p in _pages)
                      Padding(
                        padding: const EdgeInsets.all(32),
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          children: [
                            Icon(p.icon, size: 96, color: brandGreen),
                            const SizedBox(height: 24),
                            Semantics(
                              header: true,
                              child: Text(
                                p.title,
                                textAlign: TextAlign.center,
                                style: Theme.of(
                                  context,
                                ).textTheme.headlineSmall,
                              ),
                            ),
                            const SizedBox(height: 12),
                            Text(
                              p.body,
                              textAlign: TextAlign.center,
                              style: Theme.of(context).textTheme.bodyLarge,
                            ),
                          ],
                        ),
                      ),
                  ],
                ),
              ),
              Semantics(
                label:
                    'Page ${_page + 1} of ${_pages.length}: '
                    '${_pages[_page].title}',
                liveRegion: true,
                excludeSemantics: true,
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.center,
                  children: [
                    for (var i = 0; i < _pages.length; i++)
                      Container(
                        width: 8,
                        height: 8,
                        margin: const EdgeInsets.all(4),
                        decoration: BoxDecoration(
                          shape: BoxShape.circle,
                          color: i == _page
                              ? brandGreen
                              : scheme.outlineVariant,
                        ),
                      ),
                  ],
                ),
              ),
              CheckboxListTile(
                value: _dontShow,
                onChanged: (v) => setState(() => _dontShow = v ?? true),
                controlAffinity: ListTileControlAffinity.leading,
                title: const Text("Don't show again"),
              ),
              Padding(
                padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
                child: Row(
                  children: [
                    if (!last)
                      TextButton(onPressed: _close, child: const Text('Skip')),
                    const Spacer(),
                    FilledButton(
                      onPressed: last
                          ? _close
                          // Reduce motion: jump instead of sliding.
                          : (MediaQuery.maybeDisableAnimationsOf(context) ??
                                false)
                          ? () => _ctl.jumpToPage(_page + 1)
                          : () => _ctl.nextPage(
                              duration: const Duration(milliseconds: 250),
                              curve: Curves.easeOut,
                            ),
                      child: Text(last ? 'Done' : 'Next'),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
