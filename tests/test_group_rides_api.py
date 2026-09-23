"""Group rides: create, discover nearby, join, ping, leader route, end."""
import hashlib
import importlib.util
import os
import sys
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, 'plugins', filename))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


gr = _load('bwg_group_rides', 'group-rides.py')
bwg_app = _load('bwg_app_plugin', 'bwg-app.py')


class UpsertPipe:
    """In-memory stand-in for an upsert pipe keyed on `id`."""

    def __init__(self):
        self.rows = {}

    def exists(self):
        return bool(self.rows)

    def get_data(self, params=None, **_):
        def match(row):
            return all(
                (row.get(k) is None) if v is None else row.get(k) == v
                for k, v in (params or {}).items()
            )
        return pd.DataFrame([r for r in self.rows.values() if match(r)])

    def sync(self, rows):
        for r in rows:
            self.rows[r['id']] = dict(self.rows.get(r['id'], {}), **r)
        return True, 'saved'

    def clear(self, params=None, **_):
        (key, values), = params.items()
        self.rows = {k: r for k, r in self.rows.items() if r.get(key) not in values}
        return True, 'cleared'


ROUTE = {'type': 'Feature', 'properties': {},
         'geometry': {'type': 'LineString', 'coordinates': [[-82.40, 34.85], [-82.39, 34.86]]}}


def test_group_ride_lifecycle():
    rides, members = UpsertPipe(), UpsertPipe()
    with patch.object(gr, 'RIDES_PIPE', rides), patch.object(gr, 'MEMBERS_PIPE', members):
        gr._HITS.clear()
        app = FastAPI(); gr.init_app(app); bwg_app.init_app(app); client = TestClient(app)

        assert client.post('/group-rides', json={'name': 'x', 'lat': 91, 'lon': 0}).status_code == 400
        resp = client.post('/group-rides', json={'name': 'Tuesday Spin', 'rider_name': 'Ann',
                                                 'lat': 34.85, 'lon': -82.40})
        assert resp.status_code == 200, resp.text
        ride = resp.json()
        code, leader_token = ride['code'], ride['member_token']
        assert len(code) == 4 and all(c in gr.CODE_ALPHABET for c in code)
        assert not set(code) & set('IOQ')
        assert ride['share_url'] == f'https://bwg.mrsm.io/bwg-app/?ride={code}'
        # Only the hash is stored.
        stored = members.rows[ride['member_id']]['token_hash']
        assert stored == hashlib.sha256(leader_token.encode()).hexdigest()
        assert leader_token not in str(members.rows) + str(rides.rows)

        # ~200 m north is nearby; ~1 km is not.
        near = client.get('/group-rides/nearby', params={'lat': 34.8518, 'lon': -82.40}).json()
        assert [r['code'] for r in near] == [code]
        assert near[0]['leader_name'] == 'Ann' and near[0]['members'] == 1
        assert near[0]['distance_m'] == 200  # ~200 m, rounded to 50 m
        assert client.get('/group-rides/nearby', params={'lat': 34.859, 'lon': -82.40}).json() == []

        # Blank name -> "Rider 2".
        resp = client.post(f'/group-rides/{code.lower()}/join', json={'rider_name': '  ', 'lat': 34.851, 'lon': -82.40})
        assert resp.status_code == 200, resp.text
        joined = resp.json()
        assert joined['name'] == 'Rider 2' and joined['ride_id'] == ride['ride_id']
        member_token = joined['member_token']

        resp = client.post(f'/group-rides/{code}/ping', json={'member_token': member_token,
                                                               'lat': 34.852, 'lon': -82.40, 'heading': 90})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['ended'] is False and body['leader_id'] == ride['member_id']
        assert {m['name'] for m in body['members']} == {'Ann', 'Rider 2'}
        assert client.post(f'/group-rides/{code}/ping', json={'member_token': 'nope', 'lat': 34.85, 'lon': -82.4}).status_code == 401

        # Leader route is visible to members; members cannot set it.
        assert client.put(f'/group-rides/{code}/route', json={'member_token': leader_token, 'route': {'type': 'Feature', 'geometry': {'type': 'Point', 'coordinates': [0, 0]}}}).status_code == 400
        assert client.put(f'/group-rides/{code}/route', json={'member_token': leader_token, 'route': ROUTE}).status_code == 200
        assert client.put(f'/group-rides/{code}/route', json={'member_token': member_token, 'route': ROUTE}).status_code == 403
        body = client.post(f'/group-rides/{code}/ping', json={'member_token': member_token, 'lat': 34.852, 'lon': -82.40}).json()
        assert body['route'] == ROUTE and body['route_rev'] == 1
        # A caller already holding route_rev 1 gets no route payload.
        ping = {'member_token': member_token, 'lat': 34.852, 'lon': -82.40, 'route_rev': 1}
        body = client.post(f'/group-rides/{code}/ping', json=ping).json()
        assert 'route' not in body and body['route_rev'] == 1
        # null clears the route and bumps route_rev.
        resp = client.put(f'/group-rides/{code}/route', json={'member_token': leader_token, 'route': None})
        assert resp.status_code == 200 and resp.json()['route_rev'] == 2
        body = client.post(f'/group-rides/{code}/ping', json=ping).json()
        assert body['route'] is None and body['route_rev'] == 2

        assert client.post(f'/group-rides/{code}/end', json={'member_token': member_token}).status_code == 403
        assert client.post(f'/group-rides/{code}/end', json={'member_token': leader_token}).status_code == 200
        assert client.post(f'/group-rides/{code}/join', json={'rider_name': 'Bo', 'lat': 34.85, 'lon': -82.4}).status_code == 404
        assert client.post(f'/group-rides/{code}/ping', json={'member_token': member_token, 'lat': 34.85, 'lon': -82.4}).json()['ended'] is True
        assert client.get('/group-rides/nearby', params={'lat': 34.85, 'lon': -82.40}).json() == []

        resp = client.get(f'/r/{code}', follow_redirects=False)
        assert resp.status_code == 302 and resp.headers['location'] == ride['share_url']


def test_group_ride_limits_and_purge():
    rides, members = UpsertPipe(), UpsertPipe()
    old_end = pd.Timestamp.now(tz='UTC') - pd.Timedelta(days=8)
    rides.rows['old'] = {'id': 'old', 'code': 'ABCD', 'ended': old_end}
    members.rows['m-old'] = {'id': 'm-old', 'ride_id': 'old', 'token_hash': None}
    here = {'lat': 34.85, 'lon': -82.40}
    with patch.object(gr, 'RIDES_PIPE', rides), patch.object(gr, 'MEMBERS_PIPE', members), \
         patch.object(gr, 'MEMBERS_MAX', 3), patch.dict(gr._PURGED, at=0.0):
        gr._HITS.clear()
        app = FastAPI(); gr.init_app(app); client = TestClient(app)
        assert client.post('/group-rides', json={'name': 'x' * 20000, **here}).status_code == 413
        code = client.post('/group-rides', json={'name': 'Spin', **here}).json()['code']
        # Rides that ended over a week ago are gone, members included.
        assert 'old' not in rides.rows and 'm-old' not in members.rows
        for _ in range(2):
            assert client.post(f'/group-rides/{code}/join', json=here).status_code == 200
        assert client.post(f'/group-rides/{code}/join', json=here).status_code == 409  # full
        for _ in range(gr.JOIN_MAX_PER_HOUR - 3):
            client.post(f'/group-rides/{code}/join', json=here)
        assert client.post(f'/group-rides/{code}/join', json=here).status_code == 429
        for _ in range(gr.NEARBY_MAX_PER_HOUR):
            assert client.get('/group-rides/nearby', params=here).status_code == 200
        assert client.get('/group-rides/nearby', params=here).status_code == 429
