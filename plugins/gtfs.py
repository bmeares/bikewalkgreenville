#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Greenlink GTFS ingest (static feed, service dates ~1 year out).

Feed: https://gtfs.greenlink.cadavl.com/GTA/GTFS/GTFS_GTA.zip

Metrics (see projects/transit.yaml):
  routes        -> transit.routes         (one row per route, color included)
  stops         -> transit.stops          (stop + the route short names serving it)
  route_shapes  -> transit.route_shapes   (one WKT LINESTRING per shape, route props)
"""

import csv
import io
import zipfile

import meerschaum as mrsm

__version__ = '0.1.0'

required = ['requests']

GTFS_URL = 'https://gtfs.greenlink.cadavl.com/GTA/GTFS/GTFS_GTA.zip'


def _read(zf: zipfile.ZipFile, name: str) -> list[dict]:
    with zf.open(name) as f:
        return list(csv.DictReader(io.TextIOWrapper(f, 'utf-8-sig')))


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs):
    import requests
    resp = requests.get(GTFS_URL, timeout=120)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))

    routes = {r['route_id']: r for r in _read(zf, 'routes.txt')}

    def route_doc(route_id: str) -> dict:
        r = routes.get(route_id, {})
        color = (r.get('route_color') or '7B1FA2').strip() or '7B1FA2'
        return {
            'route_id': route_id,
            'short_name': r.get('route_short_name') or '',
            'long_name': r.get('route_long_name') or '',
            'color': f'#{color}',
        }

    if pipe.metric_key == 'routes':
        return [route_doc(rid) for rid in routes]

    trips = _read(zf, 'trips.txt')

    if pipe.metric_key == 'route_shapes':
        shape_points: dict[str, list] = {}
        for p in _read(zf, 'shapes.txt'):
            shape_points.setdefault(p['shape_id'], []).append(
                (int(p['shape_pt_sequence']),
                 float(p['shape_pt_lon']), float(p['shape_pt_lat']))
            )
        shape_routes = {}
        for t in trips:
            if t.get('shape_id'):
                shape_routes.setdefault(t['shape_id'], t['route_id'])
        docs = []
        for shape_id, route_id in shape_routes.items():
            pts = sorted(shape_points.get(shape_id, []))
            if len(pts) < 2:
                continue
            wkt = 'LINESTRING(' + ', '.join(f'{lon} {lat}' for _, lon, lat in pts) + ')'
            docs.append({
                'shape_id': shape_id,
                **route_doc(route_id),
                'geometry': wkt,
            })
        return docs

    if pipe.metric_key == 'stops':
        trip_route = {t['trip_id']: t['route_id'] for t in trips}
        stop_routes: dict[str, set] = {}
        with zf.open('stop_times.txt') as f:
            for row in csv.DictReader(io.TextIOWrapper(f, 'utf-8-sig')):
                rid = trip_route.get(row['trip_id'])
                if rid:
                    short = routes.get(rid, {}).get('route_short_name') or rid
                    stop_routes.setdefault(row['stop_id'], set()).add(short)
        return [
            {
                'stop_id': s['stop_id'],
                'name': s.get('stop_name') or 'Bus stop',
                'lat': float(s['stop_lat']),
                'lon': float(s['stop_lon']),
                'routes': ', '.join(sorted(stop_routes.get(s['stop_id'], ()))),
                'geometry': f"POINT({s['stop_lon']} {s['stop_lat']})",
            }
            for s in _read(zf, 'stops.txt')
            if s.get('stop_lat') and s.get('stop_lon')
        ]

    raise ValueError(f"Unknown GTFS metric '{pipe.metric_key}'.")
