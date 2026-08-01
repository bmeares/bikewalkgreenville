# E-bikes, stress tolerance, and hill-aware routing

Design approved 2026-08-01. Implements two rider-facing sub-options for biking
(e-bike, acceptable stress level) plus terrain awareness for every human-powered
mode.

## Motivation

`/map-layers/route` costs one bike profile for everyone: a fixed 9.4 mph and a
fixed stress penalty table. Two riders it serves badly are the one on an e-bike
(faster, and less bothered by traffic and hills) and the one who will happily
ride an extra mile to stay off Wade Hampton. Neither can say so today.

Terrain is missing entirely, which matters most for the mode that already gets
the most care: someone rolling a manual wheelchair up a 10% grade.

## 1. Stress tolerance — three presets, soft weights

A tolerance re-weights the existing penalties; it never removes an edge. The
router always returns something, and stretches above the chosen tolerance stay
disclosed through the existing `warnings[]` machinery.

| category | quiet | **balanced** (today) | direct |
|---|---|---|---|
| srt | 0.35 | **0.4** | 0.5 |
| bike-lane | 0.35 | **0.4** | 0.6 |
| L | 1.0 | **1.0** | 1.0 |
| ML | 2.0 | **1.3** | 1.1 |
| M | 8.0 | **2.5** | 1.4 |
| MH | 20.0 | **6.0** | 2.0 |
| H | 40.0 | **12.0** | 3.0 |
| default | 8.0 | **2.5** | 1.4 |

`balanced` reproduces today's numbers exactly, so a request that omits the
parameter is byte-identical to the current response.

`direct` deliberately *raises* the trail/lane discount. "Shortest ride" should
not detour half a mile to pick up a bike lane.

Warning thresholds follow the tolerance, because a rider who asked for direct
does not want every medium-stress block flagged:

| tolerance | flagged as `no_bike_lane` |
|---|---|
| quiet | ML, M, MH, H |
| balanced | M, MH, H (today) |
| direct | MH, H |

## 2. E-bike

Average speed 4.2 → 6.7 m/s (9.4 → 15 mph), which is a realistic trip average
for a Class 1/2 e-bike capped at 20 mph. Speed feeds `duration_min`, and through
it which multi-modal itinerary wins.

E-bikes do **not** get their own stress table. Whether traffic is tolerable is
the rider's call, and they have a control for it.

## 3. Hills

### Data

`county."TOP_CONTOUR"` — 799,536 contour lines, **4 ft interval**, SRID 6570,
elevations 560–3352 ft, GiST index `IX_TOP_CONTOUR_geometry` already present.
Registered as a pipe in `projects/county.yaml`, so no new ETL. Nearest-contour
lookup is accurate to ±2 ft, better than a public DEM.

Measured: 3,000 nearest-contour lookups in 858 ms → ~11 s for the graph's 37.7k
nodes. Graph build goes ~6 s → ~17 s. It is cached 24 h and warmed in a
background thread at API start, so no request pays for it.

### Model

Elevation is stored per node (`graph['elev']: cell → feet`), not per edge.
`_astar` already holds both `cur` and `nbr` in its relaxation loop, so climb
costs need no change to the edge tuple and nothing downstream has to be
re-indexed.

```
climb_m = max(0, (elev[nbr] - elev[cur]) / FT_PER_M)
weight += climb_m * CLIMB_FACTOR[family]
```

Descents are free but never bonused — a downhill bonus invites routes that seek
out hills for the pleasure of falling down them.

| family | CLIMB_FACTOR | CLIMB_SEC_PER_M | rationale |
|---|---|---|---|
| bike | 8.0 | 7.0 | 1 m climbed ≈ 8 m flat; 7 s/m ≈ 500 m/h VAM |
| ebike | 2.0 | 2.5 | motor flattens the hill, not the distance |
| walk | 4.0 | 6.0 | Naismith: +1 min per 10 m of ascent |
| roll | 20.0 | 15.0 | a manual chair pays for every metre of rise |

Adding a non-negative term to edge weights leaves the A* heuristic admissible
and consistent, so the search stays optimal.

ETA becomes `distance / speed + total_climb_m * CLIMB_SEC_PER_M[family]`.

### Steep-grade disclosure

`STEEP_GRADE = 0.083` (ADA's 1:12 maximum running slope). For walk and roll, a
leg steeper than that is marked `warn: 'steep'` and flows through the existing
`warn_ranges[]` / `warnings[]` / per-step plumbing the app already draws as a
dashed red overlay.

Only legs with no other deficiency are marked, so a missing sidewalk still
outranks a hill. This keeps one warning per leg, matching the current schema.

Contour coverage stops at the county line. A leg with an unknown elevation at
either end is treated as flat rather than guessed at.

## 4. Plumbing

Mode strings become composite keys — `bike:quiet` … `ebike:direct` — generated
into `MODE_FACTORS` / `MODE_SPEED_M_S` / `MODE_DEFAULT_FACTOR` at import. Every
existing `MODE_FACTORS[mode]` lookup keeps working untouched. Two helpers
resolve the parts:

- `_mode_family(mode)` → `bike` | `ebike` | `walk` | `roll` | `street`, for the
  climb constants and speeds.
- `_base_mode(mode)` → `bike` for both bike and e-bike, for the network nouns,
  access verbs, icons, and plan labels.

Plain `bike` stays a valid key meaning `bike:balanced`.

### API

```
/map-layers/route?...&ebike=1&stress=quiet|balanced|direct
```

Defaults `ebike=0`, `stress=balanced`. Both join the route cache key.

New response fields: `climb_ft` (whole trip), `ebike`, `stress`, and `climb_ft`
per step. `mode` still reports `bike` so existing clients are unaffected.

### App

- `AppState.useEbike` (bool) and `AppState.stress` (enum), persisted next to
  `roll` and `useBcycle`.
- Threaded through `api.dart` `route()`.
- Surfaced in `directions_sheet.dart` beside the wheelchair and BCycle
  switches, shown only when bike is among the selected modes.
- Route preview summary gains the e-bike label and total climb ("↑ 210 ft").

## Testing

`tests/test_route_graph.py` (synthetic graphs, no database):

- each preset picks a different route on a graph offering a quiet detour and a
  direct busy street;
- an omitted tolerance reproduces the balanced result exactly (regression guard
  against silently changing today's behavior);
- e-bike ETA is lower than bike ETA over the same geometry;
- with elevation supplied, the climb penalty picks the flat way around a hill,
  and roll avoids a grade that bike accepts;
- a leg over 8.3% is reported as `warn: 'steep'` for walk and roll.

`app-native/test/modes_test.dart`: the new `AppState` fields round-trip through
`SharedPreferences`.

## Deliberately out of scope

Grade-aware *speed* per leg (as opposed to a whole-trip climb term), and
elevation profiles in the app UI. Both want per-step elevation series, which is
a larger response payload and a chart in the app.
