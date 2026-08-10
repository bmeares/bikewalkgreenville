import 'dart:math' as math;

import 'package:flutter/material.dart';
import 'package:maplibre_gl/maplibre_gl.dart';

import 'theme.dart';

/// Turn-by-turn model + progress math for the bike / walk / transit router.
///
/// The API (`/map-layers/route`) returns a LineString plus a `steps` list of
/// maneuvers; everything here is about answering "where on that line am I, and
/// what do I do next?" from a stream of GPS fixes.

const _mPerDegLat = 111320.0;

double metersBetween(LatLng a, LatLng b) {
  final cosLat = math.cos(a.latitude * math.pi / 180.0);
  final dx = (b.longitude - a.longitude) * _mPerDegLat * cosLat;
  final dy = (b.latitude - a.latitude) * _mPerDegLat;
  return math.sqrt(dx * dx + dy * dy);
}

/// Compass bearing (degrees) from [a] to [b].
double bearingBetween(LatLng a, LatLng b) {
  final lat1 = a.latitude * math.pi / 180.0;
  final lat2 = b.latitude * math.pi / 180.0;
  final dLon = (b.longitude - a.longitude) * math.pi / 180.0;
  final y = math.sin(dLon) * math.cos(lat2);
  final x = math.cos(lat1) * math.sin(lat2) -
      math.sin(lat1) * math.cos(lat2) * math.cos(dLon);
  return (math.atan2(y, x) * 180.0 / math.pi + 360.0) % 360.0;
}

/// One maneuver from the API's `steps` array.
class RouteStep {
  final String maneuver;
  final String instruction;
  final String? name;
  final double distanceM;
  final int startIndex;

  /// `no_sidewalk` / `no_bike_lane` when this step travels on a street missing
  /// the infrastructure the mode needs, else null.
  final String? warn;

  /// How much of [distanceM] is missing that infrastructure.
  final double warnM;

  /// Feet of ascent within this step (0 when flat or unknown).
  final int climbFt;

  RouteStep({
    required this.maneuver,
    required this.instruction,
    required this.name,
    required this.distanceM,
    required this.startIndex,
    this.warn,
    this.warnM = 0.0,
    this.climbFt = 0,
  });

  factory RouteStep.fromJson(Map<String, dynamic> j) => RouteStep(
        maneuver: (j['maneuver'] ?? 'straight').toString(),
        instruction: (j['instruction'] ?? 'Continue').toString(),
        name: j['name']?.toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        startIndex: (j['start_index'] as num?)?.toInt() ?? 0,
        warn: j['warn']?.toString(),
        warnM: (j['warn_m'] as num?)?.toDouble() ?? 0.0,
        climbFt: (j['climb_ft'] as num?)?.round() ?? 0,
      );

  /// A climb worth flagging on this step: steeper than ~8% (ADA's 1:12) over
  /// more than sampling noise (8 ft — mirrors the server's noise gate).
  bool get isSteepClimb =>
      climbFt >= 8 &&
      distanceM > 0 &&
      (climbFt / 3.28084) / distanceM > 0.08;

  IconData get icon {
    switch (maneuver) {
      case 'left':
        return Icons.turn_left;
      case 'right':
        return Icons.turn_right;
      case 'slight-left':
        return Icons.turn_slight_left;
      case 'slight-right':
        return Icons.turn_slight_right;
      case 'sharp-left':
        return Icons.turn_sharp_left;
      case 'sharp-right':
        return Icons.turn_sharp_right;
      case 'uturn':
        return Icons.u_turn_left;
      case 'depart':
        return Icons.navigation;
      case 'arrive':
        return Icons.sports_score;
      case 'board':
        return Icons.departure_board;
      case 'ride':
        return Icons.directions_bus;
      case 'alight':
        return Icons.pin_drop;
      case 'rent':
        return Icons.pedal_bike;
      case 'dock':
        return Icons.lock_outline;
      default:
        return Icons.straight;
    }
  }
}

/// A stretch of the route that lacks the infrastructure the mode needs, as an
/// index range into [NavRoute.points].
class WarnRange {
  final String kind; // no_sidewalk | no_bike_lane
  final int start;
  final int end;
  final double distanceM;

  const WarnRange({
    required this.kind,
    required this.start,
    required this.end,
    required this.distanceM,
  });

  factory WarnRange.fromJson(Map<String, dynamic> j) => WarnRange(
        kind: (j['kind'] ?? 'unknown').toString(),
        start: (j['start'] as num?)?.toInt() ?? 0,
        end: (j['end'] as num?)?.toInt() ?? 0,
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
      );
}

/// Shortest infrastructure gap worth telling a rider about, in feet. A block
/// or two of missing bike lane is normal here; a fifth of a mile is a decision.
const warnMinFt = 1000.0;

/// One kind of gap, totalled across the route — what the hazards sheet says.
class RouteWarning {
  final String kind;
  final double distanceM;
  final String label; // "0.4 mi with no sidewalk"
  final String message; // full sentence for the banner / detail sheet

  const RouteWarning({
    required this.kind,
    required this.distanceM,
    required this.label,
    required this.message,
  });

  factory RouteWarning.fromJson(Map<String, dynamic> j) => RouteWarning(
        kind: (j['kind'] ?? 'unknown').toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        label: (j['label'] ?? '').toString(),
        message: (j['message'] ?? '').toString(),
      );

  IconData get icon => warnIcons[kind] ?? Icons.warning_amber_rounded;
}

/// Per-warning presentation. Keyed by the server's `kind`, so a kind the app
/// has not been taught about falls back to something honest rather than
/// borrowing another warning's wording -- a `steep` grade used to be announced
/// as a missing bike lane.
const warnIcons = <String, IconData>{
  'no_sidewalk': Icons.no_transfer_outlined,
  'no_bike_lane': Icons.directions_bike_outlined,
  'steep': Icons.trending_up,
};

/// Short phrase for a per-step warning; `{d}` worth of distance is prepended
/// by the caller.
const warnStepPhrases = <String, String>{
  'no_sidewalk': 'with no sidewalk',
  'no_bike_lane': 'with no bike lane',
  'steep': 'of steep grade',
};

/// Sentence for the maneuver card, mid-turn.
const warnStepSentences = <String, String>{
  'no_sidewalk': 'No sidewalk mapped on this stretch',
  'no_bike_lane': 'No bike lane on this stretch',
  'steep': 'Steep grade on this stretch',
};

String warnStepPhrase(String? kind) =>
    warnStepPhrases[kind] ?? 'flagged by the router';

String warnStepSentence(String? kind) =>
    warnStepSentences[kind] ?? 'This stretch is flagged by the router';

/// A different way to make the same trip, priced by the router.
class RouteAlternative {
  final String plan; // bike | walk | roll | bcycle | bike-transit | ...
  final String label;
  final String iconMode;
  final double distanceM;
  final double durationMin;
  final List<RouteWarning> warnings;

  const RouteAlternative({
    required this.plan,
    required this.label,
    required this.iconMode,
    required this.distanceM,
    required this.durationMin,
    required this.warnings,
  });

  factory RouteAlternative.fromJson(Map<String, dynamic> j) =>
      RouteAlternative(
        plan: (j['plan'] ?? '').toString(),
        label: (j['label'] ?? '').toString(),
        iconMode: (j['icon_mode'] ?? '').toString(),
        distanceM: (j['distance_m'] as num?)?.toDouble() ?? 0.0,
        durationMin: (j['duration_min'] as num?)?.toDouble() ?? 0.0,
        warnings: ((j['warnings'] as List?) ?? [])
            .map((w) => RouteWarning.fromJson(Map<String, dynamic>.from(w)))
            .toList(),
      );
}

/// A computed route: the line, its steps and the cumulative distance table
/// used to place the rider along it.
class NavRoute {
  final List<LatLng> points;
  final List<RouteStep> steps;
  final List<double> cumulative; // meters from start to points[i]
  final double distanceM;
  final double durationMin;
  /// Total ascent over the trip, in feet. 0 when the router had no elevation.
  final int climbFt;

  /// [distance_from_start_m, elevation_ft] samples for the preview sparkline,
  /// or null when the router had no elevation for this trip.
  final List<List<double>>? elevationProfile;
  /// The rider said they are on an e-bike, so the pace and hill cost reflect it.
  final bool ebike;
  final String mode; // bike | walk | roll | transit | bcycle
  final String? transitRoute; // Greenlink short name, transit only
  final String? boardStop;
  final String? routeColor; // official route color, transit only

  /// How the rider reaches the bus on a transit plan (`bike`, `walk`, `roll`).
  final String? accessMode;

  /// Router plan key (`bike`, `bike-transit`, `bcycle`, …) and its label.
  final String plan;
  final String planLabel;

  /// Stretches missing the infrastructure this mode needs, plus the per-kind
  /// totals the banner reads from.
  final List<WarnRange> warnRanges;
  final List<RouteWarning> warnings;

  /// Set when the mode's own network couldn't get there and the route fell back
  /// to plain streets; [fallbackNote] is the sentence to show.
  final String? fallback;
  final String? fallbackNote;

  /// Other itineraries the router costed for the same trip.
  final List<RouteAlternative> alternatives;

  /// Bike-share specifics, when [plan] is `bcycle`.
  final String? rentStation;
  final String? dockStation;
  final String? rentStationUri;

  /// Which alternate of this plan the server returned (`?alt=N`); 0 = the
  /// base route. [altDistinct] is false when the Nth alternate came back
  /// identical to the base — there is no genuinely different way.
  final int alt;
  final bool altDistinct;

  NavRoute._({
    required this.points,
    required this.steps,
    required this.cumulative,
    required this.distanceM,
    required this.durationMin,
    required this.climbFt,
    required this.elevationProfile,
    required this.ebike,
    required this.mode,
    required this.transitRoute,
    required this.boardStop,
    required this.routeColor,
    required this.accessMode,
    required this.plan,
    required this.planLabel,
    required this.warnRanges,
    required this.warnings,
    required this.fallback,
    required this.fallbackNote,
    required this.alternatives,
    required this.rentStation,
    required this.dockStation,
    required this.rentStationUri,
    required this.alt,
    required this.altDistinct,
  });

  factory NavRoute.fromFeature(Map<String, dynamic> feature) {
    final coords = (feature['geometry']?['coordinates'] as List? ?? [])
        .map<LatLng>((c) => LatLng(
              (c[1] as num).toDouble(),
              (c[0] as num).toDouble(),
            ))
        .toList();
    final props = Map<String, dynamic>.from(feature['properties'] ?? {});
    final steps = (props['steps'] as List? ?? [])
        .map<RouteStep>((s) => RouteStep.fromJson(Map<String, dynamic>.from(s)))
        .toList();
    final cumulative = <double>[0.0];
    for (var i = 1; i < coords.length; i++) {
      cumulative.add(cumulative[i - 1] + metersBetween(coords[i - 1], coords[i]));
    }
    final mode = (props['mode'] ?? 'bike').toString();
    return NavRoute._(
      points: coords,
      steps: steps,
      cumulative: cumulative,
      distanceM: (props['distance_m'] as num?)?.toDouble() ??
          (cumulative.isEmpty ? 0.0 : cumulative.last),
      durationMin: (props['duration_min'] as num?)?.toDouble() ?? 0.0,
      climbFt: (props['climb_ft'] as num?)?.round() ?? 0,
      elevationProfile: (props['elevation_profile'] is List &&
              (props['elevation_profile'] as List).length >= 2)
          ? [
              for (final p in props['elevation_profile'] as List)
                [(p[0] as num).toDouble(), (p[1] as num).toDouble()],
            ]
          : null,
      ebike: props['ebike'] == true,
      mode: mode,
      transitRoute: props['route']?.toString(),
      boardStop: props['board_stop']?.toString(),
      routeColor: props['route_color']?.toString(),
      accessMode: props['access_mode']?.toString(),
      plan: (props['plan'] ?? mode).toString(),
      planLabel: (props['plan_label'] ?? '').toString(),
      warnRanges: ((props['warn_ranges'] as List?) ?? [])
          .map((r) => WarnRange.fromJson(Map<String, dynamic>.from(r)))
          .toList(),
      warnings: ((props['warnings'] as List?) ?? [])
          .map((w) => RouteWarning.fromJson(Map<String, dynamic>.from(w)))
          .toList(),
      fallback: props['fallback']?.toString(),
      fallbackNote: props['fallback_note']?.toString(),
      alternatives: ((props['alternatives'] as List?) ?? [])
          .map((a) => RouteAlternative.fromJson(Map<String, dynamic>.from(a)))
          .toList(),
      rentStation: props['rent_station']?.toString(),
      dockStation: props['dock_station']?.toString(),
      rentStationUri: props['rent_station_uri']?.toString(),
      alt: (props['alt'] as num?)?.toInt() ?? 0,
      altDistinct: props['alt_distinct'] != false,
    );
  }

  bool get isEmpty => points.length < 2;

  LatLng get destination => points.last;

  bool get isTransit => mode == 'transit';

  /// "Bike" becomes "E-bike" when the rider's e-bike priced this plan — the
  /// server's plan key and label stay plain `bike` on purpose (they name the
  /// itinerary, not the rider's equipment).
  String get planDisplayLabel =>
      ebike && plan == 'bike' ? 'E-bike' : planLabel;

  IconData get planIcon => ebike && plan == 'bike'
      ? legModeIcons['ebike']!
      : (planIcons[plan] ?? Icons.directions);

  /// How the rider reaches / leaves the bus or dock on their own power.
  /// `access_mode` can arrive as a composite profile (`ebike:direct`), so it
  /// is reduced to a [routeLegColors] key.
  String get footMode {
    final a = (accessMode ?? (mode == 'roll' ? 'roll' : 'walk'))
        .split(':')
        .first;
    return a == 'ebike' ? 'bike' : a;
  }

  /// The mode each step is travelled with (indexes match [steps]): the foot
  /// or bike legs to a stop/dock, `transit` from board through alight,
  /// `bcycle` from rent through dock. Keys match [routeLegColors].
  List<String> stepModes() {
    final foot = footMode;
    var current = isTransit ? foot : (plan == 'bcycle' ? 'walk' : mode);
    final out = <String>[];
    for (final s in steps) {
      if (s.maneuver == 'board' || s.maneuver == 'ride') current = 'transit';
      if (s.maneuver == 'rent') current = 'bcycle';
      out.add(current);
      if (s.maneuver == 'alight') current = foot;
      if (s.maneuver == 'dock') current = 'walk';
    }
    return out;
  }

  /// GeoJSON for the "watch out here" overlay: one LineString per gap, drawn
  /// dashed on top of the route so a rider can see before starting exactly
  /// which blocks have no sidewalk or no bike lane.
  Map<String, dynamic> warnCollection() => {
        'type': 'FeatureCollection',
        'features': [
          for (final r in warnRanges)
            if (r.end > r.start && r.start >= 0 && r.end < points.length)
              {
                'type': 'Feature',
                'geometry': {
                  'type': 'LineString',
                  'coordinates': [
                    for (final p in points.sublist(r.start, r.end + 1))
                      [p.longitude, p.latitude],
                  ],
                },
                'properties': {'kind': r.kind},
              },
        ],
      };

  /// GeoJSON for the route line itself. A single-mode trip is one feature in
  /// its mode's color; a composite itinerary (transit, bike share) splits at
  /// the board/alight (or rent/dock) maneuvers so every leg draws in the
  /// color of how the rider travels it — bike to the stop, bus, walk the rest.
  Map<String, dynamic> routeCollection() {
    String colorFor(String m) => routeLegColors[m] ?? routeLegColors['bike']!;
    final foot = footMode;
    // (coordinate index where a leg starts, its color)
    final cuts = <(int, String)>[];
    if (isTransit) {
      cuts.add((0, colorFor(foot)));
      for (final s in steps) {
        if (s.maneuver == 'board') {
          cuts.add((s.startIndex, routeColor ?? colorFor('transit')));
        } else if (s.maneuver == 'alight') {
          cuts.add((s.startIndex, colorFor(foot)));
        }
      }
    } else if (plan == 'bcycle') {
      cuts.add((0, colorFor('walk')));
      for (final s in steps) {
        if (s.maneuver == 'rent') {
          cuts.add((s.startIndex, colorFor('bcycle')));
        } else if (s.maneuver == 'dock') {
          cuts.add((s.startIndex, colorFor('walk')));
        }
      }
    } else {
      cuts.add((0, routeColor ?? colorFor(mode)));
    }
    final features = <Map<String, dynamic>>[];
    for (var c = 0; c < cuts.length; c++) {
      final start = cuts[c].$1.clamp(0, points.length - 1);
      final end = (c + 1 < cuts.length ? cuts[c + 1].$1 : points.length - 1)
          .clamp(0, points.length - 1);
      if (end <= start) continue;
      features.add({
        'type': 'Feature',
        'geometry': {
          'type': 'LineString',
          'coordinates': [
            for (final p in points.sublist(start, end + 1))
              [p.longitude, p.latitude],
          ],
        },
        'properties': {'color': cuts[c].$2},
      });
    }
    return {'type': 'FeatureCollection', 'features': features};
  }

  // Hill severity thresholds. A stretch is "steep" past 7% grade — or past 5%
  // when it climbs 60+ ft in one go, because a long 6% hill is a real hill
  // whatever the arithmetic says. Noise gates mirror the server's: at least
  // 8 ft of rise (two contour intervals) over at least 25 m of run.
  static const hillModGrade = 0.04;
  static const hillSteepGrade = 0.07;
  static const hillVerySteepGrade = 0.10;
  static const hillLongGrade = 0.05;
  static const hillLongRiseFt = 60.0;
  static const _hillMinRunM = 25.0;
  static const _hillMinRiseFt = 8.0;

  /// Hills along the route, from the elevation profile — both directions,
  /// because descending a 10% grade is its own hazard on wheels.
  List<HillRange> hillRanges() {
    final prof = elevationProfile;
    if (prof == null || prof.length < 2 || points.length < 2) return const [];
    final out = <HillRange>[];
    for (var i = 1; i < prof.length; i++) {
      final run = prof[i][0] - prof[i - 1][0];
      final riseFt = (prof[i][1] - prof[i - 1][1]).abs();
      if (run < _hillMinRunM || riseFt < _hillMinRiseFt) continue;
      final grade = (riseFt / 3.28084) / run;
      if (grade < hillModGrade) continue;
      final severity = grade >= hillVerySteepGrade
          ? 'vsteep'
          : (grade >= hillSteepGrade ||
                  (grade >= hillLongGrade && riseFt >= hillLongRiseFt))
              ? 'steep'
              : 'mod';
      out.add(HillRange(
        startM: prof[i - 1][0],
        endM: prof[i][0],
        grade: grade,
        severity: severity,
      ));
    }
    return out;
  }

  /// Index of the route point nearest to [m] meters from the start.
  int _indexAtDistance(double m) {
    var lo = 0, hi = cumulative.length - 1;
    while (lo < hi) {
      final mid = (lo + hi) >> 1;
      if (cumulative[mid] < m) {
        lo = mid + 1;
      } else {
        hi = mid;
      }
    }
    return lo;
  }

  /// GeoJSON for the hill overlay, drawn on top of the route line and colored
  /// by severity ([hillColors]).
  Map<String, dynamic> hillCollection() => {
        'type': 'FeatureCollection',
        'features': [
          for (final h in hillRanges())
            if (_hillFeature(h) != null) _hillFeature(h)!,
        ],
      };

  Map<String, dynamic>? _hillFeature(HillRange h) {
    var si = _indexAtDistance(h.startM);
    if (si > 0 && cumulative[si] > h.startM) si--;
    final ei = _indexAtDistance(h.endM);
    if (ei <= si) return null;
    return {
      'type': 'Feature',
      'geometry': {
        'type': 'LineString',
        'coordinates': [
          for (final p in points.sublist(si, ei + 1))
            [p.longitude, p.latitude],
        ],
      },
      'properties': {
        'sev': h.severity,
        'grade': (h.grade * 100).round(),
      },
    };
  }

  /// One-line hill disclosure for the preview banner, or null when the route
  /// has nothing worth bracing for. Softened for e-bikes; wheelchair (roll)
  /// trips already get the server's ADA-based steep warning instead.
  String? hillSummary() {
    final hills = hillRanges();
    var steepM = 0.0;
    var maxGrade = 0.0;
    for (final h in hills) {
      if (h.severity != 'mod') steepM += h.endM - h.startM;
      maxGrade = math.max(maxGrade, h.grade);
    }
    if (steepM <= 0) return null;
    if (mode == 'roll' && !ebike) return null;
    final d = formatDistance(steepM);
    final pct = (maxGrade * 100).round();
    if (ebike) {
      return 'About $d of this route is on steep hills (up to ~$pct% grade) — '
          'your e-bike\'s motor will help.';
    }
    final effort = mode == 'walk' ? 'a hard walk up' : 'a hard climb';
    return 'About $d of this route is on steep hills (up to ~$pct% grade) — '
        'expect $effort. Steep stretches are shaded orange–red on the map.';
  }

  /// Why the stretch [atM] meters from the start is shaded red/orange: the
  /// hills and infrastructure gaps covering it (± [slackM], so a tap near a
  /// boundary still gets the answer). Empty means the stretch is plain
  /// route-colored and needs no explaining.
  List<String> segmentNotes(double atM, {double slackM = 30}) {
    const sevWords = {'mod': 'amber', 'steep': 'orange', 'vsteep': 'red'};
    return [
      for (final h in hillRanges())
        if (atM >= h.startM - slackM && atM <= h.endM + slackM)
          'Climbs at about ${(h.grade * 100).round()}% grade here — shaded '
          '${sevWords[h.severity]} on the map.',
      for (final r in warnRanges)
        if (r.start >= 0 &&
            r.end < cumulative.length &&
            atM >= cumulative[r.start] - slackM &&
            atM <= cumulative[r.end] + slackM)
          '${warnStepSentence(r.kind)} — drawn dashed red.',
    ];
  }

  /// Infrastructure warnings worth surfacing: gaps shorter than [minFt] feet
  /// read as noise — "about 0 ft with no bike lane" helps nobody, and in
  /// Greenville almost every trip crosses a short unmarked block, so a low
  /// threshold cried wolf on routes that were fine. Steep warnings always show.
  List<RouteWarning> visibleWarnings([double minFt = warnMinFt]) => [
        for (final w in warnings)
          if (w.kind == 'steep' || w.distanceM * 3.28084 >= minFt) w,
      ];

  /// GeoJSON for the maneuver markers: one rotated chevron per turn, so the
  /// upcoming turns are visible on the map itself and not only in the card.
  /// [fromStep] skips maneuvers already behind the rider.
  Map<String, dynamic> stepCollection({int fromStep = 0}) => {
        'type': 'FeatureCollection',
        'features': [
          for (var i = fromStep; i < steps.length; i++)
            if (_isTurn(steps[i]) && steps[i].startIndex < points.length)
              {
                'type': 'Feature',
                'geometry': {
                  'type': 'Point',
                  'coordinates': [
                    points[steps[i].startIndex].longitude,
                    points[steps[i].startIndex].latitude,
                  ],
                },
                'properties': {
                  'bearing': _bearingAt(steps[i].startIndex),
                  'first': i == fromStep,
                },
              },
        ],
      };

  static bool _isTurn(RouteStep s) => const {
        'left',
        'right',
        'slight-left',
        'slight-right',
        'sharp-left',
        'sharp-right',
        'uturn',
      }.contains(s.maneuver);

  /// Heading of the route as it leaves [index] — the chevron's rotation.
  double _bearingAt(int index) {
    if (points.length < 2) return 0.0;
    final i = index.clamp(0, points.length - 2);
    return bearingBetween(points[i], points[i + 1]);
  }
}

/// A stretch of route steeper than the moderate-hill threshold, as distances
/// from the start (the elevation profile's axis).
class HillRange {
  final double startM;
  final double endM;
  final double grade;
  final String severity; // mod | steep | vsteep

  const HillRange({
    required this.startM,
    required this.endM,
    required this.grade,
    required this.severity,
  });
}

/// Where the rider is relative to [NavRoute] right now.
class NavProgress {
  final int segmentIndex;
  final LatLng snapped;
  final double offRouteM;
  final double traveledM;
  final double remainingM;
  final int stepIndex;
  final double distanceToManeuverM;
  final double courseBearing;

  const NavProgress({
    required this.segmentIndex,
    required this.snapped,
    required this.offRouteM,
    required this.traveledM,
    required this.remainingM,
    required this.stepIndex,
    required this.distanceToManeuverM,
    required this.courseBearing,
  });

  static NavProgress? of(NavRoute route, LatLng at) {
    if (route.isEmpty) return null;

    var bestIdx = 0;
    var bestDist = double.infinity;
    var bestT = 0.0;
    for (var i = 0; i < route.points.length - 1; i++) {
      final r = _project(at, route.points[i], route.points[i + 1]);
      if (r.$2 < bestDist) {
        bestDist = r.$2;
        bestIdx = i;
        bestT = r.$1;
      }
    }

    final a = route.points[bestIdx];
    final b = route.points[bestIdx + 1];
    final snapped = LatLng(
      a.latitude + (b.latitude - a.latitude) * bestT,
      a.longitude + (b.longitude - a.longitude) * bestT,
    );
    final traveled =
        route.cumulative[bestIdx] + metersBetween(a, snapped);

    // The step in progress is the last one that started at or before us.
    var stepIndex = 0;
    for (var i = 0; i < route.steps.length; i++) {
      if (route.steps[i].startIndex <= bestIdx) {
        stepIndex = i;
      } else {
        break;
      }
    }
    final nextIndex = math.min(stepIndex + 1, route.steps.length - 1);
    final maneuverAt = route.steps[nextIndex].startIndex
        .clamp(0, route.cumulative.length - 1);

    return NavProgress(
      segmentIndex: bestIdx,
      snapped: snapped,
      offRouteM: bestDist,
      traveledM: traveled,
      remainingM: math.max(route.distanceM - traveled, 0.0),
      stepIndex: stepIndex,
      distanceToManeuverM:
          math.max(route.cumulative[maneuverAt] - traveled, 0.0),
      courseBearing: bearingBetween(a, b),
    );
  }

  /// (t along ab, distance in meters from p to the segment)
  static (double, double) _project(LatLng p, LatLng a, LatLng b) {
    final cosLat = math.cos(a.latitude * math.pi / 180.0);
    final ax = a.longitude * cosLat, ay = a.latitude;
    final bx = b.longitude * cosLat, by = b.latitude;
    final px = p.longitude * cosLat, py = p.latitude;
    final dx = bx - ax, dy = by - ay;
    final denom = dx * dx + dy * dy;
    var t = denom == 0 ? 0.0 : ((px - ax) * dx + (py - ay) * dy) / denom;
    t = t.clamp(0.0, 1.0);
    final cx = ax + dx * t, cy = ay + dy * t;
    final dist = math.sqrt(
          math.pow((px - cx) * _mPerDegLat, 2) +
              math.pow((py - cy) * _mPerDegLat, 2),
        );
    return (t, dist);
  }
}

/// What a fix means for rerouting: nothing, a full announced reroute, a
/// silent recalculation, or a silent recalculation with a one-time "I'll stay
/// quiet now" notice.
enum RerouteDecision { none, announce, silent, quietNotice }

/// Decides when going off-route earns a recalculation and how loudly to say
/// so.
///
/// The naive rule (3 fixes >45 m → beep + "Rerouting.") loops forever when a
/// rider takes a real-world shortcut the graph doesn't know (the Springer St
/// tunnel): every recalculation starts at the rider, the rider keeps not
/// following it, and the app beeps at them the whole way. So:
///
///  * A rider heading back TOWARD the route is left alone — they're
///    rejoining on their own.
///  * Repeated reroutes in one off-route spell back off exponentially
///    (15 s → 30 s → 60 s → 120 s between recalculations).
///  * Only the FIRST reroute of a spell gets the tone + voice; the rest are
///    silent, with a single spoken notice on the third so the rider knows
///    the app is deliberately staying quiet, not dead.
///  * The spell ends after ~[onRouteResetFixes] consecutive on-route fixes
///    (long enough to mean "genuinely back", not "the new route momentarily
///    passes through me").
class RerouteGovernor {
  /// Farther than this from the route line counts as off-route.
  static const offRouteM = 45.0;

  /// Consecutive off-route fixes before the first recalculation.
  static const triggerFixes = 3;

  /// A fix must be this much closer than the last one to read as "heading
  /// back toward the route" (GPS scatter is a few metres).
  static const approachToleranceM = 5.0;

  /// Consecutive on-route fixes (~1 Hz → seconds) that end an off-route
  /// spell. Short stretches where a fresh reroute passes through the rider
  /// must NOT reset the backoff, or the loop comes right back.
  static const onRouteResetFixes = 30;

  int _offHits = 0;
  int _onHits = 0;
  int _spellReroutes = 0;
  DateTime? _lastRerouteAt;
  double? _prevOffM;

  /// Reroutes so far in the current off-route spell.
  int get spellReroutes => _spellReroutes;

  /// Seconds to wait after the [n]th reroute of a spell before the next one.
  static int cooldownSeconds(int n) => math.min(15 * (1 << (n - 1)), 120);

  /// Call for every GPS fix with the current distance to the route line.
  RerouteDecision onFix(double offM, DateTime now) {
    if (offM <= offRouteM) {
      _offHits = 0;
      _prevOffM = null;
      _onHits++;
      if (_onHits >= onRouteResetFixes) {
        _spellReroutes = 0;
        _lastRerouteAt = null;
      }
      return RerouteDecision.none;
    }
    _onHits = 0;
    final approaching =
        _prevOffM != null && offM < _prevOffM! - approachToleranceM;
    _prevOffM = offM;
    if (approaching) {
      _offHits = 0;
      return RerouteDecision.none;
    }
    _offHits++;
    if (_offHits < triggerFixes) return RerouteDecision.none;
    if (_spellReroutes > 0 && _lastRerouteAt != null) {
      final cool = Duration(seconds: cooldownSeconds(_spellReroutes));
      if (now.difference(_lastRerouteAt!) < cool) return RerouteDecision.none;
    }
    _offHits = 0;
    _lastRerouteAt = now;
    _spellReroutes++;
    if (_spellReroutes == 1) return RerouteDecision.announce;
    if (_spellReroutes == 3) return RerouteDecision.quietNotice;
    return RerouteDecision.silent;
  }

  /// A new navigation session starts clean.
  void reset() {
    _offHits = 0;
    _onHits = 0;
    _spellReroutes = 0;
    _lastRerouteAt = null;
    _prevOffM = null;
  }
}

/// GIS street names abbreviate cardinal prefixes ("E Washington St") and the
/// TTS engine reads "E" as the letter. Two-letter combos arrive titleized
/// ("Ne") from the server's `.title()`, so matching is case-normalized.
const _spokenDirections = <String, String>{
  'N': 'North',
  'S': 'South',
  'E': 'East',
  'W': 'West',
  'NE': 'Northeast',
  'NW': 'Northwest',
  'SE': 'Southeast',
  'SW': 'Southwest',
};

/// Street-type suffixes the engine won't reliably expand on its own.
const _spokenSuffixes = <String, String>{
  'St': 'Street',
  'Rd': 'Road',
  'Ave': 'Avenue',
  'Av': 'Avenue',
  'Blvd': 'Boulevard',
  'Dr': 'Drive',
  'Ln': 'Lane',
  'Ct': 'Court',
  'Cir': 'Circle',
  'Pl': 'Place',
  'Hwy': 'Highway',
  'Pkwy': 'Parkway',
  'Ter': 'Terrace',
  'Trl': 'Trail',
  'Xing': 'Crossing',
  'Ext': 'Extension',
  'Mt': 'Mount',
};

/// Lowercase units in spoken distances ("400 ft" must not be "400 eff tee").
const _spokenUnits = <String, String>{'ft': 'feet', 'mi': 'miles'};

/// What the voice actually says for [text]: cardinal prefixes, street-type
/// suffixes and distance units expanded ("Turn left onto E Washington St" →
/// "Turn left onto East Washington Street"). Display text stays abbreviated —
/// this is for the TTS engine only.
///
/// "St" is ambiguous: before another capitalized word it means Saint
/// ("St Francis Dr") — unless that word is itself a suffix ("E North St Ext").
String spokenText(String text) {
  final words = text.split(' ');
  String coreOf(String w) =>
      RegExp(r'[A-Za-z]+').firstMatch(w)?.group(0) ?? '';
  String titled(String c) => c.length > 1
      ? c[0].toUpperCase() + c.substring(1).toLowerCase()
      : c;
  bool capitalized(String c) =>
      c.isNotEmpty && c[0] == c[0].toUpperCase() && c[0] != c[0].toLowerCase();

  final out = <String>[];
  for (var i = 0; i < words.length; i++) {
    final w = words[i];
    final m = RegExp(r'^([^A-Za-z]*)([A-Za-z]+)([^A-Za-z]*)$').firstMatch(w);
    if (m == null) {
      out.add(w);
      continue;
    }
    final pre = m.group(1)!, core = m.group(2)!, post = m.group(3)!;
    final nextCore = i + 1 < words.length ? coreOf(words[i + 1]) : '';
    final nextIsName = post.isEmpty &&
        capitalized(nextCore) &&
        !_spokenSuffixes.containsKey(titled(nextCore)) &&
        !_spokenDirections.containsKey(nextCore.toUpperCase());
    var replaced = core;
    if (capitalized(core) && _spokenDirections.containsKey(core.toUpperCase())) {
      replaced = _spokenDirections[core.toUpperCase()]!;
    } else if (_spokenSuffixes.containsKey(titled(core))) {
      if (titled(core) == 'St' && nextIsName) {
        replaced = 'Saint';
      } else if (titled(core) == 'Dr' && nextIsName) {
        replaced = core; // "Dr Martin Luther King" — the engine says Doctor.
      } else {
        replaced = _spokenSuffixes[titled(core)]!;
      }
    } else if (_spokenUnits.containsKey(core)) {
      replaced = _spokenUnits[core]!;
    }
    out.add('$pre$replaced$post');
  }
  return out.join(' ');
}

/// "400 ft" / "0.6 mi" — imperial, because Greenville.
String formatDistance(double meters) {
  final feet = meters * 3.28084;
  if (feet < 1000) {
    final rounded = feet < 100 ? (feet / 10).round() * 10 : (feet / 50).round() * 50;
    return '$rounded ft';
  }
  final miles = meters / 1609.344;
  return '${miles.toStringAsFixed(miles < 10 ? 1 : 0)} mi';
}

String formatDuration(double minutes) {
  if (minutes < 1) return '<1 min';
  if (minutes < 60) return '${minutes.round()} min';
  final h = minutes ~/ 60;
  final m = (minutes % 60).round();
  return m == 0 ? '$h hr' : '$h hr $m min';
}
