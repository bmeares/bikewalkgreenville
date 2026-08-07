import 'package:flutter/material.dart';

/// Brand + map styling constants.
const brandGreen = Color(0xFF6F9920);
const brandDark = Color(0xFF33470D);

/// Free, keyless vector basemap (OpenFreeMap, OpenMapTiles schema).
const basemapStyleUrl = 'https://tiles.openfreemap.org/styles/liberty';

/// OpenFreeMap's dark companion style, used when the app renders dark.
const basemapStyleDarkUrl = 'https://tiles.openfreemap.org/styles/dark';

/// Greenville downtown.
const homeLat = 34.8526;
const homeLon = -82.3940;
const homeZoom = 12.5;

/// Travel modes — the map filter and the set of ways a rider is willing to
/// travel. Jasmine's framing: pedestrian (sidewalks) + transit (bus) + cyclist
/// together show the complete picture of non-car mobility in Greenville.
///
/// These are multi-select: picking bike AND transit asks the router for a
/// bike-to-the-bus itinerary, not one or the other.
enum TravelMode { cyclist, pedestrian, transit }

const modeLabels = {
  TravelMode.cyclist: 'Bike',
  // Wheelchair users roll rather than walk; the `roll` preference switches the
  // router's weighting, and this label makes the mode theirs too.
  TravelMode.pedestrian: 'Walk',
  TravelMode.transit: 'Bus',
};

const modeIcons = {
  TravelMode.cyclist: Icons.directions_bike,
  TravelMode.pedestrian: Icons.directions_walk,
  TravelMode.transit: Icons.directions_bus,
};

/// API `modes` values for `/map-layers/route`.
const modeApiNames = {
  TravelMode.cyclist: 'bike',
  TravelMode.pedestrian: 'walk',
  TravelMode.transit: 'transit',
};

/// "Bike here" / "Walk here" / "Bus here" — directions verb per mode.
const modeVerbs = {
  TravelMode.cyclist: 'Bike here',
  TravelMode.pedestrian: 'Walk here',
  TravelMode.transit: 'Bus here',
};

/// Route preview subtitle per mode.
const modeRouteLabels = {
  TravelMode.cyclist: 'Bike route',
  TravelMode.pedestrian: 'Walking route',
  TravelMode.transit: 'Transit route',
};

/// Icon per router plan key (`properties.plan` / `alternatives[].plan`).
const planIcons = {
  'bike': Icons.directions_bike,
  'walk': Icons.directions_walk,
  'roll': Icons.accessible_forward,
  'bcycle': Icons.pedal_bike,
  'walk-transit': Icons.directions_bus,
  'roll-transit': Icons.directions_bus,
  'bike-transit': Icons.directions_bus,
};

/// Icon per travel-leg mode (steps sheet, the e-bike relabel). Keys match
/// [routeLegColors].
const legModeIcons = <String, IconData>{
  'bike': Icons.directions_bike,
  'ebike': Icons.electric_bike,
  'walk': Icons.directions_walk,
  'roll': Icons.accessible_forward,
  'transit': Icons.directions_bus,
  'bcycle': Icons.pedal_bike,
};

/// Stretches of a route that lack the infrastructure the mode needs, drawn in
/// this colour and called out in a banner. Not a "bad route" — a disclosure.
const warnRed = Color(0xFFD32F2F);

/// Per-mode colors for the route line. A single-mode route draws in the bike
/// blue like it always has; a multi-modal itinerary colors each leg by how the
/// rider travels it (bike to the stop, bus, walk the rest).
const routeLegColors = <String, String>{
  'bike': '#1565C0',
  'walk': '#00897B',
  'roll': '#00897B',
  'transit': '#7B1FA2',
  'bcycle': '#E2231A',
};

/// Hill severity → route overlay color: moderate (5–8%) amber, steep (8–12%)
/// deep orange, very steep (>12%) red. Matches the elevation preview's idea
/// of "steep" at the 8% (ADA 1:12) boundary.
const hillColors = <String, String>{
  'mod': '#FFB300',
  'steep': '#F4511E',
  'vsteep': '#C62828',
};

/// Greenville BCycle brand red, for the bike-share dock pins.
const bcycleRed = '#E2231A';

/// PCC bike-stress colors, keyed by the `stress_level` GeoJSON property.
const stressColors = {
  'H': '#d73027',
  'MH': '#fc8d59',
  'M': '#fee08b',
  'ML': '#91cf60',
  'L': '#1a9850',
};

const stressLabels = {
  'H': 'High stress',
  'MH': 'Medium-high',
  'M': 'Medium',
  'ML': 'Medium-low',
  'L': 'Low stress',
};

/// One map overlay. `path` is relative to the API base (see Api.layerUrl).
class LayerDef {
  final String id;
  final String label;
  final String path;
  final bool isPoint;

  /// Polygon layers (parking land use) render as translucent fills.
  final bool isFill;
  final String color; // hex, ignored when colorByStress
  final bool colorByStress;

  /// Data-driven coloring: match [matchProp] against [matchColors] keys,
  /// falling back to [color]. (Parking land use: lots vs garages.)
  final String? matchProp;
  final Map<String, String>? matchColors;
  final double width;
  final Set<TravelMode> modes;
  final bool defaultOn;
  final IconData icon;

  /// Point layers only: hide the symbols until this zoom so the map isn't a
  /// soup of dots at city scale. 0 = always drawn.
  final double minZoom;

  /// Point layers only: pin size multiplier at the reference zoom.
  final double pinScale;

  /// Contents change minute to minute (bike-share availability), so the source
  /// is re-fetched when the layer is switched on rather than trusted from
  /// style-load time.
  final bool live;

  /// Line layers only: stroke opacity. Sidewalks draw faint so they read as
  /// context instead of competing with the route line.
  final double opacity;

  const LayerDef({
    required this.id,
    required this.label,
    required this.path,
    required this.color,
    required this.modes,
    this.isPoint = false,
    this.isFill = false,
    this.colorByStress = false,
    this.matchProp,
    this.matchColors,
    this.width = 2.5,
    this.defaultOn = true,
    this.icon = Icons.timeline,
    this.minZoom = 0,
    this.pinScale = 1.0,
    this.live = false,
    this.opacity = 0.85,
  });
}

// Thematic LINE layers all render below 0.7 opacity (sidewalks fainter
// still): they are context, and at full strength a multi-modal route line —
// itself several colors — disappeared into the Greenlink purples and bike-lane
// greens drawn under it.
const layerDefs = <LayerDef>[
  LayerDef(
    id: 'bike-stress',
    label: 'Bike stress',
    path: '/map-layers/bike-stress.geojson',
    color: '#1a9850',
    colorByStress: true,
    width: 2.0,
    opacity: 0.6,
    modes: {TravelMode.cyclist},
    defaultOn: false,
    icon: Icons.speed,
  ),
  LayerDef(
    id: 'bike-lanes',
    label: 'Bike lanes & sharrows',
    path: '/map-layers/bike-lanes.geojson',
    color: '#2E7D32',
    width: 2.5,
    opacity: 0.55,
    modes: {TravelMode.cyclist},
    icon: Icons.directions_bike,
  ),
  LayerDef(
    id: 'srt',
    label: 'Prisma Health Swamp Rabbit Trail',
    path: '/map-layers/srt.geojson',
    color: '#FF6F00',
    width: 3.0,
    opacity: 0.65,
    modes: {TravelMode.cyclist, TravelMode.pedestrian},
    icon: Icons.cruelty_free, // Material's rabbit
  ),
  // Curated off-grid connectors (the Springer St tunnel and friends): the
  // routes locals actually ride that no official GIS layer maps. The router
  // uses them too — this layer is how a rider learns they exist.
  LayerDef(
    id: 'custom-paths',
    label: 'Shortcuts & tunnels',
    path: '/map-layers/custom-paths.geojson',
    color: '#AD1457',
    width: 3.0,
    opacity: 0.75,
    modes: {TravelMode.cyclist, TravelMode.pedestrian},
    icon: Icons.fork_right,
  ),
  // One sidewalks layer: the server merges the county lines with the city
  // lines that aren't the same sidewalk digitized twice (heavy overlap in
  // city limits made two separate toggles meaningless).
  // Light and faint on purpose: sidewalks are context, and the old solid blue
  // was indistinguishable from the blue route line drawn over it.
  LayerDef(
    id: 'sidewalks',
    label: 'Sidewalks',
    path: '/map-layers/sidewalks.geojson',
    color: '#7BAFDE',
    width: 1.8,
    opacity: 0.5,
    modes: {TravelMode.pedestrian},
    icon: Icons.directions_walk,
  ),
  LayerDef(
    id: 'bus-routes',
    label: 'Greenlink bus routes',
    path: '/map-layers/bus-routes.geojson',
    color: '#7B1FA2',
    width: 2.2,
    // Faintest of the colored lines: every Greenlink route has its own color,
    // and at full strength the tangle drowned out a multi-modal route line.
    opacity: 0.45,
    modes: {TravelMode.transit},
    // Distinct from the bus-stops icon so the layers sheet reads at a glance.
    icon: Icons.route,
  ),
  LayerDef(
    id: 'bus-stops',
    label: 'Bus stops',
    path: '/map-layers/bus-stops.geojson',
    color: '#7B1FA2',
    isPoint: true,
    modes: {TravelMode.transit},
    icon: Icons.directions_bus,
    minZoom: 13,
    pinScale: 0.85,
  ),
  LayerDef(
    id: 'bike-parking',
    label: 'Bike parking',
    path: '/bike-parking/data.geojson',
    color: '#00695C',
    isPoint: true,
    modes: {TravelMode.cyclist},
    icon: Icons.local_parking,
    minZoom: 13,
  ),
  // Greenville BCycle docks. Availability is live, so the source is refreshed
  // rather than left at whatever it was when the style loaded.
  LayerDef(
    id: 'bcycle',
    label: 'BCycle bike share',
    path: '/bcycle/stations.geojson',
    color: bcycleRed,
    isPoint: true,
    modes: {TravelMode.cyclist},
    icon: Icons.pedal_bike,
    // No minZoom: ~13 docks citywide. Someone hunting the nearest dock zooms
    // OUT to find it — hiding sparse layers at low zoom defeats their point.
    // (Dense layers like bus stops keep a minZoom; MapLibre's symbol
    // decluttering thins whatever overlaps in between.)
    pinScale: 1.05,
    live: true,
  ),
  LayerDef(
    id: 'repair-stations',
    label: 'Bike repair stations',
    path: '/bike-parking/repair-stations.geojson',
    color: '#BF360C',
    isPoint: true,
    modes: {TravelMode.cyclist},
    icon: Icons.build,
    // No minZoom — sparse layer, see bcycle above.
  ),
  // Places that welcome riders — curated by BWG, starting with the Swamp
  // Rabbit Cafe & Grocery. Deliberately on by default: it's the layer that
  // makes the map feel like Greenville's, not a generic basemap.
  LayerDef(
    id: 'bike-businesses',
    label: 'Bike friendly businesses',
    path: '/map-layers/bike-businesses.geojson',
    color: '#00897B',
    isPoint: true,
    modes: {TravelMode.cyclist, TravelMode.pedestrian, TravelMode.transit},
    icon: Icons.storefront,
    pinScale: 1.1,
  ),
  // Downtown garage occupancy, refreshed from the city's counters. Off by
  // default — it's context for a car-adjacent trip, not core to the map.
  LayerDef(
    id: 'parking-garages',
    label: 'Parking garages',
    path: '/map-layers/parking-garages.geojson',
    color: '#5C6BC0',
    isPoint: true,
    modes: {TravelMode.cyclist, TravelMode.pedestrian, TravelMode.transit},
    defaultOn: false,
    icon: Icons.garage,
    // No minZoom — ten downtown garages, sparse layer, see bcycle above.
    live: true,
  ),
  // Land use: what downtown ground is given to cars — roadway pavement
  // (green), surface lots (orange), garages (yellow); mirrors the parking
  // Grafana dashboard's split.
  LayerDef(
    id: 'parking-landuse',
    label: 'Parking land use',
    path: '/map-layers/parking-landuse.geojson',
    color: '#8D6E63',
    isFill: true,
    matchProp: 'kind',
    matchColors: {
      'roadway': '#66BB6A',
      'lot': '#F57C00',
      'garage': '#FBC02D',
    },
    modes: {TravelMode.cyclist, TravelMode.pedestrian, TravelMode.transit},
    defaultOn: false,
    icon: Icons.crop_square,
  ),
  // Community reports are the point of the app — always on, always drawn.
  LayerDef(
    id: 'reports',
    label: 'Reported issues',
    path: '/walk-audit/reports.geojson',
    color: '#F9A825',
    isPoint: true,
    modes: {TravelMode.cyclist, TravelMode.pedestrian, TravelMode.transit},
    icon: Icons.report_problem,
    pinScale: 1.15,
  ),
];

/// `#rrggbb` (as stored in [LayerDef.color]) → a Flutter [Color].
Color hexColor(String hex) =>
    Color(int.parse('ff${hex.replaceFirst('#', '')}', radix: 16));

ThemeData buildTheme() => ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: brandGreen),
      useMaterial3: true,
      // Floating, so toasts ride above the system navigation bar instead of
      // hiding behind 3-button nav.
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
    );

ThemeData buildDarkTheme() => ThemeData(
      colorScheme: ColorScheme.fromSeed(
        seedColor: brandGreen,
        brightness: Brightness.dark,
      ),
      useMaterial3: true,
      snackBarTheme: const SnackBarThemeData(
        behavior: SnackBarBehavior.floating,
      ),
    );

/// Warning banner palette that reads on both themes: cream card with brown
/// text in light mode, deep amber-brown card with light amber text in dark.
Color warnBg(BuildContext c) => Theme.of(c).brightness == Brightness.dark
    ? const Color(0xFF3E2A12)
    : const Color(0xFFFFF3E0);

Color warnFg(BuildContext c) => Theme.of(c).brightness == Brightness.dark
    ? const Color(0xFFFFCC80)
    : const Color(0xFF6D3B00);

Color warnAccent(BuildContext c) => Theme.of(c).brightness == Brightness.dark
    ? const Color(0xFFFFB74D)
    : const Color(0xFFE65100);

/// The brand green that reads on the current surface: full-dark line work
/// (brandDark) disappears on a dark card, so dark mode gets a lighter leaf.
Color brandOnSurface(BuildContext c) =>
    Theme.of(c).brightness == Brightness.dark
        ? const Color(0xFFABC77D)
        : brandDark;

void toast(BuildContext context, String msg) {
  ScaffoldMessenger.of(context)
    ..clearSnackBars()
    ..showSnackBar(SnackBar(content: Text(msg)));
}
