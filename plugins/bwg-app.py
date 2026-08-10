#! /usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Serve the BikeWalk Greenville Flutter web app.

The compiled web bundle (`app-native/build/web`, built with
`flutter build web --release --base-href /bwg-app/`) is copied into the
Meerschaum root directory as `bwg-app-web/` and mounted at `/bwg-app/`.

Deploy (from the repo root, after building):

    rsync -e 'ssh -p 2269' -a --delete app-native/build/web/ meerschaum@mrsm.io:/tmp/bwg-app-web/
    ssh -p 2269 meerschaum@mrsm.io \
      'docker cp /tmp/bwg-app-web mrsm-api-bwg-1:/meerschaum/ && docker restart mrsm-api-bwg-1'

A thin Dash page at `/dash/bwg-app` iframes the app so it can be embedded
from bikewalkgreenville.org exactly like Who Owns The Roads.
"""

import meerschaum as mrsm
from meerschaum.plugins import api_plugin, web_page, dash_plugin

__version__ = '0.1.0'


@api_plugin
def init_app(app):
    """Mount the Flutter web bundle as static files at `/bwg-app/`."""
    from fastapi.staticfiles import StaticFiles
    from meerschaum.config.paths import ROOT_DIR_PATH

    web_dir = ROOT_DIR_PATH / 'bwg-app-web'
    if web_dir.is_dir():
        app.mount(
            '/bwg-app',
            StaticFiles(directory=web_dir.as_posix(), html=True),
            name='bwg-app',
        )


@dash_plugin
def init_dash(dash_app):
    """Add `/dash/bwg-app`: a full-viewport iframe over the static bundle."""
    import dash.html as html

    @web_page(
        'bwg-app',
        login_required=False,
        skip_navbar=True,
        page_group="Bike Walk Greenville",
    )
    def page_layout():
        return [
            html.Iframe(
                src='/bwg-app/',
                allow='geolocation; camera',
                style={
                    'position': 'fixed',
                    'top': 0,
                    'left': 0,
                    'width': '100vw',
                    'height': '100vh',
                    'border': 'none',
                },
            ),
        ]
