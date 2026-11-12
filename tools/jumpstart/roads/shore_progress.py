"""Durable search telemetry and exact-basis, ordered-prefix shore checkpoints.

Integration (single writer only)::

    basis = {**all_input_and_code_hashes, "requirement_ids": ordered_ids}
    progress = ShoreProgress(checkpoint_path, basis)
    completed = progress.load_completed()  # insertion-ordered id -> site payload
    # Reuse only this prefix, in order, rebuilding the occupied-site list as usual.
    progress({"requirement_id": next_id, "root_index": i, "root_total": n,
              "length": length, "bearing": bearing,
              "counters": actual_cumulative_counters, "elapsed_seconds": elapsed})
    progress.complete_requirement(next_id, accepted_site)
    # A ferry pair is ONE requirement whose payload contains BOTH accepted ends.
    progress({"phase": "done", "elapsed_seconds": elapsed})

Call the event callback at existing search work boundaries, not per terrain sample.
The first event, requirement transitions, and phase done/failed/final events flush
immediately; other events produce a throttled heartbeat, including unchanged work.
Completed requirements always commit immediately. Progress alone is NEVER resumed
as accepted geometry. A missing, invalid, or different-basis checkpoint starts an
empty prefix with an explicit stdout notice. No search cursor is resumed.

The caller owns the complete semantic basis and site validation: include source,
manifest, fields, code, geometry, nodes, requirements and preset hashes. This is a
crash-recovery checkpoint, not an authentication mechanism for untrusted files.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
import tempfile
import time
from typing import Any


_VERSION = 1
_FINAL_PHASES = {"done", "failed", "final"}
_POSITION_KEYS = ("requirement_id", "root_index", "length", "bearing")


def _json(value: Any) -> str:
    """Canonical JSON, rejecting lossy keys/types and non-finite numbers."""
    def check(item: Any) -> None:
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child)
            return
        raise ValueError("checkpoint values must be finite, lossless JSON data")
    check(value)
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate checkpoint key: {key}")
        result[key] = value
    return result


def _site_payload(site: Any) -> bool:
    # The parent validates physical geometry. Reject scalars/empty partial payloads.
    return bool(site) and (type(site) is dict or
                          (type(site) is list and
                           all(type(item) is dict and item for item in site)))


class ShoreProgress:
    """Atomic JSON checkpoint plus monotonic-time-throttled, flushed heartbeat."""

    def __init__(self, path: str | Path, basis: dict[str, Any], *,
                 interval_seconds: float = 15.0) -> None:
        if not math.isfinite(interval_seconds) or interval_seconds <= 0:
            raise ValueError("interval_seconds must be finite and positive")
        if type(basis) is not dict:
            raise ValueError("basis must be a dict")
        self.path = Path(path)
        self.interval_seconds = interval_seconds
        self._basis_json = _json(basis)
        self.basis = json.loads(self._basis_json)
        ids = self.basis.get("requirement_ids")
        if (type(ids) is not list or any(type(item) is not str or not item for item in ids)
                or len(set(ids)) != len(ids)):
            raise ValueError("basis must contain unique ordered requirement_ids")
        self._ids = ids
        self._completed: list[dict[str, Any]] = []
        self._event: dict[str, Any] = {}
        self._position: tuple[Any, ...] = (None,) * len(_POSITION_KEYS)
        self._counters: dict[str, int | float] = {}
        self._last_flush: float | None = None
        self._started = time.monotonic()
        self._last_advancement: str | None = None
        self._last_counter_advancement: str | None = None
        self._advanced_since_flush = False
        self._load()

    @staticmethod
    def _timestamp() -> str:
        # UTC with subsecond precision; monotonic time, not this clock, throttles IO.
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds")

    def _load(self) -> None:
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except UnicodeError as exc:
            print(f"[shore] checkpoint ignored: {exc}", flush=True)
            return
        try:
            document = json.loads(text, object_pairs_hook=_unique_object)
            if (type(document) is not dict or type(document.get("version")) is not int
                    or document["version"] != _VERSION):
                raise ValueError("unsupported checkpoint")
            if _json(document.get("basis")) != self._basis_json:
                raise ValueError("input/code/geometry/semantic basis differs")
            entries = document.get("completed")
            if type(entries) is not list or len(entries) > len(self._ids):
                raise ValueError("invalid completed prefix")
            for index, entry in enumerate(entries):
                if (type(entry) is not dict or set(entry) != {"id", "site"}
                        or entry["id"] != self._ids[index]
                        or not _site_payload(entry["site"])):
                    raise ValueError("completed requirements are not an ordered prefix")
            _json(document)  # Reject non-finite data anywhere, including sites.
        except (ValueError, TypeError, RecursionError) as exc:
            print(f"[shore] checkpoint ignored: {exc}", flush=True)
            return
        self._completed = entries
        print(f"[shore] checkpoint resumed {len(entries)}/{len(self._ids)} requirements",
              flush=True)

    def load_completed(self) -> dict[str, Any]:
        """Return a detached ordered prefix; telemetry is never accepted as a site."""
        return {entry["id"]: json.loads(_json(entry["site"]))
                for entry in self._completed}

    def _persist(self, completed: list[dict[str, Any]], now: float) -> None:
        record = {
            "version": _VERSION,
            "basis": self.basis,
            "completed": completed,
            "progress": {
                "event": self._event,
                "updated_at": self._timestamp(),
                "last_advancement_at": self._last_advancement,
                "last_counter_advancement_at": self._last_counter_advancement,
                "work_status": "advanced" if self._advanced_since_flush else "unchanged",
                "process_elapsed_seconds": now - self._started,
            },
        }
        data = (_json(record) + "\n").encode("utf-8")
        # Serialize completely before opening a temporary file. A failure must not
        # damage the previous checkpoint or mutate the accepted in-memory prefix.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", dir=self.path.parent,
                                             prefix=f".{self.path.name}.",
                                             suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
            temporary = None
            directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except FileNotFoundError:
                    pass
        self._last_flush = now
        self._advanced_since_flush = False

    def _line(self, status: str) -> None:
        event = self._event
        fields = " ".join(f"{key}={event[key]}" for key in
                          ("requirement_id", "root_index", "root_total", "length",
                           "bearing", "elapsed_seconds", "phase") if key in event)
        counters = " ".join(f"{key}={value}" for key, value in self._counters.items())
        print(f"[shore] {status} completed={len(self._completed)}/{len(self._ids)} "
              f"{fields} {counters}".rstrip(), flush=True)

    def __call__(self, event: dict[str, Any]) -> None:
        """Record actual supplied fields, never invented counts or success states."""
        if type(event) is not dict:
            raise ValueError("progress event must be a dict")
        now = time.monotonic()
        transition = ("requirement_id" in event and
                      event["requirement_id"] != self._event.get("requirement_id"))
        merged = dict(self._event)
        if transition:
            for key in ("root_index", "root_total", "length", "bearing", "phase"):
                merged.pop(key, None)
        merged.update(event)
        counters = event.get("counters", self._counters)
        if (type(counters) is not dict or
                any(type(key) is not str or type(value) not in (int, float)
                    or not math.isfinite(value) for key, value in counters.items())):
            raise ValueError("progress counters must be finite numbers")
        counters = dict(counters)
        merged["counters"] = counters
        position = tuple(merged.get(key) for key in _POSITION_KEYS)
        counter_advanced = any(value > self._counters.get(key, 0)
                               for key, value in counters.items())
        if position != self._position or counter_advanced:
            self._last_advancement = self._timestamp()
            self._advanced_since_flush = True
        if counter_advanced:
            self._last_counter_advancement = self._last_advancement
        self._event, self._position, self._counters = merged, position, counters
        final = event.get("phase") in _FINAL_PHASES
        if (self._last_flush is None or transition or final
                or now - self._last_flush >= self.interval_seconds):
            status = "advanced" if self._advanced_since_flush else "unchanged"
            self._persist(self._completed, now)
            self._line(status)

    def complete_requirement(self, requirement_id: str, site: Any) -> None:
        """Commit only the next accepted requirement, or an entire accepted pair."""
        index = len(self._completed)
        if index >= len(self._ids) or requirement_id != self._ids[index]:
            raise ValueError("only the next requirement in the ordered prefix can complete")
        if not _site_payload(site):
            raise ValueError("completed requirement needs a nonempty site or site-pair payload")
        detached = json.loads(_json(site))
        completed = self._completed + [{"id": requirement_id, "site": detached}]
        self._last_advancement = self._timestamp()
        self._advanced_since_flush = True
        now = time.monotonic()
        self._persist(completed, now)
        self._completed = completed
        self._line("completed")
