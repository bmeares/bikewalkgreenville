#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Bike Parking: serve parking-point locations as GeoJSON and accept user
photo + feedback submissions from the BWG app.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  GET  /bike-parking/data.geojson   -> FeatureCollection of parking points
  POST /bike-parking/submit         -> (Bearer) multipart: spot_name, lat,
                                       lon, feedback, photo (optional)
                                       -> {ok, id, status, photo_status}
  GET  /bike-parking/photos/{name}  -> an admin-approved photo (else 404)

Submissions need a signed-in app user (`bwg-auth`). Flagged text is stored
`status='held'`; photos are stored `photo_status='pending'` and never served
until an admin approves them in /dash/moderation.

Submissions land in the `app/feedback/BikeParking` pipe (schema
`BikeParking`, table `parking_feedback`); photos are written to
`<root>/uploads/bike-parking/`.
"""

import math

import meerschaum as mrsm
from meerschaum.plugins import api_plugin

__version__ = '0.3.0'

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

# Public bike repair stands (pump + tools), also OSM-sourced.
REPAIR_PIPE: mrsm.Pipe = mrsm.Pipe(
    'plugin:bike-parking', 'repair_stations', 'greenville',
    instance='sql:bwg',
)

#: metric_key -> OSM amenity tag fetched for that pipe.
METRICS_AMENITIES = {
    'locations': 'bicycle_parking',
    'repair_stations': 'bicycle_repair_station',
}

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
            'status': 'string',        # held | published | rejected (null = published)
            'photo_status': 'string',  # pending | approved | rejected (null + photo = pending)
            'username': 'string',
        },
    },
)


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs):
    """Fetch OSM amenity points for greater Greenville (Overpass API).
    The amenity tag is chosen by the pipe's metric (see METRICS_AMENITIES)."""
    requests = mrsm.attempt_import('requests')

    amenity = METRICS_AMENITIES.get(pipe.metric_key, 'bicycle_parking')
    south, west, north, east = OSM_BBOX
    query = f"""
    [out:json][timeout:60];
    nwr["amenity"="{amenity}"]({south},{west},{north},{east});
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
        if amenity == 'bicycle_repair_station':
            name = tags.get('name') or tags.get('brand') or 'Bike repair station'
        else:
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


PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
PHOTO_STATUSES = ('approved', 'rejected', 'pending')
TEXT_STATUSES = ('published', 'rejected', 'held')
SUBMIT_MAX_PER_HOUR = 10
SUBMIT_MAX_TEXT_CHARS = 2000
SUBMIT_MAX_PHOTO_BYTES = 8 * 1024 * 1024


def _feedback_rows(columns=('ts', 'id')) -> list:
    """Feedback rows with the moderation columns backfilled for older rows."""
    if not FEEDBACK_PIPE.exists():
        return []
    df = FEEDBACK_PIPE.get_data(select_columns=list(columns) + [
        'status', 'photo_filename', 'photo_status', 'username'])
    if df is None:
        return []
    rows = df.astype(object).where(df.notna(), None).to_dict(orient='records')
    for r in rows:
        r['status'] = r.get('status') or 'published'
        r['username'] = r.get('username')
        r['photo_status'] = r.get('photo_status') or ('pending' if r.get('photo_filename') else None)
    return rows


def photo_path(filename: str, approved_only: bool = True):
    """Path of a known feedback photo on disk (approved only unless
    `approved_only=False`, which the admin moderation console uses), else None."""
    from pathlib import Path
    if Path(filename).name != filename or Path(filename).suffix.lower() not in PHOTO_EXTENSIONS:
        return None
    row = next((r for r in _feedback_rows() if r.get('photo_filename') == filename), None)
    if row is None or (approved_only and row['photo_status'] != 'approved'):
        return None
    path = _photos_dir() / filename
    return path if path.is_file() else None


def pending_photos(keep=lambda r: r['photo_status'] == 'pending') -> list:
    """Feedback photos awaiting review (moderation console)."""
    import pandas as pd
    out = []
    for r in _feedback_rows(('ts', 'id', 'spot_name', 'feedback')):
        if not keep(r):
            continue
        try:
            when = pd.Timestamp(r['ts']).tz_convert('America/New_York').strftime('%b %-d, %Y · %-I:%M %p ET')
        except (ValueError, TypeError):
            when = str(r.get('ts') or '')
        out.append({'id': r['id'], 'ts': r.get('ts'), 'date_display': when, 'category': 'bike-parking',
                    'name': r.get('spot_name'), 'comment': r.get('feedback'), 'status': r['status'],
                    'username': r['username'], 'photo_filename': r.get('photo_filename')})
    return out


def held_reports() -> list:
    """Feedback whose text is held for review (moderation console)."""
    return pending_photos(lambda r: r['status'] == 'held')


def set_status(ids, status: str) -> mrsm.SuccessTuple:
    """Admin text decision (published | rejected | held), in place on the row."""
    if status not in TEXT_STATUSES:
        return False, 'Unknown moderation decision.'
    wanted = set(ids)
    targets = [r for r in _feedback_rows() if r['id'] in wanted]
    if not targets:
        return False, 'No such feedback.'
    success, msg = FEEDBACK_PIPE.sync([{'ts': r['ts'], 'id': r['id'], 'status': status} for r in targets])
    return (True, f'Updated {len(targets)} feedback row(s).') if success else (False, msg)


def set_photo_status(ids, photo_status: str) -> mrsm.SuccessTuple:
    """Admin photo decision, written in place on the feedback row."""
    if photo_status not in PHOTO_STATUSES:
        return False, 'Unknown moderation decision.'
    wanted = set(ids)
    targets = [r for r in _feedback_rows() if r['id'] in wanted and r.get('photo_filename')]
    if not targets:
        return False, 'No such feedback photo.'
    success, msg = FEEDBACK_PIPE.sync([{'ts': r['ts'], 'id': r['id'], 'photo_status': photo_status} for r in targets])
    return (True, f'Updated {len(targets)} feedback photo(s).') if success else (False, msg)


def _auth():
    """The shared `bwg-auth` plugin module (same pattern as map-layers)."""
    global _BWG_AUTH
    if _BWG_AUTH is None:
        module = mrsm.Plugin('bwg-auth').module
        if module is None:
            import importlib.util
            from pathlib import Path
            spec = importlib.util.spec_from_file_location(
                'bwg_auth', Path(__file__).resolve().parent / 'bwg-auth.py')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        _BWG_AUTH = module
    return _BWG_AUTH


_BWG_AUTH = None


@api_plugin
def init_app(app):
    """Register the bike-parking HTTP routes on the Meerschaum API app."""
    import uuid
    from fastapi import Form, File, HTTPException, UploadFile, Request
    from fastapi.responses import FileResponse, JSONResponse

    def _points_geojson(pipe: mrsm.Pipe, default_name: str):
        features = []
        try:
            if pipe.exists():
                df = pipe.get_data()
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
                            'name': row.get('name') or default_name,
                            'capacity': row.get('capacity'),
                            'address': row.get('address') or '',
                        },
                    })
        except Exception:
            pass
        return JSONResponse({'type': 'FeatureCollection', 'features': features})

    @app.get('/bike-parking/data.geojson')
    def bike_parking_geojson():
        return _points_geojson(LOCATIONS_PIPE, 'Bike Parking')

    @app.get('/bike-parking/repair-stations.geojson')
    def repair_stations_geojson():
        return _points_geojson(REPAIR_PIPE, 'Bike repair station')

    @app.post('/bike-parking/submit')
    async def submit_bike_parking(
        request: Request,
        spot_name: str = Form(''),
        lat: float = Form(None),
        lon: float = Form(None),
        feedback: str = Form(''),
        photo: UploadFile = File(None),
    ):
        try:
            user = _auth().require_user(request, write=True)
        except HTTPException as e:
            return JSONResponse({'error': e.detail}, status_code=e.status_code)
        if len(spot_name or '') > 200 or len(feedback or '') > SUBMIT_MAX_TEXT_CHARS:
            return JSONResponse({'error': 'Text is too long (2000 characters max).'}, status_code=400)
        if (lat is None) != (lon is None) or (lat is not None and not (
                math.isfinite(lat) and math.isfinite(lon) and -90 <= lat <= 90 and -180 <= lon <= 180)):
            return JSONResponse({'error': 'A valid location is required.'}, status_code=400)
        if _auth().rate_limited('bike-parking-submit', user['username'], SUBMIT_MAX_PER_HOUR):
            return JSONResponse({'error': 'Too many submissions — please try again later.'}, status_code=429)
        rec_id = uuid.uuid4().hex
        photo_filename = None
        if photo is not None and photo.filename:
            try:
                ext = _auth().validate_image(photo)
            except HTTPException as e:
                return JSONResponse({'error': e.detail}, status_code=e.status_code)
            photo_filename = f'{rec_id}{ext}'
            path = _photos_dir() / photo_filename
            written = 0
            with open(path, 'wb') as out:
                while chunk := photo.file.read(256 * 1024):
                    written += len(chunk)
                    if written > SUBMIT_MAX_PHOTO_BYTES:
                        break
                    out.write(chunk)
            if written > SUBMIT_MAX_PHOTO_BYTES:
                path.unlink(missing_ok=True)
                return JSONResponse({'error': 'Photo is too large (8 MB max).'}, status_code=413)

        status = ('held' if _auth().moderation_check(f'{spot_name}\n{feedback}', username=user['username']) == 'held'
                  else 'published')
        photo_status = 'pending' if photo_filename else None
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
                'status': status,
                'photo_status': photo_status,
                'username': user['username'],
            }],
            blocking=False,
        )
        return JSONResponse({'ok': True, 'id': rec_id, 'status': status, 'photo_status': photo_status})

    @app.get('/bike-parking/photos/{filename}')
    def bike_parking_photo(filename: str):
        """An approved feedback photo; 404 for anything else."""
        path = photo_path(filename)
        if path is None:
            return JSONResponse({'error': 'Photo not found.'}, status_code=404)
        return FileResponse(path, headers={'Cache-Control': 'public, max-age=86400',
                                           'X-Content-Type-Options': 'nosniff'})
