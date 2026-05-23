import flet as ft
import flet_webview as fwv


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
    if "url" in _t:
        ALL_TOOLS_BY_ID[_t["id"]] = _t
    for _c in _t.get("children", []):
        ALL_TOOLS_BY_ID[_c["id"]] = _c

BRAND_GREEN = "#6F9920"


async def main(page: ft.Page):
    page.title = "Bike Walk Greenville"
    page.theme_mode = ft.ThemeMode.LIGHT
    page.theme = ft.Theme(color_scheme_seed=BRAND_GREEN)
    page.padding = 0
    page.bgcolor = ft.Colors.GREY_100

    url_launcher = ft.UrlLauncher()
    page.services.append(url_launcher)

    def open_tool(tool_id: str):
        tool = ALL_TOOLS_BY_ID.get(tool_id, {})
        if tool.get("external"):
            return lambda e: page.run_task(
                url_launcher.launch_url,
                tool["url"],
            )
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
            if tool is not None:
                page.views.append(build_webview_view(tool))
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
