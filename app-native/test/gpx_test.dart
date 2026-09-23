import 'package:bwg_app_native/gpx.dart';
import 'package:bwg_app_native/rides.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

void main() {
  test('GPX 1.1 has one trkseg per segment and escapes the name', () {
    final ride = Ride(
      id: 'r',
      name: 'Tom & Jerry <"fast">',
      startedAt: DateTime.utc(2026, 9, 5, 12),
      endedAt: DateTime.utc(2026, 9, 5, 13),
      points: const [
        LatLng(34.85, -82.4),
        LatLng(34.851, -82.4),
        LatLng(34.86, -82.41),
        LatLng(34.861, -82.41),
      ],
      segmentStarts: const [0, 2],
    );
    final gpx = rideToGpx(ride);
    expect(gpx, startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
    expect(gpx, contains('<gpx version="1.1"'));
    expect(gpx, contains('xmlns="http://www.topografix.com/GPX/1/1"'));
    expect(
      gpx,
      contains('<name>Tom &amp; Jerry &lt;&quot;fast&quot;&gt;</name>'),
    );
    expect(gpx, isNot(contains('Tom & Jerry')));
    expect('<trkseg>'.allMatches(gpx), hasLength(2));
    expect('<trkpt '.allMatches(gpx), hasLength(4));
    expect(
      gpx,
      contains(
        '<trkpt lat="34.850000" lon="-82.400000">'
        '<time>2026-09-05T12:00:00.000Z</time></trkpt>',
      ),
    );
    // Metadata + one interpolated <time> per point: monotonic, first ==
    // startedAt, last == startedAt + duration.
    final times = [
      for (final m in RegExp('<trkpt [^>]*><time>([^<]+)</time>').allMatches(gpx))
        DateTime.parse(m.group(1)!),
    ];
    expect(times, hasLength(4));
    expect(times.first, ride.startedAt);
    expect(times.last, ride.endedAt);
    for (var i = 1; i < times.length; i++) {
      expect(times[i].isBefore(times[i - 1]), isFalse);
    }
    expect(gpx.trim(), endsWith('</gpx>'));
  });
}
