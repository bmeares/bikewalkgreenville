#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Walk Audit: accept accessibility-issue reports from the BWG app, resolve the
responsible office from "Who Owns The Roads" data, and store the report so it
shows up on the map.

Reports are NOT forwarded to municipal offices. Owner resolution is stored with
each report (useful for later analysis and for the app's contact card), and an
optional notification email goes to BWG staff only.

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  POST /walk-audit/submit           -> (Bearer) multipart: category, comment,
                                       lat, lon, photo (optional)
                                       -> {..., status, photo_status}
  GET  /walk-audit/reports.geojson  -> FeatureCollection of submitted reports
                                       (dismissed, held and rejected excluded)
  GET  /walk-audit/categories.json  -> report categories for the app UI
  POST /walk-audit/dismiss          -> (Bearer) json: id, reason. Removes a
                                       report from the map (reporter or admin
                                       only, else 403); the report and the
                                       dismissal both stay in public history.
  GET  /walk-audit/history          -> every report and dismissal, newest first
  GET  /walk-audit/photos/{name}    -> an admin-approved report photo (else 404)

Every write needs a signed-in app user (`bwg-auth`). Text that trips
`moderation_check` is stored `status='held'` and stays off the map until a
moderator approves it. Photos are stored `photo_status='pending'` and are never
served, linked or emailed until an admin approves them in /dash/moderation.

Owner resolution: nearest segment in "Roads".roads (KNN, SRID 6570).

Config lives under the `plugins:walk-audit` Meerschaum config keys (set via a
compose project file locally, or the API container's config in prod):

  plugins:
    walk-audit:
      smtp:
        host, port, username, password
      notify:
        enabled: true          # false disables staff email entirely
        recipient: ...         # defaults to smtp:username

The repo is public — never commit literal credentials; interpolate them from
the environment in the compose file.
"""

import math
import threading
import time

import meerschaum as mrsm
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import warn

__version__ = '0.4.0'

CATEGORIES = [
    {'id': 'broken-sidewalk', 'label': 'Broken / uneven sidewalk'},
    {'id': 'missing-sidewalk', 'label': 'Missing sidewalk'},
    {'id': 'obstruction', 'label': 'Obstruction (pole, sign, overgrowth)'},
    {'id': 'dangerous-crossing', 'label': 'Dangerous crossing / slip lane'},
    {'id': 'bike-lane-issue', 'label': 'Bike lane issue (unprotected, blocked, debris)'},
    {'id': 'signal-issue', 'label': 'Signal / crossing button issue'},
    {'id': 'lighting', 'label': 'Poor lighting'},
    # Rider-suggested shortcuts (tunnels, cut-throughs, paths across parking
    # lots) — reviewed, then added to map-layers.py's CUSTOM_PATHS so the
    # router and the shortcuts layer both learn them.
    {'id': 'missing-shortcut',
     'label': 'Suggest a shortcut (tunnel, path, cut-through)'},
    {'id': 'other', 'label': 'Other accessibility issue'},
]
CATEGORY_LABELS = {c['id']: c['label'] for c in CATEGORIES}

# Walk-audit reports are intended for the Greenville service area.  These are
# deliberately a little wider than the county boundary to avoid rejecting a
# report right at the edge, while blocking arbitrary world-wide coordinates.
SERVICE_BOUNDS = (34.58, -82.65, 35.10, -82.10)  # min lat, min lon, max lat, max lon
SUBMIT_MAX_PER_HOUR = 10
SUBMIT_MAX_COMMENT_CHARS = 2000
SUBMIT_MAX_PHOTO_BYTES = 8 * 1024 * 1024
PHOTO_EXTENSIONS = ('.jpg', '.jpeg', '.png', '.webp')
PHOTO_STATUSES = ('approved', 'rejected', 'pending')
TEXT_STATUSES = ('published', 'rejected', 'held')
MODERATION_URL = 'https://bwg.mrsm.io/dash/moderation'
_SUBMIT_HITS: dict[str, list[float]] = {}
_SUBMIT_HITS_LOCK = threading.Lock()


def _submit_rate_limited(key: str | None) -> bool:
    """True when this user has used up their hourly walk-audit submissions."""
    key = key or 'unknown'
    now = time.time()
    with _SUBMIT_HITS_LOCK:
        hits = [t for t in _SUBMIT_HITS.get(key, []) if now - t < 3600]
        if len(hits) >= SUBMIT_MAX_PER_HOUR:
            _SUBMIT_HITS[key] = hits
            return True
        hits.append(now)
        _SUBMIT_HITS[key] = hits
    return False

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
            # held | published | rejected (older rows: null = published).
            'status': 'string',
            # pending | approved | rejected (older rows with a photo: null = pending).
            'photo_status': 'string',
            'username': 'string',
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


#: Public edit log for reports. Only `dismiss` exists today; the `action`
#: column is what a future "restore" would key on.
EDITS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'report_edits', 'WalkAudit',
    instance='sql:bwg',
    parameters={
        'autotime': True,
        'schema': 'WalkAudit',
        'target': 'report_edits',
        'columns': {'datetime': 'ts', 'id': 'id'},
        'dtypes': {
            'ts': 'datetime',
            'id': 'string',
            'report_id': 'string',
            'action': 'string',
            'reason': 'string',
            'ip': 'string',
            'username': 'string',
        },
    },
)


def _fmt_et(ts) -> str:
    """'Sep 5, 2026 · 12:33 AM ET' — the app shows this instead of an ISO stamp."""
    import pandas as pd
    try:
        dt = pd.Timestamp(ts)
        dt = dt.tz_localize('UTC') if dt.tzinfo is None else dt
        return dt.tz_convert('America/New_York').strftime('%b %-d, %Y · %-I:%M %p ET')
    except (ValueError, TypeError):
        return str(ts or '')


def _rows(pipe, columns) -> list:
    if not pipe.exists():
        return []
    df = pipe.get_data(select_columns=columns)
    if df is None:
        return []
    # astype(object): `where` alone leaves NaN in float/all-null columns (not JSON).
    return df.astype(object).where(df.notna(), None).to_dict(orient='records')


def _report_rows(columns) -> list:
    """Report rows with the moderation columns backfilled for older rows."""
    rows = _rows(REPORTS_PIPE, list(columns) + ['status', 'photo_filename', 'photo_status', 'username'])
    for r in rows:
        r['status'] = r.get('status') or 'published'
        r['username'] = r.get('username')
        if r.get('photo_filename') and not r.get('photo_status'):
            r['photo_status'] = 'pending'
        r.setdefault('photo_status', None)
    return rows


def _photo_url(r) -> str | None:
    """Public photo URL, only once a moderator approved it."""
    if r.get('photo_filename') and r.get('photo_status') == 'approved':
        return f"/walk-audit/photos/{r['photo_filename']}"
    return None


def photo_path(filename: str, approved_only: bool = True):
    """Path of a known report photo on disk (approved ones only unless
    `approved_only=False`, which the admin moderation console uses), else None."""
    from pathlib import Path
    if Path(filename).name != filename or Path(filename).suffix.lower() not in PHOTO_EXTENSIONS:
        return None
    row = next((r for r in _report_rows(['id']) if r.get('photo_filename') == filename), None)
    if row is None or (approved_only and row['photo_status'] != 'approved'):
        return None
    path = _photos_dir() / filename
    return path if path.is_file() else None


def _queue_rows(keep) -> list:
    """Report rows for the moderation console where `keep(row)`."""
    return [{
        'id': r['id'], 'ts': r.get('ts'), 'date_display': _fmt_et(r.get('ts')),
        'category': r.get('category'), 'name': CATEGORY_LABELS.get(r.get('category'), 'Issue')
        + (f" near {r['road_name']}" if r.get('road_name') else ''),
        'comment': r.get('comment'), 'status': r['status'], 'username': r['username'],
        'photo_filename': r.get('photo_filename'),
    } for r in _report_rows(['ts', 'id', 'category', 'comment', 'road_name']) if keep(r)]


def pending_photos() -> list:
    """Report photos awaiting review (moderation console)."""
    return _queue_rows(lambda r: r['photo_status'] == 'pending')


def held_reports() -> list:
    """Reports whose text is held for review (moderation console)."""
    return _queue_rows(lambda r: r['status'] == 'held')


def set_status(ids, status: str) -> mrsm.SuccessTuple:
    """Admin text decision (published | rejected | held), in place on the row."""
    if status not in TEXT_STATUSES:
        return False, 'Unknown moderation decision.'
    wanted = set(ids)
    targets = [r for r in _report_rows(['ts', 'id']) if r['id'] in wanted]
    if not targets:
        return False, 'No such report.'
    success, msg = REPORTS_PIPE.sync([{'ts': r['ts'], 'id': r['id'], 'status': status} for r in targets])
    return (True, f'Updated {len(targets)} report(s).') if success else (False, msg)


def set_photo_status(ids, photo_status: str) -> mrsm.SuccessTuple:
    """Admin photo decision, written in place on the report row."""
    if photo_status not in PHOTO_STATUSES:
        return False, 'Unknown moderation decision.'
    wanted = set(ids)
    targets = [r for r in _report_rows(['ts', 'id']) if r['id'] in wanted and r.get('photo_filename')]
    if not targets:
        return False, 'No such report photo.'
    success, msg = REPORTS_PIPE.sync([{'ts': r['ts'], 'id': r['id'], 'photo_status': photo_status} for r in targets])
    return (True, f'Updated {len(targets)} report photo(s).') if success else (False, msg)


def _edit_rows() -> list:
    return _rows(EDITS_PIPE, ['ts', 'id', 'report_id', 'action', 'reason'])


def _dismissed_ids(edits=None) -> set:
    return {e['report_id'] for e in (_edit_rows() if edits is None else edits) if e.get('action') == 'dismiss'}


def _photos_dir():
    from pathlib import Path
    from meerschaum.config.paths import ROOT_DIR_PATH
    photos_dir = Path(ROOT_DIR_PATH) / 'uploads' / 'walk-audit'
    photos_dir.mkdir(parents=True, exist_ok=True)
    return photos_dir


def _cfg(*keys, default=None):
    """Read a `plugins:walk-audit` config value; `default` when unset."""
    try:
        value = mrsm.get_config(
            'plugins', 'walk-audit', *keys,
            warn=False,
            write_missing=False,
        )
    except Exception:
        return default
    return default if value is None or value == '' else value


def _notify_recipient() -> str | None:
    """The BWG staff inbox notified on each report (never a municipal office)."""
    if not _cfg('notify', 'enabled', default=True):
        return None
    return _cfg('notify', 'recipient') or _cfg('smtp', 'username')


def _send_report_email(report: dict) -> str | None:
    """Notify BWG staff from data@bikewalkgreenville.org. Reports are never
    emailed to municipal offices. Photos are never attached (they are
    unreviewed); the body points at the moderation console instead.
    Returns the recipient used, or None."""
    host = _cfg('smtp', 'host')
    user = _cfg('smtp', 'username')
    password = _cfg('smtp', 'password')
    port = int(_cfg('smtp', 'port', default=587))
    recipient = _notify_recipient()
    if not recipient:
        return None
    if not (host and user and password):
        warn("walk-audit: plugins:walk-audit:smtp unset; report stored but not emailed.")
        return None

    category = CATEGORY_LABELS.get(report.get('category'), report.get('category') or 'Issue')
    road = report.get('road_name') or 'unknown road'
    owner = report.get('owner') or 'Unknown'
    maps_link = f"https://www.google.com/maps?q={report['lat']},{report['lon']}"

    lines = [
        "A walk audit report was submitted through the Bike Walk Greenville app.",
        ("It is held for moderator review (flagged text)" if report.get('status') == 'held'
         else "It is now visible on the app's map")
        + "; it has NOT been sent to any office.",
        "",
        f"Issue: {category}",
        f"Nearest road: {road} ({report.get('road_type') or 'unknown type'})",
        f"Likely responsible office: {owner}",
        f"Location: {report['lat']:.6f}, {report['lon']:.6f}",
        f"Map: {maps_link}",
        "",
        f"Reporter comment:\n{report.get('comment') or '(none)'}",
        "",
        *([f"Photo attached — review it in the moderation console: {MODERATION_URL}", ""]
          if report.get('photo_filename') else []),
        f"Report ID: {report['id']}",
        "-- Bike Walk Greenville Data Analytics",
    ]

    import smtplib
    from email.message import EmailMessage
    msg = EmailMessage()
    msg['Subject'] = f"[Walk Audit] {category} — {road}"
    msg['From'] = user
    msg['To'] = recipient
    msg.set_content('\n'.join(lines))

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            smtp.starttls()
            smtp.login(user, password)
            smtp.send_message(msg)
    except Exception as e:
        warn(f"walk-audit: SMTP send failed: {e}")
        return None
    return recipient


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


def _signed_in(request):
    """(user, None) for a signed-in writer, else (None, 401/403 response)."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    try:
        return _auth().require_user(request, write=True), None
    except HTTPException as e:
        return None, JSONResponse({'error': e.detail}, status_code=e.status_code)


@api_plugin
def init_app(app):
    """Register the walk-audit HTTP routes on the Meerschaum API app."""
    import uuid
    from fastapi import Form, File, HTTPException, UploadFile, Request
    from fastapi.responses import FileResponse, JSONResponse

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
        user, denied = _signed_in(request)
        if denied:
            return denied
        if (
            not (math.isfinite(lat) and math.isfinite(lon))
            or not (SERVICE_BOUNDS[0] <= lat <= SERVICE_BOUNDS[2])
            or not (SERVICE_BOUNDS[1] <= lon <= SERVICE_BOUNDS[3])
        ):
            return JSONResponse(
                {'error': 'Reports must be within the Greenville service area.'},
                status_code=400,
            )
        if len(comment or '') > SUBMIT_MAX_COMMENT_CHARS:
            return JSONResponse(
                {'error': 'Comment is too long (2000 characters max).'},
                status_code=400,
            )
        client = request.client
        if _submit_rate_limited(user['username']):
            return JSONResponse(
                {'error': 'Too many submissions — please try again later.'},
                status_code=429,
            )
        rec_id = uuid.uuid4().hex
        photo_filename = None
        photo_path = None
        if photo is not None and photo.filename:
            try:
                ext = _auth().validate_image(photo)
            except HTTPException as e:
                return JSONResponse({'error': e.detail}, status_code=e.status_code)
            photo_filename = f'{rec_id}{ext}'
            photo_path = _photos_dir() / photo_filename
            written = 0
            with open(photo_path, 'wb') as out:
                while chunk := photo.file.read(256 * 1024):
                    written += len(chunk)
                    if written > SUBMIT_MAX_PHOTO_BYTES:
                        break
                    out.write(chunk)
            if written > SUBMIT_MAX_PHOTO_BYTES:
                photo_path.unlink(missing_ok=True)
                return JSONResponse(
                    {'error': 'Photo is too large (8 MB max).'},
                    status_code=413,
                )

        road = _nearest_road(lat, lon)
        status = 'held' if _auth().moderation_check(comment, username=user['username']) == 'held' else 'published'
        photo_status = 'pending' if photo_filename else None
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
            # Staff notification inbox, not a municipal office (see module docs).
            'forwarded_to': _notify_recipient(),
            'ip': client.host if client else None,
            'user_agent': request.headers.get('user-agent'),
            'status': status,
            'photo_status': photo_status,
            'username': user['username'],
        }

        # Store synchronously so the app can refresh the reports layer and see
        # the new pin immediately; the staff email goes out off-thread.
        REPORTS_PIPE.sync([report])
        threading.Thread(
            target=_send_report_email,
            args=(report,),
            daemon=True,
        ).start()
        return JSONResponse({
            'ok': True,
            'id': rec_id,
            'road_name': road.get('road_name'),
            'owner': road.get('owner'),
            'owner_email': road.get('owner_email'),
            'owner_form': road.get('owner_form'),
            'status': status,
            'photo_status': photo_status,
        })

    @app.get('/walk-audit/reports.geojson')
    def walk_audit_reports_geojson():
        features = []
        try:
            rows = _report_rows(['ts', 'id', 'category', 'comment', 'lat', 'lon', 'road_name', 'owner'])
            dismissed = _dismissed_ids() if rows else set()
            for row in rows:
                lat, lon = row.get('lat'), row.get('lon')
                if (lat is None or lon is None or row.get('id') in dismissed
                        or row['status'] != 'published'):
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
                        'photo_url': _photo_url(row),
                    },
                })
        except Exception as e:
            warn(f"walk-audit: reports.geojson failed: {e}")
        return JSONResponse({'type': 'FeatureCollection', 'features': features})

    @app.post('/walk-audit/dismiss')
    async def dismiss_report(request: Request):
        user, denied = _signed_in(request)
        if denied:
            return denied
        if len(await request.body()) > 4096:
            return JSONResponse({'error': 'Request too large.'}, status_code=413)
        try:
            body = await request.json()
            report_id = str(body.get('id', ''))
            reason = str(body.get('reason', '')).strip()
            if not report_id or not reason or len(reason) > 2000:
                raise ValueError('Please explain the dismissal (up to 2000 characters).')
        except (ValueError, AttributeError):
            return JSONResponse({'error': 'Please explain the dismissal (up to 2000 characters).'}, status_code=400)
        report = next((r for r in _report_rows(['id']) if r['id'] == report_id
                       and r['status'] == 'published'), None)
        if report is None:
            return JSONResponse({'error': 'This report does not exist.'}, status_code=404)
        # Only the reporter or a moderator (legacy anonymous rows: moderators only).
        if not user['is_admin'] and (not report['username'] or report['username'] != user['username']):
            return JSONResponse({'error': 'You can only dismiss your own reports.'}, status_code=403)
        if report_id in _dismissed_ids():
            return JSONResponse({'error': 'This report is already dismissed.'}, status_code=409)
        client = request.client
        success, _ = EDITS_PIPE.sync([{
            'id': uuid.uuid4().hex, 'report_id': report_id, 'action': 'dismiss',
            'reason': reason, 'ip': client.host if client else None,
            'username': user['username'],
        }])
        if not success:
            return JSONResponse({'error': 'Dismissal was not saved. Please retry.'}, status_code=503)
        return {'ok': True}

    @app.get('/walk-audit/history')
    def walk_audit_history():
        """Reports and dismissals as one public edit log (no ip / user agent /
        username; held / rejected reports and unapproved photos stay out)."""
        reports = {r['id']: r for r in _report_rows(
            ['ts', 'id', 'category', 'comment', 'lat', 'lon', 'road_name'],
        ) if r['status'] == 'published'}
        edits = [e for e in _edit_rows() if e.get('report_id') in reports]
        dismissed = _dismissed_ids(edits)
        rows = []
        for r in reports.values():
            rows.append({
                'id': r['id'], 'ts': str(r.get('ts') or ''), 'ts_display': _fmt_et(r.get('ts')),
                'type': 'report', 'category': r.get('category'),
                'name': CATEGORY_LABELS.get(r.get('category'), 'Issue')
                        + (f" near {r['road_name']}" if r.get('road_name') else ''),
                'comment': r.get('comment') or '', 'active': r['id'] not in dismissed,
                'geometry': {'type': 'Point', 'coordinates': [r.get('lon'), r.get('lat')]},
                'photo_url': _photo_url(r),
            })
        for e in edits:
            r = reports.get(e.get('report_id'), {})
            rows.append({
                'id': e['id'], 'ts': str(e.get('ts') or ''), 'ts_display': _fmt_et(e.get('ts')),
                'type': e.get('action'), 'category': r.get('category'),
                'name': CATEGORY_LABELS.get(r.get('category'), 'Issue')
                        + (f" near {r['road_name']}" if r.get('road_name') else ''),
                'comment': e.get('reason') or '', 'active': False,
                'geometry': {'type': 'Point', 'coordinates': [r.get('lon'), r.get('lat')]}
                            if r else None,
            })
        rows.sort(key=lambda x: x['ts'], reverse=True)
        return {'edits': rows}

    @app.get('/walk-audit/photos/{filename}')
    def walk_audit_photo(filename: str):
        """An approved report photo; 404 for anything else."""
        path = photo_path(filename)
        if path is None:
            return JSONResponse({'error': 'Photo not found.'}, status_code=404)
        return FileResponse(path, headers={'Cache-Control': 'public, max-age=86400',
                                           'X-Content-Type-Options': 'nosniff'})
