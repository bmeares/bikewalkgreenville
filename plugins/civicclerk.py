#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Implement the plugin 'civicclerk'.

See the Writing Plugins guide for more information:
https://meerschaum.io/reference/plugins/writing-plugins/
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Union, List, Dict
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.utils.warnings import info

__version__ = '0.0.1'

BASE_URL: str = 'https://greenvillesc.api.civicclerk.com/v1/Events'


def register(pipe: mrsm.Pipe):
    """Return the default parameters for a new pipe."""
    return {
        'columns': {
            'datetime': 'createdOn',
            'primary': 'id',
        },
        'dtypes': {
            'createdByUserId': 'uuid',
            'id': 'int',
        },
        'indices': {
            'startDateTime': 'startDateTime',
        },
    }


def fetch(
    pipe: mrsm.Pipe,
    begin: datetime | int | None = None,
    end: datetime | int | None = None,
    chunksize: int | None = None,
    debug: bool = False,
    **kwargs
):
    """Return or yield dataframes."""
    requests = mrsm.attempt_import('requests', lazy=False)

    last_id = pipe.get_sync_time(debug=debug) or 0
    btm = pipe.parameters.get('fetch', {}).get('backtrack_minutes', 10)
    chunk_start_id = max(last_id - btm, 0)
    if chunksize == -1:
        chunksize = None
    chunksize = chunksize or 100

    docs = []
    while True:
        chunk_end_id = chunk_start_id + chunksize
        info(f"Fetching events between {chunk_start_id} and {chunk_end_id}...")
        response = requests.get(
            BASE_URL,
            params={
                '$filter': f'id ge {chunk_start_id} and id lt {chunk_end_id}',
                '$orderby': 'id asc',
            },
        )
        if not response:
            raise Exception(response.text)

        chunk_docs = response.json().get('value', [])
        docs.extend(chunk_docs)
        if not chunk_docs:
            info(f"Done fetching events for {pipe}.")
            break

        chunk_start_id = chunk_docs[-1]['id'] + 1

    return docs
