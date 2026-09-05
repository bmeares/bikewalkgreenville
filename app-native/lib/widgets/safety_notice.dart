import 'package:flutter/material.dart';
import 'app_sheet.dart';

const routeDisclaimer =
    'Bike Walk Greenville builds this platform and does not '
    'endorse or verify any route or user submission. You use routes and community '
    'information at your own risk.';
const routeSafety =
    'Maps can be incomplete, outdated, or wrong. Check crossings, '
    'traffic, access signs, closures, and surface conditions yourself. Never enter '
    'private or gated property without permission. Follow traffic laws, wear a '
    'helmet when biking, use lights when needed, and stop somewhere safe to use '
    'your phone. A suggested route is not a guarantee of safety or accessibility. '
    'Transit times are estimates; check the operator’s schedule.';

class SafetyNotice extends StatelessWidget {
  const SafetyNotice({super.key});
  @override
  Widget build(BuildContext context) => const Padding(
    padding: EdgeInsets.all(16),
    child: Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          'Your route, your judgment',
          style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
        ),
        SizedBox(height: 12),
        Text(routeDisclaimer),
        SizedBox(height: 12),
        Text(routeSafety),
      ],
    ),
  );
}

Future<bool> confirmRouteSafety(BuildContext context) async =>
    await showAppSheet<bool>(
      context: context,
      isScrollControlled: true,
      builder: (ctx) => SingleChildScrollView(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const SafetyNotice(),
            Padding(
              padding: const EdgeInsets.fromLTRB(16, 0, 16, 16),
              child: FilledButton(
                onPressed: () => Navigator.pop(ctx, true),
                child: const Text('I understand — start navigation'),
              ),
            ),
          ],
        ),
      ),
    ) ??
    false;
