#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Walk Audit: accept accessibility-issue reports from the BWG app, resolve the
responsible office from "Who Owns The Roads" data, store the report, and
forward it by email from data@bikewalkgreenville.org.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  POST /walk-audit/submit           -> multipart: category, comment, lat, lon,
                                       photo (optional)
  GET  /walk-audit/reports.geojson  -> FeatureCollection of submitted reports
  GET  /walk-audit/categories.json  -> report categories for the app UI

Owner resolution: nearest segment in "Roads".roads (KNN, SRID 6570). If the
owner has no email (e.g. SCDOT), the report is stored with the owner's online
form URL and the email goes to BWG staff only.

SMTP config from the environment (root .env / prod container env):
  MRSM_SMTP_HOST, MRSM_SMTP_PORT, MRSM_SMTP_USER, MRSM_SMTP_PASSWORD
  MRSM_WALK_AUDIT_TEST_RECIPIENT  -- while set, ALL mail goes here instead of
                                     the resolved office (pre-launch safety).
"""

import os
import threading

import meerschaum as mrsm
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import warn

__version__ = '0.1.0'

CATEGORIES = [
    {'id': 'broken-sidewalk', 'label': 'Broken / uneven sidewalk'},
    {'id': 'missing-sidewalk', 'label': 'Missing sidewalk'},
    {'id': 'obstruction', 'label': 'Obstruction (pole, sign, overgrowth)'},
    {'id': 'dangerous-crossing', 'label': 'Dangerous crossing / slip lane'},
    {'id': 'bike-lane-issue', 'label': 'Bike lane issue (unprotected, blocked, debris)'},
    {'id': 'signal-issue', 'label': 'Signal / crossing button issue'},
    {'id': 'lighting', 'label': 'Poor lighting'},
    {'id': 'other', 'label': 'Other accessibility issue'},
]
CATEGORY_LABELS = {c['id']: c['label'] for c in CATEGORIES}

REPORTS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'reports', 'WalkAudit',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'WalkAudit',
        'target': 'reports',
        'columns': {
            'datetime': 'ts',
            'id': 'id',
        },
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'category': 'string',
            'comment': 'string',
            'lat': 'float',
            'lon': 'float',
            'photo_filename': 'string',
            'road_name': 'string',
            'road_type': 'string',
            'owner': 'string',
            'owner_email': 'string',
            'owner_phone': 'string',
            'owner_form': 'string',
            'forwarded_to': 'string',
            'ip': 'string',
            'user_agent': 'string',
        },
    },
)

NEAREST_ROAD_QUERY = """
WITH pt AS (
    SELECT ST_Transform(ST_SetSRID(ST_MakePoint(:lon, :lat), 4326), 6570) AS geom
)
SELECT
    r."Name", r."Type", r."Owner", r."Email", r."Phone", r."Online Form",
    ST_Distance(r.geometry, pt.geom) AS distance_ft
FROM "Roads".roads AS r, pt
ORDER BY r.geometry <-> pt.geom
LIMIT 1
"""


def _nearest_road(lat: float, lon: float) -> dict:
    """Resolve the nearest road segment and its contact info; {} on failure."""
    try:
        conn = mrsm.get_connector('sql:bwg')
        df = conn.read(NEAREST_ROAD_QUERY, params={'lat': lat, 'lon': lon})
    except Exception as e:
        warn(f"walk-audit: nearest-road lookup failed: {e}")
        return {}
    if df is None or not len(df):
        return {}
    row = df.iloc[0]

    def _val(key):
        v = row.get(key)
        return None if v is None or (isinstance(v, float) and v != v) or v == 'N/A' else v

    return {
        'road_name': _val('Name'),
        'road_type': _val('Type'),
        'owner': _val('Owner'),
        'owner_email': _val('Email'),
        'owner_phone': _val('Phone'),
        'owner_form': _val('Online Form'),
        'distance_ft': round(float(row['distance_ft']), 1),
    }


def _photos_dir():
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    photos_dir = Path(ROOT_DIR_PATH) / 'uploads' / 'walk-audit'
    photos_dir.mkdir(parents=True, exist_ok=True)
    return photos_dir


def _env(key: str) -> str | None:
    """Environment first, then `<root>/.env` (the api container has no
    stack-level env hook for plugin secrets)."""
    if key in os.environ:
        return os.environ[key]
    try:
        from meerschaum.config.paths import ROOT_DIR_PATH
        env_path = ROOT_DIR_PATH / '.env'
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if line.startswith(f'{key}='):
                    return line.split('=', 1)[1].strip().strip('"\'')
    except Exception:
        pass
    return None


def _send_report_email(report: dict, photo_path=None) -> str | None:
    """Send the report from data@bikewalkgreenville.org. Returns the recipient
    actually used, or None if sending is unconfigured/failed."""
    host = _env('MRSM_SMTP_HOST')
    user = _env('MRSM_SMTP_USER')
    password = _env('MRSM_SMTP_PASSWORD')
    port = int(_env('MRSM_SMTP_PORT') or '587')
    if not (host and user and password):
        warn("walk-audit: MRSM_SMTP_* unset; report stored but not emailed.")
        return None

    # Pre-launch safety: while the test recipient is set, never email offices.
    recipient = _env('MRSM_WALK_AUDIT_TEST_RECIPIENT') or report.get('owner_email') or user

    category = CATEGORY_LABELS.get(report.get('category'), report.get('category') or 'Issue')
    road = report.get('road_name') or 'unknown road'
    owner = report.get('owner') or 'Unknown'
    maps_link = f"https://www.google.com/maps?q={report['lat']},{report['lon']}"

    lines = [
        "A walk audit report was submitted through the Bike Walk Greenville app.",
        "",
        f"Issue: {category}",
        f"Nearest road: {road} ({report.get('road_type') or 'unknown type'})",
        f"Responsible office: {owner}",
        f"Location: {report['lat']:.6f}, {report['lon']:.6f}",
        f"Map: {maps_link}",
        "",
        f"Reporter comment:\n{report.get('comment') or '(none)'}",
        "",
    ]
    if not report.get('owner_email'):
        lines.append(
            f"NOTE: {owner} has no email contact; submit via their online form: "
            f"{report.get('owner_form') or 'N/A'}"
        )
    lines += ["", f"Report ID: {report['id']}", "-- Bike Walk Greenville Data Analytics"]

    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['Subject'] = f"[Walk Audit] {category} — {road}"
    msg['From'] = user
    msg['To'] = recipient
    msg.set_content('\n'.join(lines))

    if photo_path is not None:
        try:
            data = photo_path.read_bytes()
            if len(data) <= 10 * 1024 * 1024:
                ext = photo_path.suffix.lstrip('.').lower() or 'jpeg'
                msg.add_attachment(
                    data, maintype='image',
                    subtype='jpeg' if ext == 'jpg' else ext,
                    filename=photo_path.name,
                )
        except Exception as e:
            warn(f"walk-audit: could not attach photo: {e}")

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as e:
        warn(f"walk-audit: SMTP send failed: {e}")
        return None
    return recipient


@api_plugin
def init_app(app):
    """Register the walk-audit HTTP routes on the Meerschaum API app."""
    import uuid
    import shutil
    from pathlib import Path
    from fastapi import Form, File, UploadFile, Request
    from fastapi.responses import JSONResponse

    @app.get('/walk-audit/categories.json')
    def walk_audit_categories():
        return JSONResponse({'categories': CATEGORIES})

    @app.post('/walk-audit/submit')
    async def submit_walk_audit(
        request: Request,
        category: str = Form('other'),
        comment: str = Form(''),
        lat: float = Form(...),
        lon: float = Form(...),
        photo: UploadFile = File(None),
    ):
        rec_id = uuid.uuid4().hex
        photo_filename = None
        photo_path = None
        if photo is not None and photo.filename:
            ext = Path(photo.filename).suffix or '.jpg'
            photo_filename = f'{rec_id}{ext}'
            photo_path = _photos_dir() / photo_filename
            with open(photo_path, 'wb') as out:
                shutil.copyfileobj(photo.file, out)

        road = _nearest_road(lat, lon)
        client = request.client
        report = {
            'id': rec_id,
            'category': category if category in CATEGORY_LABELS else 'other',
            'comment': comment or None,
            'lat': lat,
            'lon': lon,
            'photo_filename': photo_filename,
            'road_name': road.get('road_name'),
            'road_type': road.get('road_type'),
            'owner': road.get('owner'),
            'owner_email': road.get('owner_email'),
            'owner_phone': road.get('owner_phone'),
            'owner_form': road.get('owner_form'),
            'forwarded_to': None,
            'ip': client.host if client else None,
            'user_agent': request.headers.get('user-agent'),
        }

        # Email + store off the request thread; one sync so the row carries
        # the actual recipient (autotime would dupe on a second sync).
        def _forward():
            recipient = _send_report_email(report, photo_path)
            REPORTS_PIPE.sync([{**report, 'forwarded_to': recipient}])
        threading.Thread(target=_forward, daemon=True).start()
        return JSONResponse({
            'ok': True,
            'id': rec_id,
            'road_name': road.get('road_name'),
            'owner': road.get('owner'),
            'owner_email': road.get('owner_email'),
            'owner_form': road.get('owner_form'),
        })

    @app.get('/walk-audit/reports.geojson')
    def walk_audit_reports_geojson():
        features = []
        try:
            if REPORTS_PIPE.exists():
                df = REPORTS_PIPE.get_data(
                    select_columns=[
                        'ts', 'id', 'category', 'comment', 'lat', 'lon',
                        'road_name', 'owner',
                    ],
                )
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
                            'id': row.get('id'),
                            'category': row.get('category'),
                            'label': CATEGORY_LABELS.get(row.get('category'), 'Issue'),
                            'comment': row.get('comment') or '',
                            'road_name': row.get('road_name') or '',
                            'owner': row.get('owner') or '',
                            'ts': str(row.get('ts') or ''),
                        },
                    })
        except Exception as e:
            warn(f"walk-audit: reports.geojson failed: {e}")
        return JSONResponse({'type': 'FeatureCollection', 'features': features})
