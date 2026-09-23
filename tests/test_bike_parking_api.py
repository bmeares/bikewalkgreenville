"""Bike-parking feedback: sign-in required, held text, photos hidden until an
admin approves them."""
import importlib.util
import os
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bwg_fakes import JPEG, FakeUsers, bearer, install
from test_walk_audit_api import MemoryPipe

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugins', 'bike-parking.py')
spec = importlib.util.spec_from_file_location('bwg_bike_parking', PLUGIN)
bp = importlib.util.module_from_spec(spec)
sys.modules['bwg_bike_parking'] = bp
spec.loader.exec_module(bp)

ALICE = bearer('alice@example.com')


@pytest.fixture
def client(monkeypatch, tmp_path):
    monkeypatch.setattr(bp, '_BWG_AUTH', install(FakeUsers(banned=['troll@example.com'])))
    monkeypatch.setattr(bp, '_photos_dir', lambda: tmp_path)
    pipe = MemoryPipe()
    monkeypatch.setattr(bp, 'FEEDBACK_PIPE', pipe)
    app = FastAPI(); bp.init_app(app)
    return TestClient(app), pipe, tmp_path


def test_submit_requires_sign_in_and_hides_photo_until_approved(client):
    client, pipe, photos = client
    data = {'spot_name': 'Main St rack', 'feedback': 'Rack is loose', 'lat': '34.85', 'lon': '-82.4'}
    jpg = {'photo': ('rack.jpeg', JPEG, 'image/jpeg')}
    assert client.post('/bike-parking/submit', data=data, files=jpg).status_code == 401
    assert client.post('/bike-parking/submit', data=data, headers=bearer('troll@example.com')).status_code == 403
    assert client.post('/bike-parking/submit', data=data, headers=ALICE,
                       files={'photo': ('rack.svg', b'<svg/>', 'image/svg+xml')}).status_code == 400
    # A fake extension doesn't pass the magic-byte sniff; bad coordinates are refused.
    assert client.post('/bike-parking/submit', data=data, headers=ALICE,
                       files={'photo': ('rack.jpg', b'<html>', 'image/jpeg')}).status_code == 400
    assert client.post('/bike-parking/submit', data=dict(data, lat='91'), headers=ALICE).status_code == 400
    assert not pipe.rows and not list(photos.iterdir())

    held = client.post('/bike-parking/submit', data=dict(data, feedback='shit rack'), headers=ALICE).json()
    assert held['status'] == 'held' and held['photo_status'] is None
    ok = client.post('/bike-parking/submit', data=data, headers=ALICE, files=jpg).json()
    assert ok['status'] == 'published' and ok['photo_status'] == 'pending'
    assert {r['username'] for r in pipe.rows} == {'alice@example.com'}

    filename = f"{ok['id']}.jpg"
    assert (photos / filename).is_file()
    assert client.get(f'/bike-parking/photos/{filename}').status_code == 404
    assert [p['id'] for p in bp.pending_photos()] == [ok['id']]
    assert not bp.set_photo_status([ok['id']], 'bogus')[0]
    assert bp.set_photo_status([ok['id']], 'approved')[0]
    assert bp.pending_photos() == []
    photo = client.get(f'/bike-parking/photos/{filename}')
    assert photo.status_code == 200 and photo.headers['x-content-type-options'] == 'nosniff'
    assert [r['id'] for r in bp.held_reports()] == [held['id']]
    assert bp.set_status([held['id']], 'published')[0] and bp.held_reports() == []
    # 10 an hour per user (4 used above, the two refused photos included).
    for _ in range(bp.SUBMIT_MAX_PER_HOUR - 4):
        assert client.post('/bike-parking/submit', data=data, headers=ALICE).status_code == 200
    assert client.post('/bike-parking/submit', data=data, headers=ALICE).status_code == 429
    assert client.get('/bike-parking/photos/..%2Fsecret.jpg').status_code == 404
