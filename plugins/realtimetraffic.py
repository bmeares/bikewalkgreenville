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

def fetch(pipe: mrsm.Pipe, **kw) -> List[Dict[str, Any]]:
    """
    Scrape the real-time incidents dashboard.
    """
    import requests
    from bs4 import BeautifulSoup
    response = requests.get(URL)
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
