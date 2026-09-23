import 'package:flutter/material.dart';

enum RecordState { idle, recording, paused }

const recordRed = Color(0xFFC62828);

/// The rail's record button face, red in every state: idle = solid dot in a
/// ring, recording = stop square in a pulsing ring, paused = pause bars.
class RecordIcon extends StatefulWidget {
  final RecordState state;
  final double size;
  const RecordIcon({super.key, required this.state, this.size = 24});

  @override
  State<RecordIcon> createState() => _RecordIconState();
}

class _RecordIconState extends State<RecordIcon>
    with SingleTickerProviderStateMixin {
  late final _pulse = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 900),
  );

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _sync();
  }

  @override
  void didUpdateWidget(RecordIcon old) {
    super.didUpdateWidget(old);
    _sync();
  }

  void _sync() {
    final pulse =
        widget.state == RecordState.recording &&
        !(MediaQuery.maybeDisableAnimationsOf(context) ?? false);
    if (pulse && !_pulse.isAnimating) {
      _pulse.repeat(reverse: true);
    } else if (!pulse && _pulse.isAnimating) {
      _pulse.stop();
      _pulse.value = 0;
    }
  }

  @override
  void dispose() {
    _pulse.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => SizedBox.square(
    dimension: widget.size,
    child: AnimatedBuilder(
      animation: _pulse,
      builder: (_, _) =>
          CustomPaint(painter: _RecordPainter(widget.state, _pulse.value)),
    ),
  );
}

class _RecordPainter extends CustomPainter {
  final RecordState state;
  final double pulse; // 0..1
  _RecordPainter(this.state, this.pulse);

  @override
  void paint(Canvas canvas, Size size) {
    final c = size.center(Offset.zero);
    final r = size.shortestSide / 2;
    final ring = Paint()
      ..color = recordRed.withValues(alpha: 1 - 0.6 * pulse)
      ..style = PaintingStyle.stroke
      ..strokeWidth = r * (0.16 + 0.08 * pulse);
    canvas.drawCircle(c, r * 0.88, ring);
    final fill = Paint()..color = recordRed;
    switch (state) {
      case RecordState.idle:
        canvas.drawCircle(c, r * 0.5, fill);
      case RecordState.recording:
        final s = r * 0.8;
        canvas.drawRRect(
          RRect.fromRectAndRadius(
            Rect.fromCenter(center: c, width: s, height: s),
            Radius.circular(r * 0.1),
          ),
          fill,
        );
      case RecordState.paused:
        final w = r * 0.26, h = r * 0.9, gap = r * 0.16;
        for (final dx in [-(gap / 2 + w / 2), gap / 2 + w / 2]) {
          canvas.drawRect(
            Rect.fromCenter(center: c + Offset(dx, 0), width: w, height: h),
            fill,
          );
        }
    }
  }

  @override
  bool shouldRepaint(_RecordPainter old) =>
      old.state != state || old.pulse != pulse;
}
