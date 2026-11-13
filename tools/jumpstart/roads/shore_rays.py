"""Per-root ray prefixes over an immutable generated surface.

Only requested prefixes are sampled: a short viable berth must not read or
refuse unrequested terrain farther along a ray. Length and bearing order, scalar
coordinate arithmetic, comparisons and score arithmetic stay unchanged.
"""


class ShoreRays:
    """Cache requested ray prefixes; batch is an immutable-surface callback.

    The optional callback accepts ordered Nx2 point pairs and returns one height
    per point, exactly matching surface.at(x, z)[0]. On failure, scalar replay
    restores the successful prefix and raises the first scalar sampling error,
    or re-raises the callback error if all requested scalar samples succeed.
    """

    def __init__(self, surface, root, unit, water_level, *, batch=None):
        self.surface = surface
        self.root = root
        self.unit = unit
        self.water_level = water_level
        self.batch = batch
        self._heights = {}

    def _extend_batch(self, length):
        points = []
        pending = []
        for bearing in range(0, 360, 5):
            ray = self._heights.get(bearing)
            if ray is None:
                ux, uz = self.unit(bearing)
                ray = (ux, uz, [], [0])
            ux, uz, heights, wet_counts = ray
            start = len(points)
            for t in range(len(heights), length + 1):
                points.append((self.root[0] + ux * t, self.root[1] + uz * t))
            if len(points) != start:
                pending.append((bearing, ray, start, len(points)))
        if not points:
            return
        try:
            sampled = self.batch(points)
        except (Exception, SystemExit):
            # A failed batch returns no prefix. Replay in scalar query order so
            # successful samples remain cached and the first scalar error wins.
            for bearing, ray, start, stop in pending:
                self._heights[bearing] = ray
                heights, wet_counts = ray[2:]
                for i in range(start, stop):
                    h = self.surface.at(*points[i])[0]
                    heights.append(h)
                    wet_counts.append(wet_counts[-1] + (h < self.water_level))
            raise
        for bearing, ray, start, stop in pending:
            self._heights[bearing] = ray
            heights, wet_counts = ray[2:]
            for i in range(start, stop):
                h = float(sampled[i])
                heights.append(h)
                wet_counts.append(wet_counts[-1] + (h < self.water_level))

    def aims(self, length, need):
        if self.batch is not None:
            self._extend_batch(length)
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
