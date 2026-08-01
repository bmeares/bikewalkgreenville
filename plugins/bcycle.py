#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Greenville BCycle: bike-share stations with live availability, served to the
BWG app as GeoJSON, plus the deep links that hand a rider off to the BCycle
app to actually unlock a bike.

Source is the system's public GBFS feed (`bcycle_greenville`, GBFS 1.1):

  station_information.json  -> id, name, address, lat/lon, per-station app links
  station_status.json       -> bikes/docks available, electric count, is_renting
  system_information.json   -> system name, website, store + discovery URIs

Routes (mounted on the Meerschaum API FastAPI app, i.e. https://bwg.mrsm.io):

  GET /bcycle/stations.geojson  -> FeatureCollection of stations (live status)
  GET /bcycle/system.json       -> system name/url + rental app links

Availability is volatile, so the HTTP responses are cached for
`_LIVE_TTL_SECONDS` only and sent with `Cache-Control: no-store`. Station
*locations* are also synced into a pipe (see `fetch()` /
`projects/bcycle.yaml`) so the map still has pins when GBFS is unreachable.
"""

import threading
import time
from typing import Any

import meerschaum as mrsm
from meerschaum.plugins import api_plugin

__version__ = '0.1.0'

#: GBFS system id for Greenville, SC (MobilityData systems.csv).
SYSTEM_ID = 'bcycle_greenville'
GBFS_BASE = f'https://gbfs.bcycle.com/{SYSTEM_ID}'
USER_AGENT = f'bwg-bcycle/{__version__} (data@bikewalkgreenville.org)'

#: The feed's own `ttl` is 60s; don't hammer it harder than that.
_LIVE_TTL_SECONDS = 45
_STATIC_TTL_SECONDS = 6 * 60 * 60

#: Fallback links when `system_information.json` can't be reached. The
#: `bcycle://` scheme is what the installed app registers; the Play Store URL
#: is the graceful degradation for riders who don't have it yet.
APP_DISCOVERY_URI = 'bcycle://'
APP_STORE_URI = 'https://play.google.com/store/apps/details?id=com.bcycle'
SYSTEM_URL = 'https://greenville.bcycle.com'

_CACHE: dict[str, tuple[float, Any]] = {}
_CACHE_LOCK = threading.Lock()

# Station locations, persisted so the map degrades to pins-without-availability
# if GBFS is down. Registered + synced through projects/bcycle.yaml.
STATIONS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'plugin:bcycle', 'stations', 'greenville',
    instance='sql:bwg',
)


def _feed(name: str, ttl: float) -> dict[str, Any]:
    """GET one GBFS feed, memoized for `ttl` seconds. Returns the `data`
    object ({} on any failure — a dead feed must not 500 the map)."""
    with _CACHE_LOCK:
        hit = _CACHE.get(name)
        if hit is not None and (time.time() - hit[0]) < ttl:
            return hit[1]
    requests = mrsm.attempt_import('requests')
    try:
        resp = requests.get(
            f'{GBFS_BASE}/{name}.json',
            headers={'User-Agent': USER_AGENT},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json().get('data') or {}
    except Exception:
        data = {}
    if data:
        with _CACHE_LOCK:
            _CACHE[name] = (time.time(), data)
        return data
    # Serve stale over empty: a five-minute-old dock count beats a blank map.
    with _CACHE_LOCK:
        hit = _CACHE.get(name)
    return hit[1] if hit is not None else {}


def _short_id(station_id: str) -> str:
    """`bcycle_greenville_2186` -> `2186` (what riders see on the kiosk)."""
    return str(station_id).rsplit('_', 1)[-1]


def get_stations(live: bool = True) -> list[dict[str, Any]]:
    """Merged station_information + station_status, newest-first cached.

    Each station: id, short_id, name, address, lat, lon, plus (when `live`)
    bikes, ebikes, docks, is_renting, is_returning and last_reported.
    """
    info = _feed('station_information', _STATIC_TTL_SECONDS)
    rows = info.get('stations') or []
    status_by_id: dict[str, dict] = {}
    if live:
        for st in (_feed('station_status', _LIVE_TTL_SECONDS).get('stations') or []):
            status_by_id[str(st.get('station_id'))] = st

    out: list[dict[str, Any]] = []
    for st in rows:
        lat, lon = st.get('lat'), st.get('lon')
        if lat is None or lon is None:
            continue
        station_id = str(st.get('station_id') or '')
        uris = st.get('rental_uris') or {}
        row: dict[str, Any] = {
            'id': station_id,
            'short_id': _short_id(station_id),
            'name': st.get('name') or 'BCycle station',
            'address': st.get('address') or '',
            'lat': float(lat),
            'lon': float(lon),
            'rental_uri': uris.get('android') or uris.get('ios'),
        }
        status = status_by_id.get(station_id)
        if status is not None:
            by_type = status.get('num_bikes_available_types') or {}
            row.update({
                'bikes': _int(status.get('num_bikes_available')),
                'ebikes': _int(by_type.get('electric')),
                'docks': _int(status.get('num_docks_available')),
                'is_renting': bool(status.get('is_renting', 1)),
                'is_returning': bool(status.get('is_returning', 1)),
                'last_reported': _int(status.get('last_reported')),
            })
        out.append(row)
    out.sort(key=lambda r: r['name'])
    return out


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_system() -> dict[str, Any]:
    """System name/url + the links used to hand off to the BCycle app."""
    data = _feed('system_information', _STATIC_TTL_SECONDS)
    apps = (data.get('rental_apps') or {}).get('android') or {}
    return {
        'system_id': data.get('system_id') or SYSTEM_ID,
        'name': data.get('name') or 'Greenville BCycle',
        'url': data.get('url') or SYSTEM_URL,
        'phone': data.get('phone_number'),
        'email': data.get('email'),
        'app_discovery_uri': apps.get('discovery_uri') or APP_DISCOVERY_URI,
        'app_store_uri': apps.get('store_uri') or APP_STORE_URI,
    }


def stations_geojson(live: bool = True) -> dict[str, Any]:
    """FeatureCollection for the app's `bcycle` map layer."""
    features = []
    for st in get_stations(live=live):
        props = {k: v for k, v in st.items() if k not in ('lat', 'lon')}
        bikes = props.get('bikes')
        # `name` is what the feature sheet titles; the availability line is
        # what a rider actually decides on.
        if bikes is not None:
            ebikes = props.get('ebikes') or 0
            props['availability'] = (
                f"{bikes} bike{'' if bikes == 1 else 's'} available"
                + (f' ({ebikes} electric)' if ebikes else '')
            )
            if not props.get('is_renting'):
                props['availability'] = 'Not renting right now'
        features.append({
            'type': 'Feature',
            'geometry': {'type': 'Point', 'coordinates': [st['lon'], st['lat']]},
            'properties': props,
        })
    return {'type': 'FeatureCollection', 'features': features}


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs):
    """Snapshot station locations (not availability) into `sql:bwg` so the
    layer survives a GBFS outage."""
    return [
        {
            'id': st['id'],
            'name': st['name'],
            'address': st['address'],
            'lat': st['lat'],
            'lon': st['lon'],
            'rental_uri': st.get('rental_uri'),
        }
        for st in get_stations(live=False)
    ]


@api_plugin
def init_app(app):
    """Register the BCycle HTTP routes on the Meerschaum API app."""
    from fastapi.responses import JSONResponse

    #: Availability changes minute to minute — never let a CDN or the app's
    #: HTTP cache pin it.
    _NO_STORE = {'Cache-Control': 'no-store'}

    @app.get('/bcycle/stations.geojson')
    def bcycle_stations_geojson():
        collection = stations_geojson()
        if not collection['features']:
            # GBFS unreachable: fall back to the synced locations.
            collection = _stations_from_pipe()
        return JSONResponse(collection, headers=_NO_STORE)

    @app.get('/bcycle/system.json')
    def bcycle_system():
        return JSONResponse(get_system())

    def _stations_from_pipe() -> dict[str, Any]:
        features = []
        try:
            if STATIONS_PIPE.exists():
                df = STATIONS_PIPE.get_data()
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
                            'short_id': _short_id(row.get('id') or ''),
                            'name': row.get('name') or 'BCycle station',
                            'address': row.get('address') or '',
                            'rental_uri': row.get('rental_uri'),
                        },
                    })
        except Exception:
            pass
        return {'type': 'FeatureCollection', 'features': features}
