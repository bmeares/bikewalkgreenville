import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../theme.dart';

/// Small area chart of the route's elevation, for the trip preview.
///
/// Stretches steeper than ~8% (ADA's 1:12 — a hard push on foot, a real hill
/// on a bike) are drawn in the warning red so the preview says not just "210 ft
/// of climb" but *where* it bites.
class ElevationProfile extends StatelessWidget {
  /// [distance_from_start_m, elevation_ft] pairs, ascending by distance.
  final List<List<double>> profile;
  final double height;

  const ElevationProfile({super.key, required this.profile, this.height = 44});

  @override
  Widget build(BuildContext context) {
    if (profile.length < 2) return const SizedBox.shrink();
    final elevations = profile.map((p) => p[1]).toList();
    final lo = elevations.reduce(math.min).round();
    final hi = elevations.reduce(math.max).round();
    return Row(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: [
        Expanded(
          child: CustomPaint(
            size: Size.fromHeight(height),
            painter: _ProfilePainter(profile),
          ),
        ),
        const SizedBox(width: 8),
        Column(
          mainAxisAlignment: MainAxisAlignment.spaceBetween,
          crossAxisAlignment: CrossAxisAlignment.end,
          children: [
            Text('$hi ft',
                style: const TextStyle(fontSize: 10, color: Colors.black54)),
            Text('$lo ft',
                style: const TextStyle(fontSize: 10, color: Colors.black54)),
          ],
        ),
      ],
    );
  }
}

class _ProfilePainter extends CustomPainter {
  final List<List<double>> profile;
  _ProfilePainter(this.profile);

  static const _steepGrade = 0.08;

  @override
  void paint(Canvas canvas, Size size) {
    final maxD = profile.last[0];
    if (maxD <= 0) return;
    var minE = double.infinity, maxE = -double.infinity;
    for (final p in profile) {
      minE = math.min(minE, p[1]);
      maxE = math.max(maxE, p[1]);
    }
    // Flat routes still deserve a line somewhere sensible, not a wall.
    final span = math.max(maxE - minE, 20.0);

    Offset at(List<double> p) => Offset(
          p[0] / maxD * size.width,
          size.height - ((p[1] - minE) / span) * (size.height - 4) - 2,
        );

    // Filled area under the whole profile.
    final area = Path()..moveTo(0, size.height);
    for (final p in profile) {
      final o = at(p);
      area.lineTo(o.dx, o.dy);
    }
    area
      ..lineTo(size.width, size.height)
      ..close();
    canvas.drawPath(
      area,
      Paint()..color = brandGreen.withValues(alpha: 0.25),
    );

    // The line itself, segment by segment so steep stretches can go red.
    final ok = Paint()
      ..color = brandDark
      ..strokeWidth = 2
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;
    final steep = Paint()
      ..color = warnRed
      ..strokeWidth = 2.5
      ..strokeCap = StrokeCap.round
      ..style = PaintingStyle.stroke;
    for (var i = 1; i < profile.length; i++) {
      final a = profile[i - 1], b = profile[i];
      final run = b[0] - a[0];
      final riseM = (b[1] - a[1]).abs() / 3.28084;
      final isSteep = run > 0 && riseM / run > _steepGrade && riseM >= 2.4;
      canvas.drawLine(at(a), at(b), isSteep ? steep : ok);
    }
  }

  @override
  bool shouldRepaint(_ProfilePainter old) => old.profile != profile;
}
