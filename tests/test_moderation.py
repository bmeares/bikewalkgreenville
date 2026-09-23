"""Moderation console backend (`plugins/moderation.py`): read model, filters,
exports, admin removal and the admin-only routes. No Postgres: the community
pipe is an in-memory stub and users come from `bwg_fakes`."""
import csv
import importlib.util
import io
import json
import os
import xml.etree.ElementTree as ET

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_route_graph import ml
from bwg_fakes import FakeUsers, MemoryPipe, bearer, install
from test_bike_parking_api import bp
from test_walk_audit_api import MemoryPipe as KeyedPipe, wa

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugins', 'moderation.py')
spec = importlib.util.spec_from_file_location('bwg_moderation', PLUGIN)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

LINE = {'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.399, 34.85]]}
RING = [[-82.4, 34.85], [-82.399, 34.85], [-82.399, 34.851], [-82.4, 34.851], [-82.4, 34.85]]


def _row(id_, category, geometry, ts, **extra):
    lon, lat = (geometry['coordinates'] if geometry['type'] == 'Point' else
                geometry['coordinates'][0] if geometry['type'] == 'LineString' else geometry['coordinates'][0][0])
    return {'id': id_, 'ts': ts, 'category': category, 'name': extra.pop('name', id_), 'comment': extra.pop('comment', None),
            'geometry_json': json.dumps(geometry), 'reverts': None, 'replaces': None, 'lat': lat, 'lon': lon,
            'username': 'alice@example.com', **extra}


ROWS = [
    _row('path1', 'shortcut', LINE, '2026-09-01T12:00:00Z', name='Cut <through>', comment='Paved & quiet'),
    _row('park1', 'bike-parking', {'type': 'Point', 'coordinates': [-82.39, 34.84]}, '2026-09-02T12:00:00Z',
         comment='Two racks', photo_filename='abc.jpg', photo_status='pending'),
    _row('gate1', 'no-entry', {'type': 'Polygon', 'coordinates': [RING]}, '2026-09-03T12:00:00Z',
         username='bob@example.com'),
    _row('spam1', 'other', {'type': 'Point', 'coordinates': [-82.38, 34.83]}, '2026-09-04T12:00:00Z',
         status='held', comment='BUY NOW'),
    {'id': 'v1', 'ts': '2026-09-05T00:00:00Z', 'category': 'vote', 'confirms': 'path1', 'vote': 'up',
     'username': 'bob@example.com', 'geometry_json': None, 'reverts': None, 'replaces': None, 'lat': 0, 'lon': 0},
]


@pytest.fixture(autouse=True)
def community(monkeypatch):
    users = FakeUsers(admins=['admin@example.com'])
    monkeypatch.setattr(ml, '_BWG_AUTH', install(users))
    monkeypatch.setattr(mod, '_ML', ml)
    # The real key comes from meerschaum.api._oauth2.SECRET (importing the API).
    monkeypatch.setattr(mod, '_PHOTO_KEY', b'test-key')
    pipe = MemoryPipe([dict(r) for r in ROWS])
    monkeypatch.setattr(ml, 'COMMUNITY_PIPE', pipe)
    monkeypatch.setattr(ml, '_community_changed', lambda: ml._COMMUNITY_CACHE.update(at=0))
    ml._COMMUNITY_CACHE.update(at=0, rows=[])
    # Walk-audit / bike-parking photo sources: empty unless a test adds rows.
    for name, module, attr in (('walk-audit', wa, 'REPORTS_PIPE'), ('bike-parking', bp, 'FEEDBACK_PIPE')):
        monkeypatch.setattr(module, attr, KeyedPipe())
        monkeypatch.setitem(mod._SOURCES, name, module)
    return pipe, users


def test_read_model_filters_and_counts():
    items = mod.contributions()
    assert [c['id'] for c in items] == ['spam1', 'gate1', 'park1', 'path1']
    by_id = {c['id']: c for c in items}
    assert by_id['spam1']['status'] == 'held' and by_id['path1']['up'] == 1
    assert [c['id'] for c in mod.filter_contributions(items)] == ['gate1', 'park1', 'path1']
    assert [c['id'] for c in mod.filter_contributions(items, geometry='LineString')] == ['path1']
    assert [c['id'] for c in mod.filter_contributions(items, category='no-entry', status='all')] == ['gate1']
    assert [c['id'] for c in mod.filter_contributions(items, status='held')] == ['spam1']
    assert [c['id'] for c in mod.filter_contributions(items, start='2026-09-02', end='2026-09-03')] == ['gate1', 'park1']
    assert [c['id'] for c in mod.filter_contributions(items, q='RACKS')] == ['park1']
    assert mod.pending_counts(items) == {'photos': 1, 'held': 1}
    assert mod.mask_email('bennett@swamprabbitanalytics.com') == 'b…t@swamprabbitanalytics.com'
    users = {u['username']: u for u in mod.submitters(items)}
    assert users['alice@example.com']['total'] == 3 and users['alice@example.com']['held'] == 1
    with pytest.raises(ValueError):
        mod.validate_filters({'status': 'bogus'})


def test_exports_are_valid():
    items = mod.filter_contributions(mod.contributions(), status='all')
    osm = ET.fromstring(mod.to_osm(items))
    assert osm.get('version') == '0.6'
    ids = [int(el.get('id')) for el in osm if el.tag in ('node', 'way')]
    assert ids and all(i < 0 for i in ids) and len(set(ids)) == len(ids)
    node_ids = {el.get('id') for el in osm.iter('node')}
    tags = lambda el: {t.get('k'): t.get('v') for t in el.iter('tag')}  # noqa: E731
    ways = {tags(w)['bwg:id']: w for w in osm.iter('way')}
    path = tags(ways['path1'])
    assert path['highway'] == 'path' and path['bicycle'] == 'yes' and path['name'] == 'Cut <through>'
    assert path['note'] == 'Paved & quiet' and path['source'] == mod.SOURCE_TAG and path['bwg:category'] == 'shortcut'
    gate = ways['gate1']
    refs = [nd.get('ref') for nd in gate.iter('nd')]
    assert refs[0] == refs[-1] and len(refs) == 5 and set(refs) <= node_ids
    assert tags(gate)['access'] == 'no' and tags(gate)['bwg:category'] == 'no-entry'
    park = next(n for n in osm.iter('node') if tags(n).get('bwg:id') == 'park1')
    assert tags(park)['amenity'] == 'bicycle_parking' and 'Two racks' in tags(park)['note']

    gpx = ET.fromstring(mod.to_gpx(items))
    ns = {'g': 'http://www.topografix.com/GPX/1/1'}
    assert len(gpx.findall('g:wpt', ns)) == 2 and len(gpx.findall('g:trk', ns)) == 2
    assert len(gpx.find('g:trk', ns).findall('g:trkseg/g:trkpt', ns)) in (2, 5)

    fc = json.loads(mod.to_geojson(items))
    assert len(fc['features']) == 4 and not any('username' in f['properties'] for f in fc['features'])

    rows = list(csv.DictReader(io.StringIO(mod.to_csv(items))))
    assert {r['id'] for r in rows} == {'path1', 'park1', 'gate1', 'spam1'}
    line = next(r for r in rows if r['id'] == 'path1')
    assert line['wkt'].startswith('LINESTRING') and float(line['length_m']) > 50


def test_remove_takes_whole_chain_and_moderation_applies(community):
    pipe, _ = community
    pipe.rows.append(_row('path2', 'shortcut', LINE, '2026-09-06T00:00:00Z', replaces='path1'))
    ml._COMMUNITY_CACHE.update(at=0)
    assert not mod.remove_contributions(['path2'], username='admin@example.com', reason='')[0]
    assert mod.remove_contributions(['path2'], username='admin@example.com', reason='Not real')[0]
    states = {c['id']: c['status'] for c in mod.contributions()}
    assert states['path1'] == states['path2'] == 'removed'
    assert ml.moderate_contributions(['spam1'], username='admin@example.com', status='published')[0]
    assert {c['id']: c['status'] for c in mod.contributions()}['spam1'] == 'published'


def test_ban_unban(community):
    _, users = community
    assert mod.set_banned('alice@example.com', True)[0] is False  # not registered in the fake yet
    users.users['alice@example.com'] = {'type': 'user', 'attributes': {'bwg': {}}}
    assert mod.set_banned('alice@example.com', True)[0] and mod.is_banned('alice@example.com')
    assert mod.set_banned('alice@example.com', False)[0] and not mod.is_banned('alice@example.com')
    assert not mod.set_banned('admin@example.com', True)[0]


def test_admin_routes(monkeypatch, tmp_path):
    monkeypatch.setattr(ml, '_photos_dir', lambda: tmp_path)
    app = FastAPI()
    mod.init_app(app)
    client = TestClient(app)
    assert client.get('/bwg/moderation/pending-count').status_code == 401
    assert client.get('/bwg/moderation/pending-count', headers=bearer('alice@example.com')).status_code == 403
    response = client.get('/bwg/moderation/pending-count', headers=bearer('admin@example.com'))
    assert response.status_code == 200 and response.json() == {'photos': 1, 'held': 1}
    admin = bearer('admin@example.com')
    assert client.get('/bwg/moderation/export.osm').status_code == 401
    response = client.get('/bwg/moderation/export.geojson?category=shortcut', headers=admin)
    assert response.status_code == 200 and 'attachment' in response.headers['content-disposition']
    assert [f['id'] for f in response.json()['features']] == ['path1']
    assert client.get('/bwg/moderation/export.osm?status=nope', headers=admin).status_code == 400
    assert client.get('/bwg/moderation/export.kml', headers=admin).status_code == 404
    assert ET.fromstring(client.get('/bwg/moderation/export.osm', headers=admin).text).tag == 'osm'
    # Pending photos: never without an admin credential or a valid signature.
    assert client.get('/bwg/moderation/photo/abc.jpg').status_code == 403
    assert client.get('/bwg/moderation/photo/abc.jpg?exp=9999999999&sig=bad').status_code == 403
    signed = mod.signed_photo_url('abc.jpg')
    assert client.get(signed).status_code == 404  # signature ok, file not on disk yet
    (tmp_path / 'abc.jpg').write_bytes(b'jpeg')
    assert client.get(signed).status_code == 200
    assert client.get('/bwg/moderation/photo/abc.jpg', headers=admin).status_code == 200
    assert client.get('/bwg/moderation/photo/abc.jpg', headers=bearer('alice@example.com')).status_code == 403


def test_photo_queue_covers_walk_audit_and_bike_parking(monkeypatch, tmp_path):
    for module in (wa, bp, ml):
        monkeypatch.setattr(module, '_photos_dir', lambda: tmp_path)
    wa.REPORTS_PIPE.rows = [{'ts': '2026-09-05 04:33:23+00:00', 'id': 'r1', 'category': 'lighting',
                             'comment': 'Dark', 'lat': 34.85, 'lon': -82.4, 'photo_filename': 'r1.jpg'}]
    bp.FEEDBACK_PIPE.rows = [{'ts': '2026-09-05 04:33:23+00:00', 'id': 'f1', 'spot_name': 'Rack',
                              'photo_filename': 'f1.png', 'photo_status': 'pending'}]
    (tmp_path / 'r1.jpg').write_bytes(b'jpeg')
    (tmp_path / 'f1.png').write_bytes(b'png')
    assert {p['id'] for p in mod.source_photos()} == {'walk-audit:r1', 'bike-parking:f1'}
    assert mod.pending_counts(mod.contributions()) == {'photos': 3, 'held': 1}

    app = FastAPI(); mod.init_app(app); wa.init_app(app)
    client = TestClient(app)
    admin = bearer('admin@example.com')
    assert client.get('/bwg/moderation/pending-count', headers=admin).json() == {'photos': 3, 'held': 1}
    # Admin thumbnails come from every source's upload dir; the public route waits for approval.
    assert client.get('/bwg/moderation/photo/r1.jpg', headers=admin).status_code == 200
    assert client.get(mod.signed_photo_url('f1.png')).status_code == 200
    assert client.get('/bwg/moderation/photo/nope.jpg', headers=admin).status_code == 404
    assert client.get('/walk-audit/photos/r1.jpg').status_code == 404

    assert not mod.decide_photo('bogus:r1', username='admin@example.com', photo_status='approved')[0]
    assert mod.decide_photo('walk-audit:r1', username='admin@example.com', photo_status='approved')[0]
    assert client.get('/walk-audit/photos/r1.jpg').status_code == 200
    assert mod.decide_photo('bike-parking:f1', username='admin@example.com', photo_status='rejected')[0]
    assert mod.decide_photo('park1', username='admin@example.com', photo_status='approved')[0]
    assert mod.pending_counts() == {'photos': 0, 'held': 1}


def test_held_text_from_every_source_counts_and_decides(community):
    wa.REPORTS_PIPE.rows = [{'ts': '2026-09-05 04:33:23+00:00', 'id': 'r1', 'category': 'lighting',
                             'comment': 'DARK', 'lat': 34.85, 'lon': -82.4, 'status': 'held'}]
    bp.FEEDBACK_PIPE.rows = [{'ts': '2026-09-05 04:33:23+00:00', 'id': 'f1', 'spot_name': 'Rack', 'status': 'held'}]
    assert {h['id'] for h in mod.source_held()} == {'walk-audit:r1', 'bike-parking:f1'}
    assert mod.pending_counts() == {'photos': 1, 'held': 3}
    assert not mod.decide_held('bogus:r1', username='admin@example.com', status='published')[0]
    assert mod.decide_held('walk-audit:r1', username='admin@example.com', status='published')[0]
    assert mod.decide_held('bike-parking:f1', username='admin@example.com', status='rejected')[0]
    assert mod.decide_held('spam1', username='admin@example.com', status='published')[0]
    assert mod.pending_counts()['held'] == 0
    # Community text can be sent back to review.
    assert ml.moderate_contributions(['path1'], username='admin@example.com', status='held')[0]
    assert mod.pending_counts()['held'] == 1
