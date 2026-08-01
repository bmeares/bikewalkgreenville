#!/usr/bin/env python3
"""Routing-graph tests for `plugins/map-layers.py`.

These run against synthetic source rows (no database): `_route_source_rows`
and `_get_route_graph` are monkeypatched, so the graph builder, A* and the
turn-by-turn step builder are exercised in isolation.

    python3 -m pytest tests/test_route_graph.py     # or just run the file
"""

import importlib.util
import os
import sys
import unittest

PLUGIN = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'plugins', 'map-layers.py',
)


def _load_module():
    spec = importlib.util.spec_from_file_location('bwg_map_layers', PLUGIN)
    module = importlib.util.module_from_spec(spec)
    sys.modules['bwg_map_layers'] = module
    spec.loader.exec_module(module)
    return module


ml = _load_module()


def _line(*points) -> list:
    """(lat, lon) pairs -> GeoJSON [lon, lat] coord list."""
    return [[lon, lat] for lat, lon in points]


class RouteGraphTestCase(unittest.TestCase):
    """Common plumbing: build a graph from synthetic rows and route on it."""

    def _graph(self, rows):
        original = ml._route_source_rows
        ml._route_source_rows = lambda debug=False: rows
        try:
            return ml._build_route_graph()
        finally:
            ml._route_source_rows = original

    def _route(self, graph, origin, destination, mode='bike'):
        original = ml._get_route_graph
        ml._get_route_graph = lambda debug=False: graph
        try:
            return ml._route_core(*origin, *destination, mode=mode)
        finally:
            ml._get_route_graph = original


class TestJunctionConnectors(RouteGraphTestCase):
    """Regression: the Pearl Ave / Jones Ave / Cleveland St junction.

    Cleveland St carries a bike lane whose southern endpoint (B) sits ~12 m
    north of the street-grid junction (A) where Pearl Ave meets Jones Ave.
    A is a *mixed* node -- both a path endpoint (the Pearl Ave bike lane) and
    a street endpoint (Jones Ave) -- and the junction-connector pass used to
    exclude mixed nodes from its target set. B's connector therefore reached
    past A to the next pure-street node 48 m SOUTH down Jones Ave, so a route
    arriving at A rode south down Jones and U-turned back north to get onto
    the Cleveland St bike lane.
    """

    # Real coordinates from the reported trip (4 McHan St -> Other Lands).
    A = (34.83726, -82.39665)    # Pearl Ave x Jones Ave x Cleveland St
    B = (34.83736, -82.39663)    # south end of the Cleveland St bike lane
    JONES_S = (34.83693, -82.39667)
    PEARL_W = (34.83703, -82.40108)
    CLEVELAND_N = (34.83977, -82.39677)

    def _rows(self):
        return [
            # Pearl Ave arrives at A as a bike lane -> A is a path node...
            (_line(self.PEARL_W, self.A), 'bike-lane', 'PEARL AV', True),
            # ...and Jones Ave leaves A as a street -> A is also a street node.
            (_line(self.A, self.JONES_S), 'M', 'JONES AVE', True),
            (_line(self.JONES_S, (34.83520, -82.39667)), 'M', 'JONES AVE', True),
            # The Cleveland St bike lane starts 12 m north of A, unconnected.
            (_line(self.B, self.CLEVELAND_N), 'bike-lane', 'CLEVELAND ST', True),
        ]

    def test_lane_endpoint_connects_to_the_nearest_junction(self):
        graph = self._graph(self._rows())
        nodes, adj = graph['nodes'], graph['adj']
        a_cell = ml._grid_node(*self.A)
        b_cell = ml._grid_node(*self.B)
        self.assertIn(a_cell, adj, "the Pearl/Jones junction should be in the graph")
        self.assertIn(b_cell, adj, "the Cleveland lane endpoint should be in the graph")
        self.assertNotEqual(a_cell, b_cell, "the two nodes must not collapse into one cell")

        neighbors = {e[0] for e in adj[b_cell]}
        self.assertIn(
            a_cell, neighbors,
            "the lane endpoint must connect to the junction 12 m away, "
            f"not only to {[nodes[n] for n in neighbors]}",
        )

    def test_route_onto_the_lane_does_not_backtrack(self):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.PEARL_W, self.CLEVELAND_N)
        lats = [lat for _lon, lat in feature['geometry']['coordinates']]
        # Pearl Ave approaches from the southwest, so the opening coordinates
        # are legitimately south of A. The U-turn is a dip back south AFTER
        # the junction has been reached.
        reached = next(
            (i for i, lat in enumerate(lats) if lat >= self.A[0]), None,
        )
        self.assertIsNotNone(reached, "the route never reached the junction")
        backtrack = ml.M_PER_DEG_LAT * (self.A[0] - min(lats[reached:]))
        self.assertLessEqual(
            backtrack, 5.0,
            f"the route doubled back {backtrack:.0f} m south of the junction "
            "(the Jones Ave U-turn)",
        )

    def test_no_uturn_step_is_produced(self):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.PEARL_W, self.CLEVELAND_N)
        steps = feature['properties']['steps']
        self.assertNotIn(
            'uturn', [s['maneuver'] for s in steps],
            f"unexpected U-turn: {[s['instruction'] for s in steps]}",
        )


class TestConnectorSanity(RouteGraphTestCase):
    """A connector must never duplicate an edge a node pair already shares,
    and never loop a node back onto itself."""

    def test_no_duplicate_edges_between_a_node_pair(self):
        rows = [
            (_line((34.8500, -82.4000), (34.8510, -82.4000)), 'bike-lane', 'A ST', True),
            (_line((34.8510, -82.4000), (34.8520, -82.4000)), 'M', 'A ST', True),
            (_line((34.8501, -82.3999), (34.8506, -82.3990)), 'bike-lane', 'B ST', True),
            (_line((34.8506, -82.3990), (34.8512, -82.3985)), 'M', 'B ST', True),
        ]
        graph = self._graph(rows)
        for cell, edges in graph['adj'].items():
            targets = [e[0] for e in edges]
            self.assertEqual(
                len(targets), len(set(targets)),
                f"node {cell} has duplicate edges to {targets}",
            )
            self.assertNotIn(cell, targets, f"node {cell} has a self-loop")


if __name__ == '__main__':
    unittest.main(verbosity=2)
