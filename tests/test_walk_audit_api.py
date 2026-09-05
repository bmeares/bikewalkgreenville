"""Dismissing a report removes it from the map and logs it in public history."""
import importlib.util
import os
import sys
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'plugins', 'walk-audit.py')
spec = importlib.util.spec_from_file_location('bwg_walk_audit', PLUGIN)
wa = importlib.util.module_from_spec(spec)
sys.modules['bwg_walk_audit'] = wa
spec.loader.exec_module(wa)


class MemoryPipe:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def exists(self):
        return bool(self.rows)

    def get_data(self, select_columns=None, **_):
        df = pd.DataFrame(self.rows)
        return df[[c for c in select_columns if c in df.columns]] if select_columns else df

    def sync(self, rows):
        self.rows.extend(dict(r, ts='2026-09-05 14:30:00+00:00') for r in rows)
        return True, 'saved'


def test_dismiss_hides_report_and_logs_history():
    reports = MemoryPipe([{'ts': '2026-09-05 04:33:23+00:00', 'id': 'r1', 'category': 'lighting',
                           'comment': 'Dark underpass', 'lat': 34.85, 'lon': -82.4, 'road_name': 'Springer St'}])
    edits = MemoryPipe()
    with patch.object(wa, 'REPORTS_PIPE', reports), patch.object(wa, 'EDITS_PIPE', edits):
        app = FastAPI(); wa.init_app(app); client = TestClient(app)
        assert len(client.get('/walk-audit/reports.geojson').json()['features']) == 1
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': ''}).status_code == 400
        assert client.post('/walk-audit/dismiss', json={'id': 'nope', 'reason': 'x'}).status_code == 404
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'Light was fixed'}).status_code == 200
        assert client.post('/walk-audit/dismiss', json={'id': 'r1', 'reason': 'again'}).status_code == 409
        assert client.get('/walk-audit/reports.geojson').json()['features'] == []
        history = client.get('/walk-audit/history').json()['edits']
        assert [h['type'] for h in history] == ['dismiss', 'report']
        assert history[0]['ts_display'] == 'Sep 5, 2026 · 10:30 AM ET'
        assert history[1]['ts_display'] == 'Sep 5, 2026 · 12:33 AM ET'
        assert history[0]['comment'] == 'Light was fixed' and history[1]['active'] is False
        assert history[1]['name'] == 'Poor lighting near Springer St'
        assert history[1]['geometry'] == {'type': 'Point', 'coordinates': [-82.4, 34.85]}
        assert not any('ip' in h for h in history)


def test_submit_rejects_remote_coordinates_and_long_comments():
    reports = MemoryPipe()
    with patch.object(wa, 'REPORTS_PIPE', reports), \
         patch.object(wa, '_nearest_road', lambda lat, lon: {}):
        wa._SUBMIT_HITS.clear()
        app = FastAPI(); wa.init_app(app); client = TestClient(app)
        base = {'category': 'broken-sidewalk', 'comment': 'Observed issue',
                'lat': '34.85', 'lon': '-82.4'}
        assert client.post('/walk-audit/submit', data=dict(base, lat='37.422', lon='-122.084')).status_code == 400
        assert client.post('/walk-audit/submit', data=dict(base, comment='x' * 2001)).status_code == 400


def test_submit_rate_limits_each_client():
    reports = MemoryPipe()
    with patch.object(wa, 'REPORTS_PIPE', reports), \
         patch.object(wa, '_nearest_road', lambda lat, lon: {}):
        wa._SUBMIT_HITS.clear()
        app = FastAPI(); wa.init_app(app); client = TestClient(app)
        data = {'category': 'broken-sidewalk', 'comment': 'Observed issue',
                'lat': '34.85', 'lon': '-82.4'}
        for _ in range(wa.SUBMIT_MAX_PER_HOUR):
            assert client.post('/walk-audit/submit', data=data).status_code == 200
        assert client.post('/walk-audit/submit', data=data).status_code == 429
