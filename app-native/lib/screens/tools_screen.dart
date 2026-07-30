import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher_string.dart';

import '../theme.dart';

class _Tool {
  final String title;
  final String subtitle;
  final IconData icon;
  final String url;
  const _Tool(this.title, this.subtitle, this.icon, this.url);
}

const _tools = [
  _Tool(
    'Who Owns The Roads?',
    'Look up the office responsible for any road',
    Icons.badge_outlined,
    'https://bwg.mrsm.io/dash/who-owns-the-roads',
  ),
  _Tool(
    'Parking Dashboard',
    'Downtown garage occupancy in real time',
    Icons.local_parking,
    'https://grafana.mrsm.io/d/adbvspd/parking?orgId=2&from=now-7d&to=now&timezone=browser&var-garages=\$__all&kiosk',
  ),
  _Tool(
    'Vulnerable Road Users',
    'Pedestrian & cyclist crash data',
    Icons.personal_injury_outlined,
    'https://grafana.mrsm.io/public-dashboards/7c91bc5e81484fef83083203543589de',
  ),
  _Tool(
    'bikewalkgreenville.org',
    'About Bike Walk Greenville',
    Icons.public,
    'https://bikewalkgreenville.org',
  ),
];

/// Dashboards + about. The map is the app; this is the annex.
class ToolsScreen extends StatelessWidget {
  const ToolsScreen({super.key});

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Row(
          children: [
            Image.asset('assets/logo.png', width: 32),
            const SizedBox(width: 10),
            const Text('Dashboards & Tools'),
          ],
        ),
      ),
      body: ListView(
        padding: const EdgeInsets.all(8),
        children: [
          for (final t in _tools)
            Card(
              child: ListTile(
                leading: Icon(t.icon, color: brandGreen, size: 32),
                title: Text(t.title),
                subtitle: Text(t.subtitle),
                trailing: const Icon(Icons.open_in_new, size: 18),
                onTap: () => launchUrlString(t.url,
                    mode: LaunchMode.externalApplication),
              ),
            ),
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              'Bike Walk Greenville advocates for safe walking and biking in '
              'Greenville, SC. Reports submitted in the app are reviewed by '
              'BWG and forwarded to the responsible local office.',
              style: TextStyle(color: Colors.black54, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}
