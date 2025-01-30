#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Implement the plugin 'felt'.

See the Writing Plugins guide for more information:
https://meerschaum.io/reference/plugins/writing-plugins/
"""

from typing import Any
from datetime import datetime, timedelta, timezone
import meerschaum as mrsm
from meerschaum.config import get_plugin_config, write_plugin_config
from meerschaum.connectors import Connector, make_connector

@make_connector
class FeltConnector(Connector):
    """Implement 'felt' connectors."""

    REQUIRED_ATTRIBUTES: list[str] = ['token']
    OPTIONAL_ATTRIBUTES: list[str] = ['instance_keys']

    from ._pipes import (
        register_pipe,
    )
      
    @property
    def instance_connector(self) -> Connector:
        """
        Return the internal instance connector to use for storing metadata.
        """
        if 'instance_keys' in self.__dict__:
            return mrsm.get_connector(self.instance_keys)
        return mrsm.get_connector()
