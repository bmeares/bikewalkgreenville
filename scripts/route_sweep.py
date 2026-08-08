#!/usr/bin/env python3
"""Routing health sweep: sample many trips over the real graph and flag the
ones that look broken, so bad routing surfaces before a rider finds it.

    python3 scripts/route_sweep.py [N_TRIPS] [MODE ...]

Builds the graph from the live sql:bwg data (~25 s), routes N sampled
node-pair trips (default 150) per mode (default bike + walk), and reports
anomalies:

  * uturns   — more than one U-turn maneuver in a single route
  * unnamed  — >40% of the distance narrated with no street name
  * connector— >300 m spent on synthetic gap-crossing connectors
  * ratio    — routed distance >2.6x the straight-line distance
  * error    — no route at all / street fallback

Sampling is seeded, so two runs (e.g. before and after a weights change)
flag comparable trips. Exit code 1 when anomaly share exceeds 10%.
"""

import importlib.util
import random
import sys
import os

PLUGIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'plugins', 'map-layers.py',
)

MAX_UTURNS = 1        # + one more per 8 km — long trail trips legitimately
                      # hairpin at junctions (the Green Line leaves Cleveland
                      # Park backwards), and the pre-existing trail-exit
                      # residual (HANDOFF, fifth session) is not new breakage.
UTURN_KM_ALLOWANCE = 8.0
UNNAMED_FRAC = 0.4
CONNECTOR_M = 300.0
RATIO = 2.6
MIN_TRIP_M = 800.0   # crow-fly; sub-kilometre trips make ratios meaningless
SEED = 20260807


def _load():
    spec = importlib.util.spec_from_file_location('bwg_map_layers_sweep', PLUGIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules['bwg_map_layers_sweep'] = module
    spec.loader.exec_module(module)
    return module


def main():
    n_trips = int(sys.argv[1]) if len(sys.argv) > 1 else 150
    modes = sys.argv[2:] or ['bike', 'walk']

    ml = _load()
    graph = ml._get_route_graph()
    nodes = list(graph['nodes'].values())
    rng = random.Random(SEED)

    pairs = []
    while len(pairs) < n_trips:
        (alat, alon), (blat, blon) = rng.sample(nodes, 2)
        if ml._equirect_m(alat, alon, blat, blon) >= MIN_TRIP_M:
            pairs.append(((alat, alon), (blat, blon)))

    token = ml._NIGHT_OVERRIDE.set(False)
    flagged = []
    ok = 0
    try:
        for mode in modes:
            for (alat, alon), (blat, blon) in pairs:
                crow = ml._equirect_m(alat, alon, blat, blon)
                try:
                    f = ml._route_core(alat, alon, blat, blon, mode=mode)
                except ValueError as e:
                    flagged.append((mode, (alat, alon), (blat, blon), f'error: {e}'))
                    continue
                p = f['properties']
                steps = p['steps']
                problems = []
                uturns = sum(1 for s in steps if s['maneuver'] == 'uturn')
                allowed = MAX_UTURNS + int(p['distance_m'] / (UTURN_KM_ALLOWANCE * 1000))
                if uturns > allowed:
                    problems.append(f'{uturns} uturns over {p["distance_m"] / 1000:.0f} km')
                unnamed_m = sum(
                    s['distance_m'] for s in steps
                    if not s.get('name') and s['maneuver'] not in ('depart', 'arrive')
                )
                if p['distance_m'] > 0 and unnamed_m / p['distance_m'] > UNNAMED_FRAC:
                    problems.append(f'{unnamed_m / p["distance_m"]:.0%} unnamed')
                connector_m = (p.get('breakdown') or {}).get('connector', 0.0)
                if connector_m > CONNECTOR_M:
                    problems.append(f'{connector_m:.0f} m on connectors')
                if p['distance_m'] / crow > RATIO:
                    problems.append(f'{p["distance_m"] / crow:.1f}x crow-fly')
                if p.get('fallback'):
                    problems.append('street fallback')
                if problems:
                    flagged.append((mode, (alat, alon), (blat, blon), '; '.join(problems)))
                else:
                    ok += 1
    finally:
        ml._NIGHT_OVERRIDE.reset(token)

    total = ok + len(flagged)
    print(f'\n{total} trips routed, {ok} clean, {len(flagged)} flagged '
          f'({len(flagged) / max(total, 1):.0%}).')
    for mode, a, b, why in flagged:
        print(f'  {mode:5} {a[0]:.5f},{a[1]:.5f} -> {b[0]:.5f},{b[1]:.5f}  {why}')
    sys.exit(1 if len(flagged) > total * 0.10 else 0)


if __name__ == '__main__':
    main()
