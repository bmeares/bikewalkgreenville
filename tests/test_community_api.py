"""Exercise public edit history, concurrent revisions, rollback, and write failures."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_route_graph import ml


class MemoryPipe:
    def __init__(self):
        self.rows = []
        self.fail = False
        self.instance_connector = self

    def exists(self):
        return bool(self.rows)

    def read(self, _sql):
        return pd.DataFrame(self.rows)

    def sync(self, rows):
        if self.fail:
            return False, 'unavailable'
        self.rows.extend(dict(r, ts='2026-09-05T00:00:00Z') for r in rows)
        return True, 'saved'


def test_publish_edit_rollback_and_conflict():
    pipe = MemoryPipe()
    with patch.object(ml, 'COMMUNITY_PIPE', pipe), patch.object(ml, '_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0)), patch.object(ml, '_get_route_graph', lambda: {'nodes': {}, 'adj': {}}), patch.object(ml, '_get_transit_data', lambda: {}):
        ml._COMMUNITY_CACHE.update(at=0, rows=[])
        ml._SUBMIT_HITS.clear()
        app = FastAPI()
        ml.init_app(app)
        client = TestClient(app)
        data = {'category': 'shortcut', 'name': 'Local path', 'comment': 'Public paved path',
                'lat': '34.85', 'lon': '-82.4',
                'geometry': json.dumps({'type': 'LineString', 'coordinates': [[-82.4,34.85],[-82.399,34.85]]})}
        response = client.post('/map-layers/submit-point', data=data)
        assert response.status_code == 200, response.text
        first = response.json()['id']
        assert response.json()['status'] == 'published'
        assert len(client.get('/map-layers/community.geojson').json()['features']) == 1
        response = client.post('/map-layers/submit-point', data=dict(data, replaces=first, name='Corrected path'))
        assert response.status_code == 200, response.text
        second = response.json()['id']
        assert client.get('/map-layers/community.geojson').json()['features'][0]['properties']['name'] == 'Corrected path'
        assert client.post('/map-layers/submit-point', data=dict(data, replaces=first)).status_code == 409
        response = client.post('/map-layers/community/rollback', json={'id': second, 'reason': 'Original name was correct'})
        assert response.status_code == 200, response.text
        assert client.get('/map-layers/community.geojson').json()['features'][0]['properties']['name'] == 'Local path'
        history = client.get('/map-layers/community/history').json()['revisions']
        assert len(history) == 3
        assert not any('ip' in r or 'user_agent' in r for r in history)
        assert client.post('/map-layers/community/rollback', json={'id': second, 'reason': 'Repeated'}).status_code == 409
        pipe.fail = True
        assert client.post('/map-layers/submit-point', data=data).status_code == 503


def test_polygon_validation_publication_edit_and_rollback():
    pipe = MemoryPipe()
    with patch.object(ml, 'COMMUNITY_PIPE', pipe), patch.object(ml, '_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0)), patch.object(ml, '_get_route_graph', lambda: {'nodes': {}, 'adj': {}}), patch.object(ml, '_get_transit_data', lambda: {}):
        ml._COMMUNITY_CACHE.update(at=0, rows=[])
        ml._SUBMIT_HITS.clear()
        app = FastAPI(); ml.init_app(app); client = TestClient(app)
        ring = [[-82.4,34.85],[-82.399,34.85],[-82.399,34.851],[-82.4,34.851],[-82.4,34.85]]
        geometry = {'type':'Polygon','coordinates':[ring]}
        data = {'category':'no-entry','name':'Closed area','comment':'Temporary closure',
                'lat':34.85,'lon':-82.4,'geometry':json.dumps(geometry)}
        response = client.post('/map-layers/submit-point',data=data)
        assert response.status_code == 200, response.text
        first = response.json()['id']
        assert client.get('/map-layers/community.geojson').json()['features'][0]['geometry'] == geometry
        graph = {'exclusions':ml._exclusion_index(ml._active_community())}
        assert ml._excluded_coords(graph,[[-82.3995,34.8505]])
        response = client.post('/map-layers/submit-point',data=dict(data,replaces=first,name='Updated closure'))
        assert response.status_code == 200
        second = response.json()['id']
        assert client.post('/map-layers/community/rollback',json={'id':second,'reason':'Restore original'}).status_code == 200
        assert ml._active_community()[0]['id'] == first
        assert client.post('/map-layers/community/rollback',json={'id':first,'reason':'Reopened'}).status_code == 200
        assert ml._exclusion_index(ml._active_community()) is None
        assert client.post('/map-layers/submit-point',data=dict(data,category='shortcut')).status_code == 400
        assert client.post('/map-layers/submit-point',data=dict(data,geometry='')).status_code == 400
        crossing = {'type':'Polygon','coordinates':[[ring[0],ring[2],ring[1],ring[3],ring[0]]]}
        assert client.post('/map-layers/submit-point',data=dict(data,geometry=json.dumps(crossing))).status_code == 400
        opened = {'type':'Polygon','coordinates':[ring[:-1]]}
        assert client.post('/map-layers/submit-point',data=dict(data,geometry=json.dumps(opened))).status_code == 400


def test_history_serializes_all_null_columns():
    """Postgres reads an all-null column back as float NaN, which is not JSON."""
    class NaNPipe(MemoryPipe):
        def read(self, sql):
            df = super().read(sql)
            # An all-null text column comes back float64/NaN, not None.
            df['reverts'] = df['replaces'] = float('nan')
            return df

    pipe = NaNPipe()
    pipe.rows = [{
        'id': 'a' * 32, 'ts': pd.Timestamp('2026-09-05T00:00:00Z'), 'category': 'shortcut',
        'name': 'Springer St tunnel', 'comment': 'Cross Church St', 'reverts': None,
        'replaces': None, 'lat': 34.8377, 'lon': -82.4034,
        'geometry_json': json.dumps({'type': 'LineString', 'coordinates': [[-82.4034, 34.8377], [-82.4028, 34.8377]]}),
    }]
    with patch.object(ml, 'COMMUNITY_PIPE', pipe):
        ml._COMMUNITY_CACHE.update(at=0, rows=[])
        app = FastAPI(); ml.init_app(app); client = TestClient(app)
        response = client.get('/map-layers/community/history')
        assert response.status_code == 200, response.text
        assert response.json()['revisions'][0]['replaces'] is None


def test_confirmations_count_dedupe_and_hide_from_layer():
    pipe = MemoryPipe()
    with patch.object(ml, 'COMMUNITY_PIPE', pipe), patch.object(ml, '_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0)), patch.object(ml, '_get_route_graph', lambda: {'nodes': {}, 'adj': {}}), patch.object(ml, '_get_transit_data', lambda: {}):
        ml._COMMUNITY_CACHE.update(at=0, rows=[])
        ml._SUBMIT_HITS.clear()
        app = FastAPI(); ml.init_app(app); client = TestClient(app)
        data = {'category': 'shortcut', 'name': 'Local path', 'comment': 'Public paved path',
                'lat': '34.85', 'lon': '-82.4',
                'geometry': json.dumps({'type': 'LineString', 'coordinates': [[-82.4,34.85],[-82.399,34.85]]})}
        target = client.post('/map-layers/submit-point', data=data).json()['id']
        assert client.post('/map-layers/community/confirm', json={'id': target, 'voter': 'abc'}).status_code == 400
        r = client.post('/map-layers/community/confirm', json={'id': target, 'voter': 'voter0001'})
        assert r.status_code == 200, r.text
        assert r.json()['confirmations'] == 1
        assert client.post('/map-layers/community/confirm', json={'id': target, 'voter': 'voter0001'}).status_code == 409
        assert client.post('/map-layers/community/confirm', json={'id': target, 'voter': 'voter0002'}).json()['confirmations'] == 2
        features = client.get('/map-layers/community.geojson').json()['features']
        assert len(features) == 1 and features[0]['properties']['confirmations'] == 2
        history = client.get('/map-layers/community/history').json()['revisions']
        assert sum(r['type'] == 'confirm' for r in history) == 2
        assert not any('voter' in r for r in history)
        assert client.post('/map-layers/community/confirm', json={'id': 'nope', 'voter': 'voter0003'}).status_code == 409
