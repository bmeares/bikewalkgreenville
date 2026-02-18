import flet as ft
import flet_webview as fwv


async def main(page: ft.Page):
    url_launcher = ft.UrlLauncher()

    async def launch_search_tool():
        await url_launcher.launch_url(
            "https://bwg.mrsm.io/dash/who-owns-the-roads",
            mode=ft.LaunchMode.IN_APP_WEB_VIEW,
            web_view_configuration=ft.WebViewConfiguration(
                enable_javascript=True,
                enable_dom_storage=True,
            )
        )

    page.add(
        ft.Column([
            ft.Row([
                ft.Button(
                    "Open Search Tool",
                    on_click=launch_search_tool,
                ),
            ], wrap=True),
        ])
    )
    #  page.add(
        #  fwv.WebView(
            #  url="https://bikewalkgreenville.org/roads",
            #  on_page_started=lambda _: print("Page started"),
            #  on_page_ended=lambda _: print("Page ended"),
            #  on_web_resource_error=lambda e: print("WebView error:", e.data),
            #  expand=True,
        #  )
    #  )

ft.run(main)
