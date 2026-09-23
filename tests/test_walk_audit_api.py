"""Walk-audit reports: sign-in, held text, pending photos, dismissals and
public history."""
import importlib.util
import os
import sys
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bwg_fakes import JPEG, FakeUsers, bearer, install

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugins', 'walk-audit.py')
spec = importlib.util.spec_from_file_location('bwg_walk_audit', PLUGIN)
wa = importlib.util.module_from_spec(spec)
sys.modules['bwg_walk_audit'] = wa
spec.loader.exec_module(wa)

ALICE = bearer('alice@example.com')


class MemoryPipe:
    """Rows keyed on (ts, id): a sync that carries `ts` updates in place."""

    def __init__(self, rows=()):
        self.rows = list(rows)

    def exists(self):
        return bool(self.rows)

    def get_data(self, select_columns=None, **_):
        df = pd.DataFrame(self.rows)
        return df[[c for c in select_columns if c in df.columns]] if select_columns else df

    def sync(self, rows, **_):
        for r in rows:
            old = next((o for o in self.rows if 'ts' in r and o['id'] == r['id']), None)
            if old is not None:
                old.update(r)
            else:
                self.rows.append(dict(r, ts='2026-09-05 14:30:00+00:00'))
        return True, 'saved'


@pytest.fixture(autouse=True)
def signed_in(monkeypatch, tmp_path):
    monkeypatch.setattr(wa, '_BWG_AUTH', install(FakeUsers(admins=['admin@example.com'], banned=['troll@example.com'])))
    monkeypatch.setattr(wa, '_photos_dir', lambda: tmp_path)
    monkeypatch.setattr(wa, '_nearest_road', lambda lat, lon: {})
    wa._SUBMIT_HITS.clear()
    return tmp_path


def _client():
    app = FastAPI(); wa.init_app(app)
    return TestClient(app)


def test_dismiss_hides_report_and_logs_history():
    reports = MemoryPipe([{'ts': '2026-09-05 04:33:23+00:00', 'id': 'r1', 'category': 'lighting',
                           'comment': 'Dark underpass', 'lat': 34.85, 'lon': -82.4, 'road_name': 'Springer St',
                           'username': 'alice@example.com'},
                          {'ts': '2026-09-01 00:00:00+00:00', 'id': 'legacy', 'category': 'lighting',
                           'lat': 34.85, 'lon': -82.4}])
    edits = MemoryPipe()
    with patch.object(wa, 'REPORTS_PIPE', reports), patch.object(wa, 'EDITS_PIPE', edits):
        client = _client()
        assert len(client.get('/walk-audit/reports.geojson').json()['features']) == 2
        # Reporter or admin only; legacy rows without a username: admin only.
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'x'},
                           headers=bearer('bob@example.com')).status_code == 403
        assert client.post('/walk-audit/dismiss', json={'id': 'legacy', 'reason': 'x'}, headers=ALICE).status_code == 403
        assert client.post('/walk-audit/dismiss', json={'id': 'legacy', 'reason': 'Old'},
                           headers=bearer('admin@example.com')).status_code == 200
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'x'}).status_code == 401
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'x'},
                           headers=bearer('troll@example.com')).status_code == 403
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': ''}, headers=ALICE).status_code == 400
        assert client.post('/walk-audit/dismiss', json={'id': 'nope', 'reason': 'x'}, headers=ALICE).status_code == 404
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'Light was fixed'},
                           headers=ALICE).status_code == 200
        assert edits.rows[-1]['username'] == 'alice@example.com'
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'again'}, headers=ALICE).status_code == 409
        assert client.get('/walk-audit/reports.geojson').json()['features'] == []
        history = [h for h in client.get('/walk-audit/history').json()['edits'] if h['id'] != 'legacy'
                   and h['comment'] != 'Old']
        assert [h['type'] for h in history] == ['dismiss', 'report']
        assert history[0]['ts_display'] == 'Sep 5, 2026 · 10:30 AM ET'
        assert history[1]['ts_display'] == 'Sep 5, 2026 · 12:33 AM ET'
        assert history[0]['comment'] == 'Light was fixed' and history[1]['active'] is False
        assert history[1]['name'] == 'Poor lighting near Springer St'
        assert history[1]['geometry'] == {'type': 'Point', 'coordinates': [-82.4, 34.85]}
        assert not any('ip' in h or 'username' in h for h in history)


def test_submit_rejects_anonymous_remote_coordinates_and_long_comments():
    with patch.object(wa, 'REPORTS_PIPE', MemoryPipe()):
        client = _client()
        base = {'category': 'broken-sidewalk', 'comment': 'Observed issue', 'lat': '34.85', 'lon': '-82.4'}
        assert client.post('/walk-audit/submit', data=base).status_code == 401
        assert client.post('/walk-audit/submit', data=base, headers=bearer('troll@example.com')).status_code == 403
        assert client.post('/walk-audit/submit', data=dict(base, lat='37.422', lon='-122.084'),
                           headers=ALICE).status_code == 400
        assert client.post('/walk-audit/submit', data=dict(base, comment='x' * 2001), headers=ALICE).status_code == 400
        assert client.post('/walk-audit/submit', data=base, headers=ALICE,
                           files={'photo': ('x.gif', b'GIF89a', 'image/gif')}).status_code == 400


def test_submit_rate_limits_each_client():
    with patch.object(wa, 'REPORTS_PIPE', MemoryPipe()):
        client = _client()
        data = {'category': 'broken-sidewalk', 'lat': '34.85', 'lon': '-82.4'}
        for i in range(wa.SUBMIT_MAX_PER_HOUR):
            assert client.post('/walk-audit/submit', data=dict(data, comment=f'Issue {i}'), headers=ALICE).status_code == 200
        assert client.post('/walk-audit/submit', data=data, headers=ALICE).status_code == 429


def test_held_text_and_pending_photo_stay_hidden(signed_in):
    reports, emails = MemoryPipe(), []
    with patch.object(wa, 'REPORTS_PIPE', reports), patch.object(wa, 'EDITS_PIPE', MemoryPipe()), \
         patch.object(wa, '_send_report_email', emails.append):
        client = _client()
        base = {'category': 'obstruction', 'lat': '34.85', 'lon': '-82.4'}
        held = client.post('/walk-audit/submit', data=dict(base, comment='THIS IS A TOTALLY BLOCKED SIDEWALK'),
                           headers=ALICE).json()
        assert held['status'] == 'held' and held['photo_status'] is None
        ok = client.post('/walk-audit/submit', data=dict(base, comment='Sign in the path'), headers=ALICE,
                         files={'photo': ('pole.HEIC', JPEG, 'image/heic')}).json()
        assert ok['status'] == 'published' and ok['photo_status'] == 'pending'
        assert {r['username'] for r in reports.rows} == {'alice@example.com'}

        features = client.get('/walk-audit/reports.geojson').json()['features']
        assert [f['properties']['id'] for f in features] == [ok['id']]
        assert features[0]['properties']['photo_url'] is None
        assert [h['id'] for h in client.get('/walk-audit/history').json()['edits']] == [ok['id']]
        filename = f"{ok['id']}.jpg"
        assert (signed_in / filename).is_file()
        assert client.get(f'/walk-audit/photos/{filename}').status_code == 404
        assert client.post('/walk-audit/dismiss', json={'id': held['id'], 'reason': 'x'}, headers=ALICE).status_code == 404

        assert [p['id'] for p in wa.pending_photos()] == [ok['id']]
        assert wa.set_photo_status([ok['id']], 'approved')[0]
        assert wa.pending_photos() == []
        assert client.get(f'/walk-audit/photos/{filename}').status_code == 200
        assert client.get('/walk-audit/reports.geojson').json()['features'][0]['properties']['photo_url'] \
            == f'/walk-audit/photos/{filename}'

        # Held text: listed for the console, approved in place, rejected stays off.
        assert [r['id'] for r in wa.held_reports()] == [held['id']]
        assert not wa.set_status([held['id']], 'bogus')[0]
        assert wa.set_status([held['id']], 'published')[0] and wa.held_reports() == []
        assert len(client.get('/walk-audit/reports.geojson').json()['features']) == 2
        assert wa.set_status([held['id']], 'rejected')[0]
        assert len(client.get('/walk-audit/reports.geojson').json()['features']) == 1

        # The staff email goes out for both, the photo is linked, never attached.
        assert {e['id'] for e in emails} == {held['id'], ok['id']}


def test_email_links_console_instead_of_attaching(monkeypatch):
    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def starttls(self): pass
        def login(self, *a): pass
        def send_message(self, msg): sent.append(msg)

    import smtplib
    monkeypatch.setattr(smtplib, 'SMTP', FakeSMTP)
    cfg = {('smtp', 'host'): 'smtp.example.com', ('smtp', 'username'): 'data@example.com',
           ('smtp', 'password'): 'pw'}
    monkeypatch.setattr(wa, '_cfg', lambda *keys, default=None: cfg.get(keys, default))
    report = {'id': 'r1', 'category': 'lighting', 'lat': 34.85, 'lon': -82.4, 'photo_filename': 'r1.jpg',
              'status': 'published'}
    assert wa._send_report_email(report) == 'data@example.com'
    msg = sent[0]
    assert not msg.is_multipart() and not list(msg.iter_attachments())
    assert 'review it in the moderation console: https://bwg.mrsm.io/dash/moderation' in msg.get_content()
    assert wa._send_report_email(dict(report, photo_filename=None))
    assert 'moderation console' not in sent[1].get_content()
