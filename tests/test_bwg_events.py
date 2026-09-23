"""ICS expansion + /bwg/events.json for `plugins/bwg-events.py` (no network)."""
import importlib.util
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pandas as pd
from fastapi import FastAPI
from fastapi.testclient import TestClient

PLUGIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'plugins', 'bwg-events.py',
)
spec = importlib.util.spec_from_file_location('bwg_events', PLUGIN)
ev = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ev)

NOW = datetime(2026, 9, 23, 12, tzinfo=timezone.utc)

ICS = """BEGIN:VCALENDAR
VERSION:2.0
X-WR-TIMEZONE:America/New_York
BEGIN:VEVENT
UID:single@google.com
DTSTART:20260925T220000Z
DTEND:20260926T000000Z
SUMMARY:Community Ride
LOCATION:Welcome Center\\, Greenville
END:VEVENT
BEGIN:VEVENT
UID:weekly@google.com
DTSTART;TZID=America/New_York:20260901T180000
DTEND;TZID=America/New_York:20260901T190000
RRULE:FREQ=WEEKLY;COUNT=6
EXDATE;TZID=America/New_York:20260929T180000
SUMMARY:Tuesday Walk
END:VEVENT
BEGIN:VEVENT
UID:allday@google.com
DTSTART;VALUE=DATE:20260927
DTEND;VALUE=DATE:20260928
SUMMARY:Open Streets
END:VEVENT
BEGIN:VEVENT
UID:luma@google.com
DTSTART:20261010T140000Z
DTEND:20261010T160000Z
SUMMARY:Advocacy 101
DESCRIPTION:<p>Learn <b>how</b> to advocate &amp; win.</p><br>RSVP: <a href="https://luma.com/event/manage/evt-secret">manage</a> <a href="https://lu.ma/abc123">https://lu.ma/abc123</a>
END:VEVENT
BEGIN:VEVENT
UID:old@google.com
DTSTART:20250101T140000Z
DTEND:20250101T160000Z
SUMMARY:Long ago
END:VEVENT
END:VCALENDAR
"""


def test_parse_ics_expands_and_extracts():
    rows = ev.parse_ics(ICS, now=NOW)
    by_title = {}
    for r in rows:
        by_title.setdefault(r['title'], []).append(r)
    assert 'Long ago' not in by_title
    # Weekly Sep 1..Oct 6: window starts Sep 22 -> Sep 22, (29 excluded), Oct 6.
    walks = by_title['Tuesday Walk']
    assert [r['start'].date().isoformat() for r in walks] == ['2026-09-22', '2026-10-06']
    assert walks[0]['start'] == datetime(2026, 9, 22, 22, tzinfo=timezone.utc)  # 6pm EDT
    assert walks[0]['uid'] == 'weekly@google.com-20260922T220000Z'
    assert len({r['uid'] for r in rows}) == len(rows) == 5
    day = by_title['Open Streets'][0]
    assert day['all_day'] is True
    assert day['start'] == datetime(2026, 9, 27, 4, tzinfo=timezone.utc)  # local midnight
    assert by_title['Community Ride'][0]['all_day'] is False
    assert by_title['Community Ride'][0]['location'] == 'Welcome Center, Greenville'
    luma = by_title['Advocacy 101'][0]
    assert luma['luma_url'] == 'https://lu.ma/abc123'
    assert '<' not in luma['description'] and 'Learn how to advocate & win.' in luma['description']
    assert by_title['Community Ride'][0]['luma_url'] is None
    assert luma['html_link'].startswith('https://www.google.com/calendar/event?eid=')


class StubPipe:
    def __init__(self, df):
        self.df = df

    def exists(self):
        return self.df is not None

    def get_data(self, **kwargs):
        return self.df


def test_events_endpoint_sorted_upcoming():
    now = datetime.now(timezone.utc)
    synced = now - timedelta(minutes=5)

    def row(uid, start_h, hours=2, synced_at=synced, **kw):
        start = now + timedelta(hours=start_h)
        return {'uid': uid, 'start': start, 'end': start + timedelta(hours=hours),
                'all_day': False, 'title': uid, 'location': None, 'description': None,
                'html_link': None, 'luma_url': None, 'synced_at': synced_at, **kw}

    df = pd.DataFrame([
        row('later', 48, luma_url='https://lu.ma/x'),
        row('ended', -5),                       # ended before now
        row('ongoing', -1),                     # started, still running
        row('soon', 3, all_day=True),
        row('far', 24 * 80),                    # beyond ?days=60
        row('cancelled', 5, synced_at=synced - timedelta(hours=1)),  # stale batch
    ])
    app = FastAPI()
    ev.init_app(app)
    client = TestClient(app)
    with patch.object(ev, 'EVENTS_PIPE', StubPipe(df)):
        resp = client.get('/bwg/events.json?days=60')
    assert resp.status_code == 200
    assert resp.headers['cache-control'] == 'public, max-age=300'
    events = resp.json()['events']
    assert [e['uid'] for e in events] == ['ongoing', 'soon', 'later']
    assert events[1]['all_day'] is True and events[0]['all_day'] is False
    assert events[2]['luma_url'] == 'https://lu.ma/x'
    assert events[0]['start'].endswith('+00:00')
    with patch.object(ev, 'EVENTS_PIPE', StubPipe(None)):
        assert client.get('/bwg/events.json').json() == {'events': []}
    assert client.get('/bwg/events.json?days=0').status_code == 422


def test_bad_recurrence_is_skipped_not_fatal():
    # Date-only UNTIL on a tz-aware DTSTART: dateutil raises ValueError.
    bad = """BEGIN:VEVENT
UID:bad@google.com
DTSTART;TZID=America/New_York:20260901T180000
DTEND;TZID=America/New_York:20260901T190000
RRULE:FREQ=WEEKLY;UNTIL=20261117
SUMMARY:Broken Weekly
END:VEVENT
"""
    rows = ev.parse_ics(ICS.replace('END:VCALENDAR', bad + 'END:VCALENDAR'), now=NOW)
    assert 'Broken Weekly' not in {r['title'] for r in rows}
    assert len(rows) == 5
