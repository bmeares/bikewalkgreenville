#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Implement the plugin 'felt'.

See the Writing Plugins guide for more information:
https://meerschaum.io/reference/plugins/writing-plugins/
"""

from meerschaum.connectors import make_connector
from ._felt_connector import FeltConnector

make_connector(FeltConnector)

__version__ = '0.0.1'

# Add any dependencies to `required` (similar to `requirements.txt`).
required: list[str] = ['felt-python']


def setup(**kwargs):
    return True, "Success"
