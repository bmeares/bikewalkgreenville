"""Exercise public edit history, concurrent revisions, rollback, and write failures."""
import json
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

import pytest

from test_route_graph import ml
from bwg_fakes import JPEG, FakeUsers, bearer, install


@pytest.fixture(autouse=True)
def signed_in(monkeypatch):
    """Every request signs in as 'alice@example.com' unless it sends its own
    header; 'admin@example.com' is a Meerschaum admin."""
    users = FakeUsers(admins=['admin@example.com'], banned=['banned@example.com'])
    monkeypatch.setattr(ml, '_BWG_AUTH', install(users))
    return users


ALICE = bearer('alice@example.com')
BOB = bearer('bob@example.com')
ADMIN = bearer('admin@example.com')


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
        client = TestClient(app, headers=ALICE)
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
        response = client.post('/map-layers/community/rollback', json={'id': second, 'reason': 'Not a real path'})
        assert response.status_code == 200, response.text
        # Removing an edited contribution removes its whole chain — the earlier
        # version must NOT resurface on the map.
        assert client.get('/map-layers/community.geojson').json()['features'] == []
        history = client.get('/map-layers/community/history').json()['revisions']
        assert len(history) == 4
        assert sum(r['type'] == 'rollback' for r in history) == 2
        assert not any('ip' in r or 'user_agent' in r for r in history)
        assert client.post('/map-layers/community/rollback', json={'id': second, 'reason': 'Repeated'}).status_code == 409
        pipe.fail = True
        assert client.post('/map-layers/submit-point', data=data).status_code == 503


def test_polygon_validation_publication_edit_and_rollback():
    pipe = MemoryPipe()
    with patch.object(ml, 'COMMUNITY_PIPE', pipe), patch.object(ml, '_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0)), patch.object(ml, '_get_route_graph', lambda: {'nodes': {}, 'adj': {}}), patch.object(ml, '_get_transit_data', lambda: {}):
        ml._COMMUNITY_CACHE.update(at=0, rows=[])
        ml._SUBMIT_HITS.clear()
        app = FastAPI(); ml.init_app(app); client = TestClient(app, headers=ALICE)
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
        assert client.post('/map-layers/community/rollback',json={'id':second,'reason':'Reopened'}).status_code == 200
        # The whole chain goes: the original closure does not come back.
        assert ml._active_community() == []
        assert ml._exclusion_index(ml._active_community()) is None
        assert client.post('/map-layers/community/rollback',json={'id':first,'reason':'Again'}).status_code == 409
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
        app = FastAPI(); ml.init_app(app); client = TestClient(app, headers=ALICE)
        response = client.get('/map-layers/community/history')
        assert response.status_code == 200, response.text
        assert response.json()['revisions'][0]['replaces'] is None


def _community_client(pipe, stack):
    """Enter the usual community patches on `stack`; return a client as alice."""
    for target, value in (('COMMUNITY_PIPE', pipe),
                          ('_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0)),
                          ('_get_route_graph', lambda: {'nodes': {}, 'adj': {}}),
                          ('_get_transit_data', lambda: {})):
        stack.enter_context(patch.object(ml, target, value))
    ml._COMMUNITY_CACHE.update(at=0, rows=[])
    ml._SUBMIT_HITS.clear()
    app = FastAPI(); ml.init_app(app)
    return TestClient(app, headers=ALICE)


PATH = {'category': 'shortcut', 'name': 'Local path', 'comment': 'Public paved path',
        'lat': '34.85', 'lon': '-82.4',
        'geometry': json.dumps({'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.399, 34.85]]})}


def test_vote_toggle_flip_and_confirm_alias():
    from contextlib import ExitStack
    pipe = MemoryPipe()
    with ExitStack() as stack:
        client = _community_client(pipe, stack)
        target = client.post('/map-layers/submit-point', data=PATH).json()['id']
        assert client.post('/map-layers/community/vote', json={'id': target, 'up': True}, headers={'Authorization': ''}).status_code == 401
        assert client.post('/map-layers/community/vote', json={'id': target}).status_code == 400
        r = client.post('/map-layers/community/vote', json={'id': target, 'up': True})
        assert r.status_code == 200, r.text
        assert (r.json()['up'], r.json()['down'], r.json()['mine']) == (1, 0, 'up')
        # Flip, then toggle off.
        r = client.post('/map-layers/community/vote', json={'id': target, 'up': False}).json()
        assert (r['up'], r['down'], r['mine']) == (0, 1, 'down')
        r = client.post('/map-layers/community/vote', json={'id': target, 'up': False}).json()
        assert (r['up'], r['down'], r['mine']) == (0, 0, None)
        # Legacy alias = up, idempotent.
        assert client.post('/map-layers/community/confirm', json={'id': target}).json()['confirmations'] == 1
        assert client.post('/map-layers/community/confirm', json={'id': target}).status_code == 409
        assert client.post('/map-layers/community/vote', json={'id': target, 'up': True}, headers=BOB).json()['up'] == 2
        props = client.get('/map-layers/community.geojson').json()['features'][0]['properties']
        assert (props['upvotes'], props['downvotes']) == (2, 0) and 'confirmations' not in props
        history = client.get('/map-layers/community/history').json()['revisions']
        assert [h['type'] for h in history] == ['add'] and history[0]['mine'] is True
        assert not any('voter' in h or 'username' in h for h in history)
        assert client.post('/map-layers/community/vote', json={'id': 'nope', 'up': True}).status_code == 409
        # Votes have their own per-user budget: spending it leaves submissions alone.
        for _ in range(ml.VOTE_MAX_PER_HOUR):
            client.post('/map-layers/community/vote', json={'id': target, 'up': True})
        assert client.post('/map-layers/community/vote', json={'id': target, 'up': True}).status_code == 429
        assert client.post('/map-layers/community/vote', json={'id': target, 'up': True}, headers=BOB).status_code == 200
        assert client.post('/map-layers/submit-point', data=dict(PATH, lat='34.852')).status_code == 200


def test_writes_require_sign_in_and_banned_users_are_refused():
    from contextlib import ExitStack
    with ExitStack() as stack:
        client = _community_client(MemoryPipe(), stack)
        assert client.post('/map-layers/submit-point', data=PATH, headers={'Authorization': ''}).status_code == 401
        assert client.post('/map-layers/submit-point', data=PATH, headers=bearer('banned@example.com')).status_code == 403
        assert client.post('/map-layers/feedback', data={'feedback': 'x'}, headers={'Authorization': ''}).status_code == 401
        assert client.post('/map-layers/community/rollback', json={'id': 'x', 'reason': 'y'}, headers={'Authorization': ''}).status_code == 401


def test_photo_hidden_until_approved():
    from contextlib import ExitStack
    import tempfile
    from pathlib import Path
    pipe = MemoryPipe()
    with ExitStack() as stack, tempfile.TemporaryDirectory() as tmp:
        stack.enter_context(patch.object(ml, '_photos_dir', lambda: Path(tmp)))
        client = _community_client(pipe, stack)
        data = {'category': 'bike-parking', 'name': 'Rack', 'comment': 'x', 'lat': 34.85, 'lon': -82.4}
        assert client.post('/map-layers/submit-point', data=data,
                           files={'photo': ('p.html', b'<script>', 'text/html')}).status_code == 400
        # The name says JPEG, the bytes don't: refused.
        assert client.post('/map-layers/submit-point', data=data,
                           files={'photo': ('p.jpg', b'<svg/>', 'image/jpeg')}).status_code == 400
        r = client.post('/map-layers/submit-point', data=data, files={'photo': ('p.jpg', JPEG, 'image/jpeg')})
        assert r.status_code == 200 and r.json()['photo_status'] == 'pending'
        rid = r.json()['id']
        filename = f'{rid}.jpg'
        assert client.get('/map-layers/community.geojson').json()['features'][0]['properties']['photo_url'] is None
        assert client.get('/map-layers/community/history').json()['revisions'][0]['photo_url'] is None
        assert client.get(f'/map-layers/photos/{filename}').status_code == 404
        assert client.post('/map-layers/community/moderate', json={'id': rid, 'photo_status': 'approved'}).status_code == 403
        r = client.post('/map-layers/community/moderate', json={'id': rid, 'photo_status': 'approved'}, headers=ADMIN)
        assert r.status_code == 200, r.text
        props = client.get('/map-layers/community.geojson').json()['features'][0]['properties']
        assert props['photo_url'] == f'/map-layers/photos/{filename}'
        photo = client.get(props['photo_url'])
        assert photo.status_code == 200 and photo.content == JPEG
        assert photo.headers['x-content-type-options'] == 'nosniff'
        # Legacy rows with a photo but no status are pending (hidden).
        pipe.rows.append(dict(pipe.rows[0], id='legacy', photo_filename='legacy.jpg', photo_status=None))
        (Path(tmp) / 'legacy.jpg').write_bytes(b'x')
        ml._COMMUNITY_CACHE.update(at=0)
        assert client.get('/map-layers/photos/legacy.jpg').status_code == 404


def test_held_text_excluded_until_approved():
    from contextlib import ExitStack
    with ExitStack() as stack:
        client = _community_client(MemoryPipe(), stack)
        r = client.post('/map-layers/submit-point', data=dict(PATH, comment='BUY NOW http://a.com http://b.com'))
        assert r.status_code == 200 and r.json()['status'] == 'held'
        held = r.json()['id']
        assert client.get('/map-layers/community.geojson').json()['features'] == []
        assert ml._active_community() == []
        assert client.get('/map-layers/community/history').json()['revisions'] == []
        assert client.post('/map-layers/community/moderate', json={'id': held, 'status': 'published'}, headers=ADMIN).status_code == 200
        assert [f['properties']['id'] for f in client.get('/map-layers/community.geojson').json()['features']] == [held]


def test_rollback_own_only_unless_admin():
    from contextlib import ExitStack
    with ExitStack() as stack:
        client = _community_client(MemoryPipe(), stack)
        mine = client.post('/map-layers/submit-point', data=PATH).json()['id']
        theirs = client.post('/map-layers/submit-point', data=dict(PATH, lat='34.851'), headers=BOB).json()['id']
        assert client.post('/map-layers/community/rollback', json={'id': theirs, 'reason': 'no'}).status_code == 403
        assert client.post('/map-layers/community/rollback', json={'ids': [mine, theirs], 'reason': 'no'}).status_code == 403
        assert client.post('/map-layers/community/rollback', json={'id': mine, 'reason': 'oops'}).status_code == 200
        # Undoing my edit of bob's path brings bob's version back.
        edit = client.post('/map-layers/submit-point', data=dict(PATH, replaces=theirs, name='Mine now')).json()['id']
        assert client.post('/map-layers/community/rollback', json={'id': edit, 'reason': 'undo'}).status_code == 200
        assert [f['properties']['id'] for f in client.get('/map-layers/community.geojson').json()['features']] == [theirs]
        assert client.post('/map-layers/community/rollback', json={'id': theirs, 'reason': 'spam'}, headers=ADMIN).status_code == 200
        assert client.get('/map-layers/community.geojson').json()['features'] == []


def test_saved_routes_crud_and_per_user_isolation():
    from bwg_fakes import MemoryPipe as RoutesPipe
    routes = RoutesPipe()
    with patch.object(ml, 'SAVED_ROUTES_PIPE', routes), patch.object(ml, 'SAVED_ROUTES_MAX', 2):
        app = FastAPI(); ml.init_app(app); client = TestClient(app, headers=ALICE)
        body = {'name': 'Home to work', 'from_lat': 34.85, 'from_lon': -82.4, 'to_lat': 34.84, 'to_lon': -82.39,
                'modes': 'bike,transit', 'stress': 'quiet', 'distance_m': 3200, 'duration_min': 14,
                'geometry': {'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.39, 34.84]]}}
        assert client.get('/bwg/routes', headers={'Authorization': ''}).status_code == 401
        assert client.post('/bwg/routes', json=dict(body, from_lat=40.0)).status_code == 400
        r = client.post('/bwg/routes', json=body)
        assert r.status_code == 200, r.text
        rid = r.json()['id']
        assert r.json()['geometry']['type'] == 'LineString' and r.json()['modes'] == 'bike,transit'
        assert [x['id'] for x in client.get('/bwg/routes').json()['routes']] == [rid]
        assert client.get('/bwg/routes', headers=BOB).json()['routes'] == []
        assert client.delete(f'/bwg/routes/{rid}', headers=BOB).status_code == 404
        client.post('/bwg/routes', json=body)
        assert client.post('/bwg/routes', json=body).status_code == 409
        assert client.delete(f'/bwg/routes/{rid}').status_code == 200
        assert len(client.get('/bwg/routes').json()['routes']) == 1


def test_transit_reach_from_config_and_error_states_distance():
    stops = [{'lat': 34.85, 'lon': -82.40, 'routes': {'1'}}]
    config = {}

    def fake_config(*keys, **_):
        return config.get(keys[-1])

    with patch.object(ml, '_get_transit_data', lambda: {'stops': stops, 'shapes': [{'route': '1'}]}), \
         patch.object(ml.mrsm, 'get_config', fake_config):
        assert ml._transit_reach_m('walk') == 2000.0
        assert ml._transit_reach_m('bike:quiet') == ml._transit_reach_m('ebike:direct') == 5000.0
        config['walk_max_m'] = 800
        assert ml._transit_reach_m('walk') == 800.0
        # 3 km from the only stop: out of walking reach, inside biking reach.
        with pytest.raises(ValueError, match=r'No bus stops within 0.5 mi \(0.8 km\) walking distance of your start'):
            ml._route_transit(34.877, -82.40, 34.85, -82.40, access_mode='walk')
        config['bike_max_m'] = 1000
        with pytest.raises(ValueError, match=r'1 km\) biking distance of your start'):
            ml._route_transit(34.877, -82.40, 34.85, -82.40, access_mode='bike:balanced')


def test_batch_rollback_removes_many_in_one_request():
    pipe = MemoryPipe()
    with patch.object(ml, 'COMMUNITY_PIPE', pipe), patch.object(ml, '_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0)), patch.object(ml, '_get_route_graph', lambda: {'nodes': {}, 'adj': {}}), patch.object(ml, '_get_transit_data', lambda: {}):
        ml._COMMUNITY_CACHE.update(at=0, rows=[])
        ml._SUBMIT_HITS.clear()
        app = FastAPI(); ml.init_app(app); client = TestClient(app, headers=ALICE)
        ids = []
        for i in range(3):
            data = {'category': 'bike-parking', 'name': f'Rack {i}', 'comment': 'x', 'lat': 34.85 + i * 0.001, 'lon': -82.4}
            ids.append(client.post('/map-layers/submit-point', data=data).json()['id'])
        assert len(client.get('/map-layers/community.geojson').json()['features']) == 3
        r = client.post('/map-layers/community/rollback', json={'ids': ids[:2] + ['nope'], 'reason': 'Duplicates'})
        assert r.status_code == 200, r.text
        assert r.json()['removed'] == 2 and r.json()['skipped'] == ['nope']
        left = client.get('/map-layers/community.geojson').json()['features']
        assert [f['properties']['id'] for f in left] == [ids[2]]
        assert client.post('/map-layers/community/rollback', json={'ids': ids[:2], 'reason': 'Again'}).status_code == 409
        assert client.post('/map-layers/community/rollback', json={'ids': [], 'reason': 'Nothing'}).status_code == 400


def test_layer_feedback_rate_limit_and_photo_sniff(tmp_path):
    from contextlib import ExitStack
    from bwg_fakes import MemoryPipe as FeedbackPipe
    feedback = FeedbackPipe()
    with ExitStack() as stack:
        stack.enter_context(patch.object(ml, 'FEEDBACK_PIPE', feedback))
        stack.enter_context(patch.object(ml, '_photos_dir', lambda: tmp_path))
        client = _community_client(MemoryPipe(), stack)
        assert client.post('/map-layers/feedback', data={'feedback': 'x'},
                           files={'photo': ('a.jpg', b'MZ\x90\x00', 'image/jpeg')}).status_code == 400
        r = client.post('/map-layers/feedback', data={'feedback': 'x'}, files={'photo': ('a.heic', JPEG, 'image/heic')})
        assert r.status_code == 200 and (tmp_path / f"{r.json()['id']}.jpg").is_file()
        for _ in range(ml.FEEDBACK_MAX_PER_HOUR - 2):
            assert client.post('/map-layers/feedback', data={'feedback': 'x'}).status_code == 200
        assert client.post('/map-layers/feedback', data={'feedback': 'x'}).status_code == 429
        assert {r['username'] for r in feedback.rows} == {'alice@example.com'}
