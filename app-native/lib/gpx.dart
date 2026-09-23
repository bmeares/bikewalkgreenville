import 'dart:convert';

import 'package:share_plus/share_plus.dart';

import 'nav.dart' show metersBetween;
import 'rides.dart';

String _esc(String s) => s
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&apos;');

/// GPX 1.1 track, one `<trkseg>` per recorded segment. [Ride] keeps no
/// per-point timestamps, so each `<time>` is spread from `startedAt` to
/// `startedAt + duration` by distance along the segments (gaps between
/// segments cost no time).
// ponytail: interpolated, store per-point times if Strava pacing matters.
String rideToGpx(Ride ride) {
  final segments = ride.segments().toList();
  var total = 0.0;
  for (final seg in segments) {
    for (var i = 1; i < seg.length; i++) {
      total += metersBetween(seg[i - 1], seg[i]);
    }
  }
  final start = ride.startedAt.toUtc();
  final ms = ride.duration.inMilliseconds;
  var along = 0.0;
  String at() => start
      .add(Duration(milliseconds: total == 0 ? 0 : (ms * along / total).round()))
      .toIso8601String();
  final b = StringBuffer()
    ..writeln('<?xml version="1.0" encoding="UTF-8"?>')
    ..writeln(
      '<gpx version="1.1" creator="Bike Walk Greenville" '
      'xmlns="http://www.topografix.com/GPX/1/1">',
    )
    ..writeln(
      '  <metadata><name>${_esc(ride.name)}</name>'
      '<time>${ride.startedAt.toUtc().toIso8601String()}</time></metadata>',
    )
    ..writeln('  <trk>')
    ..writeln('    <name>${_esc(ride.name)}</name>');
  for (final segment in segments) {
    b.writeln('    <trkseg>');
    for (var i = 0; i < segment.length; i++) {
      final p = segment[i];
      if (i > 0) along += metersBetween(segment[i - 1], p);
      b.writeln(
        '      <trkpt lat="${p.latitude.toStringAsFixed(6)}" '
        'lon="${p.longitude.toStringAsFixed(6)}"><time>${at()}</time></trkpt>',
      );
    }
    b.writeln('    </trkseg>');
  }
  b
    ..writeln('  </trk>')
    ..writeln('</gpx>');
  return b.toString();
}

/// Hand the ride to the system share sheet (Strava, Drive, email…). On web
/// share_plus downloads the file when the browser cannot share files.
Future<void> shareRideGpx(Ride ride) async {
  final safe = ride.name.replaceAll(RegExp(r'[^A-Za-z0-9 _-]'), '').trim();
  final name = '${safe.isEmpty ? 'ride' : safe}.gpx';
  await SharePlus.instance.share(
    ShareParams(
      files: [
        XFile.fromData(
          utf8.encode(rideToGpx(ride)),
          mimeType: 'application/gpx+xml',
          name: name,
        ),
      ],
      fileNameOverrides: [name],
      subject: ride.name,
    ),
  );
}
