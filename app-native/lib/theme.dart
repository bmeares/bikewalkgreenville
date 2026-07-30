import 'package:flutter/material.dart';

/// Brand + map styling constants.
const brandGreen = Color(0xFF6F9920);
const brandDark = Color(0xFF33470D);

/// Free, keyless vector basemap (OpenFreeMap, OpenMapTiles schema).
const basemapStyleUrl = 'https://tiles.openfreemap.org/styles/liberty';

/// Greenville downtown.
const homeLat = 34.8526;
const homeLon = -82.3940;
const homeZoom = 12.5;

/// Travel modes — the top-level map filter. Jasmine's framing: pedestrian
/// (sidewalks) + transit (bus) + cyclist together show the complete picture
/// of non-car mobility in Greenville.
enum TravelMode { cyclist, pedestrian, transit }

const modeLabels = {
  TravelMode.cyclist: 'Bike',
  TravelMode.pedestrian: 'Walk',
  TravelMode.transit: 'Transit',
};

const modeIcons = {
  TravelMode.cyclist: Icons.directions_bike,
  TravelMode.pedestrian: Icons.directions_walk,
  TravelMode.transit: Icons.directions_bus,
};

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
  final String color; // hex, ignored when colorByStress
  final bool colorByStress;
  final double width;
  final Set<TravelMode> modes;
  final bool defaultOn;
  final IconData icon;

  const LayerDef({
    required this.id,
    required this.label,
    required this.path,
    required this.color,
    required this.modes,
    this.isPoint = false,
    this.colorByStress = false,
    this.width = 2.5,
    this.defaultOn = true,
    this.icon = Icons.timeline,
  });
}

const layerDefs = <LayerDef>[
  LayerDef(
    id: 'bike-stress',
    label: 'Bike stress (PCC)',
    path: '/map-layers/bike-stress.geojson',
    color: '#1a9850',
    colorByStress: true,
    width: 2.0,
    modes: {TravelMode.cyclist},
    defaultOn: false,
    icon: Icons.speed,
  ),
  LayerDef(
    id: 'bike-lanes',
    label: 'Bike lanes & sharrows',
    path: '/map-layers/bike-lanes.geojson',
    color: '#2E7D32',
    width: 3.0,
    modes: {TravelMode.cyclist},
    icon: Icons.directions_bike,
  ),
  LayerDef(
    id: 'srt',
    label: 'Swamp Rabbit Trail',
    path: '/map-layers/srt.geojson',
    color: '#FF6F00',
    width: 3.5,
    modes: {TravelMode.cyclist, TravelMode.pedestrian},
    icon: Icons.forest,
  ),
  LayerDef(
    id: 'sidewalks-city',
    label: 'Sidewalks (city)',
    path: '/map-layers/sidewalks-city.geojson',
    color: '#1565C0',
    width: 1.8,
    modes: {TravelMode.pedestrian},
    icon: Icons.directions_walk,
  ),
  LayerDef(
    id: 'sidewalks-county',
    label: 'Sidewalks (county)',
    path: '/map-layers/sidewalks-county.geojson',
    color: '#0288D1',
    width: 1.8,
    modes: {TravelMode.pedestrian},
    icon: Icons.directions_walk,
  ),
  LayerDef(
    id: 'bus-routes',
    label: 'Greenlink bus routes',
    path: '/map-layers/bus-routes.geojson',
    color: '#7B1FA2',
    width: 2.5,
    modes: {TravelMode.transit},
    icon: Icons.directions_bus,
  ),
  LayerDef(
    id: 'bus-stops',
    label: 'Bus stops',
    path: '/map-layers/bus-stops.geojson',
    color: '#7B1FA2',
    isPoint: true,
    modes: {TravelMode.transit},
    icon: Icons.directions_bus,
  ),
  LayerDef(
    id: 'bike-parking',
    label: 'Bike parking',
    path: '/bike-parking/data.geojson',
    color: '#00695C',
    isPoint: true,
    modes: {TravelMode.cyclist},
    icon: Icons.local_parking,
  ),
  LayerDef(
    id: 'repair-stations',
    label: 'Bike repair stations',
    path: '/bike-parking/repair-stations.geojson',
    color: '#BF360C',
    isPoint: true,
    modes: {TravelMode.cyclist},
    icon: Icons.build,
  ),
  LayerDef(
    id: 'reports',
    label: 'Reported issues',
    path: '/walk-audit/reports.geojson',
    color: '#F9A825',
    isPoint: true,
    modes: {TravelMode.cyclist, TravelMode.pedestrian, TravelMode.transit},
    defaultOn: false,
    icon: Icons.report_problem,
  ),
];

ThemeData buildTheme() => ThemeData(
      colorScheme: ColorScheme.fromSeed(seedColor: brandGreen),
      useMaterial3: true,
    );

void toast(BuildContext context, String msg) {
  ScaffoldMessenger.of(context)
    ..clearSnackBars()
    ..showSnackBar(SnackBar(content: Text(msg)));
}
