"""Real source-deck verdicts and numeric terrain work across one preview scope."""
from contextlib import ExitStack
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE))
from roads import north_itinerary as N


class NorthSourcePreviewTests(unittest.TestCase):
    def assert_repeated_source(self, kind, *, accepted, ground_only=False):
        require_source = N._require_source_connection

        def repeated(seg, source_xz, **kwargs):
            net = kwargs["net"]
            fields = kwargs["fields"]
            field = next(iter(fields.values()))
            # The source fixtures mark their entire deck road as bridge. Add a
            # real, separate ground road so rebuilding the complete preview has
            # measurable terrain work, without moving the source or its deck.
            # Its 2 m ribbon and full batter stay in zone (0, 0), entirely
            # covered by the fixture's field, and clear of the source deck.
            corridor = net.routemod.Corridor(
                field, cell_m=1.0, grade_max=0.08, roughness_weight=0.0)
            ground, _ = net._ww_leg(
                field, corridor, (-4.0, -8.0), (4.0, -8.0), width=2.0)()
            ground.update(id="numeric-preview-ground",
                          **{"from": "preview-ground-a", "to": "preview-ground-b"})
            segments = [*kwargs["segments"], ground]
            kwargs["segments"] = segments
            kwargs["population"] = N._source_crossing_population(
                segments, kwargs["crossings"], net)

            import ribbon
            flat_patch = ribbon._flat_patch
            work = {"generated": 0, "field": 0}

            def sampled_patch(zx, zz, generated):
                def sampled_height(x, z):
                    # Count actual numeric vertex generation, not construction
                    # calls: the production patch and ribbon.stamp still run.
                    work["generated"] += 1
                    return generated(x, z)
                return flat_patch(zx, zz, sampled_height)

            def counted_at(original):
                def sample(x, z):
                    work["field"] += 1
                    return original(x, z)
                return sample

            outcomes = []
            measurements = []
            with ExitStack() as stack:
                stack.enter_context(patch.object(ribbon, "_flat_patch", sampled_patch))
                for measured in fields.values():
                    stack.enter_context(patch.object(
                        measured, "at", counted_at(measured.at)))
                kwargs["preview"] = N._source_preview(fields, segments, net)
                # Creating a batch must not compile terrain for ground sources.
                self.assertEqual(work, {"generated": 0, "field": 0})
                for _ in range(2):
                    before = dict(work)
                    try:
                        require_source(seg, source_xz, **kwargs)
                    except ValueError as exc:
                        outcomes.append(str(exc))
                    else:
                        outcomes.append(None)
                    measurements.append({key: work[key] - before[key] for key in work})

            first, second = measurements
            self.assertEqual(outcomes[0], outcomes[1],
                             "reuse must preserve the full physical verdict")
            self.assertEqual(outcomes[0] is None, accepted)
            if ground_only:
                self.assertEqual(first, {"generated": 0, "field": 0})
                self.assertEqual(second, {"generated": 0, "field": 0})
            else:
                self.assertGreaterEqual(first["generated"], ribbon.tcdata.PITCH ** 2,
                                        "exercise at least one full terrain patch")
                self.assertEqual(second["generated"], 0,
                                 "a second source must not regenerate terrain patches")
                self.assertGreater(first["field"], first["generated"])
                self.assertGreater(second["field"], 0,
                                   "the second check must still examine the real bed")
            if outcomes[0] is not None:
                raise ValueError(outcomes[0])

        # Adapt the existing numeric fixture at its consumer boundary; neither
        # the routing, ribbon compiler, deck builder nor guards are substituted.
        with patch.object(N, "_require_source_connection", repeated):
            self.assertTrue(N._source_connection_regression(kind), kind)

    def test_registered_deck_reuses_numeric_terrain_and_keeps_verdict(self):
        self.assert_repeated_source("registered_deck", accepted=True)

    def test_declared_deck_reuses_numeric_terrain_and_keeps_verdict(self):
        self.assert_repeated_source("declared_deck", accepted=True)

    def test_detached_deck_is_still_physically_refused_after_reuse(self):
        self.assert_repeated_source("detached_deck", accepted=False)

    def test_ground_only_sources_leave_preview_unbuilt(self):
        self.assert_repeated_source("interior", accepted=True, ground_only=True)


if __name__ == "__main__":
    unittest.main()
