import flet as ft

# --- TEMP on-device debug: release APKs swallow Python stdout/stderr, so trace
# execution to a file the app is allowed to write and adb shell can read. -------
_DBG_DIR = "/storage/emulated/0/Android/data/org.bikewalkgreenville.bwg_app/files"
_DBG_PATH = _DBG_DIR + "/bwg_debug.log"


def _dbg(msg):
    try:
        import os
        os.makedirs(_DBG_DIR, exist_ok=True)
        with open(_DBG_PATH, "a") as f:
            f.write(str(msg) + "\n")
    except Exception:
        pass


_dbg("=== module import start ===")


def _try_import(module_name: str):
    """Lazy/graceful extension import (mirrors mrsm.attempt_import's degrade-don't-crash
    behavior). A missing native extension becomes one disabled feature, not a full crash.
    NOTE: this cannot *install* the module — deps must be in pyproject.toml + clean rebuild.
    """
    try:
        return __import__(module_name), True
    except ImportError:
        return None, False


fwv, HAS_WEBVIEW = _try_import("flet_webview")
fm, HAS_MAP = _try_import("flet_map")
fg, HAS_GEO = _try_import("flet_geolocator")
_dbg(f"extensions: webview={HAS_WEBVIEW} map={HAS_MAP} geo={HAS_GEO}")


TOOLS = [
    {
        "id": "wotr",
        "label": "Who Owns The Roads?",
        "subtitle": "Find who maintains a road and how to report issues.",
        "icon": ft.Icons.SEARCH,
        "children": [
            {
                "id": "wotr-search",
                "label": "Search Tool",
                "icon": ft.Icons.SEARCH,
                "url": "https://bwg.mrsm.io/dash/who-owns-the-roads",
            },
            {
                "id": "wotr-map",
                "label": "Map",
                "icon": ft.Icons.MAP_OUTLINED,
                "external": True,
                "url": (
                    "https://felt.com/embed/map/"
                    "Who-Owns-Our-Roads-uyICtyogTtuqrQs1Z19AtXC"
                    "?loc=34.80697,-82.33282,12.27z"
                    "&legend=1&cooperativeGestures=1"
                    "&geolocation=1&zoomControls=1&scaleBar=1"
                ),
            },
        ],
    },
    {
        "id": "bike-parking",
        "label": "Bike Parking Map",
        "subtitle": "Find bike racks and parking near you.",
        "icon": ft.Icons.PEDAL_BIKE,
        "map": True,
        # Served by plugins/bike-parking.py (@api_plugin) on the Meerschaum API.
        "data_url": "https://bwg.mrsm.io/bike-parking/data.geojson",
        "submit_url": "https://bwg.mrsm.io/bike-parking/submit",
        "center": (34.8526, -82.3940),  # downtown Greenville
        "zoom": 13.0,
    },
    {
        "id": "mobility-map",
        "label": "Greenville Mobility Map",
        "subtitle": "Bus routes, bike lanes, sidewalks, and bike stress.",
        "icon": ft.Icons.MAP,
        "map": True,
        "center": (34.8526, -82.3940),
        "zoom": 13.0,
        # Served by plugins/map-layers.py (@api_plugin) on the Meerschaum API.
        "submit_url": "https://bwg.mrsm.io/map-layers/feedback",
        "layers": [
            {
                "id": "bike-lanes",
                "label": "Bike Lanes",
                "kind": "line",
                "data_url": "https://bwg.mrsm.io/map-layers/bike-lanes.geojson",
                "color": "#2E7D32",
                "default_on": True,
            },
            {
                "id": "srt",
                "label": "Swamp Rabbit Trail",
                "kind": "line",
                "data_url": "https://bwg.mrsm.io/map-layers/srt.geojson",
                "color": "#FF6F00",
                "default_on": True,
            },
            {
                "id": "bus-routes",
                "label": "Bus Routes",
                "kind": "line",
                "data_url": "https://bwg.mrsm.io/map-layers/bus-routes.geojson",
                "color": "#7B1FA2",
            },
            {
                "id": "bus-stops",
                "label": "Bus Stops",
                "kind": "point",
                "data_url": "https://bwg.mrsm.io/map-layers/bus-stops.geojson",
                "color": "#7B1FA2",
                "icon": ft.Icons.DIRECTIONS_BUS,
            },
            {
                "id": "sidewalks-city",
                "label": "Sidewalks (City)",
                "kind": "line",
                "data_url": "https://bwg.mrsm.io/map-layers/sidewalks-city.geojson",
                "color": "#1565C0",
            },
            {
                "id": "sidewalks-county",
                "label": "Sidewalks (County)",
                "kind": "line",
                "data_url": "https://bwg.mrsm.io/map-layers/sidewalks-county.geojson",
                "color": "#0288D1",
            },
        ],
    },
    {
        "id": "parking",
        "label": "Parking Dashboard",
        "subtitle": "Parking footprint across Greenville.",
        "icon": ft.Icons.LOCAL_PARKING,
        "url": "https://grafana.mrsm.io/d/adbvspd/parking?orgId=2&from=now-7d&to=now&timezone=browser&var-garages=$__all&kiosk",
    },
    {
        "id": "vru",
        "label": "Vulnerable Road Users Map",
        "subtitle": "Crashes involving pedestrians and cyclists.",
        "icon": ft.Icons.WARNING_AMBER_ROUNDED,
        "url": "https://grafana.mrsm.io/public-dashboards/7c91bc5e81484fef83083203543589de",
    },
]

ALL_TOOLS_BY_ID: dict = {}
for _t in TOOLS:
    if "url" in _t or _t.get("map"):
        ALL_TOOLS_BY_ID[_t["id"]] = _t
    for _c in _t.get("children", []):
        ALL_TOOLS_BY_ID[_c["id"]] = _c

BRAND_GREEN = "#6F9920"

OSM_TILES = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

# Shown until/if the real endpoint loads. Lets the screen render offline + in dev.
SAMPLE_BIKE_PARKING = [
    {"name": "Falls Park Main Entrance", "lat": 34.8443, "lon": -82.4012,
     "capacity": 8, "address": "Main St & Camperdown Way"},
    {"name": "NOMA Square", "lat": 34.8536, "lon": -82.3990,
     "capacity": 6, "address": "N Main St"},
    {"name": "Swamp Rabbit Trail – Cleveland Park", "lat": 34.8389, "lon": -82.3795,
     "capacity": 12, "address": "Woodland Way"},
    {"name": "Greenville County Library", "lat": 34.8505, "lon": -82.3995,
     "capacity": 10, "address": "25 Heritage Green Pl"},
]


def _parse_geojson(payload) -> list:
    """Tolerant: accept a FeatureCollection or a bare list of point dicts."""
    points = []
    features = payload.get("features", []) if isinstance(payload, dict) else payload
    for f in features or []:
        if "geometry" in f:  # GeoJSON Feature
            coords = (f.get("geometry") or {}).get("coordinates") or []
            if len(coords) < 2:
                continue
            lon, lat = coords[0], coords[1]  # GeoJSON is [lon, lat]
            props = f.get("properties", {}) or {}
        else:  # already flat
            lat, lon, props = f.get("lat"), f.get("lon"), f
        if lat is None or lon is None:
            continue
        points.append({
            "name": props.get("name", "Bike Parking"),
            "lat": float(lat),
            "lon": float(lon),
            "capacity": props.get("capacity"),
            "address": props.get("address", ""),
        })
    return points


async def load_bike_parking(url: str) -> list:
    """Fetch parking points; fall back to samples on any failure."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            points = _parse_geojson(resp.json())
            return points or SAMPLE_BIKE_PARKING
    except Exception:
        return SAMPLE_BIKE_PARKING


def _parse_features(payload) -> list:
    """Generalized GeoJSON parser: Point / MultiPoint / LineString /
    MultiLineString -> flat list of {"type": "point"|"line", "coords", "props"}.
    Multi* geometries are exploded into one entry per part.
    Coords stay in GeoJSON [lon, lat] order; callers swap when building markers.
    """
    feats = []
    features = payload.get("features", []) if isinstance(payload, dict) else (payload or [])
    for f in features:
        geom = f.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates")
        props = f.get("properties", {}) or {}
        if not gtype or not coords:
            continue
        if gtype == "Point":
            feats.append({"type": "point", "coords": coords, "props": props})
        elif gtype == "MultiPoint":
            feats.extend(
                {"type": "point", "coords": c, "props": props} for c in coords
            )
        elif gtype == "LineString":
            feats.append({"type": "line", "coords": coords, "props": props})
        elif gtype == "MultiLineString":
            feats.extend(
                {"type": "line", "coords": line, "props": props} for line in coords
            )
    return feats


# Dev fallback: with `adb reverse tcp:8000 tcp:8000`, a tethered phone reaches
# the local `mrsm stack` API before the endpoints are deployed to production.
PROD_API_BASE = "https://bwg.mrsm.io"
DEV_API_BASE = "http://localhost:8000"

# All map-feature + dropped-pin reports go here (plugins/map-layers.py),
# regardless of which map tool is open.
MAP_FEEDBACK_URL = PROD_API_BASE + "/map-layers/feedback"


async def load_geojson_features(url: str) -> list:
    """Fetch + parse a layer's GeoJSON; empty list on any failure."""
    import httpx
    urls = [url]
    if url.startswith(PROD_API_BASE):
        urls.append(DEV_API_BASE + url[len(PROD_API_BASE):])
    for u in urls:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.get(u)
                resp.raise_for_status()
                return _parse_features(resp.json())
        except Exception:
            continue
    return []


STRESS_LABELS = {
    "H": "High",
    "MH": "Medium-high",
    "M": "Medium",
    "ML": "Medium-low",
    "L": "Low",
}

# Zoom at/above which line layers switch from the dissolved overview to
# per-feature viewport (bbox) data with tappable details.
DETAIL_ZOOM = 13.0


BIKE_TYPE_LABELS = {
    "BIKELANE": "Bike lane",
    "GREENWAY": "Greenway",
    "SHARROW": "Sharrow",
}


def describe_feature(layer_id: str, cfg: dict, props: dict) -> tuple:
    """(title, body) shown in the detail panel for a tapped map feature."""
    street = (props.get("street_name") or "").strip().title()
    if layer_id == "bike-lanes":
        raw = props.get("bike_type") or ""
        kind = BIKE_TYPE_LABELS.get(raw, raw.replace("_", " ").title())
        return street or "Bike Lane", f"Bike infrastructure: {kind}" if kind else "Bike lane"
    if layer_id == "srt":
        segment = (props.get("segment") or "").strip()
        owner = (props.get("owner") or "").strip()
        title = f"SRT: {segment}" if segment else "Swamp Rabbit Trail"
        return title, f"Maintained by {owner}" if owner else "Swamp Rabbit Trail"
    if layer_id == "dropped-pin":
        return "Dropped Pin", props.get("coords_label", "")
    if layer_id.startswith("sidewalks"):
        parts = [p for p in (
            (props.get("material") or "").title() or None,
            f"{props['side_of_street']} side" if props.get("side_of_street") else None,
        ) if p]
        return street or "Sidewalk", " · ".join(parts) or "Sidewalk"
    if layer_id == "bike-stress":
        level = STRESS_LABELS.get(props.get("stress_level") or "", "Unknown")
        return street or "Road", f"Bike stress: {level}"
    if layer_id == "bus-routes":
        return "Bus Route", "Greenlink bus route"
    if layer_id == "bus-stops":
        return "Bus Stop", "Greenlink bus stop"
    return cfg.get("label", "Map feature"), ""


async def main(page: ft.Page):
    # TEMP crash surface: release APKs swallow Python stderr, so any exception in
    # _app() shows as a black screen. Render the traceback on-page + log it.
    _dbg("main() entered")
    try:
        await _app(page)
        _dbg("_app() returned ok")
    except Exception:
        import traceback
        tb = traceback.format_exc()
        _dbg("MAIN_ERROR\n" + tb)
        print("FLET_MAIN_ERROR\n" + tb, flush=True)
        try:
            page.views.clear()
            page.views.append(
                ft.View(
                    route="/_error",
                    scroll=ft.ScrollMode.AUTO,
                    controls=[
                        ft.Text("Startup error", size=18, color="red"),
                        ft.Text(tb, selectable=True, size=11, color="red"),
                    ],
                )
            )
            page.update()
        except Exception:
            pass


async def _app(page: ft.Page):
    page.title = "Bike Walk Greenville"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=BRAND_GREEN)
    page.padding = 0
    page.bgcolor = ft.Colors.GREY_100

    url_launcher = ft.UrlLauncher()
    page.services.append(url_launcher)

    geolocator = None
    if HAS_GEO:
        geolocator = fg.Geolocator()
        page.services.append(geolocator)

    def show_unavailable(feature: str):
        page.show_dialog(
            ft.AlertDialog(
                title=ft.Text(f"{feature} unavailable"),
                content=ft.Text(
                    f"This build is missing the {feature} component. "
                    "Please update the app to the latest version."
                ),
                actions=[
                    ft.TextButton("OK", on_click=lambda e: page.pop_dialog()),
                ],
            )
        )

    file_picker = ft.FilePicker()
    page.services.append(file_picker)

    def open_tool(tool_id: str):
        tool = ALL_TOOLS_BY_ID.get(tool_id, {})
        if tool.get("external"):
            return lambda e: page.run_task(
                url_launcher.launch_url,
                tool["url"],
            )
        if tool.get("map"):
            if not HAS_MAP:
                return lambda e: show_unavailable("Map")
            return lambda e: page.run_task(page.push_route, f"/map/{tool_id}")
        if not HAS_WEBVIEW:
            return lambda e: show_unavailable("Web view")
        return lambda e: page.run_task(page.push_route, f"/webview/{tool_id}")

    def leaf_tile(item, indent: bool = False):
        trailing_icon = (
            ft.Icons.OPEN_IN_NEW if item.get("external") else ft.Icons.CHEVRON_RIGHT
        )
        return ft.ListTile(
            leading=ft.Icon(item["icon"], size=28, color=BRAND_GREEN),
            title=ft.Text(item["label"], size=16, weight=ft.FontWeight.W_500),
            trailing=ft.Icon(trailing_icon, size=24),
            content_padding=ft.Padding.only(
                left=(32 if indent else 16), right=16,
            ),
            on_click=open_tool(item["id"]),
        )

    def build_home_view() -> ft.View:
        cards = []
        for tool in TOOLS:
            if "children" in tool:
                cards.append(
                    ft.Card(
                        elevation=2,
                        content=ft.ExpansionTile(
                            leading=ft.Icon(tool["icon"], size=32, color=BRAND_GREEN),
                            title=ft.Text(
                                tool["label"],
                                size=17,
                                weight=ft.FontWeight.W_600,
                            ),
                            subtitle=ft.Text(tool["subtitle"], size=13),
                            controls=[
                                leaf_tile(child, indent=True)
                                for child in tool["children"]
                            ],
                        ),
                    )
                )
            else:
                cards.append(
                    ft.Card(
                        elevation=2,
                        content=ft.Container(
                            content=ft.ListTile(
                                leading=ft.Icon(
                                    tool["icon"], size=32, color=BRAND_GREEN,
                                ),
                                title=ft.Text(
                                    tool["label"],
                                    size=17,
                                    weight=ft.FontWeight.W_600,
                                ),
                                subtitle=ft.Text(tool["subtitle"], size=13),
                                trailing=ft.Icon(ft.Icons.CHEVRON_RIGHT, size=24),
                                on_click=open_tool(tool["id"]),
                            ),
                            padding=8,
                        ),
                    )
                )

        return ft.View(
            route="/",
            appbar=ft.AppBar(
                title=ft.Image(
                    src="logo.png",
                    height=36,
                    fit=ft.BoxFit.CONTAIN,
                ),
                center_title=True,
                bgcolor=ft.Colors.WHITE,
                color=ft.Colors.BLACK,
                toolbar_height=64,
            ),
            bgcolor=ft.Colors.GREY_100,
            padding=0,
            controls=[
                ft.SafeArea(
                    content=ft.Column(
                        [
                            ft.Container(
                                content=ft.Text(
                                    "Tools",
                                    size=14,
                                    weight=ft.FontWeight.W_600,
                                    color=ft.Colors.GREY_700,
                                ),
                                padding=ft.Padding.only(
                                    left=16, top=16, right=16, bottom=4,
                                ),
                            ),
                            *cards,
                            ft.Container(expand=True),
                            ft.Container(
                                content=ft.Text(
                                    "bikewalkgreenville.org",
                                    size=12,
                                    color=ft.Colors.GREY_600,
                                    text_align=ft.TextAlign.CENTER,
                                ),
                                padding=12,
                                alignment=ft.Alignment.CENTER,
                            ),
                        ],
                        spacing=4,
                        expand=True,
                        scroll=ft.ScrollMode.AUTO,
                    ),
                    expand=True,
                ),
            ],
        )

    def build_map_view(tool: dict) -> ft.View:
        center = tool.get("center", (34.8526, -82.3940))
        marker_layer = fm.MarkerLayer(markers=[])
        user_layer = fm.MarkerLayer(markers=[])
        pin_layer = fm.MarkerLayer(markers=[])  # user-dropped report pin
        selected = {"pt": None}  # currently tapped feature/spot
        user_pos = {"loc": None}  # last GPS fix (lat, lon)

        # --- Toggleable overlay layers (Mobility Map) ------------------------
        # Each entry in tool["layers"] becomes a PolylineLayer (lines) or
        # MarkerLayer (points), lazily populated from its geojson on first
        # enable. Z-order: tiles -> lines -> points -> tool markers -> user.
        layer_states: dict = {}
        overlay_line_layers = []
        overlay_point_layers = []
        for _cfg in tool.get("layers", []):
            if _cfg["kind"] == "line":
                _fl = fm.PolylineLayer(polylines=[], visible=False)
                overlay_line_layers.append(_fl)
            else:
                _fl = fm.MarkerLayer(markers=[], visible=False)
                overlay_point_layers.append(_fl)
            layer_states[_cfg["id"]] = {"cfg": _cfg, "layer": _fl, "loaded": False}

        layers_loading = ft.Container(
            visible=False,
            content=ft.ProgressRing(width=22, height=22, stroke_width=3),
            bgcolor=ft.Colors.WHITE,
            border_radius=20,
            padding=8,
        )

        # Selected-feature highlights: a fat yellow underlay for lines, a
        # halo circle for point markers.
        highlight_line_layer = fm.PolylineLayer(polylines=[])
        highlight_circle_layer = fm.CircleLayer(circles=[])

        camera_state = {
            "center": (center[0], center[1]),
            "zoom": tool.get("zoom", 13.0),
        }

        the_map = fm.Map(
            expand=True,
            initial_center=fm.MapLatitudeLongitude(center[0], center[1]),
            initial_zoom=tool.get("zoom", 13.0),
            layers=[
                fm.TileLayer(
                    url_template=OSM_TILES,
                    user_agent_package_name="org.bikewalkgreenville",
                ),
                highlight_line_layer,
                *overlay_line_layers,
                *overlay_point_layers,
                highlight_circle_layer,
                marker_layer,
                pin_layer,
                user_layer,
            ],
        )

        def _layer_color(cfg: dict, props: dict) -> str:
            color_by = cfg.get("color_by")
            if color_by:
                return color_by["map"].get(
                    props.get(color_by["property"]),
                    cfg.get("color", BRAND_GREEN),
                )
            return cfg.get("color", BRAND_GREEN)

        def make_layer_marker(cfg: dict, feat: dict) -> fm.Marker:
            lon, lat = feat["coords"][0], feat["coords"][1]
            title, body = describe_feature(cfg["id"], cfg, feat["props"])
            pt = {
                "name": title,
                "body": body,
                "lat": float(lat),
                "lon": float(lon),
                "layer": cfg["id"],
                "props": feat["props"],
                "icon": cfg.get("icon", ft.Icons.PLACE),
                "color": cfg.get("color", BRAND_GREEN),
            }
            return fm.Marker(
                coordinates=fm.MapLatitudeLongitude(float(lat), float(lon)),
                width=32,
                height=32,
                content=ft.Container(
                    content=ft.Icon(
                        cfg.get("icon", ft.Icons.PLACE),
                        color=ft.Colors.WHITE,
                        size=16,
                    ),
                    bgcolor=cfg.get("color", BRAND_GREEN),
                    border_radius=16,
                    alignment=ft.Alignment.CENTER,
                    on_click=select_point(pt),
                ),
            )

        def _build_polylines(cfg: dict, feats: list) -> list:
            return [
                fm.PolylineMarker(
                    coordinates=[
                        fm.MapLatitudeLongitude(c[1], c[0])
                        for c in f["coords"]
                    ],
                    color=_layer_color(cfg, f["props"]),
                    stroke_width=3,
                )
                for f in feats
                if f["type"] == "line" and len(f["coords"]) >= 2
            ]

        async def load_layer(layer_id: str):
            st = layer_states[layer_id]
            if st["loaded"]:
                return
            layers_loading.visible = True
            page.update()
            try:
                feats = await load_geojson_features(st["cfg"]["data_url"])
                cfg = st["cfg"]
                if cfg["kind"] == "line":
                    st["overview"] = _build_polylines(cfg, feats)
                    st["layer"].polylines = st["overview"]
                    st["feats"] = []
                    st["mode"] = "overview"
                else:
                    st["layer"].markers = [
                        make_layer_marker(cfg, f)
                        for f in feats
                        if f["type"] == "point"
                    ]
                st["loaded"] = True
                if not feats:
                    _toast(f"Couldn't load {cfg['label']}.")
            finally:
                layers_loading.visible = False
                page.update()
            if st["cfg"]["kind"] == "line":
                await refresh_viewport()

        def toggle_layer(layer_id: str):
            def handler(e):
                st = layer_states[layer_id]
                st["layer"].visible = e.control.value
                page.update()
                if e.control.value and not st["loaded"]:
                    page.run_task(load_layer, layer_id)
            return handler

        def _layer_swatch(cfg: dict) -> ft.Control:
            color_by = cfg.get("color_by")
            if color_by:
                return ft.Row(
                    [
                        ft.Container(
                            width=8, height=18, bgcolor=c, border_radius=2,
                        )
                        for c in color_by["map"].values()
                    ],
                    spacing=2,
                )
            return ft.Container(
                width=18,
                height=18,
                bgcolor=cfg.get("color", BRAND_GREEN),
                border_radius=4,
            )

        def open_layers_sheet(e):
            rows = [
                ft.Text("Map layers", size=16, weight=ft.FontWeight.W_600),
            ]
            for layer_id, st in layer_states.items():
                cfg = st["cfg"]
                rows.append(
                    ft.Row(
                        [
                            _layer_swatch(cfg),
                            ft.Text(cfg["label"], size=15, expand=True),
                            ft.Switch(
                                value=st["layer"].visible,
                                on_change=toggle_layer(layer_id),
                                active_color=BRAND_GREEN,
                            ),
                        ],
                        spacing=12,
                    )
                )
                legend = (cfg.get("color_by") or {}).get("legend")
                if legend:
                    rows.append(
                        ft.Row(
                            [
                                ft.Row(
                                    [
                                        ft.Container(
                                            width=12, height=12, border_radius=2,
                                            bgcolor=cfg["color_by"]["map"][key],
                                        ),
                                        ft.Text(label, size=11,
                                                color=ft.Colors.GREY_700),
                                    ],
                                    spacing=4,
                                )
                                for key, label in legend
                            ],
                            wrap=True,
                            spacing=10,
                        )
                    )
            page.show_dialog(
                ft.BottomSheet(
                    content=ft.Container(
                        content=ft.Column(rows, tight=True, spacing=10),
                        padding=ft.Padding.only(
                            left=20, right=20, top=16, bottom=28,
                        ),
                    ),
                )
            )

        # --- Viewport (bbox) refetch: zoomed in past DETAIL_ZOOM, line layers
        # swap their dissolved overview for per-feature data with properties,
        # enabling tap-to-inspect. Zooming back out restores the overview. ----
        viewport_token = {"n": 0}

        def _viewport_bbox() -> str:
            import math
            lat, lon = camera_state["center"]
            zoom = camera_state["zoom"]
            m_per_px = 156543.03 * math.cos(math.radians(lat)) / (2 ** zoom)
            width_px = page.width or 400
            height_px = page.height or 800
            pad = 1.6  # fetch beyond the edges so small pans need no refetch
            half_lon = (width_px / 2) * m_per_px / (111320 * math.cos(math.radians(lat))) * pad
            half_lat = (height_px / 2) * m_per_px / 111320 * pad
            return f"{lon - half_lon},{lat - half_lat},{lon + half_lon},{lat + half_lat}"

        async def refresh_viewport():
            zoom = camera_state["zoom"]
            if zoom < DETAIL_ZOOM:
                changed = False
                for st in layer_states.values():
                    if st["cfg"]["kind"] == "line" and st.get("mode") == "detail":
                        st["layer"].polylines = st.get("overview") or []
                        st["feats"] = []
                        st["mode"] = "overview"
                        changed = True
                if changed:
                    page.update()
                return
            bbox = _viewport_bbox()
            for layer_id, st in layer_states.items():
                cfg = st["cfg"]
                if cfg["kind"] != "line" or not st["layer"].visible or not st["loaded"]:
                    continue
                url = f'{cfg["data_url"]}?bbox={bbox}&zoom={int(zoom)}'
                feats = await load_geojson_features(url)
                if not feats:
                    continue
                st["feats"] = feats
                st["mode"] = "detail"
                st["layer"].polylines = _build_polylines(cfg, feats)
            page.update()

        def on_position_change(e):
            try:
                camera_state["center"] = (
                    e.camera.center.latitude, e.camera.center.longitude,
                )
                camera_state["zoom"] = e.camera.zoom
            except Exception:
                return
            viewport_token["n"] += 1
            token = viewport_token["n"]

            async def _debounced():
                import asyncio
                await asyncio.sleep(0.7)
                if viewport_token["n"] != token:
                    return
                await refresh_viewport()

            page.run_task(_debounced)

        # --- Tap-to-inspect: nearest visible line feature within ~20 px. -----
        def _segment_dist_sq(px, py, ax, ay, bx, by):
            dx, dy = bx - ax, by - ay
            if dx == 0 and dy == 0:
                return (px - ax) ** 2 + (py - ay) ** 2
            t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
            t = max(0.0, min(1.0, t))
            qx, qy = ax + t * dx, ay + t * dy
            return (px - qx) ** 2 + (py - qy) ** 2

        def _feature_dist_m(lat, lon, coords, coslat):
            # Equirectangular approx in degree space, scaled to meters.
            # Index access: coords may be [lon, lat] or [lon, lat, z].
            px, py = lon * coslat, lat
            best = None
            for a, b in zip(coords, coords[1:]):
                d = _segment_dist_sq(
                    px, py, a[0] * coslat, a[1], b[0] * coslat, b[1],
                )
                if best is None or d < best:
                    best = d
            return None if best is None else (best ** 0.5) * 111320

        def clear_highlight():
            highlight_line_layer.polylines = []
            highlight_circle_layer.circles = []

        def select_layer_feature(layer_id: str, feat: dict, lat: float, lon: float):
            cfg = layer_states[layer_id]["cfg"]
            title, body = describe_feature(layer_id, cfg, feat["props"])
            clear_highlight()
            highlight_line_layer.polylines = [
                fm.PolylineMarker(
                    coordinates=[
                        fm.MapLatitudeLongitude(c[1], c[0])
                        for c in feat["coords"]
                    ],
                    color="#FFD600",
                    stroke_width=9,
                )
            ]
            selected["pt"] = {
                "name": title,
                "body": body,
                "lat": lat,
                "lon": lon,
                "layer": layer_id,
                "props": feat["props"],
            }
            detail_icon.icon = cfg.get("icon", ft.Icons.ROUTE)
            detail_title.value = title
            detail_body.value = body
            detail_panel.visible = True
            page.update()

        def on_map_tap(e):
            import math
            try:
                lat = e.coordinates.latitude
                lon = e.coordinates.longitude
            except Exception:
                return
            coslat = math.cos(math.radians(lat))
            m_per_px = (
                156543.03 * coslat / (2 ** camera_state["zoom"])
            )
            threshold_m = 20 * m_per_px
            best = None  # (dist_m, layer_id, feat)
            for layer_id, st in layer_states.items():
                if st["cfg"]["kind"] != "line" or not st["layer"].visible:
                    continue
                for f in st.get("feats") or []:
                    if f["type"] != "line":
                        continue
                    d = _feature_dist_m(lat, lon, f["coords"], coslat)
                    if d is not None and d <= threshold_m and (
                        best is None or d < best[0]
                    ):
                        best = (d, layer_id, f)
            if best:
                select_layer_feature(best[1], best[2], lat, lon)
            else:
                clear_highlight()
                detail_panel.visible = False
                page.update()

        # --- Drop-a-pin reporting: long-press anywhere, or the "Report here"
        # button (uses the GPS fix, falling back to the map center). ----------
        def drop_pin(lat: float, lon: float, open_dialog: bool = False):
            pt = {
                "name": "Dropped Pin",
                "body": f"{lat:.5f}, {lon:.5f}",
                "lat": lat,
                "lon": lon,
                "layer": "dropped-pin",
                "props": {"coords_label": f"{lat:.5f}, {lon:.5f}"},
                "icon": ft.Icons.PLACE,
            }
            pin_layer.markers = [
                fm.Marker(
                    coordinates=fm.MapLatitudeLongitude(lat, lon),
                    width=44,
                    height=44,
                    content=ft.Icon(
                        ft.Icons.PLACE, color=ft.Colors.RED_700, size=44,
                    ),
                )
            ]
            selected["pt"] = pt
            detail_icon.icon = ft.Icons.PLACE
            detail_title.value = pt["name"]
            detail_body.value = pt["body"]
            clear_highlight()
            detail_panel.visible = True
            page.update()
            if open_dialog:
                open_report_dialog()

        def on_map_long_press(e):
            try:
                drop_pin(e.coordinates.latitude, e.coordinates.longitude)
            except Exception:
                pass

        async def report_here():
            lat, lon = camera_state["center"]
            if geolocator is not None:
                try:
                    await geolocator.request_permission()
                    pos = await geolocator.get_current_position()
                    if pos:
                        lat, lon = pos.latitude, pos.longitude
                        user_pos["loc"] = (lat, lon)
                        try:
                            await the_map.center_on(
                                fm.MapLatitudeLongitude(lat, lon), zoom=16,
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
            drop_pin(lat, lon, open_dialog=True)

        the_map.on_position_change = on_position_change
        the_map.on_tap = on_map_tap
        the_map.on_long_press = on_map_long_press

        # Layers marked default_on load immediately.
        for _layer_id, _st in layer_states.items():
            if _st["cfg"].get("default_on"):
                _st["layer"].visible = True
                page.run_task(load_layer, _layer_id)

        # --- Bottom detail panel, hidden until a marker is tapped. ----------
        detail_title = ft.Text(weight=ft.FontWeight.W_600, size=16)
        detail_body = ft.Text(size=13, color=ft.Colors.GREY_700)
        detail_icon = ft.Icon(ft.Icons.PEDAL_BIKE, color=BRAND_GREEN, size=32)

        def _hide_detail():
            detail_panel.visible = False
            clear_highlight()
            pin_layer.markers = []
            page.update()

        def directions_click(e):
            pt = selected["pt"]
            if not pt:
                return
            url = (
                "https://www.google.com/maps/dir/?api=1"
                f"&destination={pt['lat']},{pt['lon']}"
                "&travelmode=bicycling"
            )
            page.run_task(url_launcher.launch_url, url)

        detail_panel = ft.Container(
            visible=False,
            bgcolor=ft.Colors.WHITE,
            padding=16,
            content=ft.Column(
                [
                    ft.Row(
                        [
                            detail_icon,
                            ft.Column(
                                [detail_title, detail_body], spacing=2, expand=True,
                            ),
                            ft.IconButton(
                                icon=ft.Icons.CLOSE,
                                on_click=lambda e: _hide_detail(),
                            ),
                        ],
                        spacing=12,
                    ),
                    ft.Row(
                        [
                            ft.FilledButton(
                                "Directions",
                                icon=ft.Icons.DIRECTIONS_BIKE,
                                on_click=directions_click,
                                style=ft.ButtonStyle(
                                    bgcolor=BRAND_GREEN, color=ft.Colors.WHITE,
                                ),
                            ),
                            ft.OutlinedButton(
                                "Report / Photo",
                                icon=ft.Icons.ADD_A_PHOTO_OUTLINED,
                                on_click=lambda e: open_report_dialog(),
                                visible=bool(tool.get("submit_url")),
                            ),
                        ],
                        spacing=8,
                    ),
                ],
                spacing=8,
                tight=True,
            ),
        )

        def select_point(pt: dict):
            def handler(e):
                selected["pt"] = pt
                detail_title.value = pt["name"]
                if pt.get("body"):
                    detail_body.value = pt["body"]
                else:
                    cap = pt.get("capacity")
                    parts = [p for p in (
                        pt.get("address"),
                        f"{cap} spaces" if cap else None,
                    ) if p]
                    detail_body.value = " · ".join(parts) or "Bike parking"
                detail_icon.icon = pt.get("icon", ft.Icons.PEDAL_BIKE)
                clear_highlight()
                highlight_circle_layer.circles = [
                    fm.CircleMarker(
                        radius=26,
                        coordinates=fm.MapLatitudeLongitude(pt["lat"], pt["lon"]),
                        color="#66FFD600",
                        border_color="#FFD600",
                        border_stroke_width=3,
                    )
                ]
                detail_panel.visible = True
                page.update()
            return handler

        def make_marker(pt: dict) -> fm.Marker:
            return fm.Marker(
                coordinates=fm.MapLatitudeLongitude(pt["lat"], pt["lon"]),
                width=40,
                height=40,
                content=ft.Container(
                    content=ft.Icon(
                        ft.Icons.PEDAL_BIKE, color=ft.Colors.WHITE, size=20,
                    ),
                    bgcolor=BRAND_GREEN,
                    border_radius=20,
                    alignment=ft.Alignment.CENTER,
                    on_click=select_point(pt),
                ),
            )

        async def populate():
            points = await load_bike_parking(tool["data_url"])
            marker_layer.markers = [make_marker(p) for p in points]
            page.update()

        if tool.get("data_url"):
            page.run_task(populate)

        # --- Current location + recenter ------------------------------------
        async def locate_me(silent: bool = False):
            if geolocator is None:
                if not silent:
                    _toast("Location is unavailable in this build.")
                return
            try:
                await geolocator.request_permission()
                pos = await geolocator.get_current_position()
            except Exception:
                if not silent:
                    _toast("Couldn't get your location.")
                return
            if not pos:
                return
            user_pos["loc"] = (pos.latitude, pos.longitude)
            loc = fm.MapLatitudeLongitude(pos.latitude, pos.longitude)
            user_layer.markers = [
                fm.Marker(
                    coordinates=loc,
                    width=28,
                    height=28,
                    content=ft.Icon(
                        ft.Icons.MY_LOCATION, color=ft.Colors.BLUE, size=22,
                    ),
                )
            ]
            page.update()
            try:
                await the_map.center_on(loc, zoom=15)
            except Exception:
                pass

        # Locate on open (silent: no error dialogs if denied/unavailable).
        page.run_task(locate_me, True)

        # --- Photo + feedback submission ------------------------------------
        def open_report_dialog():
            pt = selected["pt"]
            is_layer_feature = bool((pt or {}).get("layer"))
            feedback_field = ft.TextField(
                label=(
                    "What's the issue here?"
                    if is_layer_feature
                    else "What's up with this spot?"
                ),
                hint_text=(
                    "e.g. blocked / damaged / unsafe / needs repair"
                    if is_layer_feature
                    else "e.g. rack is full / damaged / great spot"
                ),
                multiline=True,
                min_lines=2,
                max_lines=4,
            )
            photo_state = {"path": None}
            photo_status = ft.Text(
                "No photo attached", size=12, color=ft.Colors.GREY_600,
            )
            error_text = ft.Text("", size=12, color=ft.Colors.RED)

            async def pick_photo(e):
                try:
                    files = await file_picker.pick_files(
                        dialog_title="Choose a photo",
                        file_type=ft.FilePickerFileType.IMAGE,
                    )
                except Exception:
                    files = None
                if files:
                    photo_state["path"] = files[0].path
                    name = (files[0].path or "photo").split("/")[-1]
                    photo_status.value = f"Attached: {name}"
                    photo_status.color = BRAND_GREEN
                    page.update()

            async def submit(e):
                import os
                import json as _json
                import httpx
                if (pt or {}).get("layer"):
                    # Map-feature feedback -> plugins/map-layers.py
                    data = {
                        "layer": pt["layer"],
                        "name": pt.get("name", ""),
                        "lat": str(pt.get("lat", "")),
                        "lon": str(pt.get("lon", "")),
                        "props": _json.dumps(pt.get("props") or {}),
                        "feedback": feedback_field.value or "",
                    }
                else:
                    # Bike-parking feedback -> plugins/bike-parking.py
                    data = {
                        "spot_name": (pt or {}).get("name", ""),
                        "lat": str((pt or {}).get("lat", "")),
                        "lon": str((pt or {}).get("lon", "")),
                        "feedback": feedback_field.value or "",
                    }
                files = None
                path = photo_state["path"]
                if path and os.path.exists(path):
                    with open(path, "rb") as fh:
                        files = {"photo": (os.path.basename(path), fh.read())}
                submit_url = (
                    MAP_FEEDBACK_URL
                    if (pt or {}).get("layer")
                    else tool["submit_url"]
                )
                urls = [submit_url]
                if submit_url.startswith(PROD_API_BASE):
                    urls.append(DEV_API_BASE + submit_url[len(PROD_API_BASE):])
                last_error = None
                for u in urls:
                    try:
                        async with httpx.AsyncClient(timeout=30) as client:
                            resp = await client.post(u, data=data, files=files)
                            resp.raise_for_status()
                        last_error = None
                        break
                    except Exception as ex:
                        last_error = ex
                if last_error is not None:
                    error_text.value = f"Submit failed: {last_error}"
                    page.update()
                    return
                page.pop_dialog()
                _toast("Thanks! Your report was submitted.")

            dialog = ft.AlertDialog(
                modal=True,
                title=ft.Text(
                    f"Report: {pt['name']}" if pt else "Report bike parking",
                ),
                content=ft.Column(
                    [
                        feedback_field,
                        ft.OutlinedButton(
                            "Attach photo",
                            icon=ft.Icons.ADD_A_PHOTO_OUTLINED,
                            on_click=pick_photo,
                        ),
                        photo_status,
                        error_text,
                    ],
                    tight=True,
                    spacing=10,
                    width=320,
                ),
                actions=[
                    ft.TextButton("Cancel", on_click=lambda e: page.pop_dialog()),
                    ft.FilledButton(
                        "Submit",
                        on_click=submit,
                        style=ft.ButtonStyle(
                            bgcolor=BRAND_GREEN, color=ft.Colors.WHITE,
                        ),
                    ),
                ],
            )
            page.show_dialog(dialog)

        def _toast(msg: str):
            page.show_dialog(
                ft.AlertDialog(
                    title=ft.Text(msg),
                    actions=[
                        ft.TextButton("OK", on_click=lambda e: page.pop_dialog()),
                    ],
                )
            )

        return ft.View(
            route=f"/map/{tool['id']}",
            appbar=ft.AppBar(
                leading=ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: page.run_task(page.push_route, "/"),
                    icon_color=ft.Colors.WHITE,
                ),
                title=ft.Text(tool["label"], weight=ft.FontWeight.W_600),
                center_title=False,
                bgcolor=BRAND_GREEN,
                color=ft.Colors.WHITE,
            ),
            padding=0,
            controls=[
                ft.Stack(
                    [
                        the_map,
                        ft.Container(
                            content=ft.FloatingActionButton(
                                icon=ft.Icons.MY_LOCATION,
                                on_click=lambda e: page.run_task(locate_me),
                                bgcolor=ft.Colors.WHITE,
                                foreground_color=BRAND_GREEN,
                                mini=True,
                            ),
                            right=16,
                            bottom=110,
                        ),
                        ft.Container(
                            content=ft.FloatingActionButton(
                                icon=ft.Icons.EXPLORE,
                                tooltip="Orient to north",
                                on_click=lambda e: page.run_task(
                                    the_map.reset_rotation,
                                ),
                                bgcolor=ft.Colors.WHITE,
                                foreground_color=BRAND_GREEN,
                                mini=True,
                            ),
                            right=16,
                            bottom=210 if layer_states else 160,
                        ),
                        ft.Container(
                            content=ft.FloatingActionButton(
                                icon=ft.Icons.ADD_LOCATION_ALT,
                                tooltip="Drop a pin and report an issue here",
                                on_click=lambda e: page.run_task(report_here),
                                bgcolor=ft.Colors.RED_700,
                                foreground_color=ft.Colors.WHITE,
                            ),
                            left=16,
                            bottom=110,
                        ),
                        *(
                            [
                                ft.Container(
                                    content=ft.FloatingActionButton(
                                        icon=ft.Icons.LAYERS,
                                        on_click=open_layers_sheet,
                                        bgcolor=ft.Colors.WHITE,
                                        foreground_color=BRAND_GREEN,
                                        mini=True,
                                    ),
                                    right=16,
                                    bottom=160,
                                ),
                                ft.Container(
                                    content=layers_loading,
                                    left=16,
                                    top=16,
                                ),
                            ]
                            if layer_states
                            else []
                        ),
                        ft.Container(
                            content=detail_panel,
                            bottom=0,
                            left=0,
                            right=0,
                        ),
                    ],
                    expand=True,
                ),
            ],
        )

    def build_webview_view(tool: dict) -> ft.View:
        webview = fwv.WebView(url=tool["url"], expand=True)

        async def enable_js():
            try:
                await webview.set_javascript_mode(fwv.JavaScriptMode.UNRESTRICTED)
            except Exception:
                pass

        page.run_task(enable_js)

        return ft.View(
            route=f"/webview/{tool['id']}",
            appbar=ft.AppBar(
                leading=ft.IconButton(
                    icon=ft.Icons.ARROW_BACK,
                    on_click=lambda e: page.run_task(page.push_route, "/"),
                    icon_color=ft.Colors.WHITE,
                ),
                title=ft.Text(tool["label"], weight=ft.FontWeight.W_600),
                center_title=False,
                bgcolor=BRAND_GREEN,
                color=ft.Colors.WHITE,
            ),
            padding=0,
            controls=[webview],
        )

    def route_change(*args):
        page.views.clear()
        page.views.append(build_home_view())
        route = page.route or "/"
        if route.startswith("/webview/"):
            tool_id = route.split("/webview/", 1)[1]
            tool = ALL_TOOLS_BY_ID.get(tool_id)
            if tool is not None and HAS_WEBVIEW:
                page.views.append(build_webview_view(tool))
        elif route.startswith("/map/"):
            tool_id = route.split("/map/", 1)[1]
            tool = ALL_TOOLS_BY_ID.get(tool_id)
            if tool is not None and HAS_MAP:
                page.views.append(build_map_view(tool))
        page.update()

    async def view_pop(e):
        if len(page.views) > 1:
            page.views.pop()
            top = page.views[-1]
            await page.push_route(top.route or "/")

    page.on_route_change = route_change
    page.on_view_pop = view_pop
    route_change()


_dbg("calling ft.run(main)")
ft.run(main)
