#! /usr/bin/env python3
# -*- coding: utf-8 -*-
# vim:fenc=utf-8

"""
Scrape real-time incidents data.
"""

import re
import meerschaum as mrsm
from meerschaum.utils.typing import Dict, List, Any

required = ['beautifulsoup4', 'requests']

URL: str = "https://realtimetraffic.scdps.gov/smartwebclient/"
HEADERS: Dict[str, str] = {
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'en-US,en;q=0.5',
    'Connection': 'keep-alive',
    'Cookie': 'ASP.NET_SessionId=wkcgbmwg1not3to3eu1lm2a0',
    'Host': 'realtimetraffic.scdps.gov',
    'Sec-Fetch-Dest': 'document',
    'Sec-Fetch-Mode': 'navigate',
    'Sec-Fetch-Site': 'cross-site',
    'Upgrade-Insecure-Requests': '1',
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/110.0',
}

def fetch(pipe: mrsm.Pipe, **kw) -> List[Dict[str, Any]]:
    """
    Scrape the real-time incidents dashboard.
    """
    import requests
    from bs4 import BeautifulSoup
    response = requests.get(URL, headers=HEADERS)
    if not response.ok:
        return None
    soup = BeautifulSoup(response.text, 'html.parser')

    table = None
    tables = soup.find_all('table')
    for t in tables:
        if 'newFont' in t.get('class'):
            table = t
            break
    if table is None:
        return None

    rows = table.find_all('tr')
    header_table = list(rows[0].children)[1]
    headers_row = header_table.find('tr', id=re.compile(f"(HeadersRow)"))
    headers_cells = headers_row.find_all('td')
    columns = [c.text for c in headers_cells if c.text[0].isalpha()]

    docs = []
    data_rows = table.find_all('tr', id=re.compile(f"(DataRow)"))
    for row in data_rows:
        cells = row.find_all('td')
        doc = {col: cell.text.strip() for col, cell in zip(columns, cells)}
        docs.append(doc)

    return docs
