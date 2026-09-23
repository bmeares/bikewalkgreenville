import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../app_state.dart';
import 'app_sheet.dart';

const routeDisclaimer =
    'Bike Walk Greenville builds this platform and does not '
    'endorse or verify any route or user submission. You use routes and community '
    'information at your own risk.';

/// Bump when the text below changes: everyone agrees again once.
const disclaimerVersion = 2;
const disclaimerTitle = 'Beta App Safety and Liability Disclaimer';
const disclaimerParagraphs = [
  'This app is a beta navigation tool intended for informational purposes only. Routes and conditions are based on OpenStreetMap data, user-submitted information, and other sources that may be incomplete, inaccurate, outdated, or unverified. Suggested routes are not guaranteed to be safe, accessible, lawful, passable, or appropriate for your abilities or mode of travel.',
  'Always use your own judgment, remain aware of your surroundings, obey all signs and laws, and avoid interacting with the app while moving. Actual conditions—including traffic, construction, closures, surface hazards, weather, lighting, and accessibility—may differ from those shown. Do not use the app for emergencies.',
  'By selecting “I Agree,” you acknowledge that bicycling and walking involve inherent risks and that you use this app and any suggested route at your own risk. To the fullest extent permitted by law, Bike Walk Greenville, its officers, employees, volunteers, contractors, contributors, and data providers are not responsible for injuries, losses, damages, or other consequences arising from your use of or reliance on the app.',
];
const disclaimerCheckbox =
    'I have read and agree to the Safety and Liability Disclaimer.';

/// The full disclaimer text (Settings, the read-only sheet, the agree sheet).
class SafetyNotice extends StatelessWidget {
  const SafetyNotice({super.key});
  @override
  Widget build(BuildContext context) => Padding(
    padding: const EdgeInsets.all(16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Semantics(
          header: true,
          child: const Text(
            disclaimerTitle,
            style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
          ),
        ),
        for (final p in disclaimerParagraphs) ...[
          const SizedBox(height: 12),
          Text(p),
        ],
      ],
    ),
  );
}

/// Read-only full text with a Close button.
Future<void> showDisclaimerSheet(BuildContext context) => showAppSheet<void>(
  context: context,
  isScrollControlled: true,
  builder: (ctx) => SingleChildScrollView(
    child: Column(
      mainAxisSize: MainAxisSize.min,
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        const SafetyNotice(),
        Padding(
          padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
          child: FilledButton(
            onPressed: () => Navigator.pop(ctx),
            child: const Text('Close'),
          ),
        ),
      ],
    ),
  ),
);

/// Before navigation starts: the full disclaimer + checkbox + "I Agree" once
/// per [disclaimerVersion]; afterwards returns true straight away (the route
/// card carries the compact [DisclaimerLine]).
Future<bool> confirmRouteSafety(BuildContext context) async {
  final state = context.read<AppState>();
  if (state.disclaimerAcceptedVersion >= disclaimerVersion) return true;
  // Signed in, AuthState sees the change and pushes the version to /me.
  final agreed =
      await showAppSheet<bool>(
        context: context,
        isScrollControlled: true,
        builder: (ctx) => const SingleChildScrollView(child: _AgreeForm()),
      ) ??
      false;
  if (agreed) state.acceptDisclaimer(disclaimerVersion);
  return agreed;
}

class _AgreeForm extends StatefulWidget {
  const _AgreeForm();
  @override
  State<_AgreeForm> createState() => _AgreeFormState();
}

class _AgreeFormState extends State<_AgreeForm> {
  bool _checked = false;
  @override
  Widget build(BuildContext context) => Column(
    mainAxisSize: MainAxisSize.min,
    crossAxisAlignment: CrossAxisAlignment.stretch,
    children: [
      const SafetyNotice(),
      CheckboxListTile(
        value: _checked,
        onChanged: (v) => setState(() => _checked = v ?? false),
        controlAffinity: ListTileControlAffinity.leading,
        title: const Text(disclaimerCheckbox),
      ),
      Padding(
        padding: const EdgeInsets.fromLTRB(16, 8, 16, 16),
        child: FilledButton(
          onPressed: _checked ? () => Navigator.pop(context, true) : null,
          child: const Text('I Agree'),
        ),
      ),
    ],
  );
}

/// One-line notice on the route card; tapping it (anywhere — the whole line
/// is one 48 dp target) opens the full text.
class DisclaimerLine extends StatelessWidget {
  final Color color;
  const DisclaimerLine({super.key, this.color = Colors.white});

  static const _lead = 'Beta: routes may be wrong. Ride at your own risk. ';
  static const _link = 'Safety and Liability Disclaimer';

  @override
  Widget build(BuildContext context) {
    final style = TextStyle(color: color, fontSize: 11);
    return Semantics(
      button: true,
      label: '$_lead$_link',
      onTapHint: 'read the disclaimer',
      excludeSemantics: true,
      onTap: () => showDisclaimerSheet(context),
      child: InkWell(
        onTap: () => showDisclaimerSheet(context),
        child: ConstrainedBox(
          constraints: const BoxConstraints(minHeight: 48),
          child: Align(
            alignment: Alignment.centerLeft,
            widthFactor: 1,
            child: Text.rich(
              TextSpan(
                text: _lead,
                children: [
                  TextSpan(
                    text: _link,
                    style: style.copyWith(
                      decoration: TextDecoration.underline,
                      decorationColor: color,
                      fontWeight: FontWeight.w600,
                    ),
                  ),
                ],
              ),
              style: style,
            ),
          ),
        ),
      ),
    );
  }
}
