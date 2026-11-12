"""Decision boundaries and bounded work for immutable shore ray prefixes."""
import math
import unittest

from shore_rays import ShoreRays


def unit(bearing):
    a = math.radians(bearing)
    return math.sin(a), math.cos(a)


class Surface:
    def __init__(self, height):
        self.height = height
        self.samples = 0

    def at(self, x, z):
        self.samples += 1
        h = self.height(x, z)
        return h, h, 0.0


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


if __name__ == '__main__':
    unittest.main()
