#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Exports pipes' data as GeoJSON files.
"""

import meerschaum as mrsm
from meerschaum.actions import make_action
from meerschaum.utils.warnings import info, warn

__version__ = '0.0.1'

bwg = mrsm.Plugin('bwg')

@make_action
def export_geojson(**kwargs) -> mrsm.SuccessTuple:
    """Run `mrsm export geosjon` to trigger."""
    from meerschaum.utils.dataframe import to_json
    data_path = bwg.module.get_data_path()
    geojson_output_path = data_path / 'output' / 'geojson'
    if not geojson_output_path.exists():
        geojson_output_path.mkdir(parents=True, exist_ok=True)

    pipes = mrsm.get_pipes(as_list=True, **kwargs)
    for pipe in pipes:
        info(f"Exporting {pipe}...")
        schema = pipe.parameters.get('schema') or 'public'
        schema_path = geojson_output_path / schema
        if not schema_path.exists():
            schema_path.mkdir(parents=True, exist_ok=True)

        pipe_output_path = schema_path / (pipe.target + '.geojson')
        df = pipe.get_data(**kwargs)
        if df is None:
            warn(f"No data returned for {pipe}.")
            continue

        try:
            json_str = to_json(df, safe_copy=False)
            with open(pipe_output_path, 'w+') as f:
                f.write(json_str)
        except Exception as e:
            return False, f"Failed to export {pipe}:\n{e}"

        mrsm.pprint((True, f"Wrote file '{pipe_output_path}'."))

    return True, "Success"
