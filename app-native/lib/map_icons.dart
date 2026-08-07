import 'dart:typed_data';
import 'dart:ui' as ui;

import 'package:flutter/material.dart';

/// Map pin bitmaps.
///
/// MapLibre has no notion of Material icons, so each point layer's icon is
/// painted once into a PNG here and handed to `controller.addImage()`; the
/// symbol layer then references it by name. Bitmaps are rendered at the
/// device pixel ratio so `iconSize: 1.0` means exactly [pinHeight] dp tall.
const pinWidth = 30.0;
const pinHeight = 40.0;

/// Paints a teardrop pin in [color] with [icon] knocked out in white.
///
/// [devicePixelRatio] keeps the bitmap crisp: MapLibre draws added images in
/// physical pixels, so a 3x device needs a 3x bitmap to render at 1.0 scale.
Future<Uint8List> renderPin({
  required IconData icon,
  required Color color,
  required double devicePixelRatio,
  double scale = 1.0,
}) async {
  final ratio = devicePixelRatio * scale;
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  canvas.scale(ratio);

  const center = Offset(pinWidth / 2, pinWidth / 2);
  const radius = pinWidth / 2 - 2;

  final head = Path()..addOval(Rect.fromCircle(center: center, radius: radius));
  final tail = Path()
    ..moveTo(center.dx - radius * 0.55, center.dy + radius * 0.72)
    ..lineTo(center.dx, pinHeight - 1)
    ..lineTo(center.dx + radius * 0.55, center.dy + radius * 0.72)
    ..close();
  final pin = Path.combine(PathOperation.union, head, tail);

  // Soft drop shadow so pins read against both the basemap and line layers.
  canvas.drawShadow(pin, Colors.black.withValues(alpha: 0.5), 2.0, false);
  canvas.drawPath(pin, Paint()..color = color..isAntiAlias = true);
  canvas.drawPath(
    pin,
    Paint()
      ..color = Colors.white
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.0
      ..isAntiAlias = true,
  );

  final glyph = TextPainter(textDirection: TextDirection.ltr)
    ..text = TextSpan(
      text: String.fromCharCode(icon.codePoint),
      style: TextStyle(
        fontSize: radius * 1.25,
        fontFamily: icon.fontFamily,
        package: icon.fontPackage,
        color: Colors.white,
        height: 1.0,
      ),
    )
    ..layout();
  glyph.paint(canvas, center - Offset(glyph.width / 2, glyph.height / 2));

  final image = await recorder.endRecording().toImage(
        (pinWidth * ratio).ceil(),
        (pinHeight * ratio).ceil(),
      );
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  image.dispose();
  return bytes!.buffer.asUint8List();
}

/// Size of the navigation puck bitmap, in dp.
const puckSize = 48.0;

/// Paints the rider's own marker for navigation: a Google-Maps-style arrow,
/// or — when [icon] is given — the travelling mode's glyph (cyclist, walker,
/// wheelchair user) in a shaded sphere with a heading wedge.
///
/// Painted pointing north; the symbol layer rotates it to the GPS bearing
/// with `icon-rotation-alignment: map` and keeps it facing the camera with
/// `icon-pitch-alignment: viewport`. The radial-gradient shading + grounded
/// ellipse shadow are what make it read as an object standing ON the tilted
/// map rather than paint flattened onto it. (A true 3D model needs a custom
/// native MapLibre render layer — this is the honest 2.5D version.)
Future<Uint8List> renderPuck({
  required Color color,
  required double devicePixelRatio,
  IconData? icon,
}) async {
  final ratio = devicePixelRatio;
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  canvas.scale(ratio);

  const c = puckSize / 2;
  final hsl = HSLColor.fromColor(color);
  final lit = hsl.withLightness((hsl.lightness + 0.22).clamp(0.0, 1.0)).toColor();
  final dark = hsl.withLightness((hsl.lightness - 0.18).clamp(0.0, 1.0)).toColor();

  // Grounded ellipse shadow under the marker: the depth cue a flat
  // drawShadow can't give once the icon is billboarded upright.
  canvas.drawOval(
    Rect.fromCenter(
        center: const Offset(c, puckSize - 5.0), width: 26.0, height: 8.0),
    Paint()
      ..color = Colors.black.withValues(alpha: 0.35)
      ..maskFilter = const MaskFilter.blur(BlurStyle.normal, 2.5),
  );

  if (icon == null) {
    // The classic chevron, shaded top-to-bottom so it reads as a solid.
    final arrow = Path()
      ..moveTo(c, c - 16.0)
      ..lineTo(c + 12.0, c + 12.0)
      ..lineTo(c, c + 5.5)
      ..lineTo(c - 12.0, c + 12.0)
      ..close();
    canvas.drawShadow(arrow, Colors.black.withValues(alpha: 0.5), 3.0, false);
    canvas.drawPath(
      arrow,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 4.0
        ..strokeJoin = StrokeJoin.round
        ..isAntiAlias = true,
    );
    canvas.drawPath(
      arrow,
      Paint()
        ..shader = ui.Gradient.linear(
          const Offset(c, c - 16.0),
          const Offset(c, c + 12.0),
          [lit, color, dark],
          [0.0, 0.55, 1.0],
        )
        ..isAntiAlias = true,
    );
  } else {
    // Mode glyph on a shaded sphere, with a wedge on top for the heading.
    const r = puckSize / 2 - 9.0;
    const center = Offset(c, c - 2.0);
    final wedge = Path()
      ..moveTo(c, 1.0)
      ..lineTo(c + 7.5, center.dy - r + 3.0)
      ..lineTo(c - 7.5, center.dy - r + 3.0)
      ..close();
    canvas.drawPath(wedge, Paint()..color = dark..isAntiAlias = true);
    canvas.drawPath(
      wedge,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.0
        ..isAntiAlias = true,
    );
    final disc = Rect.fromCircle(center: center, radius: r);
    // Off-center highlight = sphere. The rim ring grounds it.
    canvas.drawOval(
      disc,
      Paint()
        ..shader = ui.Gradient.radial(
          center - const Offset(r * 0.35, r * 0.45),
          r * 1.7,
          [lit, color, dark],
          [0.0, 0.5, 1.0],
        )
        ..isAntiAlias = true,
    );
    canvas.drawOval(
      disc,
      Paint()
        ..color = Colors.white
        ..style = PaintingStyle.stroke
        ..strokeWidth = 2.5
        ..isAntiAlias = true,
    );
    final glyph = TextPainter(textDirection: TextDirection.ltr)
      ..text = TextSpan(
        text: String.fromCharCode(icon.codePoint),
        style: TextStyle(
          fontSize: r * 1.3,
          fontFamily: icon.fontFamily,
          package: icon.fontPackage,
          color: Colors.white,
          height: 1.0,
          shadows: const [
            Shadow(color: Colors.black38, offset: Offset(0, 1), blurRadius: 2),
          ],
        ),
      )
      ..layout();
    glyph.paint(canvas, center - Offset(glyph.width / 2, glyph.height / 2));
  }

  final image = await recorder.endRecording().toImage(
        (puckSize * ratio).ceil(),
        (puckSize * ratio).ceil(),
      );
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  image.dispose();
  return bytes!.buffer.asUint8List();
}

/// Size of the turn marker bitmap, in dp.
const turnMarkerSize = 22.0;

/// Paints the arrowhead dropped on the map at every upcoming turn.
///
/// The symbol layer rotates it with `icon-rotate` against the route's heading
/// (`icon-rotation-alignment: map`), so the arrow points the way the rider will
/// be travelling after the turn — the map itself shows the upcoming turns,
/// instead of the maneuver card being the only place they exist. Painted
/// pointing north (up) because `icon-rotate: 0` means north.
Future<Uint8List> renderTurnMarker({
  required Color color,
  required double devicePixelRatio,
  double scale = 1.0,
}) async {
  final ratio = devicePixelRatio * scale;
  final recorder = ui.PictureRecorder();
  final canvas = Canvas(recorder);
  canvas.scale(ratio);

  const c = turnMarkerSize / 2;
  final disc = Rect.fromCircle(center: const Offset(c, c), radius: c - 1.5);
  canvas.drawShadow(
      Path()..addOval(disc), Colors.black.withValues(alpha: 0.4), 1.5, false);
  canvas.drawOval(disc, Paint()..color = Colors.white..isAntiAlias = true);
  canvas.drawOval(
    disc,
    Paint()
      ..color = color
      ..style = PaintingStyle.stroke
      ..strokeWidth = 2.0
      ..isAntiAlias = true,
  );

  final arrow = Path()
    ..moveTo(c, c - 6.0)
    ..lineTo(c + 4.6, c + 5.0)
    ..lineTo(c, c + 2.4)
    ..lineTo(c - 4.6, c + 5.0)
    ..close();
  canvas.drawPath(arrow, Paint()..color = color..isAntiAlias = true);

  final image = await recorder.endRecording().toImage(
        (turnMarkerSize * ratio).ceil(),
        (turnMarkerSize * ratio).ceil(),
      );
  final bytes = await image.toByteData(format: ui.ImageByteFormat.png);
  image.dispose();
  return bytes!.buffer.asUint8List();
}
