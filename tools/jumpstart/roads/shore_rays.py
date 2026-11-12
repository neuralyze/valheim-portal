"""Per-root ray prefixes over an immutable generated surface.

Only requested prefixes are sampled: a short viable berth must not read or
refuse unrequested terrain farther along a ray. Length and bearing order, scalar
coordinate arithmetic, comparisons and score arithmetic stay unchanged.
"""


class ShoreRays:
    def __init__(self, surface, root, unit, water_level):
        self.surface = surface
        self.root = root
        self.unit = unit
        self.water_level = water_level
        self._heights = {}

    def aims(self, length, need):
        aims = []
        for bearing in range(0, 360, 5):
            ray = self._heights.get(bearing)
            if ray is None:
                ux, uz = self.unit(bearing)
                ray = (ux, uz, [], [0])
                self._heights[bearing] = ray
            ux, uz, heights, wet_counts = ray
            for t in range(len(heights), length + 1):
                h = self.surface.at(
                    self.root[0] + ux * t, self.root[1] + uz * t)[0]
                heights.append(h)
                wet_counts.append(wet_counts[-1] + (h < self.water_level))
            size = length + 1
            half = size // 2
            if (wet_counts[size] - wet_counts[half] != size - half
                    or self.water_level - heights[length] < need):
                continue
            score = (wet_counts[size] / size * 10
                     + min(self.water_level - heights[length], 8))
            aims.append((-score, bearing))
        return aims
