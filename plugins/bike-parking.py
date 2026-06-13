#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bike Parking: serve parking-point locations as GeoJSON and accept user
photo + feedback submissions from the BWG app.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  GET  /bike-parking/data.geojson   -> FeatureCollection of parking points
  POST /bike-parking/submit         -> multipart: spot_name, lat, lon,
                                       feedback, photo (optional)

Submissions land in the `app/feedback/BikeParking` pipe (schema
`BikeParking`, table `parking_feedback`); photos are written to
`<root>/uploads/bike-parking/`.
"""

import meerschaum as mrsm
from meerschaum.plugins import api_plugin

__version__ = '0.2.0'

#: Greenville County-ish bbox (south, west, north, east) for the OSM fetch.
OSM_BBOX = (34.58, -82.65, 35.10, -82.10)
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'

# Parking points, synced via this plugin's `fetch()` (OSM Overpass).
# Registered + synced through projects/bike-parking.yaml:
#   mrsm compose sync pipes --file projects/bike-parking.yaml
LOCATIONS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'plugin:bike-parking', 'locations', 'greenville',
    instance='sql:bwg',
)

# User-submitted photos + feedback.
FEEDBACK_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'feedback', 'BikeParking',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'BikeParking',
        'target': 'parking_feedback',
        'columns': {
            'datetime': 'ts',
            'id': 'id',
        },
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'spot_name': 'string',
            'lat': 'float',
            'lon': 'float',
            'feedback': 'string',
            'photo_filename': 'string',
            'ip': 'string',
            'user_agent': 'string',
        },
    },
)


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs):
    """Fetch OSM `amenity=bicycle_parking` points for greater Greenville
    (Overpass API). Returns docs for the locations pipe."""
    requests = mrsm.attempt_import('requests')

    south, west, north, east = OSM_BBOX
    query = f"""
    [out:json][timeout:60];
    nwr["amenity"="bicycle_parking"]({south},{west},{north},{east});
    out center tags;
    """
    resp = requests.post(
        OVERPASS_URL,
        data={'data': query},
        headers={'User-Agent': f'bwg-bike-parking/{__version__} (data@bikewalkgreenville.org)'},
        timeout=90,
    )
    resp.raise_for_status()
    elements = resp.json().get('elements', [])

    docs = []
    for el in elements:
        lat = el.get('lat') or (el.get('center') or {}).get('lat')
        lon = el.get('lon') or (el.get('center') or {}).get('lon')
        if lat is None or lon is None:
            continue
        tags = el.get('tags', {})
        try:
            capacity = int(tags['capacity'])
        except (KeyError, ValueError, TypeError):
            capacity = None
        name = tags.get('name') or tags.get('description') or (
            f"Bike rack ({capacity} spaces)" if capacity else "Bike rack"
        )
        address = ', '.join(
            part for part in (
                ' '.join(
                    p for p in (tags.get('addr:housenumber'), tags.get('addr:street'))
                    if p
                ),
                tags.get('addr:city'),
            ) if part
        )
        docs.append({
            'id': f"osm-{el.get('type')}-{el.get('id')}",
            'name': name,
            'lat': float(lat),
            'lon': float(lon),
            'capacity': capacity,
            'address': address,
        })

    return docs


def _photos_dir():
    """Directory where uploaded photos are stored (created on demand)."""
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    photos_dir = Path(ROOT_DIR_PATH) / 'uploads' / 'bike-parking'
    photos_dir.mkdir(parents=True, exist_ok=True)
    return photos_dir


@api_plugin
def init_app(app):
    """Register the bike-parking HTTP routes on the Meerschaum API app."""
    import uuid
    import shutil
    from pathlib import Path
    from fastapi import Form, File, UploadFile, Request
    from fastapi.responses import JSONResponse

    @app.get('/bike-parking/data.geojson')
    def bike_parking_geojson():
        features = []
        try:
            if LOCATIONS_PIPE.exists():
                df = LOCATIONS_PIPE.get_data()
                for row in (df.to_dict(orient='records') if df is not None else []):
                    lat, lon = row.get('lat'), row.get('lon')
                    if lat is None or lon is None:
                        continue
                    features.append({
                        'type': 'Feature',
                        'geometry': {
                            'type': 'Point',
                            'coordinates': [float(lon), float(lat)],
                        },
                        'properties': {
                            'name': row.get('name') or 'Bike Parking',
                            'capacity': row.get('capacity'),
                            'address': row.get('address') or '',
                        },
                    })
        except Exception:
            pass
        return JSONResponse({'type': 'FeatureCollection', 'features': features})

    @app.post('/bike-parking/submit')
    async def submit_bike_parking(
        request: Request,
        spot_name: str = Form(''),
        lat: float = Form(None),
        lon: float = Form(None),
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
                'spot_name': spot_name or None,
                'lat': lat,
                'lon': lon,
                'feedback': feedback or None,
                'photo_filename': photo_filename,
                'ip': client.host if client else None,
                'user_agent': request.headers.get('user-agent'),
            }],
            blocking=False,
        )
        return JSONResponse({'ok': True, 'id': rec_id})
