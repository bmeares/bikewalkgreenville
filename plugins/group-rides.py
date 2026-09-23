#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Group rides: anonymous leader + members sharing live positions.

Routes (mounted on the Meerschaum API app, i.e. https://bwg.mrsm.io):

  POST /group-rides                 {name, rider_name, lat, lon} -> new ride (caller leads)
  GET  /group-rides/nearby?lat&lon  rides whose leader is within 305 m, pinged < 2 min
                                    (distance_m rounded to 50 m)
  POST /group-rides/{code}/join     {rider_name, lat, lon}
  POST /group-rides/{code}/ping     {member_token, lat, lon, heading?, speed?, route_rev?} -> push + pull
                                    (always `route_rev`; `route` only when route_rev differs/absent)
  PUT  /group-rides/{code}/route    {member_token, route|null} (leader only; null clears; bumps route_rev)
  POST /group-rides/{code}/leave    {member_token}
  POST /group-rides/{code}/end      {member_token} (leader only)

State lives in two upsert pipes on `sql:bwg` (`GroupRides.rides`,
`GroupRides.members`); Meerschaum creates each table on its first sync.
Only `sha256(member_token)` is stored. Rides end lazily (checked on every
request) after 6 h, or 30 min without a leader ping; rides (and their members)
that ended over 7 days ago are deleted lazily when a new ride starts.
Limits: create 30/h, nearby 120/h, join 30/h per IP; 60 members a ride (409);
bodies over 16 KB are refused (413; the leader's route PUT allows 200 KB).
`distance_m` in /nearby is rounded to 50 m.
"""

import hashlib
import hmac
import json
import math
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone

import meerschaum as mrsm
from meerschaum.plugins import api_plugin

__version__ = '0.1.0'

SHARE_URL = 'https://bwg.mrsm.io/bwg-app/?ride={code}'
#: No I, O, Q (look like 1/0), no L (looks like I), no S/Z (5/2).
CODE_ALPHABET = 'ABCDEFGHJKMNPRTUVWXY'
CODE_RE = re.compile(f'^[{CODE_ALPHABET}]{{4}}$')
NEARBY_M = 305
NEARBY_LEADER_S = 120
MEMBER_STALE_S = 300
LEADER_SILENCE_S = 30 * 60
RIDE_MAX_S = 6 * 3600
NAME_MAX = 30
ROUTE_MAX_BYTES = 200_000
CREATE_JOIN_MAX_PER_HOUR = 30
NEARBY_MAX_PER_HOUR = 120
JOIN_MAX_PER_HOUR = 30
MEMBERS_MAX = 60
BODY_MAX_BYTES = 16 * 1024
DISTANCE_ROUND_M = 50
PURGE_AFTER_S = 7 * 86400

RIDES_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'group_rides', 'rides',
    instance='sql:bwg',
    parameters={
        'schema': 'GroupRides',
        'target': 'rides',
        'upsert': True,
        'columns': {'primary': 'id'},
        'dtypes': {
            'id': 'string',
            'code': 'string',
            'name': 'string',
            'leader_member_id': 'string',
            'route': 'string',
            'route_rev': 'int',
            'created': 'datetime',
            'ended': 'datetime',
            'last_leader_ping': 'datetime',
        },
    },
)

MEMBERS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'app', 'group_rides', 'members',
    instance='sql:bwg',
    parameters={
        'schema': 'GroupRides',
        'target': 'members',
        'upsert': True,
        'columns': {'primary': 'id'},
        'dtypes': {
            'id': 'string',
            'ride_id': 'string',
            'name': 'string',
            'token_hash': 'string',
            'lat': 'float',
            'lon': 'float',
            'heading': 'float',
            'speed': 'float',
            'updated': 'datetime',
            'is_leader': 'bool',
        },
    },
)

# ponytail: in-process per-IP limiter; per worker, resets on restart.
# Move to a shared table if the API ever runs multiple workers behind a LB.
_HITS: dict[str, list[float]] = {}
_HITS_LOCK = threading.Lock()
# ponytail: one lock serialises read-modify-write per process; fine for ~30 riders.
_LOCK = threading.Lock()


def _rate_limited(ip: str | None, bucket: str = 'create', limit: int = CREATE_JOIN_MAX_PER_HOUR) -> bool:
    key = f"{bucket}:{ip or 'unknown'}"
    now = time.time()
    with _HITS_LOCK:
        hits = [t for t in _HITS.get(key, []) if now - t < 3600]
        limited = len(hits) >= limit
        if not limited:
            hits.append(now)
        _HITS[key] = hits
    return limited


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _null(v) -> bool:
    if v is None:
        return True
    try:
        return bool(v != v)  # NaN / NaT
    except Exception:
        return False


def _epoch(v) -> float | None:
    if _null(v):
        return None
    import pandas as pd
    ts = pd.Timestamp(v)
    if ts.tzinfo is None:
        ts = ts.tz_localize('UTC')
    return ts.timestamp()


def _iso(v) -> str | None:
    e = _epoch(v)
    return None if e is None else datetime.fromtimestamp(e, timezone.utc).isoformat()


def _float(v):
    return None if _null(v) else float(v)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _rows(pipe, params) -> list[dict]:
    if not pipe.exists():
        return []
    df = pipe.get_data(params=params)
    return [] if df is None else df.to_dict(orient='records')


def _distance_m(lat1, lon1, lat2, lon2) -> float:
    """Equirectangular approximation; plenty for a few hundred metres."""
    x = math.radians(lon2 - lon1) * math.cos(math.radians((lat1 + lat2) / 2))
    y = math.radians(lat2 - lat1)
    return 6371000 * math.hypot(x, y)


def _moderated(text: str) -> bool:
    """True when bwg-auth's `moderation_check` holds `text` (no username: the
    per-user repeat counter doesn't apply). No-op if the plugin is absent."""
    try:
        from meerschaum.plugins import import_plugins
        bwg_auth = import_plugins('bwg-auth', warn=False)
        check = getattr(bwg_auth, 'moderation_check', None)
        return bool(check) and check(text) == 'held'
    except Exception:
        return False


def _clean_name(raw, fallback: str) -> str:
    name = ' '.join(str(raw or '').split())[:NAME_MAX]
    if not name or _moderated(name):
        return fallback
    return name


def _sync(pipe, row: dict):
    success, msg = pipe.sync([row])
    if not success:
        from fastapi import HTTPException
        raise HTTPException(503, f'Could not save: {msg}')


def _expire(ride: dict) -> dict:
    """End `ride` if it outlived 6 h or its leader went silent for 30 min."""
    if not _null(ride.get('ended')):
        return ride
    now = time.time()
    created = _epoch(ride.get('created')) or now
    last = _epoch(ride.get('last_leader_ping')) or created
    if now - created > RIDE_MAX_S or now - last > LEADER_SILENCE_S:
        ride = dict(ride, ended=_now())
        _sync(RIDES_PIPE, ride)
    return ride


def _active_ride(code: str) -> dict | None:
    code = (code or '').upper()
    if not CODE_RE.match(code):
        return None
    for ride in _rows(RIDES_PIPE, {'code': code, 'ended': None}):
        if _null(ride.get('ended')):
            ride = _expire(ride)
            if _null(ride.get('ended')):
                return ride
    return None


def _members(ride_id: str) -> list[dict]:
    return _rows(MEMBERS_PIPE, {'ride_id': ride_id})


def _check_latlon(lat, lon):
    from fastapi import HTTPException
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise HTTPException(400, 'lat and lon are required numbers')
    if not (-90 <= lat <= 90 and -180 <= lon <= 180) or not (math.isfinite(lat) and math.isfinite(lon)):
        raise HTTPException(400, 'lat/lon out of range')
    return lat, lon


def _new_code() -> str:
    for _ in range(50):
        code = ''.join(secrets.choice(CODE_ALPHABET) for _ in range(4))
        if _active_ride(code) is None:
            return code
    from fastapi import HTTPException
    raise HTTPException(503, 'No ride codes available; try again')


def _new_member(ride_id: str, name: str, lat, lon, is_leader: bool) -> tuple[dict, str]:
    token = secrets.token_urlsafe(32)
    member = {
        'id': uuid.uuid4().hex,
        'ride_id': ride_id,
        'name': name,
        'token_hash': _hash(token),
        'lat': lat,
        'lon': lon,
        'heading': None,
        'speed': None,
        'updated': _now(),
        'is_leader': is_leader,
    }
    _sync(MEMBERS_PIPE, member)
    return member, token


def _auth(code: str, token, allow_ended: bool = False) -> tuple[dict, dict, list[dict]]:
    """Return (ride, member, members) for `token`.
    404 unknown (or ended, unless `allow_ended`) ride, 401 bad token."""
    from fastapi import HTTPException
    code = (code or '').upper()
    rides = _rows(RIDES_PIPE, {'code': code}) if CODE_RE.match(code) else []
    if not allow_ended:
        rides = [r for r in rides if _null(r.get('ended'))]
    rides = [_expire(r) for r in rides]
    if not allow_ended:
        rides = [r for r in rides if _null(r.get('ended'))]
    if not rides:
        raise HTTPException(404, 'Ride not found or ended')
    if not isinstance(token, str) or not token:
        raise HTTPException(401, 'Bad member token')
    digest = _hash(token)
    for ride in rides:
        members = _members(ride['id'])
        for member in members:
            stored = member.get('token_hash')
            if isinstance(stored, str) and hmac.compare_digest(stored, digest):
                return ride, member, members
    raise HTTPException(401, 'Bad member token')


def _rev(ride: dict) -> int:
    v = ride.get('route_rev')
    return 0 if _null(v) else int(v)


def _end(ride: dict):
    _sync(RIDES_PIPE, dict(ride, ended=_now()))


_PURGED = {'at': 0.0}


def _purge_old_rides():
    """Delete rides (and members) that ended over 7 days ago; at most hourly."""
    now = time.time()
    if now - _PURGED['at'] < 3600 or not RIDES_PIPE.exists():
        return
    _PURGED['at'] = now
    df = RIDES_PIPE.get_data(select_columns=['id', 'ended'])
    old = [r['id'] for r in ([] if df is None else df.to_dict(orient='records'))
           if (_epoch(r.get('ended')) or now) < now - PURGE_AFTER_S]
    if old:
        MEMBERS_PIPE.clear(params={'ride_id': old})
        RIDES_PIPE.clear(params={'id': old})


@api_plugin
def init_app(app):
    """Register the group-ride routes."""
    from fastapi import Body, Depends, HTTPException, Request

    def _ip(request: Request):
        return request.client.host if request.client else None

    def _body_cap(limit: int):
        async def check(request: Request):
            if len(await request.body()) > limit:
                raise HTTPException(413, 'Request too large')
        return Depends(check)

    small = [_body_cap(BODY_MAX_BYTES)]

    @app.post('/group-rides', dependencies=small)
    def create_ride(request: Request, body: dict = Body(...)):
        lat, lon = _check_latlon(body.get('lat'), body.get('lon'))
        if _rate_limited(_ip(request)):
            raise HTTPException(429, 'Too many rides; try again later')
        with _LOCK:
            try:
                _purge_old_rides()
            except Exception as e:
                from meerschaum.utils.warnings import warn
                warn(f'group-rides: purge failed: {e}')
            code = _new_code()
            ride_id = uuid.uuid4().hex
            member, token = _new_member(
                ride_id, _clean_name(body.get('rider_name'), 'Rider 1'), lat, lon, True,
            )
            now = _now()
            _sync(RIDES_PIPE, {
                'id': ride_id,
                'code': code,
                'name': _clean_name(body.get('name'), 'Group ride'),
                'leader_member_id': member['id'],
                'route': None,
                'route_rev': 0,
                'created': now,
                'ended': None,
                'last_leader_ping': now,
            })
        return {
            'code': code,
            'ride_id': ride_id,
            'member_id': member['id'],
            'member_token': token,
            'share_url': SHARE_URL.format(code=code),
        }

    @app.get('/group-rides/nearby')
    def nearby_rides(request: Request, lat: float, lon: float):
        lat, lon = _check_latlon(lat, lon)
        if _rate_limited(_ip(request), 'nearby', NEARBY_MAX_PER_HOUR):
            raise HTTPException(429, 'Too many lookups; try again later')
        now = time.time()
        out = []
        for ride in _rows(RIDES_PIPE, {'ended': None}):
            if not _null(ride.get('ended')) or not _null(_expire(ride).get('ended')):
                continue
            if now - (_epoch(ride.get('last_leader_ping')) or 0) > NEARBY_LEADER_S:
                continue
            members = [
                m for m in _members(ride['id'])
                if not _null(m.get('token_hash'))
                and now - (_epoch(m.get('updated')) or 0) <= MEMBER_STALE_S
            ]
            leader = next((m for m in members if m['id'] == ride['leader_member_id']), None)
            if leader is None or _null(leader.get('lat')):
                continue
            dist = _distance_m(lat, lon, float(leader['lat']), float(leader['lon']))
            if dist <= NEARBY_M:
                out.append({
                    'code': ride['code'],
                    'name': ride['name'],
                    'leader_name': leader['name'],
                    # Coarse on purpose: a stranger can't trilaterate the leader.
                    'distance_m': int(round(dist / DISTANCE_ROUND_M) * DISTANCE_ROUND_M),
                    'members': len(members),
                })
        return sorted(out, key=lambda r: r['distance_m'])

    @app.post('/group-rides/{code}/join', dependencies=small)
    def join_ride(code: str, request: Request, body: dict = Body(...)):
        lat, lon = _check_latlon(body.get('lat'), body.get('lon'))
        if _rate_limited(_ip(request), 'join', JOIN_MAX_PER_HOUR):
            raise HTTPException(429, 'Too many joins; try again later')
        with _LOCK:
            ride = _active_ride(code)
            if ride is None:
                raise HTTPException(404, 'Ride not found or ended')
            members = _members(ride['id'])
            if sum(not _null(m.get('token_hash')) for m in members) >= MEMBERS_MAX:
                raise HTTPException(409, f'This ride is full ({MEMBERS_MAX} riders)')
            fallback = f'Rider {len(members) + 1}'
            member, token = _new_member(
                ride['id'], _clean_name(body.get('rider_name'), fallback), lat, lon, False,
            )
        return {
            'ride_id': ride['id'],
            'member_id': member['id'],
            'member_token': token,
            'name': member['name'],
            'ride_name': ride['name'],
            'share_url': SHARE_URL.format(code=code),
        }

    @app.post('/group-rides/{code}/ping', dependencies=small)
    def ping_ride(code: str, body: dict = Body(...)):
        lat, lon = _check_latlon(body.get('lat'), body.get('lon'))
        heading, speed = body.get('heading'), body.get('speed')
        for v in (heading, speed):
            if v is not None and not (isinstance(v, (int, float)) and math.isfinite(v)):
                raise HTTPException(400, 'heading/speed must be numbers')
        with _LOCK:
            ride, me, members = _auth(code, body.get('member_token'), allow_ended=True)
            if not _null(ride.get('ended')):
                return {'ended': True, 'leader_id': ride['leader_member_id'], 'route': None,
                        'route_rev': _rev(ride), 'members': []}
            now = _now()
            me = dict(me, lat=lat, lon=lon, heading=heading, speed=speed, updated=now)
            _sync(MEMBERS_PIPE, me)
            if me['id'] == ride['leader_member_id']:
                ride = dict(ride, last_leader_ping=now)
                _sync(RIDES_PIPE, ride)
        members = [me if m['id'] == me['id'] else m for m in members]
        cutoff = time.time() - MEMBER_STALE_S
        route, rev = ride.get('route'), _rev(ride)
        out = {
            'ended': False,
            'ride_name': ride['name'],
            'share_url': SHARE_URL.format(code=code),
            'leader_id': ride['leader_member_id'],
            'route_rev': rev,
            'members': [
                {
                    'id': m['id'],
                    'name': m['name'],
                    'lat': _float(m.get('lat')),
                    'lon': _float(m.get('lon')),
                    'heading': _float(m.get('heading')),
                    'updated': _iso(m.get('updated')),
                    'is_leader': m['id'] == ride['leader_member_id'],
                }
                for m in members
                if not _null(m.get('token_hash')) and (_epoch(m.get('updated')) or 0) >= cutoff
            ],
        }
        # The route only travels when the caller's copy is stale.
        if body.get('route_rev') != rev:
            out['route'] = None if _null(route) else json.loads(route)
        return out

    @app.put('/group-rides/{code}/route', dependencies=[_body_cap(ROUTE_MAX_BYTES + BODY_MAX_BYTES)])
    def set_route(code: str, body: dict = Body(...)):
        route = body.get('route')
        if route is not None and (not isinstance(route, dict) or route.get('type') != 'Feature'):
            raise HTTPException(400, 'route must be a GeoJSON Feature')
        geom = (route or {}).get('geometry') or {}
        coords = geom.get('coordinates') if isinstance(geom, dict) else None
        if route is not None and (
            not isinstance(geom, dict) or geom.get('type') != 'LineString'
            or not isinstance(coords, list) or len(coords) < 2
            or not all(
                isinstance(c, list) and len(c) >= 2
                and all(isinstance(x, (int, float)) for x in c[:2])
                for c in coords
            )
        ):
            raise HTTPException(400, 'route geometry must be a LineString')
        text = None if route is None else json.dumps(route, separators=(',', ':'))
        if text and len(text.encode()) > ROUTE_MAX_BYTES:
            raise HTTPException(413, 'route too large')
        with _LOCK:
            ride, me, _ = _auth(code, body.get('member_token'))
            if me['id'] != ride['leader_member_id']:
                raise HTTPException(403, 'Only the leader can set the route')
            rev = _rev(ride) + 1
            _sync(RIDES_PIPE, dict(ride, route=text, route_rev=rev))
        return {'ok': True, 'route_rev': rev}

    @app.post('/group-rides/{code}/leave', dependencies=small)
    def leave_ride(code: str, body: dict = Body(...)):
        with _LOCK:
            ride, me, _ = _auth(code, body.get('member_token'))
            # ponytail: the leader leaving ends the ride (no hand-off).
            if me['id'] == ride['leader_member_id']:
                _end(ride)
            # Clearing the hash revokes the token and hides the member.
            _sync(MEMBERS_PIPE, dict(me, token_hash=None))
        return {'ok': True}

    @app.post('/group-rides/{code}/end', dependencies=small)
    def end_ride(code: str, body: dict = Body(...)):
        with _LOCK:
            ride, me, _ = _auth(code, body.get('member_token'))
            if me['id'] != ride['leader_member_id']:
                raise HTTPException(403, 'Only the leader can end the ride')
            _end(ride)
        return {'ok': True}
