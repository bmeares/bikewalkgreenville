#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Sync municipality population data from census.gov Population Estimates.

Identifies geographies by FIPS code (robust across decades since names vary,
e.g. "Greenville Co." in 1980s vs "Greenville County" later).

Counties: 1980-present. Cities: 2000-present (no pre-2000 city data exists).
"""

import re
from datetime import datetime
import meerschaum as mrsm

__version__ = '0.0.2'

required: list[str] = ['pandas', 'requests']

BASE = "https://www2.census.gov/programs-surveys/popest"

CITY_SOURCES = [
    {
        'kind': 'fixed_su9910',
        'url': f"{BASE}/tables/1990-2000/cities/totals/su-99-10_{{state_abbr}}.txt",
    },
    {
        'kind': 'csv_wide',
        'url': f"{BASE}/datasets/2000-2010/intercensal/cities/sub-est00int.csv",
        'sumlev': '162',
        'state_col': 'STATE',
        'geo_col': 'PLACE',
    },
    {
        'kind': 'csv_wide',
        'url': f"{BASE}/datasets/2010-2020/cities/SUB-EST2020_{{state_int}}.csv",
        'sumlev': '162',
        'state_col': 'STATE',
        'geo_col': 'PLACE',
    },
    {
        'kind': 'csv_wide',
        'url': f"{BASE}/datasets/2020-2024/cities/totals/sub-est2024_{{state_int}}.csv",
        'sumlev': '162',
        'state_col': 'STATE',
        'geo_col': 'PLACE',
    },
]

COUNTY_SOURCES = [
    {
        'kind': 'fixed_8089',
        'url': f"{BASE}/tables/1980-1990/counties/totals/e8089co.txt",
    },
    {
        'kind': 'fixed_9099',
        'url': f"{BASE}/tables/1990-2000/counties/totals/99c8_{{state_int}}.txt",
    },
    {
        'kind': 'csv_wide',
        'url': f"{BASE}/datasets/2000-2010/intercensal/county/co-est00int-tot.csv",
        'sumlev': '050',
        'state_col': 'STATE',
        'geo_col': 'COUNTY',
    },
    {
        'kind': 'csv_wide',
        'url': f"{BASE}/datasets/2010-2020/counties/totals/co-est2020.csv",
        'sumlev': '050',
        'state_col': 'STATE',
        'geo_col': 'COUNTY',
    },
    {
        'kind': 'csv_wide',
        'url': f"{BASE}/datasets/2020-2024/counties/totals/co-est2024-alldata.csv",
        'sumlev': '050',
        'state_col': 'STATE',
        'geo_col': 'COUNTY',
    },
]

YEAR_COL_RE = re.compile(r'^POPESTIMATE(\d{4})$')


def register(pipe: mrsm.Pipe):
    return {
        'columns': {
            'datetime': 'year',
            'name': 'name',
        },
        'census': pipe.parameters.get('census', {}),
    }


def fetch(pipe: mrsm.Pipe, begin=None, end=None, **kwargs):
    pd = mrsm.attempt_import('pandas', venv='census')
    cf = pipe.parameters.get('census', {}) or {}
    type_ = cf.get('type', 'cities')
    state_fips = str(cf.get('state', '45')).zfill(2)
    state_abbr = str(cf.get('state_abbr', '')).lower()
    geo_fips = str(cf.get('fips', '')).strip()
    display_name = cf.get('name') or ''

    sources = CITY_SOURCES if type_ == 'cities' else COUNTY_SOURCES
    geo_width = 5 if type_ == 'cities' else 3
    geo_fips_padded = geo_fips.zfill(geo_width) if geo_fips else ''

    frames = []
    for src in sources:
        try:
            df = _load(src, state_fips, state_abbr, geo_fips_padded, pd)
        except Exception as e:
            mrsm.warn(f"Skipping {src['url']}: {e}")
            continue
        if df is not None and not df.empty:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=['name', 'year', 'population'])

    out = pd.concat(frames, ignore_index=True)
    out['name'] = display_name or out['name']
    out = (
        out.sort_values('year')
           .drop_duplicates(subset=['name', 'year'], keep='last')
           .reset_index(drop=True)
    )
    out['population'] = out['population'].astype('Int64')
    return out


def _load(src, state_fips, state_abbr, geo_fips, pd):
    url = src['url'].format(state_int=int(state_fips), state_abbr=state_abbr)
    kind = src['kind']
    if kind == 'csv_wide':
        return _load_csv_wide(url, src, state_fips, geo_fips, pd)
    if kind == 'fixed_8089':
        return _load_8089(url, state_fips, geo_fips, pd)
    if kind == 'fixed_9099':
        return _load_9099(url, state_fips, geo_fips, pd)
    if kind == 'fixed_su9910':
        if not state_abbr:
            raise ValueError("state_abbr required for su-99-10 source")
        return _load_su9910(url, state_fips, geo_fips, pd)
    raise ValueError(f"Unknown kind: {kind}")


def _load_csv_wide(url, src, state_fips, geo_fips, pd):
    df = pd.read_csv(
        url,
        dtype={'SUMLEV': str, 'STATE': str, 'COUNTY': str, 'PLACE': str},
        encoding='latin-1',
    )
    df = df[df['SUMLEV'].str.zfill(3) == src['sumlev']]
    df = df[df[src['state_col']].str.zfill(2) == state_fips]
    if geo_fips:
        geo_col = src['geo_col']
        width = 5 if geo_col == 'PLACE' else 3
        df = df[df[geo_col].str.zfill(width) == geo_fips]
    if df.empty:
        return None

    name_col = 'NAME' if 'NAME' in df.columns else 'CTYNAME'
    year_cols = [c for c in df.columns if YEAR_COL_RE.match(c)]
    long = df.melt(
        id_vars=[name_col],
        value_vars=year_cols,
        var_name='year',
        value_name='population',
    ).rename(columns={name_col: 'name'})
    long['year'] = pd.to_datetime(
        long['year'].str.replace('POPESTIMATE', '', regex=False) + '-01-01'
    )
    return long[['name', 'year', 'population']]


def _http_text(url):
    requests = mrsm.attempt_import('requests', venv='census')
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    return resp.text


_HEADER_8089 = re.compile(r'^Code\s+Area Name')
_ROW_8089 = re.compile(
    r'^\s*(\d{5})\s+(.+?)\s{2,}([\d,]+(?:\s+[\d,]+){4})\s*$'
)


def _load_8089(url, state_fips, geo_fips, pd):
    text = _http_text(url)
    full_fips = f"{state_fips}{geo_fips}" if geo_fips else None
    rows = []
    years = None
    for line in text.split('\n'):
        if _HEADER_8089.match(line):
            years = [int(y) for y in re.findall(r'\b(19[89]\d)\b', line)]
            continue
        m = _ROW_8089.match(line)
        if not m:
            continue
        fips, name, values_str = m.groups()
        if full_fips and fips != full_fips:
            continue
        if not full_fips and not fips.startswith(state_fips):
            continue
        if fips.endswith('000'):
            continue
        values = [int(v.replace(',', '')) for v in values_str.split()]
        if not years or len(years) != len(values):
            continue
        for y, v in zip(years, values):
            rows.append({
                'name': name.strip(),
                'year': pd.Timestamp(f'{y}-01-01'),
                'population': v,
            })
    return pd.DataFrame(rows) if rows else None


_ROW_SU9910 = re.compile(
    r'^\s*157\s+(\d{2})\s+(\d{3})\s+(\d{5})\s+\w{2}\s+(.+?)\s{2,}'
    r'((?:[\d,]+\s+){4,5}[\d,]+)\s*$'
)
_YEARS_SU9910_B1 = [1999, 1998, 1997, 1996, 1995, 1994]
_YEARS_SU9910_B2 = [1993, 1992, 1991, 1990]


def _load_su9910(url, state_fips, geo_fips, pd):
    text = _http_text(url)
    rows = []
    years = None
    for line in text.split('\n'):
        if line.startswith('Block 1'):
            years = _YEARS_SU9910_B1
            continue
        if line.startswith('Block 2'):
            years = _YEARS_SU9910_B2
            continue
        m = _ROW_SU9910.match(line)
        if not m:
            continue
        st, county, place, name, values_str = m.groups()
        if st != state_fips:
            continue
        if geo_fips and place != geo_fips:
            continue
        values = [int(v.replace(',', '')) for v in values_str.split()]
        if years is _YEARS_SU9910_B2:
            values = values[:4]
        if len(values) != len(years):
            continue
        for y, v in zip(years, values):
            rows.append({
                'name': name.strip(),
                'year': pd.Timestamp(f'{y}-01-01'),
                'population': v,
            })
    return pd.DataFrame(rows) if rows else None


_ROW_9099 = re.compile(
    r'^\s*1\s+(\d{2,5})\s+((?:[\d,]+\s+){11})(.+?)\s*$'
)
_YEARS_9099 = [1999, 1998, 1997, 1996, 1995, 1994, 1993, 1992, 1991, 1990]


def _load_9099(url, state_fips, geo_fips, pd):
    text = _http_text(url)
    full_fips = f"{state_fips}{geo_fips}" if geo_fips else None
    rows = []
    in_block_1 = False
    for line in text.split('\n'):
        if line.startswith('Block 1:'):
            in_block_1 = True
            continue
        if line.startswith('Block ') and not line.startswith('Block 1:'):
            in_block_1 = False
            continue
        if not in_block_1:
            continue
        m = _ROW_9099.match(line)
        if not m:
            continue
        fips, values_str, name = m.groups()
        if len(fips) < 5:
            continue
        if full_fips and fips != full_fips:
            continue
        values = [int(v.replace(',', '')) for v in values_str.split()]
        # First 10 values are July 1 estimates for 1999..1990; skip 11th (April base).
        for y, v in zip(_YEARS_9099, values[:10]):
            rows.append({
                'name': name.strip(),
                'year': pd.Timestamp(f'{y}-01-01'),
                'population': v,
            })
    return pd.DataFrame(rows) if rows else None
