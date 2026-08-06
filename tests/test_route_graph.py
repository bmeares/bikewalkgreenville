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
    street wins (0.4 x 2.65 > 1.0); with the biased factor (0.28) the trail
    wins. This test is the pin on that bias.
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
        """The walking bias is milder (0.55): a 2.65x detour is too far to
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


if __name__ == '__main__':
    unittest.main(verbosity=2)
