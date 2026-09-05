import 'package:flutter/material.dart';
import 'package:maplibre_gl/maplibre_gl.dart';
import '../api.dart';
import '../theme.dart';
import '../widgets/app_sheet.dart';
import '../widgets/safety_notice.dart';

/// Every coordinate pair in a GeoJSON geometry (any nesting), as LatLngs.
List<LatLng> geometryLatLngs(dynamic geometry) {
  final out = <LatLng>[];
  void walk(dynamic node) {
    if (node is! List || node.isEmpty) return;
    if (node.length >= 2 && node[0] is num && node[1] is num) {
      out.add(LatLng((node[1] as num).toDouble(), (node[0] as num).toDouble()));
      return;
    }
    node.forEach(walk);
  }

  if (geometry is Map) walk(geometry['coordinates']);
  return out;
}

/// One public edit log: community contributions (add / edit / rollback) from
/// map-layers plus reported issues and their dismissals from walk-audit.
const editKinds = <String, ({String label, IconData icon})>{
  'add': (label: 'Added', icon: Icons.add_location_alt_outlined),
  'edit': (label: 'Edited', icon: Icons.edit_outlined),
  'rollback': (label: 'Rolled back', icon: Icons.undo),
  'confirm': (label: 'Confirmed it exists', icon: Icons.thumb_up_alt_outlined),
  'report': (label: 'Reported issue', icon: Icons.report_problem_outlined),
  'dismiss': (label: 'Dismissed report', icon: Icons.visibility_off_outlined),
};

class CommunityScreen extends StatefulWidget {
  const CommunityScreen({super.key});
  @override
  State<CommunityScreen> createState() => _CommunityScreenState();
}

class _CommunityScreenState extends State<CommunityScreen> {
  late Future<List<Map<String, dynamic>>> _history = _load();
  String? _busy;

  Future<List<Map<String, dynamic>>> _load() async {
    final results = await Future.wait([
      api.communityHistory(),
      // Older backends have no walk-audit history; the community feed still shows.
      api.walkAuditHistory().catchError((_) => <Map<String, dynamic>>[]),
    ]);
    final rows = [...results[0], ...results[1]];
    rows.sort((a, b) => '${b['ts']}'.compareTo('${a['ts']}'));
    return rows;
  }

  Future<void> _rollback(Map<String, dynamic> row) async {
    final reason = await askReason(
      context,
      title: 'Roll back this contribution?',
      body:
          'This removes it from the community map and routing. The original and your reason stay in public history.',
      action: 'Roll back',
    );
    if (reason == null || !mounted) return;
    setState(() => _busy = row['id']);
    try {
      await api.rollbackContribution(row['id'], reason);
      if (mounted) setState(() => _history = _load());
    } catch (_) {
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(
            content: Text('Could not roll back. Refresh and try again.'),
          ),
        );
      }
    } finally {
      if (mounted) setState(() => _busy = null);
    }
  }

  Color _colorFor(Map<String, dynamic> row) {
    final type = row['type'];
    if (type == 'rollback' || type == 'dismiss') return Colors.grey;
    if (type == 'report') return hexColor('#F9A825');
    if (row['category'] == 'no-entry') return hexColor('#C62828');
    return hexColor('#7B1FA2');
  }

  @override
  Widget build(BuildContext context) => Scaffold(
    appBar: AppBar(
      title: const Text('Community edits'),
      actions: [
        IconButton(
          tooltip: 'Refresh history',
          icon: const Icon(Icons.refresh),
          onPressed: () => setState(() => _history = _load()),
        ),
      ],
    ),
    body: FutureBuilder<List<Map<String, dynamic>>>(
      future: _history,
      builder: (ctx, snapshot) {
        if (snapshot.hasError) {
          return const Center(
            child: Text('History could not load. Tap refresh to retry.'),
          );
        }
        if (!snapshot.hasData) {
          return const Center(child: CircularProgressIndicator());
        }
        return ListView(
          children: [
            const Padding(
              padding: EdgeInsets.all(16),
              child: Text(
                '$routeDisclaimer\n\n'
                'A shared map for biking, walking, rolling, and transit in Greenville. '
                'Tap the map to add a place, correct information, or draw a local route. '
                'Changes publish immediately. Roll back inaccurate contributions or '
                'dismiss resolved reports with a public reason. Tap an entry to see it on the map.',
              ),
            ),
            if (snapshot.data!.isEmpty)
              const ListTile(
                title: Text('Be the first to share local knowledge.'),
              ),
            for (final row in snapshot.data!)
              ListTile(
                isThreeLine: true,
                leading: _GeometryThumb(
                  geometry: row['geometry'],
                  color: _colorFor(row),
                  fallback: editKinds[row['type']]?.icon ?? Icons.history,
                ),
                title: Text(
                  row['name']?.toString().isNotEmpty == true
                      ? row['name']
                      : row['category'] ?? 'Contribution',
                ),
                subtitle: Text(
                  '${editKinds[row['type']]?.label ?? row['type'] ?? ''}'
                  ' · ${row['ts_display'] ?? row['ts'] ?? ''}'
                  '${row['active'] == true ? ' · On the map' : ''}'
                  '\n${row['comment'] ?? ''}',
                ),
                onTap: row['geometry'] == null
                    ? null
                    : () => Navigator.pop(context, row),
                trailing: row['active'] == true && row['type'] != 'report'
                    ? IconButton(
                        tooltip: 'Roll back contribution',
                        icon: _busy == row['id']
                            ? const SizedBox(
                                width: 20,
                                height: 20,
                                child: CircularProgressIndicator(),
                              )
                            : const Icon(Icons.undo),
                        onPressed: _busy == null ? () => _rollback(row) : null,
                      )
                    : null,
              ),
          ],
        );
      },
    ),
  );
}

/// A 48 px sketch of the contribution's shape: dot, polyline, or filled ring.
/// No basemap (that would need a static tile service); the shape plus the
/// tap-to-fly-there is the preview.
class _GeometryThumb extends StatelessWidget {
  const _GeometryThumb({
    required this.geometry,
    required this.color,
    required this.fallback,
  });
  final dynamic geometry;
  final Color color;
  final IconData fallback;

  @override
  Widget build(BuildContext context) {
    final points = geometryLatLngs(geometry);
    return Container(
      width: 48,
      height: 48,
      decoration: BoxDecoration(
        color: color.withValues(alpha: 0.12),
        borderRadius: BorderRadius.circular(8),
      ),
      child: points.isEmpty
          ? Icon(fallback, color: color)
          : CustomPaint(
              painter: _GeometryPainter(
                points,
                color,
                filled: geometry is Map && geometry['type'] == 'Polygon',
              ),
            ),
    );
  }
}

class _GeometryPainter extends CustomPainter {
  _GeometryPainter(this.points, this.color, {required this.filled});
  final List<LatLng> points;
  final Color color;
  final bool filled;

  @override
  void paint(Canvas canvas, Size size) {
    final paint = Paint()
      ..color = color
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round
      ..strokeJoin = StrokeJoin.round
      ..style = PaintingStyle.stroke;
    if (points.length == 1) {
      canvas.drawCircle(size.center(Offset.zero), 5, paint..style = PaintingStyle.fill);
      return;
    }
    var minLat = 90.0, maxLat = -90.0, minLon = 180.0, maxLon = -180.0;
    for (final p in points) {
      if (p.latitude < minLat) minLat = p.latitude;
      if (p.latitude > maxLat) maxLat = p.latitude;
      if (p.longitude < minLon) minLon = p.longitude;
      if (p.longitude > maxLon) maxLon = p.longitude;
    }
    // Uniform scale so the shape keeps its proportions, centred in the box.
    const pad = 8.0;
    final span = (maxLon - minLon).clamp(1e-9, double.infinity);
    final spanLat = (maxLat - minLat).clamp(1e-9, double.infinity);
    final scale = ((size.width - 2 * pad) / span).clamp(
      0.0,
      (size.height - 2 * pad) / spanLat,
    );
    final dx = (size.width - span * scale) / 2;
    final dy = (size.height - spanLat * scale) / 2;
    final path = Path();
    for (var i = 0; i < points.length; i++) {
      final x = dx + (points[i].longitude - minLon) * scale;
      final y = dy + (maxLat - points[i].latitude) * scale;
      i == 0 ? path.moveTo(x, y) : path.lineTo(x, y);
    }
    if (filled) {
      canvas.drawPath(
        path,
        Paint()
          ..color = color.withValues(alpha: 0.3)
          ..style = PaintingStyle.fill,
      );
    }
    canvas.drawPath(path, paint);
  }

  @override
  bool shouldRepaint(_GeometryPainter old) =>
      old.points != points || old.color != color || old.filled != filled;
}
