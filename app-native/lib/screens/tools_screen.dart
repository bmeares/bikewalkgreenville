import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher_string.dart';

import '../theme.dart';
import 'settings_screen.dart';

class _Link {
  final String title;
  final IconData icon;
  final String url;
  const _Link(this.title, this.icon, this.url);
}

class _Tool {
  final String title;
  final String subtitle;
  final IconData icon;
  final String? url;

  /// Expandable children (e.g. the several faces of "Who Owns The Roads").
  final List<_Link> links;

  const _Tool(this.title, this.subtitle, this.icon, {this.url, this.links = const []});
}

const _tools = [
  _Tool(
    'Who Owns The Roads?',
    'Look up the office responsible for any road',
    Icons.badge_outlined,
    links: [
      _Link(
        'Search tool',
        Icons.search,
        'https://bwg.mrsm.io/dash/who-owns-the-roads',
      ),
      _Link(
        'Interactive map',
        Icons.map_outlined,
        'https://felt.com/embed/map/'
            'Who-Owns-Our-Roads-uyICtyogTtuqrQs1Z19AtXC'
            '?loc=34.80697,-82.33282,12.27z'
            '&legend=1&cooperativeGestures=1'
            '&geolocation=1&zoomControls=1&scaleBar=1',
      ),
      _Link(
        'Read the story',
        Icons.article_outlined,
        'https://bikewalkgreenville.org/roads',
      ),
    ],
  ),
  _Tool(
    'Parking Dashboard',
    'Downtown garage occupancy in real time',
    Icons.local_parking,
    url:
        'https://grafana.mrsm.io/d/adbvspd/parking?orgId=2&from=now-7d&to=now&timezone=browser&var-garages=\$__all&kiosk',
  ),
  _Tool(
    'Vulnerable Road Users',
    'Pedestrian & cyclist crash data',
    Icons.personal_injury_outlined,
    url: 'https://grafana.mrsm.io/public-dashboards/7c91bc5e81484fef83083203543589de',
  ),
  _Tool(
    'bikewalkgreenville.org',
    'About Bike Walk Greenville',
    Icons.public,
    url: 'https://bikewalkgreenville.org',
  ),
];

/// Dashboards + about. The map is the app; this is the annex.
class ToolsScreen extends StatelessWidget {
  const ToolsScreen({super.key});

  void _open(String url) =>
      launchUrlString(url, mode: LaunchMode.externalApplication);

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
          Card(
            child: ListTile(
              leading: const Icon(Icons.settings, color: brandGreen, size: 32),
              title: const Text('Settings'),
              subtitle: const Text(
                  'Travel preferences, warnings, navigation marker'),
              trailing: const Icon(Icons.chevron_right),
              onTap: () => Navigator.push(
                context,
                MaterialPageRoute(builder: (_) => const SettingsScreen()),
              ),
            ),
          ),
          for (final t in _tools)
            Card(
              child: t.links.isEmpty
                  ? ListTile(
                      leading: Icon(t.icon, color: brandGreen, size: 32),
                      title: Text(t.title),
                      subtitle: Text(t.subtitle),
                      trailing: const Icon(Icons.open_in_new, size: 18),
                      onTap: () => _open(t.url!),
                    )
                  : ExpansionTile(
                      initiallyExpanded: true,
                      shape: const Border(),
                      leading: Icon(t.icon, color: brandGreen, size: 32),
                      title: Text(t.title),
                      subtitle: Text(t.subtitle),
                      children: [
                        for (final l in t.links)
                          ListTile(
                            dense: true,
                            contentPadding:
                                const EdgeInsets.only(left: 32, right: 16),
                            leading: Icon(l.icon, color: brandDark),
                            title: Text(l.title),
                            trailing: const Icon(Icons.open_in_new, size: 18),
                            onTap: () => _open(l.url),
                          ),
                      ],
                    ),
            ),
          const Padding(
            padding: EdgeInsets.all(16),
            child: Text(
              'Bike Walk Greenville advocates for safe walking and biking in '
              'Greenville, SC. Reports submitted in the app appear on the map '
              'and are reviewed by BWG.',
              style: TextStyle(color: Colors.black54, fontSize: 13),
            ),
          ),
        ],
      ),
    );
  }
}
