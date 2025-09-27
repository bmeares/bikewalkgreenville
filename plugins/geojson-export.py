#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Exports pipes' data as GeoJSON files.
"""

from typing import Any
from datetime import datetime
import meerschaum as mrsm
from meerschaum.actions import make_action
from meerschaum.utils.warnings import info, warn

__version__ = '0.0.2'

bwg = mrsm.Plugin('bwg')

@make_action
def export_geojson(
    begin: datetime | int | None = None,
    end: datetime | int | None = None,
    params: dict[str, Any] | None = None,
    debug: bool = False,
    **kwargs,
) -> mrsm.SuccessTuple:
    """Run `mrsm export geosjon` to trigger."""
    data_path = bwg.module.get_data_path()
    geojson_output_path = data_path / 'output' / 'geojson'
    if not geojson_output_path.exists():
        geojson_output_path.mkdir(parents=True, exist_ok=True)

    pipes = mrsm.get_pipes(as_list=True, **kwargs)
    num_successes = 0
    for pipe in pipes:
        info(f"Exporting {pipe}...")
        geometry_cols = [
            col
            for col, typ in pipe.dtypes.items()
            if 'geometry' in typ.lower() or 'geography' in typ.lower()
        ]
        if not geometry_cols:
            warn(f"No geometry columns detected for {pipe}.")
            continue

        col = geometry_cols[0]

        schema = pipe.parameters.get('schema') or 'public'
        schema_path = geojson_output_path / schema
        if not schema_path.exists():
            schema_path.mkdir(parents=True, exist_ok=True)

        pipe_output_path = schema_path / (pipe.target + '.geojson')
        df = pipe.get_data(
            [col],
            begin=begin,
            end=end,
            params=params,
            debug=debug,
        )
        if df is None:
            warn(f"No data returned for {pipe}.")
            continue

        try:
            json_str = df.to_json(drop_id=True, to_wgs84=True)
            with open(pipe_output_path, 'w+') as f:
                f.write(json_str)
        except Exception as e:
            return False, f"Failed to export {pipe}:\n{e}"

        mrsm.pprint((True, f"Wrote file '{pipe_output_path}'."))
        num_successes += 1

    if num_successes == 0:
        return False, "Did not export any pipes."

    return (
        True,
        f"Successfully exported {num_successes} pipe" + ('s' if num_successes != 1 else '') + '.'
    )
