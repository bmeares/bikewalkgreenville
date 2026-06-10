#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Map Layers: serve infrastructure layers (bus routes, bike lanes, sidewalks,
PCC bike stress, ...) as mobile-friendly GeoJSON for the BWG app.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  GET /map-layers/index.json        -> catalog of available layers + style hints
  GET /map-layers/{layer}.geojson   -> FeatureCollection (pregenerated file if
                                       present, else generated on demand)

Pregeneration (run nightly after the source pipes sync):

  mrsm compose exec export map_layers

writes `<data>/output/geojson/app/<layer>.geojson` alongside the existing
`export geojson` output tree.

Geometries are simplified in their native SC state-plane CRS (ft units) before
reprojection to WGS84 to keep payloads small enough for flet_map on-device.
"""

import time
from typing import Any

import meerschaum as mrsm
from meerschaum.actions import make_action
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import info, warn

__version__ = '0.1.0'

bwg = mrsm.Plugin('bwg')

FT_PER_M = 3.28084

#: Single source of truth for app map layers.
#: `pipe`: (connector, metric[, location]) on instance sql:bwg.
#: `props`: output property name -> DB column name.
#: `tolerance_m`: Douglas-Peucker tolerance in meters (lines only; applied in
#:   the native ft-based CRS before reprojection).
LAYERS: dict[str, dict[str, Any]] = {
    'bus-routes': {
        'label': 'Bus Routes',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'BusRoutes'),
        'props': {'dir_id': 'dir_id'},
        'tolerance_m': 5,
        'color': '#7B1FA2',
    },
    'bus-stops': {
        'label': 'Bus Stops',
        'kind': 'point',
        'pipe': ('plugin:city-gis', 'BusStops'),
        'props': {},
        'color': '#7B1FA2',
        'icon': 'directions_bus',
    },
    'bike-lanes': {
        'label': 'Bike Lanes',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'BicycleInfrastructure'),
        'props': {'bike_type': 'BIKE_TYPE', 'street_name': 'STREET_NAM'},
        'tolerance_m': 5,
        'color': '#2E7D32',
    },
    'sidewalks-city': {
        'label': 'Sidewalks (City)',
        'kind': 'line',
        'pipe': ('plugin:city-gis', 'Sidewalks'),
        'props': {},
        'detail_props': {
            'material': 'MATERIAL',
            'side_of_street': 'SIDE_OF_ST',
            'street_name': 'STREET_NAM',
        },
        'tolerance_m': 8,
        'dissolve_by': True,
        'color': '#1565C0',
    },
    'sidewalks-county': {
        'label': 'Sidewalks (County)',
        'kind': 'line',
        'pipe': ('plugin:greenville-county', 'sidewalks'),
        'props': {},
        'tolerance_m': 8,
        'dissolve_by': True,
        'color': '#0288D1',
    },
    'srt': {
        'label': 'Swamp Rabbit Trail',
        'kind': 'line',
        'pipe': ('sql:bwg', 'srt_segments', 'owners'),
        'props': {'owner': 'Owner', 'segment': 'Segment'},
        'tolerance_m': 3,
        'color': '#FF6F00',
    },
    'bike-stress': {
        'label': 'Bike Stress',
        'kind': 'line',
        'pipe': ('sql:bwg', 'stress_levels', 'greenville'),
        'props': {'stress_level': 'stress_level'},
        'detail_props': {'street_name': 'street_name'},
        'tolerance_m': 10,
        'dissolve_by': 'stress_level',
        'color_by': {
            'property': 'stress_level',
            'map': {
                'H': '#d73027',
                'MH': '#fc8d59',
                'M': '#fee08b',
                'ML': '#91cf60',
                'L': '#1a9850',
            },
        },
    },
}

#: On-demand generation cache: layer id -> (epoch, geojson string).
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_TTL_SECONDS = 6 * 60 * 60


def _layer_pipe(layer: dict[str, Any]) -> mrsm.Pipe:
    keys = layer['pipe']
    return mrsm.Pipe(*keys, instance='sql:bwg')


def _output_dir():
    """Pregenerated layer files live with the `export geojson` output tree.
    Falls back to the Meerschaum root (e.g. inside the API container, where the
    `bwg` plugin and its data_path config aren't installed)."""
    if bwg.module is not None:
        return bwg.module.get_data_path() / 'output' / 'geojson' / 'app'
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    return Path(ROOT_DIR_PATH) / 'output' / 'geojson' / 'app'


def _build_layer_geojson(layer_id: str, debug: bool = False) -> str | None:
    """Read the layer's pipe, simplify + reproject, return a GeoJSON string."""
    layer = LAYERS[layer_id]
    pipe = _layer_pipe(layer)
    if not pipe.exists(debug=debug):
        warn(f"Pipe for layer '{layer_id}' does not exist: {pipe}")
        return None

    geometry_cols = [
        col
        for col, typ in pipe.dtypes.items()
        if 'geometry' in typ.lower() or 'geography' in typ.lower()
    ]
    if not geometry_cols:
        warn(f"No geometry column for layer '{layer_id}' ({pipe}).")
        return None
    geom_col = geometry_cols[0]

    props = layer.get('props', {})
    df = pipe.get_data([geom_col] + list(props.values()), debug=debug)
    if df is None or len(df) == 0:
        warn(f"No data for layer '{layer_id}' ({pipe}).")
        return None

    df = df.rename(columns={db_col: out_name for out_name, db_col in props.items()})

    try:
        df.geometry = df.geometry.force_2d()  # Z coords (e.g. SRT) waste bytes
    except Exception:
        pass

    # Merge contiguous segments (huge feature-count + payload reduction for
    # sidewalks/stress, which arrive as thousands of tiny per-block segments).
    dissolve_by = layer.get('dissolve_by')
    if dissolve_by:
        df = (
            df.dissolve(by=dissolve_by, as_index=False)
            if dissolve_by is not True
            else df.dissolve()
        )
        df.geometry = df.geometry.line_merge()

    tolerance_m = layer.get('tolerance_m')
    if tolerance_m and layer.get('kind') == 'line':
        crs = df.crs
        if crs is not None and crs.is_projected:
            df.geometry = df.geometry.simplify(
                tolerance_m * FT_PER_M,
                preserve_topology=True,
            )

    return df.to_json(drop_id=True, to_wgs84=True)


#: Cached native SRID per layer (one `ST_SRID` query each).
_SRIDS: dict[str, int] = {}


def _layer_srid(layer_id: str, conn, schema: str, target: str) -> int:
    if layer_id not in _SRIDS:
        df = conn.read(f'SELECT ST_SRID("geometry") AS "srid" FROM "{schema}"."{target}" LIMIT 1')
        _SRIDS[layer_id] = int(df['srid'][0])
    return _SRIDS[layer_id]


def _build_bbox_geojson(
    layer_id: str,
    minlon: float,
    minlat: float,
    maxlon: float,
    maxlat: float,
    zoom: int,
) -> str:
    """Viewport query: bbox-filtered, zoom-simplified features WITH per-feature
    detail properties (the full-layer path dissolves those away). Pure PostGIS —
    no geopandas round-trip. All numeric inputs are coerced before
    interpolation; `layer_id` is validated against LAYERS by the caller.
    """
    import json
    import math

    layer = LAYERS[layer_id]
    pipe = _layer_pipe(layer)
    conn = pipe.instance_connector
    schema = pipe.parameters.get('schema') or 'public'
    target = pipe.target

    minlon, minlat, maxlon, maxlat = (
        float(minlon), float(minlat), float(maxlon), float(maxlat),
    )
    zoom = max(1, min(int(zoom), 22))

    props = {**layer.get('props', {}), **layer.get('detail_props', {})}
    prop_cols = ''.join(
        f', "{db_col}" AS "{out_name}"'
        for out_name, db_col in props.items()
    )

    # ~1 px worth of simplification at this zoom (Greenville latitude), in the
    # layer's native units (US-ft state plane, or degrees for 4326 layers).
    m_per_px = 156543.03 * math.cos(math.radians(34.85)) / (2 ** zoom)
    srid = _layer_srid(layer_id, conn, schema, target)
    if srid == 4326:
        tolerance = m_per_px / 111320
    else:
        tolerance = max(m_per_px * FT_PER_M, 1.0)

    envelope = (
        f"ST_Transform(ST_MakeEnvelope({minlon}, {minlat}, {maxlon}, {maxlat}, 4326), {srid})"
    )
    query = f'''
    SELECT
        ST_AsGeoJSON(
            ST_Force2D(
                ST_Transform(ST_SimplifyPreserveTopology("geometry", {tolerance}), 4326)
            )
        ) AS "gj"
        {prop_cols}
    FROM "{schema}"."{target}"
    WHERE ST_Intersects("geometry", {envelope})
    LIMIT 20000
    '''
    def _clean(v):
        if hasattr(v, 'item'):
            v = v.item()
        if isinstance(v, float) and v != v:  # NaN -> null
            return None
        return v

    df = conn.read(query)
    features = []
    for row in df.to_dict(orient='records'):
        gj = row.pop('gj', None)
        if not gj:
            continue
        features.append({
            'type': 'Feature',
            'geometry': json.loads(gj),
            'properties': {k: _clean(v) for k, v in row.items()},
        })
    return json.dumps({'type': 'FeatureCollection', 'features': features})


# User feedback on any map feature (bus stop, sidewalk segment, bike lane, ...).
FEEDBACK_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'feedback', 'MapLayers',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'MapLayers',
        'target': 'layer_feedback',
        'columns': {
            'datetime': 'ts',
            'id': 'id',
        },
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'layer': 'string',
            'name': 'string',
            'lat': 'float',
            'lon': 'float',
            'props': 'string',
            'feedback': 'string',
            'photo_filename': 'string',
            'ip': 'string',
            'user_agent': 'string',
        },
    },
)


def _photos_dir():
    """Directory where uploaded photos are stored (created on demand)."""
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    photos_dir = Path(ROOT_DIR_PATH) / 'uploads' / 'map-layers'
    photos_dir.mkdir(parents=True, exist_ok=True)
    return photos_dir


def _layer_index() -> list[dict[str, Any]]:
    return [
        {
            'id': layer_id,
            'label': layer['label'],
            'kind': layer['kind'],
            'url': f'/map-layers/{layer_id}.geojson',
            'props': list(layer.get('props', {})),
            **{
                key: layer[key]
                for key in ('color', 'color_by', 'icon')
                if key in layer
            },
        }
        for layer_id, layer in LAYERS.items()
    ]


@make_action
def export_map_layers(debug: bool = False, **kwargs) -> mrsm.SuccessTuple:
    """Run `mrsm export map_layers` to pregenerate the app layer files."""
    import json

    output_dir = _output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)

    num_successes = 0
    for layer_id in LAYERS:
        info(f"Exporting layer '{layer_id}'...")
        try:
            json_str = _build_layer_geojson(layer_id, debug=debug)
        except Exception as e:
            return False, f"Failed to export layer '{layer_id}':\n{e}"
        if json_str is None:
            continue

        layer_path = output_dir / f'{layer_id}.geojson'
        with open(layer_path, 'w+') as f:
            f.write(json_str)
        mrsm.pprint((True, f"Wrote file '{layer_path}'."))
        num_successes += 1

    with open(output_dir / 'index.json', 'w+') as f:
        json.dump({'layers': _layer_index()}, f)

    if num_successes == 0:
        return False, "Did not export any layers."

    return True, f"Successfully exported {num_successes} of {len(LAYERS)} layers."


@api_plugin
def init_app(app):
    """Register the map-layers HTTP routes on the Meerschaum API app."""
    import uuid
    import shutil
    from pathlib import Path
    from fastapi import Form, File, UploadFile, Request
    from fastapi.responses import JSONResponse, FileResponse, Response

    @app.get('/map-layers/index.json')
    def map_layers_index():
        return JSONResponse({'layers': _layer_index()})

    @app.get('/map-layers/{layer_id}.geojson')
    def map_layer_geojson(
        layer_id: str,
        bbox: str = None,
        zoom: int = 13,
    ):
        if layer_id not in LAYERS:
            return JSONResponse({'error': f"Unknown layer '{layer_id}'."}, status_code=404)

        # Viewport query: ?bbox=minlon,minlat,maxlon,maxlat&zoom=14
        if bbox:
            try:
                minlon, minlat, maxlon, maxlat = (float(v) for v in bbox.split(','))
            except Exception:
                return JSONResponse({'error': 'Invalid bbox.'}, status_code=400)
            try:
                json_str = _build_bbox_geojson(
                    layer_id, minlon, minlat, maxlon, maxlat, zoom,
                )
            except Exception as e:
                return JSONResponse({'error': str(e)}, status_code=500)
            return Response(json_str, media_type='application/geo+json')

        pregenerated = _output_dir() / f'{layer_id}.geojson'
        if pregenerated.exists():
            return FileResponse(pregenerated, media_type='application/geo+json')

        cached = _CACHE.get(layer_id)
        if cached and (time.time() - cached[0]) < _CACHE_TTL_SECONDS:
            return Response(cached[1], media_type='application/geo+json')

        try:
            json_str = _build_layer_geojson(layer_id)
        except Exception as e:
            return JSONResponse({'error': str(e)}, status_code=500)
        if json_str is None:
            return JSONResponse({'error': f"No data for layer '{layer_id}'."}, status_code=404)

        _CACHE[layer_id] = (time.time(), json_str)
        return Response(json_str, media_type='application/geo+json')

    @app.post('/map-layers/feedback')
    async def submit_layer_feedback(
        request: Request,
        layer: str = Form(''),
        name: str = Form(''),
        lat: float = Form(None),
        lon: float = Form(None),
        props: str = Form(''),
        feedback: str = Form(''),
        photo: UploadFile = File(None),
    ):
        rec_id = uuid.uuid4().hex
        photo_filename = None
        if photo is not None and photo.filename:
            ext = Path(photo.filename).suffix or '.jpg'
            photo_filename = f'{rec_id}{ext}'
            with open(_photos_dir() / photo_filename, 'wb') as out:
                shutil.copyfileobj(photo.file, out)

        client = request.client
        FEEDBACK_PIPE.sync(
            [{
                'id': rec_id,
                'layer': layer or None,
                'name': name or None,
                'lat': lat,
                'lon': lon,
                'props': props or None,
                'feedback': feedback or None,
                'photo_filename': photo_filename,
                'ip': client.host if client else None,
                'user_agent': request.headers.get('user-agent'),
            }],
            blocking=False,
        )
        return JSONResponse({'ok': True, 'id': rec_id})
