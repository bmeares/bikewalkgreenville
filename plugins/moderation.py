#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Moderation console for community contributions (Bike Walk Greenville app),
plus the photo queue for walk-audit reports and bike-parking feedback.

Dash page:  /dash/moderation   (Meerschaum admins on the API instance only)

Routes (mounted on the Meerschaum API, i.e. https://bwg.mrsm.io):

  GET /bwg/moderation/pending-count             (admin Bearer) -> {photos, held}
      (both count community + walk-audit + bike-parking)
  GET /bwg/moderation/export.{gpx|osm|geojson|csv}  (admin Bearer)
      ?category=&status=published|held|rejected|removed|all&start=YYYY-MM-DD
       &end=YYYY-MM-DD&q=&geometry=Point|LineString|Polygon
  GET /bwg/moderation/photo/{filename}          (admin Bearer, admin Dash
      session cookie, or the short-lived ?exp=&sig= the console signs)

Everything reads the community revisions through `map-layers` and writes
decisions through its `moderate_contributions` (approve/reject) or an admin
rollback row (remove). Walk-audit / bike-parking photos are listed through
each plugin's `pending_photos()` and decided with its `set_photo_status()`;
their held text through `held_reports()` / `set_status()` (queue ids are
'<source>:<row id>'). Exports are files for JOSM / road owners: this never
talks to the OSM API.
"""

import csv
import hashlib
import hmac
import io
import json
import time
import uuid
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

import meerschaum as mrsm
from meerschaum.plugins import api_plugin, dash_plugin, web_page

__version__ = '0.2.0'

PUBLIC_BASE = 'https://bwg.mrsm.io'
SOURCE_TAG = 'Bike Walk Greenville community app'
STATUSES = ('published', 'held', 'rejected', 'removed', 'all')
GEOMETRY_TYPES = ('Point', 'LineString', 'Polygon')
EXPORT_FORMATS = {
    'gpx': 'application/gpx+xml',
    'osm': 'application/vnd.openstreetmap.data+xml',
    'geojson': 'application/geo+json',
    'csv': 'text/csv',
}
PATH_CATEGORIES = ('route-suggestion', 'shortcut')
# Point categories with an unambiguous OSM feature tag.
POINT_TAGS = {
    'bike-parking': {'amenity': 'bicycle_parking'},
    'repair-station': {'amenity': 'bicycle_repair_station'},
    'water-fountain': {'amenity': 'drinking_water'},
}
PHOTO_URL_TTL_S = 60 * 60
TABLE_MAX_ROWS = 300
SESSION_COOKIE = 'mrsm-session-id'
#: Non-community photo sources: plugin name -> its module (loaded lazily).
PHOTO_SOURCES = ('walk-audit', 'bike-parking')

_ML = None
_SOURCES: dict = {}


def _load_plugin(name: str):
    """The mounted plugin module `name` (sibling `<name>.py` as a fallback)."""
    module = None
    try:
        module = mrsm.Plugin(name).module
    except Exception:
        module = None
    if module is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            'bwg_' + name.replace('-', '_'), Path(__file__).resolve().parent / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _ml():
    """The mounted `map-layers` plugin module."""
    global _ML
    if _ML is None:
        _ML = _load_plugin('map-layers')
    return _ML


def _source(name: str):
    """The `walk-audit` / `bike-parking` plugin module."""
    if name not in _SOURCES:
        _SOURCES[name] = _load_plugin(name)
    return _SOURCES[name]


def _auth():
    """`bwg-auth`, the same module object map-layers uses."""
    return _ml()._auth()


# ------------------------------------------------------------- read model

def _ts(value):
    import pandas as pd
    try:
        ts = pd.Timestamp(value)
    except (ValueError, TypeError):
        return None
    if ts is pd.NaT:
        return None
    return ts.tz_localize('UTC') if ts.tzinfo is None else ts.tz_convert('UTC')


def mask_email(username: str | None) -> str:
    """'bennett@x.org' -> 'b…t@x.org'. The console never shows full emails."""
    if not username:
        return '(anonymous)'
    local, _, domain = str(username).partition('@')
    masked = local[:1] + '…' + (local[-1:] if len(local) > 1 else '')
    return f'{masked}@{domain}' if domain else masked


def user_key(username: str | None) -> str:
    """Opaque id for a submitter so raw emails stay out of the page's DOM."""
    return hashlib.sha256(str(username or '').encode()).hexdigest()[:16]


def contributions(rows=None) -> list[dict]:
    """Every contribution revision (newest first) with its moderation state.

    `status`: published (live on the map) | held | rejected | removed (rolled
    back) | replaced (an older version of an edited contribution).
    """
    ml = _ml()
    rows = ml._community_rows() if rows is None else rows
    reverted = {r['reverts'] for r in rows if r.get('reverts')}
    active = {r['id'] for r in ml._active_community(rows)}
    counts, _ = ml._votes(rows)
    out = []
    for r in rows:
        if r.get('category') in ml._META_CATEGORIES or r.get('reverts') or r.get('category') == 'rollback':
            continue
        if r['id'] in reverted:
            status = 'removed'
        elif r.get('status') in ('held', 'rejected'):
            status = r['status']
        else:
            status = 'published' if r['id'] in active else 'replaced'
        try:
            geometry = json.loads(r['geometry_json']) if r.get('geometry_json') else None
        except (TypeError, ValueError):
            geometry = None
        if not isinstance(geometry, dict) or geometry.get('type') not in GEOMETRY_TYPES:
            geometry = {'type': 'Point', 'coordinates': [r.get('lon'), r.get('lat')]}
        ts = _ts(r.get('ts'))
        count = counts.get(r['id'], {})
        out.append({
            'id': r['id'], 'ts': ts, 'created': ts.isoformat() if ts is not None else '',
            'date_display': ml._fmt_et(r.get('ts')),
            'category': r.get('category'), 'name': r.get('name'), 'comment': r.get('comment'),
            'status': status,
            'photo_status': r.get('photo_status') if r.get('photo_filename') else None,
            'photo_filename': r.get('photo_filename'),
            'photo_url': ml._photo_url(r),
            'up': count.get('up', 0), 'down': count.get('down', 0),
            'username': r.get('username'), 'replaces': r.get('replaces'),
            'lat': r.get('lat'), 'lon': r.get('lon'), 'geometry': geometry,
        })
    out.sort(key=lambda c: (c['ts'] is not None, c['ts'] or 0), reverse=True)
    return out


def _parse_day(value) -> date | None:
    if not value:
        return None
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def filter_contributions(items, *, category=None, status='published', start=None, end=None,
                         q=None, geometry=None, username=None) -> list[dict]:
    """Filters shared by the console, its downloads and the export route.
    `start`/`end` are inclusive days in Eastern time."""
    status = status or 'published'
    start, end = _parse_day(start), _parse_day(end)
    needle = (q or '').strip().lower()
    out = []
    for c in items:
        if category and c['category'] != category:
            continue
        if status != 'all' and c['status'] != status:
            continue
        if geometry and c['geometry']['type'] != geometry:
            continue
        if username and c['username'] != username:
            continue
        day = c['ts'].tz_convert('America/New_York').date() if c['ts'] is not None else None
        if (start and (day is None or day < start)) or (end and (day is None or day > end)):
            continue
        if needle and needle not in ' '.join(str(c[k] or '') for k in ('name', 'comment', 'category', 'id')).lower():
            continue
        out.append(c)
    return out


def validate_filters(params) -> dict:
    """Query/form values -> filter kwargs; ValueError on anything unexpected."""
    ml = _ml()
    category = (params.get('category') or '').strip() or None
    if category and category not in ml.SUBMISSION_CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(ml.SUBMISSION_CATEGORIES)}.")
    status = (params.get('status') or 'published').strip()
    if status not in STATUSES:
        raise ValueError(f"status must be one of {', '.join(STATUSES)}.")
    geometry = (params.get('geometry') or '').strip() or None
    if geometry and geometry not in GEOMETRY_TYPES:
        raise ValueError(f"geometry must be one of {', '.join(GEOMETRY_TYPES)}.")
    q = (params.get('q') or '').strip()
    if len(q) > 200:
        raise ValueError('Search text is up to 200 characters.')
    try:
        start, end = _parse_day(params.get('start')), _parse_day(params.get('end'))
    except ValueError:
        raise ValueError('Dates must look like YYYY-MM-DD.') from None
    return {'category': category, 'status': status, 'geometry': geometry, 'q': q or None,
            'start': start, 'end': end}


def pending_counts(items=None, sources=None, held=None) -> dict:
    """Photos awaiting review / held text, across all sources."""
    items = contributions() if items is None else items
    sources = source_photos() if sources is None else sources
    held = source_held() if held is None else held
    return {
        'photos': len(pending_photos(items)) + len(sources),
        'held': sum(1 for c in items if c['status'] == 'held') + len(held),
    }


def source_held() -> list[dict]:
    """Held walk-audit / bike-parking text, ids prefixed '<source>:'."""
    from meerschaum.utils.warnings import warn
    out = []
    for name in PHOTO_SOURCES:
        try:
            out += [dict(p, id=f"{name}:{p['id']}", source=name) for p in _source(name).held_reports()]
        except Exception as e:
            warn(f'moderation: {name} held reports unavailable: {e}')
    return out


def decide_held(target: str, *, username: str, status: str) -> mrsm.SuccessTuple:
    """Approve ('published') / reject held text; `target` as in `decide_photo`."""
    source, _, row_id = str(target).rpartition(':')
    if not source:
        return _ml().moderate_contributions([target], username=username, status=status)
    if source not in PHOTO_SOURCES:
        return False, 'Unknown report source.'
    return _source(source).set_status([row_id], status)


def source_photos() -> list[dict]:
    """Pending walk-audit / bike-parking photos, ids prefixed '<source>:'."""
    from meerschaum.utils.warnings import warn
    out = []
    for name in PHOTO_SOURCES:
        try:
            out += [dict(p, id=f"{name}:{p['id']}", source=name) for p in _source(name).pending_photos()]
        except Exception as e:
            warn(f'moderation: {name} photos unavailable: {e}')
    return out


def decide_photo(target: str, *, username: str, photo_status: str) -> mrsm.SuccessTuple:
    """Approve/reject one queued photo; `target` is a community id or
    '<source>:<row id>' from `source_photos()`."""
    source, _, row_id = str(target).rpartition(':')
    if not source:
        return _ml().moderate_contributions([target], username=username, photo_status=photo_status)
    if source not in PHOTO_SOURCES:
        return False, 'Unknown photo source.'
    return _source(source).set_photo_status([row_id], photo_status)


def pending_photos(items) -> list[dict]:
    return [c for c in items if c['photo_status'] == 'pending'
            and c['status'] in ('published', 'held', 'replaced')]


def submitters(items) -> list[dict]:
    """One row per signed-in submitter: counts and latest activity."""
    users: dict[str, dict] = {}
    for c in items:
        if not c['username']:
            continue
        u = users.setdefault(c['username'], {'username': c['username'], 'total': 0, 'live': 0,
                                             'held': 0, 'removed': 0, 'latest': None, 'latest_display': ''})
        u['total'] += 1
        u['live'] += c['status'] == 'published'
        u['held'] += c['status'] == 'held'
        u['removed'] += c['status'] in ('removed', 'rejected')
        if c['ts'] is not None and (u['latest'] is None or c['ts'] > u['latest']):
            u['latest'], u['latest_display'] = c['ts'], c['date_display']
    return sorted(users.values(), key=lambda u: (u['latest'] is not None, u['latest'] or 0), reverse=True)


# ------------------------------------------------------------- write actions

def remove_contributions(ids, *, username: str, reason: str) -> mrsm.SuccessTuple:
    """Admin removal: rolls back each contribution's WHOLE `replaces` chain,
    exactly like an admin's `POST /map-layers/community/rollback`.
    ponytail: mirrors the admin branch of that endpoint (~15 lines); lift both
    into one map-layers helper if the rollback rules ever change."""
    ml = _ml()
    reason = (reason or '').strip()
    if not reason or len(reason) > 2000:
        return False, 'Give a reason for the removal (up to 2000 characters).'
    ids = list(dict.fromkeys(str(i) for i in ids if i))[:200]
    with ml._COMMUNITY_LOCK:
        ml._COMMUNITY_CACHE['at'] = 0.0
        rows = ml._community_rows()
        by_id = {r['id']: r for r in rows}
        active = {r['id'] for r in ml._active_community(rows)}
        reverted = {r['reverts'] for r in rows if r.get('reverts')}
        targets: list[str] = []
        for i in ids:
            cur = i if i in active else None
            while cur and cur in by_id and cur not in reverted and cur not in targets:
                targets.append(cur)
                cur = by_id[cur].get('replaces')
        if not targets:
            return False, 'Already removed, or not live on the map.'
        success, msg = ml.COMMUNITY_PIPE.sync([{
            'id': uuid.uuid4().hex, 'category': 'rollback',
            'name': by_id[t].get('name'), 'comment': reason, 'reverts': t, 'replaces': None,
            'username': username,
            'geometry_json': json.dumps({'type': 'Point', 'coordinates': [by_id[t]['lon'], by_id[t]['lat']]}),
            'lat': by_id[t]['lat'], 'lon': by_id[t]['lon'],
        } for t in targets])
    if not success:
        return False, f'Removal was not saved: {msg}'
    ml._community_changed()
    return True, f'Removed {len(targets)} version(s).'


def set_banned(username: str, banned: bool) -> mrsm.SuccessTuple:
    """Flip `attributes.bwg.banned` on the API instance's Meerschaum user."""
    auth = _auth()
    conn = auth._users_conn()
    user = auth._user(username, conn)
    if conn.get_user_id(user) is None:
        return False, 'No such account.'
    if banned and conn.get_user_type(user) == 'admin':
        return False, 'Admins cannot be banned; demote them first.'
    attrs = dict(auth._attributes(username, conn))
    bwg = dict(attrs.get('bwg') or {}) if isinstance(attrs.get('bwg'), dict) else {}
    bwg['banned'] = bool(banned)
    attrs['bwg'] = bwg
    attrs.setdefault('scopes', [auth.BWG_SCOPE])
    success, msg = auth._save_attributes(username, attrs, conn)
    # This process's Bearer cache; other workers pick it up within 60 s.
    auth._USER_CACHE.clear()
    return (True, f"{mask_email(username)} {'banned' if banned else 'unbanned'}.") if success else (False, msg)


def is_banned(username: str) -> bool:
    auth = _auth()
    bwg = auth._attributes(username).get('bwg')
    return bool(isinstance(bwg, dict) and bwg.get('banned'))


# ------------------------------------------------------------- exports

def _rings(geometry) -> list[list]:
    """The coordinate sequences of a geometry (a point is a 1-vertex list)."""
    coords = geometry['coordinates']
    if geometry['type'] == 'Point':
        return [[coords]]
    if geometry['type'] == 'LineString':
        return [coords]
    return list(coords)


def to_geojson(items) -> str:
    return json.dumps({'type': 'FeatureCollection', 'features': [{
        'type': 'Feature', 'id': c['id'], 'geometry': c['geometry'],
        'properties': {'id': c['id'], 'created': c['created'], 'category': c['category'],
                       'name': c['name'], 'comment': c['comment'], 'status': c['status'],
                       'upvotes': c['up'], 'downvotes': c['down'],
                       'photo_url': PUBLIC_BASE + c['photo_url'] if c['photo_url'] else None,
                       'source': SOURCE_TAG},
    } for c in items]})


def to_csv(items) -> str:
    """Flat table for road owners: one row per contribution, WKT geometry."""
    ml = _ml()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(['id', 'created', 'category', 'name', 'comment', 'status', 'upvotes',
                     'downvotes', 'geometry_type', 'lat', 'lon', 'length_m', 'photo_url', 'wkt'])
    for c in items:
        g = c['geometry']
        pts = _rings(g)[0]
        wkt = {
            'Point': lambda: f'POINT ({pts[0][0]} {pts[0][1]})',
            'LineString': lambda: 'LINESTRING (' + ', '.join(f'{x} {y}' for x, y in pts) + ')',
            'Polygon': lambda: 'POLYGON (' + ', '.join(
                '(' + ', '.join(f'{x} {y}' for x, y in ring) + ')' for ring in g['coordinates']) + ')',
        }[g['type']]()
        length = round(ml._poly_len_m(pts), 1) if g['type'] == 'LineString' else ''
        writer.writerow([c['id'], c['created'], c['category'], c['name'] or '', c['comment'] or '',
                         c['status'], c['up'], c['down'], g['type'], c['lat'], c['lon'], length,
                         PUBLIC_BASE + c['photo_url'] if c['photo_url'] else '', wkt])
    return buf.getvalue()


def _xml(root) -> str:
    ET.indent(root)
    return "<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding='unicode') + '\n'


def to_gpx(items) -> str:
    """Waypoints for points, tracks for paths (and area outlines)."""
    root = ET.Element('gpx', {'version': '1.1', 'creator': SOURCE_TAG,
                              'xmlns': 'http://www.topografix.com/GPX/1/1'})
    ET.SubElement(ET.SubElement(root, 'metadata'), 'name').text = 'Bike Walk Greenville community contributions'

    def describe(el, c):
        ET.SubElement(el, 'name').text = c['name'] or c['category']
        if c['comment']:
            ET.SubElement(el, 'desc').text = c['comment']
        ET.SubElement(el, 'type').text = c['category']

    for c in (c for c in items if c['geometry']['type'] == 'Point'):
        lon, lat = c['geometry']['coordinates'][:2]
        wpt = ET.SubElement(root, 'wpt', {'lat': str(lat), 'lon': str(lon)})
        if c['created']:
            ET.SubElement(wpt, 'time').text = c['ts'].strftime('%Y-%m-%dT%H:%M:%SZ')
        describe(wpt, c)
    for c in (c for c in items if c['geometry']['type'] != 'Point'):
        trk = ET.SubElement(root, 'trk')
        describe(trk, c)
        for ring in _rings(c['geometry']):
            seg = ET.SubElement(trk, 'trkseg')
            for lon, lat in (p[:2] for p in ring):
                ET.SubElement(seg, 'trkpt', {'lat': str(lat), 'lon': str(lon)})
    return _xml(root)


def osm_tags(c) -> dict:
    """OSM tags for one contribution. Paths (route-suggestion/shortcut lines)
    -> highway=path + bicycle=yes + name + note=comment; no-entry areas ->
    access=no; a few point categories get their amenity tag; everything else
    carries only source / bwg:category / bwg:id / note."""
    kind = c['geometry']['type']
    tags = {'source': SOURCE_TAG, 'bwg:category': c['category'], 'bwg:id': c['id']}
    if kind == 'LineString' and c['category'] in PATH_CATEGORIES:
        tags.update({'highway': 'path', 'bicycle': 'yes'})
        if c['name']:
            tags['name'] = c['name']
        if c['comment']:
            tags['note'] = c['comment']
        return tags
    if kind == 'Polygon' and c['category'] == 'no-entry':
        tags['access'] = 'no'
    elif kind == 'Point':
        tags.update(POINT_TAGS.get(c['category'], {}))
    note = ' - '.join(x for x in (c['name'], c['comment']) if x)
    if note:
        tags['note'] = note
    return tags


def to_osm(items) -> str:
    """JOSM-importable OSM XML: new objects (negative ids), `upload="false"`
    so JOSM warns before anyone uploads unreviewed community data."""
    root = ET.Element('osm', {'version': '0.6', 'generator': SOURCE_TAG, 'upload': 'false'})
    ways = []
    next_id = [0]

    def new_id():
        next_id[0] -= 1
        return next_id[0]

    def node(lon, lat, tags=None):
        el = ET.SubElement(root, 'node', {'id': str(new_id()), 'lat': f'{lat:.7f}', 'lon': f'{lon:.7f}'})
        for k, v in (tags or {}).items():
            ET.SubElement(el, 'tag', {'k': k, 'v': str(v)})
        return el.get('id')

    for c in items:
        g = c['geometry']
        if g['type'] == 'Point':
            node(*g['coordinates'][:2], tags=osm_tags(c))
            continue
        pts = _rings(g)[0]
        closed = g['type'] == 'Polygon'
        refs = [node(*p[:2]) for p in (pts[:-1] if closed else pts)]
        ways.append((refs + refs[:1] if closed else refs, osm_tags(c)))
    # Nodes before ways, as JOSM / osmosis expect.
    for refs, tags in ways:
        way = ET.SubElement(root, 'way', {'id': str(new_id())})
        for ref in refs:
            ET.SubElement(way, 'nd', {'ref': ref})
        for k, v in tags.items():
            ET.SubElement(way, 'tag', {'k': k, 'v': str(v)})
    return _xml(root)


EXPORTERS = {'gpx': to_gpx, 'osm': to_osm, 'geojson': to_geojson, 'csv': to_csv}


def export(fmt: str, items) -> tuple[str, str]:
    """(file contents, filename)."""
    return EXPORTERS[fmt](items), f'bwg-community-{datetime.now(timezone.utc):%Y-%m-%d}.{fmt}'


# ------------------------------------------------------------- admin checks

def is_admin_username(username: str | None) -> bool:
    """Admin on the API instance, re-read every call (no session cache)."""
    if not username:
        return False
    auth = _auth()
    try:
        conn = auth._users_conn()
        return conn.get_user_type(auth._user(username, conn)) == 'admin'
    except Exception:
        return False


def session_admin(session_id) -> str | None:
    """The admin username behind a Meerschaum web session id, else None."""
    if not session_id:
        return None
    try:
        from meerschaum.api.dash.sessions import get_username_from_session
        username = get_username_from_session(str(session_id))
    except Exception:
        return None
    return username if is_admin_username(username) else None


def _photo_key() -> bytes:
    """HMAC key for signed thumbnail URLs, stable across API workers: derived
    from the API's own random, file-backed session secret (shared by every
    worker; never from a connector URI, which may be guessable or logged)."""
    global _PHOTO_KEY
    if _PHOTO_KEY is None:
        from meerschaum.api._oauth2 import SECRET
        _PHOTO_KEY = hmac.new(SECRET, b'bwg-moderation-photo', hashlib.sha256).digest()
    return _PHOTO_KEY


_PHOTO_KEY = None


def _photo_sig(filename: str, exp: int) -> str:
    return hmac.new(_photo_key(), f'{filename}:{exp}'.encode(), hashlib.sha256).hexdigest()


def signed_photo_url(filename: str) -> str:
    exp = int(time.time()) + PHOTO_URL_TTL_S
    return f'/bwg/moderation/photo/{filename}?exp={exp}&sig={_photo_sig(filename, exp)}'


def photo_signature_ok(filename: str, exp, sig) -> bool:
    try:
        exp = int(exp)
    except (TypeError, ValueError):
        return False
    return exp > time.time() and hmac.compare_digest(_photo_sig(filename, exp), str(sig or ''))


def _bearer_admin(request):
    """(user, None) for an admin Bearer, else (None, JSONResponse 401/403)."""
    from fastapi import HTTPException
    from fastapi.responses import JSONResponse
    try:
        user = _auth().require_user(request, write=False)
    except HTTPException as e:
        return None, JSONResponse({'error': e.detail}, status_code=e.status_code)
    if not user['is_admin']:
        return None, JSONResponse({'error': 'Moderators only.'}, status_code=403)
    return user, None


# ------------------------------------------------------------- API routes

@api_plugin
def init_app(app):
    from fastapi import Request
    from fastapi.responses import FileResponse, JSONResponse, Response

    @app.get('/bwg/moderation/pending-count')
    def moderation_pending_count(request: Request):
        _, denied = _bearer_admin(request)
        if denied:
            return denied
        try:
            return pending_counts()
        except RuntimeError as e:
            return JSONResponse({'error': str(e)}, status_code=503)

    @app.get('/bwg/moderation/export.{fmt}')
    def moderation_export(fmt: str, request: Request):
        _, denied = _bearer_admin(request)
        if denied:
            return denied
        if fmt not in EXPORTERS:
            return JSONResponse({'error': f"Format must be one of {', '.join(EXPORTERS)}."}, status_code=404)
        try:
            filters = validate_filters(request.query_params)
        except ValueError as e:
            return JSONResponse({'error': str(e)}, status_code=400)
        try:
            content, filename = export(fmt, filter_contributions(contributions(), **filters))
        except RuntimeError as e:
            return JSONResponse({'error': str(e)}, status_code=503)
        return Response(content, media_type=EXPORT_FORMATS[fmt],
                        headers={'Content-Disposition': f'attachment; filename="{filename}"'})

    @app.get('/bwg/moderation/photo/{filename}')
    def moderation_photo(filename: str, request: Request, exp: str = '', sig: str = ''):
        """Any contribution photo (pending included), admins only."""
        allowed = (
            photo_signature_ok(filename, exp, sig)
            or session_admin(request.cookies.get(SESSION_COOKIE))
            or (_auth().bwg_user(request) or {}).get('is_admin')
        )
        if not allowed:
            return JSONResponse({'error': 'Moderators only.'}, status_code=403)
        ml = _ml()
        path = ml._photos_dir() / filename
        known = any(r.get('photo_filename') == filename for r in ml._community_rows()
                    if r.get('category') not in ml._META_CATEGORIES)
        if not known:
            # Walk-audit / bike-parking photo (each plugin validates the name).
            path = next((p for p in (_source(n).photo_path(filename, approved_only=False)
                                     for n in PHOTO_SOURCES) if p), None)
        elif (Path(filename).name != filename
                or Path(filename).suffix.lower() not in ml.PHOTO_EXTENSIONS or not path.is_file()):
            path = None
        if path is None:
            return JSONResponse({'error': 'Photo not found.'}, status_code=404)
        return FileResponse(path, headers={'Cache-Control': 'private, max-age=3600',
                                           'X-Content-Type-Options': 'nosniff'})


# ------------------------------------------------------------- Dash console

@dash_plugin
def init_dash(dash_app):
    import dash.dcc as dcc
    import dash.html as html
    import dash_bootstrap_components as dbc
    from dash import ALL, Input, Output, State, callback_context, no_update
    from dash.exceptions import PreventUpdate

    ml = _ml()

    def _admin(session_store) -> str | None:
        return session_admin((session_store or {}).get('session-id') if isinstance(session_store, dict) else None)

    @web_page('moderation', login_required=True, page_group='Bike Walk Greenville')
    def moderation_layout():
        return dbc.Container([
            dcc.Location(id='mod-location'),
            dcc.Store(id='mod-refresh', data=0),
            dcc.Download(id='mod-download'),
            html.Div(id='mod-root'),
        ], fluid=True, className='py-3')

    def _filters():
        dropdown = lambda id_, options, value, placeholder: dcc.Dropdown(  # noqa: E731
            id=id_, options=options, value=value, placeholder=placeholder, clearable=value is None)
        return dbc.Row([
            dbc.Col(dropdown('mod-f-category', list(ml.SUBMISSION_CATEGORIES), None, 'Any category'), xs=12, md=3),
            dbc.Col(dropdown('mod-f-status', [{'label': s.capitalize(), 'value': s} for s in STATUSES],
                             'published', 'Status'), xs=6, md=2),
            dbc.Col(dropdown('mod-f-geometry', [{'label': g, 'value': g} for g in GEOMETRY_TYPES],
                             None, 'Any geometry'), xs=6, md=2),
            dbc.Col(dcc.DatePickerRange(id='mod-f-dates', clearable=True,
                                        start_date_placeholder_text='From', end_date_placeholder_text='To'),
                    xs=12, md=3),
            dbc.Col(dbc.Input(id='mod-f-q', placeholder='Search name / comment', debounce=True), xs=12, md=2),
        ], className='g-2 mb-3')

    @dash_app.callback(
        Output('mod-root', 'children'),
        Input('mod-location', 'pathname'),
        State('session-store', 'data'),
    )
    def render_root(_pathname, session_store):
        if not _admin(session_store):
            return dbc.Alert([
                html.H4('403: moderators only'),
                html.P('Sign in to Meerschaum with an admin account on this server to moderate.'),
                html.A('Sign in', href='/dash/login'),
            ], color='warning')
        return [
            html.H3('Community moderation'),
            html.Div(id='mod-alert'),
            _filters(),
            dbc.Tabs([
                dbc.Tab(label='Photos', tab_id='photos'),
                dbc.Tab(label='Contributions', tab_id='contributions'),
                dbc.Tab(label='Users', tab_id='users'),
                dbc.Tab(label='Export', tab_id='export'),
            ], id='mod-tabs', active_tab='photos', className='mb-3'),
            dbc.Input(id='mod-reason', placeholder='Reason for removal (required for Remove)',
                      maxLength=2000, className='mb-3'),
            dcc.Loading(html.Div(id='mod-tab-content')),
        ]

    def _photos_tab(items):
        queue = [dict(c, source='community') for c in pending_photos(items)] + source_photos()
        if not queue:
            return html.P('No photos waiting for review.')
        return dbc.Row([dbc.Col(dbc.Card([
            html.A(dbc.CardImg(src=signed_photo_url(c['photo_filename']), top=True,
                               style={'maxHeight': '260px', 'objectFit': 'cover'}, alt=c['name'] or 'photo'),
                   href=signed_photo_url(c['photo_filename']), target='_blank'),
            dbc.CardBody([
                html.H6(c['name'] or '(no name)'),
                html.Small(f"{c['source']} · {c['category']} · {c['date_display']} · {c['status']}"),
                html.P(c['comment'] or '', className='small mt-1 mb-2'),
                dbc.Button('Approve', id={'type': 'mod-act', 'action': 'photo-approved', 'id': c['id']},
                           color='success', size='sm', className='me-2'),
                dbc.Button('Reject', id={'type': 'mod-act', 'action': 'photo-rejected', 'id': c['id']},
                           color='danger', size='sm'),
            ]),
        ], className='h-100'), xs=12, sm=6, lg=4, className='mb-3') for c in queue])

    def _contributions_tab(items):
        def actions(c):
            buttons = []
            if c['status'] == 'held':
                buttons.append(dbc.Button('Approve', id={'type': 'mod-act', 'action': 'published', 'id': c['id']},
                                          color='success', size='sm', className='me-1'))
            if c['status'] in ('held', 'published'):
                buttons.append(dbc.Button('Reject', id={'type': 'mod-act', 'action': 'rejected', 'id': c['id']},
                                          color='warning', size='sm', className='me-1'))
            if c['status'] == 'published':
                buttons.append(dbc.Button('Remove', id={'type': 'mod-act', 'action': 'remove', 'id': c['id']},
                                          color='danger', size='sm'))
            return buttons
        shown = items[:TABLE_MAX_ROWS]
        header = html.Thead(html.Tr([html.Th(h) for h in (
            'Date', 'Category', 'Name', 'Status', 'Photo', 'Up/Down', 'Submitter', '')]))
        body = html.Tbody([html.Tr([
            html.Td(c['date_display']), html.Td(c['category']),
            html.Td([html.Div(c['name'] or ''), html.Small(c['comment'] or '', className='text-muted')]),
            html.Td(dbc.Badge(c['status'], color={'held': 'warning', 'published': 'success'}.get(c['status'], 'secondary'))),
            html.Td(c['photo_status'] or ''), html.Td(f"{c['up']} / {c['down']}"),
            html.Td(mask_email(c['username'])), html.Td(actions(c), style={'whiteSpace': 'nowrap'}),
        ]) for c in shown])
        note = f'Showing {len(shown)} of {len(items)} (narrow the filters to see more).' if len(items) > len(shown) else f'{len(items)} contribution(s).'
        return [*_held_list(), html.Small(note),
                dbc.Table([header, body], striped=True, hover=True, responsive=True, size='sm')]

    def _held_list():
        """Every held text (community + walk-audit + bike-parking), unfiltered."""
        held = [dict(c, source='community') for c in contributions() if c['status'] == 'held'] + source_held()
        if not held:
            return []
        rows = [html.Tr([
            html.Td(c['source']), html.Td(c['date_display']), html.Td(c['category']),
            html.Td([html.Div(c['name'] or ''), html.Small(c['comment'] or '', className='text-muted')]),
            html.Td(mask_email(c['username'])),
            html.Td([dbc.Button('Approve', id={'type': 'mod-act', 'action': 'held-published', 'id': c['id']},
                                color='success', size='sm', className='me-1'),
                     dbc.Button('Reject', id={'type': 'mod-act', 'action': 'held-rejected', 'id': c['id']},
                                color='warning', size='sm')], style={'whiteSpace': 'nowrap'}),
        ]) for c in held]
        header = html.Thead(html.Tr([html.Th(h) for h in ('Source', 'Date', 'Category', 'Text', 'Submitter', '')]))
        return [html.H5(f'Held reports ({len(held)})'),
                dbc.Table([header, html.Tbody(rows)], striped=True, responsive=True, size='sm', className='mb-4')]

    def _users_tab(items):
        rows = []
        for u in submitters(items):
            banned = is_banned(u['username'])
            rows.append(html.Tr([
                html.Td(mask_email(u['username'])), html.Td(u['total']), html.Td(u['live']),
                html.Td(u['held']), html.Td(u['removed']), html.Td(u['latest_display']),
                html.Td(dbc.Button('Unban' if banned else 'Ban',
                                   id={'type': 'mod-act', 'action': 'unban' if banned else 'ban',
                                       'id': user_key(u['username'])},
                                   color='secondary' if banned else 'danger', size='sm')),
            ]))
        if not rows:
            return html.P('No signed-in submitters yet.')
        header = html.Thead(html.Tr([html.Th(h) for h in (
            'Submitter', 'Total', 'Live', 'Held', 'Removed/rejected', 'Latest', '')]))
        return [html.Small('Counts ignore the filters above.'),
                dbc.Table([header, html.Tbody(rows)], striped=True, responsive=True, size='sm')]

    def _export_tab(items):
        return [
            html.P(f'{len(items)} contribution(s) match the filters above. Files are for JOSM / road '
                   'owners: nothing is uploaded to OpenStreetMap.'),
            html.Div([dbc.Button(f'Download {label}', id={'type': 'mod-export', 'fmt': fmt},
                                 color='primary', className='me-2 mb-2')
                      for fmt, label in (('gpx', 'GPX'), ('osm', 'OSM XML'), ('geojson', 'GeoJSON'), ('csv', 'CSV'))]),
        ]

    def _filter_kwargs(category, status, geometry, start, end, q):
        return validate_filters({'category': category, 'status': status, 'geometry': geometry,
                                 'start': start, 'end': end, 'q': q})

    filter_states = [
        Input('mod-f-category', 'value'), Input('mod-f-status', 'value'), Input('mod-f-geometry', 'value'),
        Input('mod-f-dates', 'start_date'), Input('mod-f-dates', 'end_date'), Input('mod-f-q', 'value'),
    ]

    @dash_app.callback(
        Output('mod-tab-content', 'children'),
        Input('mod-tabs', 'active_tab'),
        Input('mod-refresh', 'data'),
        *filter_states,
        State('session-store', 'data'),
    )
    def render_tab(tab, _refresh, category, status, geometry, start, end, q, session_store):
        if not _admin(session_store):
            return dbc.Alert('Moderators only.', color='warning')
        try:
            items = contributions()
            if tab == 'photos':
                return _photos_tab(items)
            if tab == 'users':
                return _users_tab(items)
            filtered = filter_contributions(items, **_filter_kwargs(category, status, geometry, start, end, q))
        except (ValueError, RuntimeError) as e:
            return dbc.Alert(str(e), color='danger')
        return _export_tab(filtered) if tab == 'export' else _contributions_tab(filtered)

    @dash_app.callback(
        Output('mod-refresh', 'data'),
        Output('mod-alert', 'children'),
        Input({'type': 'mod-act', 'action': ALL, 'id': ALL}, 'n_clicks'),
        State('mod-reason', 'value'),
        State('mod-refresh', 'data'),
        State('session-store', 'data'),
        prevent_initial_call=True,
    )
    def act(_clicks, reason, refresh, session_store):
        trigger = callback_context.triggered_id
        if not trigger or not any(t.get('value') for t in callback_context.triggered):
            raise PreventUpdate
        username = _admin(session_store)
        if not username:
            return no_update, dbc.Alert('Moderators only.', color='danger')
        action, target = trigger['action'], trigger['id']
        if action in ('ban', 'unban'):
            match = next((u['username'] for u in submitters(contributions()) if user_key(u['username']) == target), None)
            success, msg = set_banned(match, action == 'ban') if match else (False, 'No such submitter.')
        elif action == 'remove':
            success, msg = remove_contributions([target], username=username, reason=reason)
        elif action.startswith('photo-'):
            success, msg = decide_photo(target, username=username, photo_status=action[len('photo-'):])
        elif action.startswith('held-'):
            success, msg = decide_held(target, username=username, status=action[len('held-'):])
        else:
            success, msg = ml.moderate_contributions([target], username=username, status=action)
        return (refresh or 0) + 1, dbc.Alert(msg, color='success' if success else 'danger',
                                             dismissable=True, duration=6000 if success else None)

    @dash_app.callback(
        Output('mod-download', 'data'),
        Input({'type': 'mod-export', 'fmt': ALL}, 'n_clicks'),
        *[State(i.component_id, i.component_property) for i in filter_states],
        State('session-store', 'data'),
        prevent_initial_call=True,
    )
    def download(_clicks, category, status, geometry, start, end, q, session_store):
        trigger = callback_context.triggered_id
        if not trigger or not any(t.get('value') for t in callback_context.triggered):
            raise PreventUpdate
        if not _admin(session_store):
            raise PreventUpdate
        try:
            items = filter_contributions(contributions(), **_filter_kwargs(category, status, geometry, start, end, q))
        except (ValueError, RuntimeError):
            raise PreventUpdate
        content, filename = export(trigger['fmt'], items)
        return {'content': content, 'filename': filename, 'type': EXPORT_FORMATS[trigger['fmt']]}
