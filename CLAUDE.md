# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Two subsystems

This repo contains two unrelated codebases that share data:

1. **Meerschaum data pipeline** (repo root) — ETL + dashboards for Greenville traffic, road ownership, collisions, etc. Python plugins + YAML compose files.
2. **`bwg_app/`** — Flet (Python) mobile app that surfaces the dashboards on Android/iOS.

They communicate only via the public dashboards at `bwg.mrsm.io`.

## Meerschaum pipeline

Stack runs in Docker. Bring it up:

```bash
mrsm stack up -d
```

Services: TimescaleDB (Postgres+time-series), Grafana (`localhost:3000`, admin/admin), Meerschaum Web (`localhost:8000`, dashboards live under `/dash/<plugin>`).

### Layout

- `mrsm-compose.yaml` — root compose; defines pipes shared across the project.
- `projects/*.yaml` — per-domain compose files (e.g. `who-owns-the-roads.yaml`, `parking.yaml`, `srt.yaml`). Each has its own pipes but reuses `plugins/` and `root_dir: ../root`.
- `plugins/*.py` — Meerschaum plugins. Two flavors:
  - **Connectors** (declare a `fetch()` or SQL): pull/transform data into pipes.
  - **Web pages** (`@web_page` / `@dash_plugin`): mount Dash apps under the Meerschaum web UI. `who-owns-the-roads.py` is the canonical example.
- `plugins/streetlights/` — package-style plugin (CSV ingest, requires CSVs dropped into `plugins/streetlights/data/`).
- `data/`, `output/`, `root/`, `cache/` — generated; gitignored. `output/boundaries.gpkg` is referenced as the `sql:boundaries` geopackage connector.
- `gis.db` — local SQLite cache.

### Pipe model

Pipes are addressed by `(connector, metric, location)`. They reference each other in SQL via Jinja: `{{Pipe('plugin:scdps', 'collisions', 'sc')}}`. `sql:bwg` is the main DB connector; pipes targeting `sql:bwg` write into Postgres schemas like `Roads`, `Boundaries`, `Parking`.

### Common workflow

```bash
# Dry-run register all pipes in the current compose file
mrsm compose up --dry

# Run a specific compose file (per-project)
mrsm compose -f projects/who-owns-the-roads.yaml up --dry

# Sync (ingest + transform) by tag or pipe key
mrsm compose sync pipes -t streetlights
mrsm compose sync pipes -c plugin:scdps -m collisions

# Show pipes
mrsm compose show pipes
```

## bwg_app (Flet Android app)

### Run desktop dev

```bash
cd bwg_app
uv run flet run            # desktop
uv run flet run --web      # browser
```

### Build APK

Requires Flutter, Android SDK 36 + build-tools 36, NDK 27.0.12077973, JDK 21. Env (already persisted in `~/.bashrc`):

```bash
JAVA_HOME=/usr/lib/jvm/java-21-temurin-jdk
ANDROID_HOME=$HOME/Android/Sdk
PATH=$HOME/flutter/3.38.7/bin:$ANDROID_HOME/cmdline-tools/latest/bin:$ANDROID_HOME/platform-tools:$PATH
```

Then:

```bash
cd bwg_app
uv run flet build apk
# → build/apk/app-release.apk
```

### App structure

- `src/main.py` — Flet entry. Currently a launcher: `ft.UrlLauncher.launch_url(..., mode=IN_APP_WEB_VIEW)` opens dashboards (e.g. `https://bwg.mrsm.io/dash/who-owns-the-roads`) inside a system webview. Do **not** `import flet_webview` unless added to `pyproject.toml` deps — it is not bundled and will traceback on device.
- `src/assets/` — `icon.png`, `splash_android.png` (Flet picks these up automatically).
- `pyproject.toml` `[tool.flet]` table — org/product/copyright. Bumping the version requires editing here.

## Cross-cutting

- All dashboards consumed by `bwg_app` live under `bwg.mrsm.io/dash/<plugin>`, served by the Meerschaum Web container from this repo's plugins. Adding a new screen to the app generally means: write a `@dash_plugin` in `plugins/`, deploy the stack, then point the app at the URL.
- `.env` at root holds the `sql:bwg` connection string (`MRSM_SQL_BWG`). Do not commit.
