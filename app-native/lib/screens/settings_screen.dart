import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import '../theme.dart';
import '../widgets/safety_notice.dart';

/// User preferences: how routes are chosen, what earns a warning, and how the
/// rider is drawn on the map. Everything persists via [AppState].
class SettingsScreen extends StatelessWidget {
  const SettingsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final state = context.watch<AppState>();
    return Scaffold(
      appBar: AppBar(title: const Text('Settings')),
      body: ListView(
        padding: const EdgeInsets.symmetric(vertical: 8),
        children: [
          const SafetyNotice(),
          const Divider(),
          _header(context, 'Riding'),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 4, 16, 0),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  'How much traffic is OK on a bike?',
                  style: Theme.of(context).textTheme.labelLarge,
                ),
                const SizedBox(height: 6),
                SegmentedButton<BikeStress>(
                  segments: [
                    for (final level in BikeStress.values)
                      ButtonSegment(
                        value: level,
                        label: Text(bikeStressLabels[level]!),
                      ),
                  ],
                  selected: {state.stress},
                  showSelectedIcon: false,
                  onSelectionChanged: (sel) => state.setStress(sel.first),
                ),
                const SizedBox(height: 4),
                Text(
                  bikeStressBlurbs[state.stress]!,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
          SwitchListTile(
            secondary: const Icon(Icons.electric_bike),
            title: const Text('I ride an e-bike'),
            subtitle: const Text('Faster, and hills cost you a lot less'),
            value: state.useEbike,
            onChanged: state.setUseEbike,
          ),
          SwitchListTile(
            secondary: const Icon(Icons.accessible_forward),
            title: const Text('I use a wheelchair'),
            subtitle: const Text(
              'Strongly prefers routes with sidewalks and flags the gaps',
            ),
            value: state.roll,
            onChanged: state.setRoll,
          ),
          SwitchListTile(
            secondary: Icon(Icons.pedal_bike, color: hexColor(bcycleRed)),
            title: const Text('Include BCycle bike share'),
            subtitle: const Text(
              'Walk to a dock, ride a rental, dock it near the end',
            ),
            value: state.useBcycle,
            onChanged: state.setUseBcycle,
          ),
          SwitchListTile(
            secondary: const Icon(Icons.cruelty_free),
            title: const Text('Prefer the Prisma Health Swamp Rabbit Trail'),
            subtitle: const Text(
              'Routes choose the trail even when a street way is shorter — '
              'applies to Quiet, Balanced and Direct',
            ),
            value: state.preferTrail,
            onChanged: state.setPreferTrail,
          ),
          SwitchListTile(
            secondary: const Icon(Icons.groups_outlined),
            title: const Text('Prefer community routes'),
            subtitle: const Text(
              'Favor shortcuts and routes drawn by other riders',
            ),
            value: state.preferCommunity,
            onChanged: state.setPreferCommunity,
          ),
          const Divider(),
          _header(context, 'Accessibility'),
          SwitchListTile(
            secondary: const Icon(Icons.contrast),
            title: const Text('High contrast'),
            subtitle: const Text(
              'Stronger colors and bolder map lines for low vision',
            ),
            value: state.highContrast,
            onChanged: state.setHighContrast,
          ),
          SwitchListTile(
            secondary: const Icon(Icons.format_size),
            title: const Text('Large text & controls'),
            subtitle: const Text('Scales the whole app up about a third'),
            value: state.largeUi,
            onChanged: state.setLargeUi,
          ),
          const Divider(),
          _header(context, 'Appearance'),
          RadioGroup<ThemeMode>(
            groupValue: state.themeMode,
            onChanged: (v) => state.setThemeMode(v!),
            child: const Column(
              children: [
                RadioListTile<ThemeMode>(
                  value: ThemeMode.system,
                  secondary: Icon(Icons.brightness_auto),
                  title: Text('Match device'),
                  subtitle: Text('Follows your phone\'s light/dark setting'),
                ),
                RadioListTile<ThemeMode>(
                  value: ThemeMode.light,
                  secondary: Icon(Icons.light_mode),
                  title: Text('Light'),
                ),
                RadioListTile<ThemeMode>(
                  value: ThemeMode.dark,
                  secondary: Icon(Icons.dark_mode),
                  title: Text('Dark'),
                ),
              ],
            ),
          ),
          const Divider(),
          _header(context, 'Experimental advocacy layers'),
          Padding(
            padding: const EdgeInsets.fromLTRB(16, 0, 16, 4),
            child: Text(
              'Data layers for advocacy work. Turning one on adds its toggle '
              'to the map\'s layers sheet.',
              style: Theme.of(context).textTheme.bodySmall,
            ),
          ),
          for (final def in layerDefs.where((d) => d.advocacy))
            SwitchListTile(
              dense: true,
              secondary: Icon(def.icon, color: hexColor(def.color)),
              title: Text(def.label),
              value: state.advocacyEnabled(def.id),
              onChanged: (v) => state.setAdvocacyLayer(def.id, v),
            ),
          const Divider(),
          _header(context, 'Navigation'),
          RadioGroup<PuckStyle>(
            groupValue: state.puckStyle,
            onChanged: (v) => state.setPuckStyle(v!),
            child: Column(
              children: [
                RadioListTile<PuckStyle>(
                  value: PuckStyle.arrow,
                  secondary: const Icon(Icons.navigation),
                  title: const Text('Arrow'),
                  subtitle: const Text('A classic navigation arrow marks you'),
                ),
                RadioListTile<PuckStyle>(
                  value: PuckStyle.mode,
                  secondary: Icon(state.iconFor(state.mode)),
                  title: const Text('Travel mode icon'),
                  subtitle: const Text(
                    'A cyclist, walker or wheelchair user — whichever you are',
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }

  Widget _header(BuildContext context, String text) => Padding(
    padding: const EdgeInsets.fromLTRB(16, 12, 16, 4),
    child: Text(
      text,
      style: Theme.of(context).textTheme.titleSmall?.copyWith(
        color: brandGreen,
        fontWeight: FontWeight.w700,
      ),
    ),
  );
}
