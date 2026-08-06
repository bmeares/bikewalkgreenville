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
const puckSize = 44.0;

/// Paints the rider's own marker for navigation: a Google-Maps-style arrow,
/// or — when [icon] is given — the travelling mode's glyph (cyclist, walker,
/// wheelchair user) in a disc with a heading wedge.
///
/// Painted pointing north; the symbol layer rotates it to the GPS bearing
/// with `icon-rotation-alignment: map`, so it turns with the street grid the
/// way the Google Maps arrow does.
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
  if (icon == null) {
    // The classic chevron: solid arrow, white outline, soft shadow.
    final arrow = Path()
      ..moveTo(c, c - 15.0)
      ..lineTo(c + 11.0, c + 12.0)
      ..lineTo(c, c + 6.0)
      ..lineTo(c - 11.0, c + 12.0)
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
    canvas.drawPath(arrow, Paint()..color = color..isAntiAlias = true);
  } else {
    // Mode glyph in a disc, with a wedge on top for the heading.
    const r = puckSize / 2 - 8.0;
    final wedge = Path()
      ..moveTo(c, 1.0)
      ..lineTo(c + 7.0, c - r + 3.0)
      ..lineTo(c - 7.0, c - r + 3.0)
      ..close();
    final disc = Path()..addOval(Rect.fromCircle(center: const Offset(c, c), radius: r));
    final shape = Path.combine(PathOperation.union, disc, wedge);
    canvas.drawShadow(shape, Colors.black.withValues(alpha: 0.5), 3.0, false);
    canvas.drawPath(shape, Paint()..color = color..isAntiAlias = true);
    canvas.drawPath(
      shape,
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
        ),
      )
      ..layout();
    glyph.paint(
        canvas, Offset(c, c) - Offset(glyph.width / 2, glyph.height / 2));
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
