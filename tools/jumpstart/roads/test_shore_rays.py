"""Decision boundaries and bounded work for immutable shore ray prefixes."""
import math
import unittest

import numpy as np

from shore_rays import ShoreRays


def unit(bearing):
    a = math.radians(bearing)
    return math.sin(a), math.cos(a)


class Surface:
    def __init__(self, height):
        self.height = height
        self.samples = 0
        self.points = []

    def at(self, x, z):
        self.samples += 1
        self.points.append((x, z))
        h = self.height(x, z)
        return h, h, 0.0


class BatchSurface(Surface):
    def __init__(self, height):
        super().__init__(height)
        self.batches = []
        self.batch_points = []

    def sample_points(self, points):
        self.batches.append(list(points))
        heights = []
        for x, z in points:
            self.batch_points.append((x, z))
            heights.append(self.height(x, z))
        return np.asarray(heights, dtype=np.float64)


class RayTests(unittest.TestCase):
    def test_every_heading_and_score_survives_prefix_reuse(self):
        surface = Surface(lambda x, z: 20.0)
        rays = ShoreRays(surface, (-7.5, 3.5), unit, 30.0)
        expected = [(-18.0, bearing) for bearing in range(0, 360, 5)]
        for length in range(20, 98, 2):
            self.assertEqual(rays.aims(length, 2.75), expected)
        self.assertEqual(surface.samples, 72 * 97)
        self.assertEqual(rays.aims(20, 2.75), expected)
        self.assertEqual(surface.samples, 72 * 97)

    def test_exact_required_depth_is_accepted_but_shallower_is_not(self):
        exact = ShoreRays(Surface(lambda x, z: 27.25), (0, 0), unit, 30.0)
        self.assertEqual(exact.aims(20, 2.75),
                         [(-12.75, bearing) for bearing in range(0, 360, 5)])
        shallow = ShoreRays(Surface(lambda x, z: math.nextafter(27.25, math.inf)),
                            (0, 0), unit, 30.0)
        self.assertEqual(shallow.aims(20, 2.75), [])

    def test_short_prefix_does_not_read_unrequested_far_terrain(self):
        def height(x, z):
            if math.hypot(x, z) > 30.5:
                raise ValueError('outside measured terrain')
            return 20.0
        rays = ShoreRays(Surface(height), (0, 0), unit, 30.0)
        self.assertEqual(dict((b, s) for s, b in rays.aims(20, 2.75))[0], -18.0)
        with self.assertRaises(ValueError):
            rays.aims(32, 2.75)

    def test_landward_heading_is_rejected_as_shore_ray_extends(self):
        rays = ShoreRays(Surface(lambda x, z: 32.0 - z / 10.0), (0, 0), unit, 30.0)
        self.assertEqual(rays.aims(20, 2.75), [])
        scores = {b: s for s, b in rays.aims(96, 2.75)}
        self.assertIn(0, scores)
        self.assertNotIn(180, scores)
        self.assertEqual(scores[0], -(76 / 97 * 10 + (30.0 - (32.0 - 96 / 10.0))))

    def test_batch_matches_scalar_headings_scores_and_query_order(self):
        root = (-7.5, 3.5)
        heights = [
            lambda x, z: 20.0,
            lambda x, z: 32.0 - z / 10.0,
            lambda x, z: 28.0 + 5.0 * math.sin(x / 13.0)
            + 3.0 * math.cos(z / 17.0),
            lambda x, z: 30.0 if x < -10.0 else 26.0 + z / 20.0,
        ]
        for height in heights:
            with self.subTest(height=height):
                scalar_surface = Surface(height)
                batch_surface = BatchSurface(height)
                scalar = ShoreRays(scalar_surface, root, unit, 30.0)
                batched = ShoreRays(batch_surface, root, unit, 30.0,
                                    batch=batch_surface.sample_points)
                previous = 0
                for length, need in [(20, 2.75), (32, 2.75), (24, 2.75),
                                     (32, 4.0), (60, 2.75), (12, 1.0),
                                     (96, 2.75), (96, 2.75)]:
                    calls = len(batch_surface.batches)
                    self.assertEqual(batched.aims(length, need),
                                     scalar.aims(length, need))
                    new_points = scalar_surface.points[previous:]
                    if new_points:
                        self.assertEqual(len(batch_surface.batches), calls + 1)
                        self.assertEqual(batch_surface.batches[-1], new_points)
                    else:
                        self.assertEqual(len(batch_surface.batches), calls)
                    self.assertEqual(batched._heights, scalar._heights)
                    previous = len(scalar_surface.points)
                self.assertEqual(batch_surface.batch_points, scalar_surface.points)
                self.assertEqual(batch_surface.points, [])

    def test_batch_preserves_exact_depth_and_water_boundaries(self):
        for height, need, score in [
                (27.25, 2.75, -12.75),
                (math.nextafter(27.25, math.inf), 2.75, None),
                (math.nextafter(27.25, -math.inf), 2.75,
                 -(10.0 + (30.0 - math.nextafter(27.25, -math.inf)))),
                (30.0, 0.0, None),
                (math.nextafter(30.0, -math.inf), 0.0,
                 -(10.0 + (30.0 - math.nextafter(30.0, -math.inf))))]:
            with self.subTest(height=height, need=need):
                scalar = ShoreRays(Surface(lambda x, z: height),
                                   (0, 0), unit, 30.0)
                surface = BatchSurface(lambda x, z: height)
                batched = ShoreRays(surface, (0, 0), unit, 30.0,
                                    batch=surface.sample_points)
                expected = ([] if score is None else
                            [(score, b) for b in range(0, 360, 5)])
                for length in (20, 32, 12, 32):
                    self.assertEqual(batched.aims(length, need),
                                     scalar.aims(length, need))
                    self.assertEqual(batched.aims(length, need), expected)

    def test_batch_does_not_request_unneeded_far_terrain(self):
        def height(x, z):
            if math.hypot(x, z) > 20.5:
                raise ValueError(('outside measured terrain', x, z))
            return 20.0 + x / 100.0

        surface = BatchSurface(height)
        scalar_surface = Surface(height)
        batched = ShoreRays(surface, (0, 0), unit, 30.0,
                            batch=surface.sample_points)
        scalar = ShoreRays(scalar_surface, (0, 0), unit, 30.0)
        for length in (20, 8, 20):
            self.assertEqual(batched.aims(length, 2.75),
                             scalar.aims(length, 2.75))
        self.assertEqual(len(surface.batches), 1)
        self.assertEqual(surface.batch_points, scalar_surface.points)
        self.assertEqual(len(surface.batch_points), 72 * 21)
        self.assertTrue(all(math.hypot(x, z) <= 20.5
                            for x, z in surface.batch_points))
        self.assertEqual(surface.points, [])

    def test_batch_scope_error_restores_exact_scalar_partial_prefix(self):
        def height(x, z):
            if x > 8.0 and z > 20.0:
                raise ValueError(('outside measured terrain', x, z))
            return 28.0 - z / 10.0 + x / 100.0

        for initial_length in (None, 20):
            with self.subTest(initial_length=initial_length):
                scalar_surface = Surface(height)
                surface = BatchSurface(height)
                scalar = ShoreRays(scalar_surface, (0, 0), unit, 30.0)
                batched = ShoreRays(surface, (0, 0), unit, 30.0,
                                    batch=surface.sample_points)
                if initial_length is not None:
                    self.assertEqual(batched.aims(initial_length, 2.75),
                                     scalar.aims(initial_length, 2.75))
                for _ in range(2):
                    scalar_start = len(scalar_surface.points)
                    batch_start = len(surface.batch_points)
                    replay_start = len(surface.points)
                    with self.assertRaises(ValueError) as scalar_error:
                        scalar.aims(40, 2.75)
                    with self.assertRaises(ValueError) as batch_error:
                        batched.aims(40, 2.75)
                    queried = scalar_surface.points[scalar_start:]
                    self.assertEqual(batch_error.exception.args,
                                     scalar_error.exception.args)
                    self.assertEqual(surface.batch_points[batch_start:], queried)
                    self.assertEqual(surface.points[replay_start:], queried)
                    self.assertEqual(batched._heights, scalar._heights)
                    self.assertEqual(queried[-1], (unit(15)[0] * 31,
                                                   unit(15)[1] * 31))
                self.assertEqual(batched.aims(20, 2.75),
                                 scalar.aims(20, 2.75))
                self.assertEqual(batched._heights, scalar._heights)

    def test_native_coverage_exit_preserves_prefix_for_later_short_request(self):
        from shore_sampling import GeneratedSampler
        from test_shore_sampling import field, surface as generated_surface

        for initial in (None, 3):
            with self.subTest(initial=initial):
                fields = {"native": field("native", -5.0, -5.0, n=11,
                                          height=20.0)}
                ordinary = generated_surface(fields)
                native = generated_surface(fields)
                sampler = GeneratedSampler(fields, native.field_names)
                scalar = ShoreRays(ordinary, (0.0, 0.0), unit, 30.0)
                batched = ShoreRays(native, (0.0, 0.0), unit, 30.0,
                                    batch=sampler.sample_points)
                if initial is not None:
                    self.assertEqual(batched.aims(initial, 2.75),
                                     scalar.aims(initial, 2.75))
                with self.assertRaises(SystemExit) as expected:
                    scalar.aims(6, 2.75)
                with self.assertRaises(SystemExit) as actual:
                    batched.aims(6, 2.75)
                self.assertEqual(str(actual.exception), str(expected.exception))
                self.assertEqual(batched._heights, scalar._heights)
                self.assertEqual(batched.aims(3, 2.75), scalar.aims(3, 2.75))

    def test_batch_callback_error_is_not_swallowed_after_scalar_replay(self):
        surface = Surface(lambda x, z: 28.0 - z / 10.0)
        failure = RuntimeError('batch provider failed')

        def failed_batch(points):
            raise failure

        batched = ShoreRays(surface, (0, 0), unit, 30.0, batch=failed_batch)
        scalar = ShoreRays(Surface(surface.height), (0, 0), unit, 30.0)
        with self.assertRaises(RuntimeError) as caught:
            batched.aims(20, 2.75)
        self.assertIs(caught.exception, failure)
        self.assertEqual(surface.samples, 72 * 21)
        self.assertEqual(batched.aims(20, 2.75), scalar.aims(20, 2.75))
        self.assertEqual(surface.samples, 72 * 21)


if __name__ == '__main__':
    unittest.main()
