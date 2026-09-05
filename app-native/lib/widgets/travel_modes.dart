import 'package:flutter/foundation.dart';
import 'package:flutter/material.dart';
import 'package:pointer_interceptor/pointer_interceptor.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';

/// Selection and vehicle variants have separate, labeled controls.
class TravelModes extends StatelessWidget {
  final VoidCallback? onChanged;
  const TravelModes({super.key, this.onChanged});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return PointerInterceptor(
      intercepting: kIsWeb,
      child: Material(
        color: Theme.of(context).colorScheme.surface,
        elevation: 2,
        borderRadius: BorderRadius.circular(16),
        child: Padding(
          padding: const EdgeInsets.symmetric(horizontal: 10, vertical: 6),
          child: Wrap(
            spacing: 6,
            crossAxisAlignment: WrapCrossAlignment.center,
            children: [
              for (final mode in TravelMode.values)
                Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    FilterChip(
                    showCheckmark: false,
                      avatar: Icon(state.iconFor(mode), size: 18),
                      label: Text(state.labelFor(mode)),
                      selected: state.modes.contains(mode),
                      onSelected: (selected) {
                        final next = {...state.modes};
                        selected ? next.add(mode) : next.remove(mode);
                        if (next.isEmpty) return;
                        state.setModes(next);
                        onChanged?.call();
                      },
                    ),
                    if (mode != TravelMode.transit)
                      PopupMenuButton<bool>(
                        tooltip: mode == TravelMode.cyclist
                            ? 'Choose Bike or E-bike'
                            : 'Choose Walk or Roll',
                        icon: const Icon(Icons.expand_more, size: 20),
                        padding: EdgeInsets.zero,
                        constraints: const BoxConstraints(
                          minWidth: 32,
                          minHeight: 40,
                        ),
                        initialValue: mode == TravelMode.cyclist
                            ? state.useEbike
                            : state.roll,
                        onSelected: (variant) {
                          if (mode == TravelMode.cyclist) {
                            state.setUseEbike(variant);
                          } else {
                            state.setRoll(variant);
                          }
                          state.setModes({...state.modes, mode});
                          onChanged?.call();
                        },
                        itemBuilder: (_) => [
                          PopupMenuItem(
                            value: false,
                            child: PointerInterceptor(
                              intercepting: kIsWeb,
                              child: Text(
                                mode == TravelMode.cyclist ? 'Bike' : 'Walk',
                              ),
                            ),
                          ),
                          PopupMenuItem(
                            value: true,
                            child: PointerInterceptor(
                              intercepting: kIsWeb,
                              child: Text(
                                mode == TravelMode.cyclist
                                    ? 'E-bike'
                                    : 'Roll / wheelchair',
                              ),
                            ),
                          ),
                        ],
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
