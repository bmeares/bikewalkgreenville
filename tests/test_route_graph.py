#!/usr/bin/env python3
"""Routing-graph tests for `plugins/map-layers.py`.

These run against synthetic source rows (no database): `_route_source_rows`
and `_get_route_graph` are monkeypatched, so the graph builder, A* and the
turn-by-turn step builder are exercised in isolation.

    python3 -m pytest tests/

New tests are written pytest-style (plain functions + assert); the older
unittest classes predate that and pytest collects both.
"""

import importlib.util
import os
import sys
import unittest

import pytest

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


@pytest.fixture(autouse=True)
def isolated_community(monkeypatch):
    # Routing unit tests must not read the production community database.
    monkeypatch.setattr(ml, '_community_rows', lambda: [])


def _line(*points) -> list:
    """(lat, lon) pairs -> GeoJSON [lon, lat] coord list."""
    return [[lon, lat] for lat, lon in points]


class RouteGraphTestCase(unittest.TestCase):
    """Common plumbing: build a graph from synthetic rows and route on it."""

    def _graph(self, rows, elevation=None):
        """Build a graph from synthetic rows. `elevation` is (lat, lon) ->
        feet; nodes are matched to it by grid cell, so callers can give the
        elevation of a place without knowing its cell id. Omitted means flat,
        which is also what the county's contours give you outside their
        coverage."""
        original_rows = ml._route_source_rows
        original_elev = ml._node_elevations
        ml._route_source_rows = lambda debug=False: rows

        def _fake_elevations(nodes, debug=False):
            if not elevation:
                return {}
            by_cell = {ml._grid_node(lat, lon): ft
                       for (lat, lon), ft in elevation.items()}
            return {c: by_cell[c] for c in nodes if c in by_cell}

        ml._node_elevations = _fake_elevations
        try:
            return ml._build_route_graph()
        finally:
            ml._route_source_rows = original_rows
            ml._node_elevations = original_elev

    def _route(self, graph, origin, destination, mode='bike', **kwargs):
        original = ml._get_route_graph
        ml._get_route_graph = lambda debug=False: graph
        try:
            return ml._route_core(*origin, *destination, mode=mode, **kwargs)
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


class TestStressTolerance(RouteGraphTestCase):
    """A rider picks how much traffic they will accept.

    Geometry: a straight high-stress arterial from A to C, and a low-stress
    dogleg A -> B -> C about 3.7x as long. That ratio is deliberate: `direct`
    prices an H street at 3.0, so anything shorter than 3x would be taken by
    every tolerance and the test would prove nothing.
    """

    A = (34.8500, -82.4000)
    B = (34.8530, -82.4130)
    C = (34.8560, -82.4000)

    def _rows(self):
        return [
            (_line(self.A, self.C), 'H', 'ARTERIAL BLVD', True),
            (_line(self.A, self.B), 'L', 'QUIET ST', True),
            (_line(self.B, self.C), 'L', 'QUIET ST', True),
        ]

    def _names(self, mode):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.A, self.C, mode=mode)
        return {s['name'] for s in feature['properties']['steps'] if s['name']}

    def test_quiet_takes_the_detour(self):
        self.assertIn('Quiet St', self._names('bike:quiet'))

    def test_direct_takes_the_arterial(self):
        self.assertIn('Arterial Blvd', self._names('bike:direct'))

    def test_plain_bike_still_means_balanced(self):
        """The regression guard: omitting the option must reproduce the
        historical weights exactly."""
        self.assertEqual(self._names('bike'), self._names('bike:balanced'))
        self.assertEqual(
            ml.MODE_FACTORS['bike'], ml.MODE_FACTORS['bike:balanced'],
        )
        self.assertEqual(
            ml.MODE_DEFAULT_FACTOR['bike'],
            ml.MODE_DEFAULT_FACTOR['bike:balanced'],
        )

    def test_warning_threshold_follows_the_tolerance(self):
        graph = self._graph([(_line(self.A, self.C), 'ML', 'MILD ST', True)])
        kinds = {
            level: {
                w['kind']
                for w in self._route(
                    graph, self.A, self.C, mode=f'bike:{level}',
                )['properties']['warn_ranges']
            }
            for level in ('quiet', 'balanced', 'direct')
        }
        self.assertIn('no_bike_lane', kinds['quiet'])
        self.assertNotIn('no_bike_lane', kinds['balanced'])
        self.assertNotIn('no_bike_lane', kinds['direct'])


class TestSrtBias(RouteGraphTestCase):
    """The Swamp Rabbit Trail is the backbone: a bike route should take the
    trail even when it is substantially longer than a calm direct street.

    Geometry: a straight low-stress street A -> C, and an SRT dogleg
    A -> B -> C about 2.65x as long. With the pre-v0.6.0 srt factor (0.4) the
    street wins (0.4 x 2.65 > 1.0); with the biased factor (0.18 since
    v0.10.0) the trail wins. This test is the pin on that bias.
    """

    A = (34.8500, -82.4000)
    B = (34.8530, -82.4090)
    C = (34.8560, -82.4000)

    def _rows(self):
        return [
            (_line(self.A, self.C), 'L', 'CALM ST', True),
            (_line(self.A, self.B), 'srt', 'SWAMP RABBIT TRAIL', False),
            (_line(self.B, self.C), 'srt', 'SWAMP RABBIT TRAIL', False),
        ]

    def test_a_bike_prefers_the_trail(self):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.A, self.C, mode='bike:balanced')
        names = {s['name'] for s in feature['properties']['steps'] if s['name']}
        self.assertIn('Swamp Rabbit Trail', names)

    def test_walking_still_takes_the_direct_street(self):
        """The walking bias is milder (0.45): a 2.65x detour is too far to
        walk for the trail, so the direct street should still win."""
        graph = self._graph(self._rows())
        feature = self._route(graph, self.A, self.C, mode='walk')
        names = {s['name'] for s in feature['properties']['steps'] if s['name']}
        self.assertIn('Calm St', names)


class TestElevationProfile(RouteGraphTestCase):
    """The route feature carries `elevation_profile` for the app's preview
    sparkline: [distance_from_start_m, elevation_ft] per leg boundary."""

    A = (34.8500, -82.4000)
    HILL = (34.8530, -82.4000)
    ELEV = {A: 900.0, HILL: 1100.0}

    def _rows(self):
        return [(_line(self.A, self.HILL), 'L', 'HILL ST', True)]

    def test_profile_spans_the_climb(self):
        graph = self._graph(self._rows(), elevation=self.ELEV)
        feature = self._route(graph, self.A, self.HILL, mode='bike:balanced')
        profile = feature['properties']['elevation_profile']
        self.assertIsNotNone(profile)
        self.assertGreaterEqual(len(profile), 2)
        dists = [p[0] for p in profile]
        self.assertEqual(dists, sorted(dists), "distances must ascend")
        elevs = [p[1] for p in profile]
        self.assertAlmostEqual(elevs[0], 900.0, delta=10)
        self.assertAlmostEqual(elevs[-1], 1100.0, delta=10)

    def test_no_elevation_means_no_profile(self):
        graph = self._graph(self._rows(), elevation=None)
        feature = self._route(graph, self.A, self.HILL, mode='bike:balanced')
        self.assertIsNone(feature['properties']['elevation_profile'])


class TestEbike(RouteGraphTestCase):
    A = (34.8500, -82.4000)
    B = (34.8560, -82.4000)

    def _rows(self):
        return [(_line(self.A, self.B), 'L', 'FLAT ST', True)]

    def test_ebike_is_quicker_over_the_same_ground(self):
        graph = self._graph(self._rows())
        bike = self._route(graph, self.A, self.B, mode='bike:balanced')
        ebike = self._route(graph, self.A, self.B, mode='ebike:balanced')
        self.assertEqual(
            bike['properties']['distance_m'], ebike['properties']['distance_m'],
        )
        self.assertLess(
            ebike['properties']['duration_min'],
            bike['properties']['duration_min'],
        )

    def test_ebike_still_reports_itself_as_a_bike(self):
        graph = self._graph(self._rows())
        props = self._route(
            graph, self.A, self.B, mode='ebike:direct',
        )['properties']
        self.assertEqual(props['mode'], 'bike')
        self.assertTrue(props['ebike'])
        self.assertEqual(props['stress'], 'direct')


class TestHills(RouteGraphTestCase):
    """Terrain. A short steep way over a hill, and a longer flat way around."""

    A = (34.8500, -82.4000)
    HILL = (34.8530, -82.4000)     # directly between A and C
    C = (34.8560, -82.4000)
    WEST = (34.8530, -82.4040)     # the long way round, at valley level

    def _rows(self):
        return [
            (_line(self.A, self.HILL), 'L', 'HILL ST', True),
            (_line(self.HILL, self.C), 'L', 'HILL ST', True),
            (_line(self.A, self.WEST), 'L', 'VALLEY RD', True),
            (_line(self.WEST, self.C), 'L', 'VALLEY RD', True),
        ]

    #: 200 ft of climb over ~333 m is an 18% grade -- steeper than anything
    #: a wheelchair ramp may be, and a serious hill on a bike.
    ELEV = {A: 900.0, HILL: 1100.0, C: 900.0, WEST: 900.0}

    def _names(self, mode, elevation):
        graph = self._graph(self._rows(), elevation=elevation)
        feature = self._route(graph, self.A, self.C, mode=mode)
        return {s['name'] for s in feature['properties']['steps'] if s['name']}

    def test_flat_ground_takes_the_short_way(self):
        """Without elevation the hill route is simply shorter, so this pins
        that the detour below is caused by the terrain and nothing else."""
        self.assertIn('Hill St', self._names('bike:balanced', None))

    def test_a_bike_goes_around_the_hill(self):
        self.assertIn('Valley Rd', self._names('bike:balanced', self.ELEV))

    def test_an_ebike_shrugs_and_goes_over(self):
        self.assertIn('Hill St', self._names('ebike:balanced', self.ELEV))

    def test_a_wheelchair_goes_around(self):
        self.assertIn('Valley Rd', self._names('roll', self.ELEV))

    def test_climb_is_reported_and_costs_time(self):
        graph = self._graph(self._rows(), elevation=self.ELEV)
        over = self._route(graph, self.A, self.HILL, mode='bike:balanced')
        self.assertGreater(over['properties']['climb_ft'], 150)
        self.assertLess(over['properties']['climb_ft'], 250)
        # Same ground, downhill: no climb, and quicker.
        down = self._route(graph, self.HILL, self.A, mode='bike:balanced')
        self.assertEqual(down['properties']['climb_ft'], 0)
        self.assertLess(
            down['properties']['duration_min'],
            over['properties']['duration_min'],
        )

    def test_a_steep_leg_is_disclosed_to_people_on_foot(self):
        graph = self._graph(self._rows(), elevation=self.ELEV)
        feature = self._route(graph, self.A, self.HILL, mode='roll')
        kinds = {w['kind'] for w in feature['properties']['warn_ranges']}
        self.assertIn('steep', kinds)
        self.assertTrue(
            any('steep' in w['message'].lower() or 'steep' in w['label'].lower()
                for w in feature['properties']['warnings']),
            feature['properties']['warnings'],
        )

    def test_bikes_are_not_told_about_grades(self):
        """The steep callout is a walking/rolling disclosure. Cyclists get the
        hill priced into the route and the ETA instead."""
        graph = self._graph(self._rows(), elevation=self.ELEV)
        feature = self._route(graph, self.A, self.HILL, mode='bike:balanced')
        kinds = {w['kind'] for w in feature['properties']['warn_ranges']}
        self.assertNotIn('steep', kinds)

    def test_missing_elevation_is_treated_as_flat(self):
        """Contours stop at the county line; a node with no elevation must not
        read as sea level and invent a cliff."""
        partial = {self.A: 900.0}      # HILL and C unknown
        graph = self._graph(self._rows(), elevation=partial)
        feature = self._route(graph, self.A, self.C, mode='roll')
        self.assertEqual(feature['properties']['climb_ft'], 0)


class TestHillReviewFindings(RouteGraphTestCase):
    """Fixes for defects Codex found in the first cut of the hill work."""

    A = (34.8500, -82.4000)
    HILL = (34.8530, -82.4000)
    C = (34.8560, -82.4000)
    WEST = (34.8530, -82.4040)
    ELEV = {A: 900.0, HILL: 1100.0, C: 900.0, WEST: 900.0}

    def _rows(self):
        return [
            (_line(self.A, self.HILL), 'L', 'HILL ST', True),
            (_line(self.HILL, self.C), 'L', 'HILL ST', True),
            (_line(self.A, self.WEST), 'L', 'VALLEY RD', True),
            (_line(self.WEST, self.C), 'L', 'VALLEY RD', True),
        ]

    def test_a_steep_descent_is_disclosed_too(self):
        """ADA's 1:12 governs the slope, not which way you are pointed --
        rolling DOWN a 12% grade is its own hazard."""
        graph = self._graph(self._rows(), elevation=self.ELEV)
        downhill = self._route(graph, self.HILL, self.A, mode='roll')
        self.assertEqual(downhill['properties']['climb_ft'], 0,
                         "going down should cost no climb")
        kinds = {w['kind'] for w in downhill['properties']['warn_ranges']}
        self.assertIn('steep', kinds,
                      "a steep descent still has to be disclosed")

    def test_the_street_fallback_still_avoids_hills(self):
        """The fallback drops the mode's PREFERENCES, not its physics. A
        wheelchair user pushed onto the street fallback is still in a
        wheelchair and the hill is still there."""
        graph = self._graph(self._rows(), elevation=self.ELEV)
        original = ml._get_route_graph
        ml._get_route_graph = lambda debug=False: graph
        try:
            feature = ml._route_core(
                *self.A, *self.C, mode='street', warn_mode='roll',
            )
        finally:
            ml._get_route_graph = original
        names = {s['name'] for s in feature['properties']['steps'] if s['name']}
        self.assertIn('Valley Rd', names,
                      "street-weighted routing still has to price the grade")

    def test_folding_a_short_step_keeps_its_climb(self):
        """A sub-25 m hop folds into its neighbour; its climb must go with it,
        or the steps understate what the trip total already counted."""
        legs = [
            {'coords': [[-82.40, 34.85], [-82.40, 34.851]], 'name': 'A ST',
             'category': 'L', 'length_m': 200.0, 'start_index': 0,
             'warn': None, 'climb_m': 10.0},
            {'coords': [[-82.40, 34.851], [-82.3999, 34.8511]], 'name': 'B ST',
             'category': 'L', 'length_m': 10.0, 'start_index': 1,
             'warn': None, 'climb_m': 5.0},
        ]
        steps = ml._build_steps(
            legs, [[-82.40, 34.85], [-82.40, 34.851], [-82.3999, 34.8511]],
            speed_m_s=1.35, climb_sec_per_m=6.0,
        )
        total = sum(s['climb_ft'] for s in steps)
        self.assertAlmostEqual(
            total, round(15.0 * ml.FT_PER_M), delta=2,
            msg=f"climb lost in the fold: {[(s['name'], s['climb_ft']) for s in steps]}",
        )


class TestSteepWarningNoiseGates(RouteGraphTestCase):
    """Contour sampling quantizes each node to +/-2 ft, so two nodes on flat
    ground can differ by a whole 4 ft interval. Over a short leg that reads as
    a double-digit grade. Crying wolf is worse than staying quiet here --
    someone plans a wheelchair route around a hill that isn't there.
    """

    # 20 m apart: past the 12 m grid cell, short of the 25 m minimum run.
    SHORT_A = (34.8500, -82.4000)
    SHORT_B = (34.8500 + 20 / 111320.0, -82.4000)
    # 100 m apart at 30 ft of rise -> a genuine 9.1% grade. Kept under
    # ROUTE_SUBDIVIDE_M so it stays one chunk with no unmapped midpoint.
    LONG_A = (34.8600, -82.4000)
    LONG_B = (34.8600 + 100 / 111320.0, -82.4000)

    def test_a_quantization_jump_on_a_short_leg_is_not_called_steep(self):
        """6 ft over 20 m is a 9.1% grade on paper, but 6 ft is inside the
        sampling error of two adjacent nodes and 20 m is under the minimum
        run. It must not be announced."""
        graph = self._graph(
            [(_line(self.SHORT_A, self.SHORT_B), 'L', 'SHORT ST', True)],
            elevation={self.SHORT_A: 900.0, self.SHORT_B: 906.0},
        )
        feature = self._route(graph, self.SHORT_A, self.SHORT_B, mode='roll')
        kinds = {w['kind'] for w in feature['properties']['warn_ranges']}
        self.assertNotIn('steep', kinds,
                         "quantization noise was reported as a steep grade")

    def test_a_real_hill_is_still_called_steep(self):
        """The gates must not silence an actual grade: 30 ft over 100 m."""
        graph = self._graph(
            [(_line(self.LONG_A, self.LONG_B), 'L', 'LONG HILL RD', True)],
            elevation={self.LONG_A: 900.0, self.LONG_B: 930.0},
        )
        feature = self._route(graph, self.LONG_A, self.LONG_B, mode='roll')
        kinds = {w['kind'] for w in feature['properties']['warn_ranges']}
        self.assertIn('steep', kinds,
                      "a genuine 9% grade must still be disclosed")

    def test_the_gates_are_ordered_sanely(self):
        """Two contour intervals puts the signal outside the +/-4 ft error
        bound two adjacent nodes can carry between them."""
        self.assertGreaterEqual(ml.STEEP_MIN_RISE_FT, 8.0)
        self.assertGreaterEqual(ml.STEEP_MIN_RUN_M, 25.0)


class TestModeKeyHelpers(unittest.TestCase):
    def test_family_base_and_level(self):
        self.assertEqual(ml._mode_family('ebike:quiet'), 'ebike')
        self.assertEqual(ml._base_mode('ebike:quiet'), 'bike')
        self.assertEqual(ml._base_mode('bike:direct'), 'bike')
        self.assertEqual(ml._base_mode('roll'), 'roll')
        self.assertEqual(ml._stress_level('bike:quiet'), 'quiet')
        self.assertEqual(ml._stress_level('walk'), ml.DEFAULT_STRESS)

    def test_bike_mode_composition_rejects_nonsense(self):
        self.assertEqual(ml._bike_mode(False, 'quiet'), 'bike:quiet')
        self.assertEqual(ml._bike_mode(True, 'direct'), 'ebike:direct')
        self.assertEqual(
            ml._bike_mode(False, 'sideways'), f'bike:{ml.DEFAULT_STRESS}',
        )

    def test_every_composite_key_is_routable(self):
        for family in ('bike', 'ebike'):
            for level in ml.STRESS_LEVELS:
                key = f'{family}:{level}'
                for table in (
                    ml.MODE_FACTORS, ml.MODE_DEFAULT_FACTOR,
                    ml.MODE_SPEED_M_S, ml.MODE_NETWORK_NOUN,
                ):
                    self.assertIn(key, table)
                self.assertIn(ml._mode_family(key), ml.CLIMB_FACTOR)
                self.assertIn(ml._mode_family(key), ml.CLIMB_SEC_PER_M)


class TestAlternateRoutes(RouteGraphTestCase):
    """`?alt=N`: penalizing the previous route's edges finds the parallel
    street when one exists, and honestly returns the same route when not."""

    # Two ways east: Main St straight across, or the longer North Ave detour.
    W = (34.8500, -82.3960)
    E = (34.8500, -82.3900)
    N = (34.8510, -82.3930)

    def _rows(self):
        return [
            (_line(self.W, self.E), 'M', 'MAIN ST', True),
            (_line(self.W, self.N), 'M', 'NORTH AVE', True),
            (_line(self.N, self.E), 'M', 'NORTH AVE', True),
        ]

    def test_avoid_pairs_take_the_other_street(self):
        graph = self._graph(self._rows())
        pairs = set()
        base = self._route(graph, self.W, self.E, pairs_out=pairs)
        self.assertTrue(pairs, "the base route should report its node pairs")
        alt = self._route(graph, self.W, self.E, avoid_pairs=pairs)
        self.assertNotEqual(
            base['geometry']['coordinates'], alt['geometry']['coordinates'],
            "with Main St penalized, the route should take North Ave",
        )
        base_names = {s['name'] for s in base['properties']['steps'] if s.get('name')}
        alt_names = {s['name'] for s in alt['properties']['steps'] if s.get('name')}
        self.assertIn('Main St', base_names)
        self.assertIn('North Ave', alt_names)
        self.assertNotIn('Main St', alt_names)
        self.assertGreater(
            alt['properties']['distance_m'], base['properties']['distance_m'],
            "the alternate exists because it lost on distance the first time",
        )

    def test_compute_plan_marks_a_distinct_alternate(self):
        graph = self._graph(self._rows())
        original = ml._get_route_graph
        ml._get_route_graph = lambda debug=False: graph
        ml._ROUTE_CACHE.clear()
        try:
            base = ml._compute_plan('bike', *self.W, *self.E)
            alt = ml._compute_plan('bike', *self.W, *self.E, alt=1)
        finally:
            ml._get_route_graph = original
            ml._ROUTE_CACHE.clear()
        self.assertNotIn('alt', base['properties'])
        self.assertEqual(alt['properties']['alt'], 1)
        self.assertTrue(alt['properties']['alt_distinct'])
        self.assertNotEqual(
            base['geometry']['coordinates'], alt['geometry']['coordinates'],
        )

    def test_no_alternative_is_disclosed_not_invented(self):
        # Only Main St exists: the "alternate" is the same route, and says so.
        graph = self._graph([(_line(self.W, self.E), 'M', 'MAIN ST', True)])
        original = ml._get_route_graph
        ml._get_route_graph = lambda debug=False: graph
        ml._ROUTE_CACHE.clear()
        try:
            base = ml._compute_plan('bike', *self.W, *self.E)
            alt = ml._compute_plan('bike', *self.W, *self.E, alt=1)
        finally:
            ml._get_route_graph = original
            ml._ROUTE_CACHE.clear()
        self.assertFalse(alt['properties']['alt_distinct'])
        self.assertEqual(
            base['geometry']['coordinates'], alt['geometry']['coordinates'],
        )


class TestTurnDirectionLookahead(RouteGraphTestCase):
    """Regression: the Anderson St -> Dunbar St wrong-way announcement.

    The route geometry jogged a couple of metres RIGHT at the junction before
    the real LEFT turn. Bearings measured on a single (metres-long) segment
    read the jog as the turn, so the app said "Turn right" at a left turn --
    the one mistake turn-by-turn navigation must never make. Maneuver bearings
    are now measured over ~STEP_BEARING_LOOKAHEAD_M of geometry.
    """

    def test_a_tiny_jog_right_before_a_left_turn_reads_left(self):
        # Heading north on Anderson, then a 3 m jog east-ish, then west on
        # Dunbar. dLat of 1e-4 deg ~= 11 m; dLon of 1e-4 deg ~= 9 m.
        anderson = {
            'coords': [[-82.4000, 34.8480], [-82.4000, 34.8500]],
            'name': 'ANDERSON ST', 'category': 'L', 'length_m': 222.0,
            'start_index': 0, 'warn': None, 'climb_m': 0.0,
        }
        dunbar = {
            # First segment jogs ~3 m NE (a right-hand bearing change), the
            # rest runs clearly WEST -- the actual left turn.
            'coords': [
                [-82.4000, 34.8500],
                [-82.399975, 34.850020],
                [-82.4010, 34.850020],
                [-82.4020, 34.850020],
            ],
            'name': 'DUNBAR ST', 'category': 'L', 'length_m': 190.0,
            'start_index': 1, 'warn': None, 'climb_m': 0.0,
        }
        coordinates = anderson['coords'] + dunbar['coords'][1:]
        steps = ml._build_steps([anderson, dunbar], coordinates)
        turn = next(s for s in steps if s['name'] == 'Dunbar St')
        self.assertIn(
            'left', turn['maneuver'],
            f"a left turn was announced as '{turn['maneuver']}' "
            f"({turn['instruction']}) because of a {3:.0f} m junction jog",
        )

    def test_a_real_right_turn_still_reads_right(self):
        a = {
            'coords': [[-82.4000, 34.8480], [-82.4000, 34.8500]],
            'name': 'ANDERSON ST', 'category': 'L', 'length_m': 222.0,
            'start_index': 0, 'warn': None, 'climb_m': 0.0,
        }
        b = {
            'coords': [[-82.4000, 34.8500], [-82.3990, 34.8500], [-82.3980, 34.8500]],
            'name': 'EAST ST', 'category': 'L', 'length_m': 180.0,
            'start_index': 1, 'warn': None, 'climb_m': 0.0,
        }
        steps = ml._build_steps([a, b], a['coords'] + b['coords'][1:])
        turn = next(s for s in steps if s['name'] == 'East St')
        self.assertIn('right', turn['maneuver'])


class TestCustomPaths(RouteGraphTestCase):
    """CUSTOM_PATHS entries (the Springer St tunnel) must be routable: an
    off-grid connector beats the long way around once it's in the graph."""

    # Springer St dead-ends at Church St; the tunnel + apartment lot connect
    # it to University Ridge. Without the path, the only mapped way is a long
    # detour south.
    SPRINGER_W = (34.8380, -82.4030)
    PORTAL_W = (34.8380391, -82.4008463)
    U_RIDGE = (34.8399346, -82.4009103)
    U_RIDGE_E = (34.8399, -82.3970)
    DETOUR_S = (34.8340, -82.4008)

    def _rows(self):
        return [
            (_line(self.SPRINGER_W, self.PORTAL_W), 'L', 'SPRINGER ST', True),
            (_line(self.U_RIDGE, self.U_RIDGE_E), 'M', 'UNIVERSITY RIDGE', True),
            # The long way around (Springer -> south -> east -> north).
            (_line(self.PORTAL_W, self.DETOUR_S), 'M', 'CHURCH ST', True),
            (_line(self.DETOUR_S, (34.8340, -82.3970)), 'M', 'LONG WAY', True),
            (_line((34.8340, -82.3970), self.U_RIDGE_E), 'M', 'LONG WAY', True),
            # The tunnel path itself, exactly as CUSTOM_PATHS feeds it in.
            (
                [
                    [-82.4008463, 34.8380391],
                    [-82.4004550, 34.8380570],
                    [-82.4007000, 34.8390000],
                    [-82.4009103, 34.8399346],
                ],
                'path', 'Springer St tunnel path', True,
            ),
        ]

    def test_the_tunnel_wins_over_the_detour(self):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.SPRINGER_W, self.U_RIDGE_E)
        names = {s['name'] for s in feature['properties']['steps'] if s['name']}
        self.assertIn('Springer St tunnel path', names,
                      f"route skipped the tunnel: {sorted(names)}")
        self.assertNotIn('Long Way', names)

    def test_the_path_is_not_flagged_as_missing_infrastructure(self):
        graph = self._graph(self._rows())
        for mode in ('bike', 'walk', 'roll'):
            feature = self._route(
                graph, self.SPRINGER_W, self.U_RIDGE_E, mode=mode,
            )
            for w in feature['properties']['warn_ranges']:
                for s in feature['properties']['steps']:
                    if s['name'] and 'Tunnel' in s['name']:
                        self.assertIsNone(
                            s.get('warn'),
                            f"{mode}: the path warned {s['warn']}",
                        )

    def test_custom_paths_reach_route_source_rows(self):
        """The real _route_source_rows appends CUSTOM_PATHS (this suite mocks
        it everywhere else, so pin the wiring itself). The list may be empty
        — v0.13.0 removed the hand-drawn Springer entry once OSM supplied the
        real tunnel — but any entry present must be well-formed."""
        for p in ml.CUSTOM_PATHS:
            self.assertGreaterEqual(len(p['coords']), 2)
            self.assertTrue(p.get('name'))
        # Categories 'path'/'footway' are wired into every routing table.
        for cat in ('path', 'footway'):
            self.assertIn(cat, ml.OWN_SURFACE_CATEGORIES)
            self.assertIn(cat, ml.BIKE_FACILITY_CATEGORIES)
            for level, factors in ml.BIKE_STRESS_FACTORS.items():
                self.assertIn(cat, factors, f"stress '{level}' missing '{cat}'")
            for mode in ('walk', 'roll'):
                self.assertIn(cat, ml.MODE_FACTORS[mode])


class TestSpeedStressFloor(unittest.TestCase):
    """Posted speed limits corroborate the PCC stress ratings: high-speed
    roads (Pete Hollis Blvd's 40 mph blocks, any 45+ road) are floored to a
    stress level that matches the traffic, escalation only."""

    def test_speed_floors_escalate(self):
        self.assertEqual(ml._stress_floor('L', 45), 'H')
        self.assertEqual(ml._stress_floor('M', 45), 'H')
        self.assertEqual(ml._stress_floor('L', 40), 'MH')
        self.assertEqual(ml._stress_floor('M', 40), 'MH')
        self.assertEqual(ml._stress_floor('L', 35), 'M')

    def test_speed_never_downgrades(self):
        self.assertEqual(ml._stress_floor('H', 35), 'H')
        self.assertEqual(ml._stress_floor('MH', 40), 'MH')
        self.assertEqual(ml._stress_floor('H', 45), 'H')

    def test_slow_or_unknown_speed_leaves_the_rating_alone(self):
        self.assertEqual(ml._stress_floor('L', 25), 'L')
        self.assertEqual(ml._stress_floor('M', None), 'M')

    def test_non_stress_categories_pass_through(self):
        for cat in ('srt', 'bike-lane', 'path', None):
            self.assertEqual(ml._stress_floor(cat, 55), cat)

    def test_a_fast_road_now_earns_the_bike_lane_warning(self):
        """The point of the corroboration: a 45 mph road that PCC rated L
        must both price like an arterial and be disclosed as one."""
        self.assertIn(ml._stress_floor('L', 45), ml.STRESSFUL_CATEGORIES)


class TestOsmPaths(RouteGraphTestCase):
    """OSM ways (cycleways, foot paths, street tunnels) fill the shortcut
    gaps no county GIS layer maps."""

    def _overpass(self):
        def way(wid, tags, *latlons):
            return {
                'type': 'way', 'id': wid, 'tags': tags,
                'geometry': [{'lat': lat, 'lon': lon} for lat, lon in latlons],
            }
        return {'elements': [
            way(1, {'highway': 'footway', 'name': 'Publix path'},
                (34.85, -82.40), (34.851, -82.40)),
            way(2, {'highway': 'residential', 'tunnel': 'yes',
                    'name': 'Springer Street'},
                (34.8380, -82.4008), (34.8381, -82.4004)),
            way(3, {'highway': 'cycleway', 'name': 'Swamp Rabbit Trail'},
                (34.86, -82.42), (34.861, -82.42)),  # skipped: already 'srt'
            way(4, {'highway': 'path'}, (34.87, -82.41)),  # too short
        ]}

    def test_parse_categorizes_paths_and_street_tunnels(self):
        entries = ml._parse_overpass_paths(self._overpass())
        by_name = {e['name']: e for e in entries}
        self.assertIn('Publix path', by_name)
        self.assertFalse(by_name['Publix path']['street'])
        self.assertIn('Springer Street', by_name)
        self.assertTrue(by_name['Springer Street']['street'])
        self.assertNotIn('Swamp Rabbit Trail', by_name,
                         "the SRT is its own category; the OSM copy must be skipped")
        self.assertEqual(len(entries), 2, "the 1-point way must be dropped")

    def test_osm_categories(self):
        """Street tunnels ride as 'L', cycleways/paths as 'path', bare
        footways as 'footway' (car-free but NOT trail-grade — 0.9 for bikes
        so apartment breezeways never beat the real street beside them)."""
        self.assertEqual(ml._osm_category('residential', True), 'L')
        self.assertEqual(ml._osm_category('cycleway', False), 'path')
        self.assertEqual(ml._osm_category('pedestrian', False), 'path')
        self.assertEqual(ml._osm_category('path', False), 'path')
        self.assertEqual(ml._osm_category('footway', False), 'footway')
        for level in ml.STRESS_LEVELS:
            self.assertGreaterEqual(
                ml.BIKE_STRESS_FACTORS[level]['footway'], 0.8,
                "a footway must never be trail-cheap for a bike",
            )

    def test_a_bike_lane_on_a_fast_road_never_beats_a_calm_street(self):
        """The Church St lesson: 0.4 x the 35 mph lane penalty must be >= the
        L-street factor, or painted arterial paint out-prices quiet streets."""
        for mph, factor in ml.LANE_SPEED_PENALTY:
            self.assertGreaterEqual(0.4 * factor, 1.0, f"{mph} mph lane too cheap")


class TestNominatimLabel(unittest.TestCase):
    """Nominatim puts the house number in its own comma part; the label must
    not present a bare "4" with the street exiled to the subtitle."""

    def test_house_number_glued_to_street(self):
        label, sub = ml._nominatim_label(
            '4, McHan Street, Downtown, Greenville, '
            'Greenville County, South Carolina, 29605, United States'
        )
        self.assertEqual(label, '4 McHan Street')
        self.assertEqual(sub, 'Downtown, Greenville')

    def test_place_names_pass_through(self):
        label, sub = ml._nominatim_label(
            'Swamp Rabbit Cafe, 205, Cedar Lane Road, Greenville, ...'
        )
        self.assertEqual(label, 'Swamp Rabbit Cafe')
        self.assertEqual(sub, '205, Cedar Lane Road')

    def test_letter_suffixed_number_still_glues(self):
        label, _sub = ml._nominatim_label('221B, Baker Street, Greenville')
        self.assertEqual(label, '221B Baker Street')

    def test_degenerate_inputs(self):
        self.assertEqual(ml._nominatim_label(''), ('', ''))
        self.assertEqual(ml._nominatim_label('4'), ('4', ''))


# --- pytest-style from here down ---------------------------------------


def test_titleize_preserves_mc_names():
    """.title() alone mangles Mc surnames: 'MCHAN ST' -> 'Mchan St', and the
    street is named after the McHans. Mirrors the DB's SMART_CAPITALIZE."""
    assert ml._titleize('MCHAN ST') == 'McHan St'
    assert ml._titleize('MCBEE AVE') == 'McBee Ave'
    assert ml._titleize('MCDANIEL AVE') == 'McDaniel Ave'
    assert ml._titleize('E WASHINGTON ST') == 'E Washington St'


def test_titleize_leaves_mixed_case_alone():
    assert ml._titleize('McHan St') == 'McHan St'
    assert ml._titleize('Springer St tunnel path') == 'Springer St tunnel path'
    assert ml._titleize(None) is None
    assert ml._titleize('') is None


def test_instruction_speaks_mchan_correctly():
    assert ml._instruction('left', 'MCHAN ST', 90.0) == 'Turn left onto McHan St'


# --- crash danger, lane-speed penalty, night lighting (v0.12.0) ----------


def test_danger_factor_scales_and_caps():
    assert ml._danger_factor(0.0) == 1.0
    assert ml._danger_factor(None) == 1.0
    # Downtown-ish score: mild penalty.
    assert 1.3 < ml._danger_factor(0.6) < 1.6
    # Pete Hollis / White Horse class scores hit the cap.
    assert ml._danger_factor(3.0) == ml._danger_factor(99.0)
    assert ml._danger_factor(99.0) == 1.0 + ml.DANGER_CAP * ml.DANGER_RATE


def test_lane_speed_penalty_erodes_the_paint_discount():
    assert ml._lane_speed_factor(None) == 1.0
    assert ml._lane_speed_factor(30) == 1.0
    assert ml._lane_speed_factor(35) == 2.5
    assert ml._lane_speed_factor(40) == 3.0
    assert ml._lane_speed_factor(45) == 4.0
    # A painted lane on a fast road must never price better than a calm
    # street (the Church St lesson).
    assert 0.4 * ml._lane_speed_factor(35) >= 1.0


def test_lane_stress_multiplier_scales_with_the_riders_tolerance():
    """The stress penalty on painted lanes is priced per-rider at query time:
    a fixed build-time penalty calibrated for `balanced` read as CHEAP next
    to `quiet`'s 8x/20x/40x street factors, so quiet riders — the most
    Church-St-averse — were the only ones still routed onto Church's lane."""
    balanced = ml.BIKE_STRESS_FACTORS['balanced']
    quiet = ml.BIKE_STRESS_FACTORS['quiet']
    direct = ml.BIKE_STRESS_FACTORS['direct']
    # Balanced calibration unchanged from v0.12: H-street lane x10 the paint
    # price (0.4 x 10 = 4.0 net — on the streets that kill, paint barely
    # helps).
    assert ml._lane_stress_multiplier(balanced, 'H') == 10.0
    assert ml.MODE_FACTORS['bike']['M'] < 0.4 * ml._lane_stress_multiplier(
        balanced, 'H') < ml.MODE_FACTORS['bike']['MH']
    # Quiet: an H-street lane must price WORSE than any calm street — net
    # 40/3 ≈ 13x, vs 2.0 for an ML street. This is the Church St regression.
    quiet_h_lane_net = quiet['bike-lane'] * ml._lane_stress_multiplier(quiet, 'H')
    assert quiet_h_lane_net > quiet['M']
    assert quiet_h_lane_net > quiet['ML']
    # Direct riders shrug: the same lane nets about a calm street.
    assert direct['bike-lane'] * ml._lane_stress_multiplier(direct, 'H') <= 1.5
    # Calm streets keep the full discount; unknown/missing stress is neutral.
    assert ml._lane_stress_multiplier(balanced, 'L') == 1.0
    assert ml._lane_stress_multiplier(balanced, None) == 1.0
    # Walking a bike-laned street is just walking a street.
    assert ml._lane_stress_multiplier(ml.MODE_FACTORS['walk'], 'H') == 1.0
    # Street-fallback factors ({}) must not blow up.
    assert ml._lane_stress_multiplier({}, 'H') == 1.0


class TestQuietRiderAvoidsArterialPaint(RouteGraphTestCase):
    """A painted lane on an H street must not attract exactly the riders who
    asked for quiet (the McHan -> Legacy Park report: quiet rode S Church St's
    lane while balanced took the calm streets)."""

    A = (34.8500, -82.4000)
    B = (34.8530, -82.4030)   # dogleg via the calm streets (~1.7x)
    C = (34.8560, -82.4000)

    def _rows(self):
        return [
            # Straight shot: bike lane painted on an H-rated arterial.
            # 7-tuple row carries (danger, lit, lane_stress).
            (_line(self.A, self.C), 'bike-lane', 'ARTERIAL LN', True, 1.0, True, 'H'),
            (_line(self.A, self.B), 'ML', 'CALM ST', True, 1.0, True, None),
            (_line(self.B, self.C), 'ML', 'CALM ST', True, 1.0, True, None),
        ]

    def _names(self, mode):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.A, self.C, mode=mode)
        return {s['name'] for s in feature['properties']['steps'] if s['name']}

    def test_quiet_takes_the_calm_streets(self):
        names = self._names('bike:quiet')
        self.assertIn('Calm St', names)
        self.assertNotIn('Arterial Ln', names)

    def test_direct_still_takes_the_lane(self):
        self.assertIn('Arterial Ln', self._names('bike:direct'))


class TestTrailPreferenceToggle(RouteGraphTestCase):
    """`?trail=0`: the SRT prices like a plain calm street, so the shorter
    street route wins; with the default bias the trail detour wins."""

    A = (34.8500, -82.4000)
    B = (34.8530, -82.4030)   # trail dogleg (~1.7x the street)
    C = (34.8560, -82.4000)

    def _rows(self):
        return [
            (_line(self.A, self.C), 'L', 'PLAIN ST', True, 1.0, True, None),
            (_line(self.A, self.B), 'srt', 'SRT', True, 1.0, True, None),
            (_line(self.B, self.C), 'srt', 'SRT', True, 1.0, True, None),
        ]

    def _names(self, trail):
        token = ml._TRAIL_PREF.set(trail)
        try:
            graph = self._graph(self._rows())
            feature = self._route(graph, self.A, self.C, mode='bike')
            return {s['name'] for s in feature['properties']['steps'] if s['name']}
        finally:
            ml._TRAIL_PREF.reset(token)

    def test_default_bias_rides_the_trail(self):
        self.assertIn('Srt', self._names(True))

    def test_trail_off_takes_the_street(self):
        names = self._names(False)
        self.assertIn('Plain St', names)
        self.assertNotIn('Srt', names)


class TestTunnelNeverFusesWithTheStreetAbove(RouteGraphTestCase):
    """The graph is 2D and the 12 m grid snap fused the Springer St tunnel
    with the S Church St edges crossing above it — riders were told to turn
    left onto Church St from inside the tunnel. Tunnel rows (8th tuple
    element) now live in their own node namespace and rejoin the network
    only through portals bridged to their own street's name."""

    W = (34.8500, -82.4020)    # Springer, west surface end
    WP = (34.8500, -82.4010)   # west portal
    EP = (34.8500, -82.3990)   # east portal
    E = (34.8500, -82.3980)    # Springer, east surface end
    CN = (34.8510, -82.4000)   # Church, north end
    MID = (34.8500, -82.4000)  # where Church crosses OVER the tunnel
    CS = (34.8490, -82.4000)   # Church, south end

    def _rows(self):
        return [
            (_line(self.W, self.WP), 'L', 'SPRINGER ST', True),
            # The tunnel: 8-tuple, tunnel=True, passing exactly under MID.
            (_line(self.WP, self.MID, self.EP), 'L', 'Springer Street',
             True, 1.0, True, None, True),
            (_line(self.EP, self.E), 'L', 'SPRINGER ST', True),
            (_line(self.CN, self.MID, self.CS), 'L', 'CHURCH ST', True),
            # A real surface connection so Church stays in the component.
            (_line(self.CS, self.W), 'L', 'PEARL AVE', True),
        ]

    def test_no_node_joins_the_tunnel_and_the_street_above(self):
        graph = self._graph(self._rows())
        for lst in graph['adj'].values():
            names = {e[6] for e in lst}
            self.assertFalse(
                'Springer Street' in names and 'CHURCH ST' in names,
                f'tunnel fused with the street above: {names}',
            )

    def test_the_tunnel_still_routes_end_to_end(self):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.W, self.E)
        names = {s['name'] for s in feature['properties']['steps'] if s['name']}
        self.assertIn('Springer Street', names)
        # Direct through the bore, not around via Pearl/Church.
        crow = ml._equirect_m(*self.W, *self.E)
        self.assertLess(feature['properties']['distance_m'], crow * 1.3)

    def test_church_is_reached_around_not_through_the_bore(self):
        graph = self._graph(self._rows())
        feature = self._route(graph, self.EP, self.CN)
        # The only way onto Church is the real Pearl Ave junction: the trip
        # must be far longer than the (impossible) hop up through the roof
        # of the tunnel.
        crow = ml._equirect_m(*self.EP, *self.CN)
        self.assertGreater(feature['properties']['distance_m'], crow * 2.0)


def test_tunnel_roof_rows_are_dropped_at_ingest():
    """PCC/county digitized Springer St straight across Church St; those
    surface rows are the roof of the tunnel and must not reach the graph."""
    roof = [[-82.40080, 34.83805], [-82.40060, 34.83805]]
    ends_at_portal = [[-82.40120, 34.83803], [-82.40088, 34.83804]]
    assert ml._tunnel_roof('SPRINGER ST', roof)
    assert ml._tunnel_roof('Springer Street', roof)
    assert not ml._tunnel_roof('SPRINGER ST', ends_at_portal)
    assert not ml._tunnel_roof('S CHURCH ST', roof)
    assert not ml._tunnel_roof(None, roof)


def test_grade_separated_rows_are_clipped_at_ingest():
    """Wakefield St is severed by the Church St embankment: the crossing
    vertex is clipped out, the street survives on both sides."""
    part = [
        [-82.40365, 34.83832],           # west end
        [-82.4004476, 34.8386591],       # the fake Church crossing node
        [-82.39855, 34.83865],           # east end (at Briar)
    ]
    clipped = ml._clip_grade_separated('WAKEFIELD ST', part)
    assert clipped == []  # both survivors are single points -> dropped
    longer = [
        [-82.40365, 34.83832],
        [-82.40200, 34.83850],
        [-82.4004476, 34.8386591],
        [-82.39950, 34.83866],
        [-82.39855, 34.83865],
    ]
    clipped = ml._clip_grade_separated('Wakefield Street', longer)
    assert len(clipped) == 2
    assert clipped[0] == longer[:2]
    assert clipped[1] == longer[3:]
    # Other streets pass through untouched.
    assert ml._clip_grade_separated('BRIAR ST', longer) == [longer]
    assert ml._clip_grade_separated(None, longer) == [longer]


class TestTrailTierStreets(RouteGraphTestCase):
    """Furman College Way is closed to cars and feeds the SRT: its rows are
    re-categorized 'srt' at graph build, so every stress level biases onto
    it like the trail itself."""

    def test_furman_college_way_prices_as_trail(self):
        a, b = (34.8429, -82.4038), (34.8439, -82.4016)
        graph = self._graph([
            (_line(a, b), 'L', 'FURMAN COLLEGE WAY', True),
            (_line(b, (34.8449, -82.4000)), 'L', 'FURMAN ST', True),
        ])
        categories = {
            e[6]: e[3] for lst in graph['adj'].values() for e in lst
        }
        assert categories['FURMAN COLLEGE WAY'] == 'srt'
        assert categories['FURMAN ST'] == 'L'


def test_custom_paths_route_coords_feed_the_graph_not_the_layer():
    """The Springer entry draws from the west tunnel portal but routes only
    the east-of-Church stretch (surface coords under the bore would re-fuse
    with Church St — the v0.13 lesson)."""
    import json
    springer = ml.CUSTOM_PATHS[0]
    assert springer['coords'][0] == [-82.40085, 34.83804]
    assert springer['route_coords'][0][0] > -82.4004  # east of Church
    layer = json.loads(ml._build_custom_paths())
    feature = layer['features'][0]
    assert feature['geometry']['coordinates'] == springer['coords']
    assert 'route_coords' not in feature['properties']


def test_connectors_never_bridge_a_grade_separation_window():
    """The Church St bike lane's chunk end sits ~10 m from the tunnel's west
    portal, ON the bridge — a junction connector there is a fake ramp."""
    # Inside the Springer tunnel-roof box.
    assert ml._crosses_grade_separation(34.83805, -82.40078, 34.83803, -82.40089)
    # Inside the Wakefield window.
    assert ml._crosses_grade_separation(34.83866, -82.40045, 34.83868, -82.40030)
    # Well away from every box.
    assert not ml._crosses_grade_separation(34.8500, -82.3900, 34.8501, -82.3901)


def test_gis_rename_says_the_streets_real_name():
    """Fred Garrett St was renamed from Howe St; the county GIS still says
    Howe. Steps, search labels and road-info say the real name."""
    assert ml._gis_rename('HOWE ST') == 'Fred Garrett St'
    assert ml._gis_rename('Howe St') == 'Fred Garrett St'
    assert ml._gis_rename('HOWENING RD') == 'HOWENING RD'
    assert ml._gis_rename(None) is None
    assert ml._rename_label('4 Howe St') == '4 Fred Garrett St'
    assert ml._rename_label('HOWE STREET') == 'Fred Garrett St'
    assert ml._rename_label('Howell Rd') == 'Howell Rd'


def test_is_night_honors_the_override_and_the_clock():
    from datetime import datetime
    token = ml._NIGHT_OVERRIDE.set(True)
    try:
        assert ml._is_night() is True
    finally:
        ml._NIGHT_OVERRIDE.reset(token)
    token = ml._NIGHT_OVERRIDE.set(False)
    try:
        assert ml._is_night() is False
    finally:
        ml._NIGHT_OVERRIDE.reset(token)
    assert ml._is_night(datetime(2026, 6, 15, 14, 0)) is False   # summer 2pm
    assert ml._is_night(datetime(2026, 6, 15, 22, 0)) is True    # 10pm
    assert ml._is_night(datetime(2026, 12, 15, 17, 30)) is True  # winter 5:30pm
    assert ml._is_night(datetime(2026, 12, 15, 12, 0)) is False


class TestCrashDangerRouting(RouteGraphTestCase):
    """A crash-scarred street loses to a slightly longer clean one, even when
    both carry the same stress rating — the White Horse Rd lesson."""

    A = (34.8500, -82.4000)
    B = (34.8530, -82.4030)   # dogleg via the clean street (~1.7x)
    C = (34.8560, -82.4000)

    def _rows(self, danger):
        return [
            # Deadly straight street: 6-tuple row carries (danger, lit).
            (_line(self.A, self.C), 'L', 'DEADLY RD', True, danger, True),
            (_line(self.A, self.B), 'L', 'SAFE ST', True, 1.0, True),
            (_line(self.B, self.C), 'L', 'SAFE ST', True, 1.0, True),
        ]

    def _names(self, danger, mode='bike'):
        graph = self._graph(self._rows(danger))
        feature = self._route(graph, self.A, self.C, mode=mode)
        return {s['name'] for s in feature['properties']['steps'] if s['name']}

    def test_no_crash_history_takes_the_short_way(self):
        self.assertIn('Deadly Rd', self._names(1.0))

    def test_crash_history_takes_the_detour(self):
        cap = 1.0 + ml.DANGER_CAP * ml.DANGER_RATE
        self.assertIn('Safe St', self._names(cap))
        self.assertNotIn('Deadly Rd', self._names(cap))

    def test_legacy_4_tuple_rows_still_build(self):
        graph = self._graph([
            (_line(self.A, self.C), 'L', 'PLAIN ST', True),
        ])
        feature = self._route(graph, self.A, self.C)
        names = {s['name'] for s in feature['properties']['steps'] if s['name']}
        self.assertIn('Plain St', names)


class TestNightLighting(RouteGraphTestCase):
    """After dark, an unlit street loses to a lit parallel; by day the
    shorter unlit street wins as usual."""

    A = (34.8500, -82.4000)
    B = (34.8530, -82.4025)   # lit dogleg, ~1.5x the direct distance
    C = (34.8560, -82.4000)

    def _rows(self):
        return [
            (_line(self.A, self.C), 'L', 'DARK ST', True, 1.0, False),
            (_line(self.A, self.B), 'L', 'LIT AVE', True, 1.0, True),
            (_line(self.B, self.C), 'L', 'LIT AVE', True, 1.0, True),
        ]

    def _names(self, night, mode='walk'):
        token = ml._NIGHT_OVERRIDE.set(night)
        try:
            graph = self._graph(self._rows())
            feature = self._route(graph, self.A, self.C, mode=mode)
        finally:
            ml._NIGHT_OVERRIDE.reset(token)
        return {s['name'] for s in feature['properties']['steps'] if s['name']}

    def test_daytime_takes_the_short_dark_street(self):
        self.assertIn('Dark St', self._names(False))

    def test_night_walk_takes_the_lit_street(self):
        names = self._names(True)
        self.assertIn('Lit Ave', names)
        self.assertNotIn('Dark St', names)


if __name__ == '__main__':
    unittest.main(verbosity=2)

class TestAccessAndCrossingSafety(RouteGraphTestCase):
    def test_access_overrides_preserve_mode_permissions(self):
        self.assertEqual(ml._allowed_modes({'access': 'private'}), set())
        self.assertEqual(ml._allowed_modes({'access': 'private', 'foot': 'yes'}), {'walk', 'roll'})
        self.assertEqual(ml._allowed_modes({'barrier': 'gate'}), set())
        self.assertEqual(ml._allowed_modes({'barrier': 'gate', 'bicycle': 'yes'}), {'bike'})
        self.assertNotIn('roll', ml._allowed_modes({'highway': 'steps'}))
        self.assertNotIn('bike', ml._allowed_modes({'bicycle:conditional': 'yes @ (sunrise-sunset)'}))

    def test_private_geometry_cannot_be_reintroduced_by_a_public_copy(self):
        coords = _line((34.85, -82.40), (34.85, -82.395))
        public = _line((34.851, -82.40), (34.851, -82.395))
        graph = self._graph([
            (coords, 'L', 'Private duplicate', True),
            (coords, 'private', 'Private original', True, 1.0, True, None, False, frozenset()),
            (public, 'L', 'Public road', True),
        ])
        self.assertFalse(any(e[6] == 'Private duplicate' for es in graph['adj'].values() for e in es))
        self.assertFalse(ml._access_link_allowed(graph, coords[0], public[0], 'bike'))

    def test_gate_blocks_a_mapped_path(self):
        coords = _line((34.85, -82.40), (34.85, -82.399))
        gate = [[-82.3995, 34.85], [-82.39949999, 34.85]]
        graph = self._graph([
            (coords, 'path', 'Gated shortcut', True),
            (gate, 'private', None, True, 1.0, True, None, False, frozenset(), {'barrier': 'gate'}),
            (_line((34.852, -82.40), (34.852, -82.398)), 'L', 'Public road', True),
        ])
        self.assertFalse(any(e[6] == 'Gated shortcut' for es in graph['adj'].values() for e in es))

    def test_uncontrolled_crossing_is_costed_even_between_quiet_edges(self):
        # Two calm routes between endpoints: one crosses a busy junction.
        nodes = {'s': (34.85, -82.40), 'x': (34.85, -82.399),
                 'g': (34.85, -82.398), 'd': (34.851, -82.399)}
        adj = {n: [] for n in nodes}
        def edge(a, b, length):
            adj[a].append((b, length, length, 'L', [], False, 'Quiet street', True))
        edge('s', 'x', 100); edge('x', 'g', 100)
        edge('s', 'd', 200); edge('d', 'g', 200)
        graph = {'nodes': nodes, 'adj': adj, 'crossings': {'x': 'Arterial'}}
        self.assertEqual([n for n, _ in ml._astar(graph, 's', 'g')], ['s', 'd', 'g'])
        graph['controlled_crossings'] = {'x'}
        self.assertEqual([n for n, _ in ml._astar(graph, 's', 'g')], ['s', 'x', 'g'])

    def test_street_fallback_keeps_mode_access_constraints(self):
        nodes = {'s': (34.85, -82.40), 'g': (34.85, -82.399)}
        foot_only = ('g', 1, 100, 'path', [], False, 'Foot only', True,
                     (1.0, True, None, frozenset(('walk', 'roll'))))
        graph = {'nodes': nodes, 'adj': {'s': [foot_only], 'g': []}}
        self.assertIsNone(ml._astar(graph, 's', 'g', mode='street', climb_mode='bike'))
        self.assertIsNotNone(ml._astar(graph, 's', 'g', mode='walk'))


def test_community_geometry_validation_and_immutable_rollback():
    import json
    good = {'type': 'LineString', 'coordinates': [[-82.4, 34.85], [-82.399, 34.85]]}
    assert ml._validate_contribution_geometry(json.dumps(good), 34.85, -82.4) == good
    for bad in ({'type': 'Polygon', 'coordinates': []},
                {'type': 'Point', 'coordinates': [float('nan'), 34.85]},
                {'type': 'LineString', 'coordinates': [[-82.4, 34.85]]},
                {'type': 'Point', 'coordinates': [0, 0]}):
        with __import__('pytest').raises(ValueError):
            ml._validate_contribution_geometry(json.dumps(bad), 34.85, -82.4)
    original = {'id': 'first', 'name': 'Local path', 'reverts': None}
    rollback = {'id': 'second', 'reverts': 'first', 'comment': 'Gate closed'}
    assert ml._active_community([original]) == [original]
    assert ml._active_community([original, rollback]) == []
    assert original['name'] == 'Local path'


def test_transit_access_never_invents_a_straight_line_after_routing_failure():
    from unittest.mock import patch
    with patch.object(ml, '_route', side_effect=ValueError('Private gate')):
        with __import__('pytest').raises(ValueError, match='Private gate'):
            ml._walk_or_direct(34.85, -82.4, 34.851, -82.399)


def _directed_graph(nodes, links):
    adj = {n: [] for n in nodes}
    for a, b, length in links:
        coords = _line(nodes[a], nodes[b])
        adj[a].append((b, length, length, 'L', coords, False, 'Street', True))
    return {'nodes': nodes, 'adj': adj}


def test_avoid_geometric_uturn_even_when_raw_distance_is_shorter():
    nodes = {'s': (34.85,-82.402), 'a': (34.85,-82.4),
             'u': (34.85001,-82.4005), 'r': (34.8495,-82.4), 'g': (34.8495,-82.4005)}
    graph = _directed_graph(nodes, [('s','a',180),('a','u',45),('u','g',56),('a','r',56),('r','g',55)])
    assert [n for n, _ in ml._astar(graph,'s','g')] == ['s','a','r','g']


def test_unavoidable_reversal_still_returns_connected_route():
    nodes = {'s': (34.85,-82.402), 'a': (34.85,-82.4), 'g': (34.85001,-82.4005)}
    graph = _directed_graph(nodes, [('s','a',180),('a','g',45)])
    assert [n for n, _ in ml._astar(graph,'s','g')] == ['s','a','g']


def test_incoming_direction_is_part_of_search_state():
    # The cheaper arrival at J faces away from G. Keeping only one distance
    # per node discards the slightly longer arrival that avoids the U-turn.
    nodes = {'s':(34.848,-82.401), 'a':(34.85,-82.401),
             'b':(34.85,-82.399), 'j':(34.85,-82.4), 'g':(34.85,-82.4008)}
    graph = _directed_graph(nodes,[('s','a',300),('a','j',90),('s','b',400),('b','j',90),('j','g',72)])
    assert [n for n,_ in ml._astar(graph,'s','g')] == ['s','b','j','g']


def test_parking_aisles_are_not_routable_but_curated_tunnel_is_retained():
    case = RouteGraphTestCase()
    public = frozenset(('bike','walk','roll'))
    a,b,c = (34.85,-82.4),(34.85,-82.399),(34.851,-82.4)
    parking = (_line(a,b),'path','Parking aisle',True,1.0,True,None,False,public,
               {'highway':'service','service':'parking_aisle'})
    graph = case._graph([parking,(_line(a,c),'L','Public street',True)])
    assert all(e[6] != 'Parking aisle' for edges in graph['adj'].values() for e in edges)
    assert not ml._automatic_parking_access((_line(a,b),'L','Tunnel',True,1,True,None,True,public,
                                            {'highway':'service','tunnel':'yes'}))


def test_no_entry_blocks_edges_and_first_last_meter_links():
    import json
    nodes = {'s':(34.85,-82.402),'g':(34.85,-82.398),'d':(34.852,-82.4)}
    graph = _directed_graph(nodes,[('s','g',360),('s','d',280),('d','g',280)])
    polygon = {'type':'Polygon','coordinates':[[[-82.4005,34.8495],[-82.3995,34.8495],[-82.3995,34.8505],[-82.4005,34.8505],[-82.4005,34.8495]]]}
    graph['exclusions'] = ml._exclusion_index([{'category':'no-entry','geometry_json':json.dumps(polygon)}])
    for mode in ('bike','walk','roll','street'):
        assert [n for n,_ in ml._astar(graph,'s','g',mode=mode)] == ['s','d','g']
    assert not ml._access_link_allowed(graph,[-82.4,34.85],[-82.398,34.85],'walk')
    assert not ml._access_link_allowed(graph,[-82.4,34.85],[-82.4,34.85],'walk')
    graph['exclusions'] = None
    assert [n for n,_ in ml._astar(graph,'s','g')] == ['s','g']


@pytest.mark.parametrize('raw', [float('nan'), None, '', '{broken', 'null'])
def test_missing_imported_access_metadata_uses_access_aware_cache(monkeypatch, raw):
    import pandas as pd
    from types import SimpleNamespace
    pipe = SimpleNamespace(exists=lambda **kw:True,parameters={},target='paths',
                           instance_connector=SimpleNamespace(read=lambda *a,**kw:pd.DataFrame([{'tags_json':raw}])))
    monkeypatch.setattr(ml,'OSM_PATHS_PIPE',pipe)
    assert ml._osm_path_rows_from_pipe() is None


def test_bus_ride_cannot_bypass_community_exclusion(monkeypatch):
    import json
    polygon = {'type':'Polygon','coordinates':[[[-82.4005,34.8495],[-82.3995,34.8495],[-82.3995,34.8505],[-82.4005,34.8505],[-82.4005,34.8495]]]}
    monkeypatch.setattr(ml,'_active_community',lambda:[{'category':'no-entry','geometry_json':json.dumps(polygon)}])
    def candidate(key,*a,**kw):
        return {'type':'Feature','geometry':{'type':'LineString','coordinates':
            [[-82.402,34.85],[-82.398,34.85]] if key.endswith('transit') else [[-82.402,34.85],[-82.4,34.852],[-82.398,34.85]]},
            'properties':{'plan':key,'distance_m':500,'duration_min':1 if key.endswith('transit') else 10}}
    monkeypatch.setattr(ml,'_compute_plan',candidate)
    feature=ml._route_multimodal(34.85,-82.402,34.85,-82.398,{'walk','transit'})
    assert feature['properties']['plan'] == 'walk'
    assert feature['properties']['alternatives'] == []
    assert 'no-entry' in feature['properties']['unavailable'][0]['reason']
