import flet as ft


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


async def main(page: ft.Page):
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
        selected = {"pt": None}  # currently tapped parking spot

        the_map = fm.Map(
            expand=True,
            initial_center=fm.MapLatitudeLongitude(center[0], center[1]),
            initial_zoom=tool.get("zoom", 13.0),
            layers=[
                fm.TileLayer(
                    url_template=OSM_TILES,
                    user_agent_package_name="org.bikewalkgreenville",
                ),
                marker_layer,
                user_layer,
            ],
        )

        # --- Bottom detail panel, hidden until a marker is tapped. ----------
        detail_title = ft.Text(weight=ft.FontWeight.W_600, size=16)
        detail_body = ft.Text(size=13, color=ft.Colors.GREY_700)

        def _hide_detail():
            detail_panel.visible = False
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
                            ft.Icon(ft.Icons.PEDAL_BIKE, color=BRAND_GREEN, size=32),
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
                cap = pt.get("capacity")
                parts = [p for p in (
                    pt.get("address"),
                    f"{cap} spaces" if cap else None,
                ) if p]
                detail_body.value = " · ".join(parts) or "Bike parking"
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

        page.run_task(populate)

        # --- Current location + recenter ------------------------------------
        async def locate_me():
            if geolocator is None:
                _toast("Location is unavailable in this build.")
                return
            try:
                await geolocator.request_permission()
                pos = await geolocator.get_current_position()
            except Exception:
                _toast("Couldn't get your location.")
                return
            if not pos:
                return
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

        # --- Photo + feedback submission ------------------------------------
        def open_report_dialog():
            pt = selected["pt"]
            feedback_field = ft.TextField(
                label="What's up with this spot?",
                hint_text="e.g. rack is full / damaged / great spot",
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
                import httpx
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
                try:
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.post(
                            tool["submit_url"], data=data, files=files,
                        )
                        resp.raise_for_status()
                except Exception as ex:
                    error_text.value = f"Submit failed: {ex}"
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


ft.run(main)
