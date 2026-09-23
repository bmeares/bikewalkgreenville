#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
BWG calendar events: the public Google Calendar ICS feed, expanded into one
row per occurrence for the app's "Upcoming events" card.

Pipe `plugin:bwg-events` / `events` (target `BwgApp.events` on `sql:bwg`):
  uid, start, end (UTC), all_day, title, location, description (plain text),
  html_link (Google Calendar event page), luma_url, synced_at

Route (mounted on the Meerschaum API app, i.e. https://bwg.mrsm.io):
  GET /bwg/events.json?days=60 -> {events: [...]} upcoming, sorted by start

Prod job (inside the API container):
  mrsm register pipe -c plugin:bwg-events -m events -i sql:bwg
  mrsm sync pipes -c plugin:bwg-events -m events -i sql:bwg --loop --min-seconds 3600 --name bwg-events -d
"""

import base64
import re
from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

import meerschaum as mrsm
from meerschaum.plugins import api_plugin
from meerschaum.utils.warnings import warn

__version__ = '0.1.0'

required = ['icalendar', 'python-dateutil', 'requests']

ICS_URL = (
    'https://calendar.google.com/calendar/ical/'
    'c_33275914f364c4ba73d477f6ead8495a4e50f057d9e99761fe53ab3076bc406a'
    '%40group.calendar.google.com/public/basic.ics'
)
DEFAULT_TZ = 'America/New_York'
PAST_DAYS = 1
FUTURE_DAYS = 90

#: First public Luma link. Organizer "manage" links leak into descriptions
#: when admins paste from the dashboard — never hand those to riders.
LUMA_RE = re.compile(r'https?://(?:www\.)?(?:lu\.ma|luma\.com)/(?!event/manage)[^\s"\'<>)]+')

EVENTS_PIPE: mrsm.Pipe = mrsm.Pipe(
    'plugin:bwg-events', 'events',
    instance='sql:bwg',
)


def register(pipe: mrsm.Pipe) -> dict[str, Any]:
    return {
        'schema': 'BwgApp',
        'target': 'events',
        'columns': {
            'datetime': 'start',
            'id': 'uid',
        },
        'dtypes': {
            'start': 'datetime',
            'end': 'datetime',
            'synced_at': 'datetime',
            'all_day': 'bool',
            'uid': 'string',
            'title': 'string',
            'location': 'string',
            'description': 'string',
            'html_link': 'string',
            'luma_url': 'string',
        },
    }


class _Text(HTMLParser):
    _BREAKS = {'br', 'p', 'div', 'li', 'tr'}

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self._BREAKS:
            self.parts.append('\n')

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(text: str) -> str:
    parser = _Text()
    parser.feed(text or '')
    parser.close()
    out = ''.join(parser.parts).replace('\xa0', ' ')
    out = re.sub(r'[ \t]+\n', '\n', out)
    return re.sub(r'\n{3,}', '\n\n', out).strip()


def find_luma(*texts: str) -> str | None:
    for text in texts:
        match = LUMA_RE.search(text or '')
        if match:
            return match.group(0).rstrip('.,;')
    return None


def _calendar_id(ics_url: str) -> str | None:
    match = re.search(r'/ical/([^/]+)/', ics_url or '')
    return unquote(match.group(1)) if match else None


def _html_link(uid: str, instance_utc: datetime | None, calendar_id: str | None) -> str | None:
    """Google Calendar event page: eid = b64("<event id>[_<instance>] <cal id>")."""
    if not calendar_id or not uid.endswith('@google.com'):
        return None
    event_id = uid[:-len('@google.com')]
    if instance_utc is not None:
        event_id += '_' + instance_utc.strftime('%Y%m%dT%H%M%SZ')
    eid = base64.urlsafe_b64encode(f'{event_id} {calendar_id}'.encode()).decode().rstrip('=')
    return f'https://www.google.com/calendar/event?eid={eid}'


def _to_utc(value: date | datetime, tz: ZoneInfo) -> datetime:
    """All-day dates become local midnight so the app shows the right day."""
    if not isinstance(value, datetime):
        value = datetime(value.year, value.month, value.day)
    if value.tzinfo is None:
        value = value.replace(tzinfo=tz)
    return value.astimezone(timezone.utc)


def parse_ics(
    text: str | bytes,
    now: datetime | None = None,
    past_days: int = PAST_DAYS,
    future_days: int = FUTURE_DAYS,
    ics_url: str = ICS_URL,
) -> list[dict[str, Any]]:
    """Expand an ICS calendar into occurrence rows within the window."""
    icalendar = mrsm.attempt_import('icalendar')
    from dateutil.rrule import rrulestr

    now = now or datetime.now(timezone.utc)
    win_start = now - timedelta(days=past_days)
    win_end = now + timedelta(days=future_days)
    cal = icalendar.Calendar.from_ical(text)
    tz = ZoneInfo(str(cal.get('X-WR-TIMEZONE') or DEFAULT_TZ))
    calendar_id = _calendar_id(ics_url)

    events = [c for c in cal.walk('VEVENT') if c.get('DTSTART') is not None]
    # (uid, recurrence-id in UTC) -> override component.
    overrides = {
        (str(c.get('UID')), _to_utc(c.decoded('RECURRENCE-ID'), tz)): c
        for c in events if c.get('RECURRENCE-ID') is not None
    }

    def row(comp, start_utc: datetime, end_utc: datetime, all_day: bool,
            instance_utc: datetime | None) -> dict[str, Any]:
        uid = str(comp.get('UID'))
        raw_desc = str(comp.get('DESCRIPTION') or '')
        url = str(comp.get('URL') or '')
        return {
            'uid': uid if instance_utc is None else f"{uid}-{instance_utc:%Y%m%dT%H%M%SZ}",
            'start': start_utc,
            'end': end_utc,
            'all_day': all_day,
            'title': str(comp.get('SUMMARY') or 'Event').strip(),
            'location': str(comp.get('LOCATION') or '').strip() or None,
            'description': strip_html(raw_desc) or None,
            'html_link': url or _html_link(uid, instance_utc, calendar_id),
            'luma_url': find_luma(raw_desc, url),
        }

    def span(comp) -> tuple[date | datetime, timedelta, bool]:
        dtstart = comp.decoded('DTSTART')
        all_day = not isinstance(dtstart, datetime)
        if comp.get('DTEND') is not None:
            duration = comp.decoded('DTEND') - dtstart
        elif comp.get('DURATION') is not None:
            duration = comp.decoded('DURATION')
        else:
            duration = timedelta(days=1) if all_day else timedelta(0)
        return dtstart, duration, all_day

    def in_window(start_utc, end_utc) -> bool:
        return end_utc >= win_start and start_utc <= win_end

    def expand(comp) -> list[dict[str, Any]]:
        out = []
        if str(comp.get('STATUS') or '').upper() == 'CANCELLED':
            return out
        dtstart, duration, all_day = span(comp)
        uid = str(comp.get('UID'))

        if comp.get('RECURRENCE-ID') is not None:
            instance = _to_utc(comp.decoded('RECURRENCE-ID'), tz)
            start_utc = _to_utc(dtstart, tz)
            end_utc = _to_utc(dtstart + duration, tz)
            if in_window(start_utc, end_utc):
                out.append(row(comp, start_utc, end_utc, all_day, instance))
            return out

        if comp.get('RRULE') is None:
            start_utc = _to_utc(dtstart, tz)
            end_utc = _to_utc(dtstart + duration, tz)
            if in_window(start_utc, end_utc):
                out.append(row(comp, start_utc, end_utc, all_day, None))
            return out

        # Recurring master. Expand in the event's own wall clock (DST-safe).
        # ponytail: RDATE is ignored; Google's UI never emits it.
        base = dtstart if not all_day else datetime(dtstart.year, dtstart.month, dtstart.day)
        rule = rrulestr(comp.get('RRULE').to_ical().decode(), dtstart=base, ignoretz=all_day)
        exdates = set()
        exprops = comp.get('EXDATE') or []
        for ex in exprops if isinstance(exprops, list) else [exprops]:
            exdates.update(_to_utc(d.dt, tz) for d in ex.dts)
        lo, hi = win_start - duration, win_end
        if base.tzinfo is None:  # all-day / floating: compare in local wall time
            lo, hi = (d.astimezone(tz).replace(tzinfo=None) for d in (lo, hi))
        for occ in rule.between(lo, hi, inc=True):
            instance = _to_utc(occ.date() if all_day else occ, tz)
            if instance in exdates or (uid, instance) in overrides:
                continue
            start_utc = instance
            end_utc = _to_utc((occ.date() if all_day else occ) + duration, tz)
            if in_window(start_utc, end_utc):
                out.append(row(comp, start_utc, end_utc, all_day, instance))
        return out

    rows = []
    for comp in events:
        try:
            rows += expand(comp)
        except Exception as e:
            # One malformed event (e.g. a date-only RRULE UNTIL on a tz-aware
            # DTSTART) must not take the whole calendar down.
            warn(f"bwg-events: skipped event {comp.get('UID')}: {e}")

    rows.sort(key=lambda r: r['start'])
    return rows


def _ics_url() -> str:
    try:
        url = mrsm.get_config('plugins', 'bwg-events', 'ics_url', warn=False, write_missing=False)
    except Exception:
        url = None
    return url or ICS_URL


def fetch(pipe: mrsm.Pipe, debug: bool = False, **kwargs):
    requests = mrsm.attempt_import('requests')
    url = _ics_url()
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    rows = parse_ics(resp.content, ics_url=url)
    # Every row of a sync shares `synced_at`; the endpoint serves only the
    # newest batch, so cancelled/moved events drop out without a delete.
    synced_at = datetime.now(timezone.utc)
    for r in rows:
        r['synced_at'] = synced_at
    return rows


def _iso(value) -> str | None:
    import pandas as pd
    return None if pd.isna(value) else pd.Timestamp(value).tz_convert('UTC').isoformat()


def upcoming_events(days: int = 60, now: datetime | None = None) -> list[dict[str, Any]]:
    import pandas as pd
    now = pd.Timestamp(now or datetime.now(timezone.utc))
    try:
        if not EVENTS_PIPE.exists():
            return []
        df = EVENTS_PIPE.get_data(begin=(now - pd.Timedelta(days=PAST_DAYS + 1)).to_pydatetime())
    except Exception:
        return []
    if df is None or len(df) == 0:
        return []
    for col in ('start', 'end', 'synced_at'):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True)
    if 'synced_at' in df.columns and df['synced_at'].notna().any():
        df = df[df['synced_at'] == df['synced_at'].max()]
    df = df[(df['end'] >= now) & (df['start'] <= now + pd.Timedelta(days=days))]
    df = df.sort_values('start')
    keys = ('uid', 'title', 'location', 'description', 'html_link', 'luma_url')
    events = []
    for r in df.to_dict(orient='records'):
        ev = {k: (r.get(k) if isinstance(r.get(k), str) else None) for k in keys}
        ev.update({
            'start': _iso(r['start']),
            'end': _iso(r['end']),
            'all_day': r.get('all_day') is True or str(r.get('all_day')).lower() == 'true',
        })
        events.append(ev)
    return events


@api_plugin
def init_app(app):
    from fastapi import Query
    from fastapi.responses import JSONResponse

    @app.get('/bwg/events.json')
    def bwg_events(days: int = Query(60, ge=1, le=FUTURE_DAYS)):
        return JSONResponse(
            {'events': upcoming_events(days)},
            headers={'Cache-Control': 'public, max-age=300'},
        )
