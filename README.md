# SRT Streetlights Traffic Data
This Meerschaum project builds several tables from the Streetlights SRT data for Bike Walk Greenville.

## Setup

You will need [Meerschaum](https://meerschaum.io) and [Meerschaum Compose](https://meerschaum.io/reference/compose/) installed for this project:

```bash
python -m pip install --upgrade meerschaum
mrsm install plugin compose
```

Then bring up the default stack:

```bash
mrsm stack up -d
```

Inside the `plugins/traffic` directory, create a folder `data` and drop in the Streetlights CSV files.

## Ingest the Data

Sync the pipes to ingest and aggregate the data:

```bash
mrsm compose up --dry
mrsm compose sync pipes -t streetlights
```

## Stack

The [default Meerschaum stack](https://meerschaum.io/reference/stack/) should have started three services:

- **TimescaleDB**  
  A time-series-optimized derivative of PostgreSQL.

- **Grafana**  
  A popular web-based BI visualization platform. Navigate to http://localhost:3000 and log in with `admin`, `admin`.

- **Meerschaum Web**  
  An API and dashboard web server to manage your pipes. Navigate to http://localhost:8000 and create a local account for the dashboard, and navigate to http://localhost:8000/docs for the API specification.