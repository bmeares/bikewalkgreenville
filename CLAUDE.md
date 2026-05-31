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

### Bump the version EVERY release (or in-place updates silently no-op)

Android compares `versionCode` to decide whether an installed APK gets replaced. Flet derives:
- `versionName` ← `[project] version` in `pyproject.toml`
- `versionCode` ← `--build-number` (default `1`)

If neither changes between builds, sideloading/`adb install -r` over an existing install is a **no-op** — Android keeps the old `libapp.so`, so new code/deps never land and you're forced to uninstall + reinstall (this is exactly what bit the `flet_map` fix). Always increase BOTH each release:

```bash
cd bwg_app
# bump [project] version in pyproject.toml first (e.g. 0.1.1 → 0.1.2), then:
uv run flet build apk --build-number 3 --build-version 0.1.2
```

`--build-number` must strictly increase every release; it is the integer Android actually compares.

### Defensive extension imports (graceful degradation)

`src/main.py` imports `flet_webview` / `flet_map` / `flet_geolocator` via a `_try_import()` helper (mirrors the degrade-don't-crash half of `mrsm.attempt_import`) into `HAS_WEBVIEW` / `HAS_MAP` / `HAS_GEO` flags. A missing native extension now disables just that one tool (shows an "unavailable" dialog) instead of crashing the whole app to a traceback on line 3. This does **not** install or fix the extension — deps must still be in `pyproject.toml` + a clean rebuild (see below); it only prevents a hard crash. When adding a new extension, import it through `_try_import()` and gate its entry point on the flag.

### Flet extension dependencies (avoid the dependency traps)

Native controls beyond core Flet ship as **separate extension packages** that bundle both a Python module and a Flutter plugin: `flet-webview` (webview), `flet-map` (native `flutter_map`), `flet-geolocator` (GPS). Rules learned the hard way:

- **Pin every extension to the exact same version as `flet`.** They release in lockstep; mismatched versions fail to resolve. Add with `uv add flet-map==<flet-version>` (e.g. `==0.80.5`).
- **Only `import` an extension that is in `[project] dependencies`.** Importing one that isn't bundled (it lives only in your dev venv) tracebacks on device as `No module named flet_map` / `flet_webview` / etc. Adding the import and the dep must happen together.
- **After adding/changing any extension dep, do a CLEAN rebuild.** Stale `build/` artifacts cause `version solving failed ... <pkg> from path which doesn't exist (build/flutter-packages/<pkg>)`:

  ```bash
  cd bwg_app
  rm -rf build          # full clean — regenerates build/flutter-packages from scratch
  uv run flet build apk
  ```

  Do **not** rely on `flet build apk --clear-cache` for this — it can empty `build/flutter-packages/` without repopulating it, deadlocking the next resolve. `rm -rf build` is the reliable fix.
- **`No module named <ext>` on device** almost always means the APK was built before the dep was added (stale apk) → `rm -rf build` and rebuild.
- A build that prints `Building .apk for Android...` has already passed Python packaging/resolution — extension deps are fine at that point; any later failure is Flutter/Gradle, not deps.

### App structure

- `src/main.py` — Flet entry. Route-based: a launcher home view, `/webview/<id>` (system webview over `bwg.mrsm.io/dash/...` dashboards via `flet_webview`), and `/map/<id>` (native `flet_map` — e.g. the Bike Parking map, with `flet_geolocator` for current location and a photo+feedback report that POSTs to `plugins/bike-parking.py`). All three extensions are in `pyproject.toml` deps; see "Flet extension dependencies" above before adding more.
- `src/assets/` — `icon.png`, `splash_android.png` (Flet picks these up automatically).
- `pyproject.toml` `[tool.flet]` table — org/product/copyright. Bumping the version requires editing here.

## Cross-cutting

- All dashboards consumed by `bwg_app` live under `bwg.mrsm.io/dash/<plugin>`, served by the Meerschaum Web container from this repo's plugins. Adding a new screen to the app generally means: write a `@dash_plugin` in `plugins/`, deploy the stack, then point the app at the URL.
- `.env` at root holds the `sql:bwg` connection string (`MRSM_SQL_BWG`). Do not commit.
