"""Bounded, rank-ordered physical crossing queries in forked processes.

Only scheduler fixtures, not world evidence, belong in crossing_parallel_cases.
The physical context and crossing are inherited at fork, never sent per query.
Callers must close the returned iterator (normally with contextlib.closing),
including on an accepted candidate, an exception, or any other early exit.
Workers are private read-only query processes and are killed/reaped on close;
queries must not perform external writes or depend on shared mutable state.
"""
from __future__ import annotations

from collections import deque
import multiprocessing as mp
from multiprocessing.connection import wait
import operator
import signal
import sys
import time


def _candidate_worker(physical, crossing, connection, inherited_connections):
    # Otherwise a sibling's inherited parent endpoint can keep a dead worker's
    # pipe open and conceal EOF from the parent.
    for inherited in inherited_connections:
        inherited.close()
    trace_flush = getattr(sys, '_vh_producer_trace_flush', None)
    terminating = False

    if callable(trace_flush):
        def terminate(signum, _frame):
            nonlocal terminating
            terminating = True
            try:
                trace_flush()
            finally:
                raise SystemExit(128 + signum)
        signal.signal(signal.SIGTERM, terminate)
    try:
        # Do not let an early result cancel a sibling before its trace-aware
        # shutdown handler is installed.
        connection.send(("ready", None))
        while True:
            try:
                near_bank, far_bank, datum = connection.recv()
            except EOFError:
                return
            try:
                proof = physical.candidate(crossing, near_bank, far_bank, datum)
            except BaseException as error:
                if terminating:
                    raise
                outcome = (False, error)
            else:
                outcome = (True, proof)
            finally:
                if callable(trace_flush) and not terminating:
                    # Publish no proof until child-only producer reads/imports
                    # are recorded. A failed flush must remain visible debt.
                    trace_flush()
            try:
                connection.send(outcome)
            except Exception as error:
                # A serialization failure is an error at THIS request, never
                # a synthetic refusal or a silently lost worker exception.
                connection.send((False, RuntimeError(
                    f"cannot transfer physical candidate outcome: {error!r}")))
    finally:
        try:
            if callable(trace_flush) and not terminating:
                trace_flush()
        finally:
            connection.close()


def ordered_candidates(physical, crossing, requests, workers):
    """Yield ``(original_request, proof)`` in exactly the iterable's order.

    At most ``2 * workers`` requests are outstanding, including completed
    results awaiting their rank. Idle workers can advance within that bounded
    lookahead while an earlier query runs. Request metadata stays in the
    parent, so need not be pickleable. Only banks, datum, proofs and exception
    objects cross pipes. Candidate errors (including CoverageError and
    SystemExit), worker exits, and ordinary input-iteration/submission errors are
    raised only when their rank is consumed. Parent cancellation is immediate.

    ``workers=1`` directly evaluates in the caller. Parallel execution requires
    the configured multiprocessing start method to be fork; spawn/forkserver
    are explicitly refused, not silently substituted. Use this sequentially
    from a single-threaded process with an immutable physical-context snapshot.
    Worker-local query caches are deliberately not merged back into the parent.
    """
    workers = operator.index(workers)
    if workers < 1:
        raise ValueError("crossing workers must be at least 1")
    requests = iter(requests)
    if workers == 1:
        for request in requests:
            yield request, physical.candidate(crossing, request['near_bank'],
                                             request['far_bank'], request['datum'])
        return
    context = mp.get_context()
    if context.get_start_method() != 'fork':
        raise ValueError("parallel crossing queries require multiprocessing start method 'fork'; "
                         f"got {context.get_start_method()!r}")

    processes, connections, pending = [], [], deque()
    running, idle = {}, deque()
    exhausted = False
    lookahead = 2 * workers

    def submit(slot):
        nonlocal exhausted
        try:
            request = next(requests)
        except StopIteration:
            exhausted = True
            return
        except Exception as error:
            exhausted = True
            pending.append([None, (False, error)])
            return
        try:
            payload = request['near_bank'], request['far_bank'], request['datum']
            # A slot has at most one request: its preceding outcome has been
            # drained before it becomes idle, so its child is ready to recv.
            connections[slot].send(payload)
        except Exception as error:
            exhausted = True
            pending.append([request, (False, error)])
        else:
            entry = [request, None]
            pending.append(entry)
            running[slot] = entry

    def collect(*, block):
        if not running:
            return False
        ready = wait(tuple(handle for slot in running
                           for handle in (connections[slot], processes[slot].sentinel)),
                     timeout=None if block else 0)
        collected = False
        for slot, entry in tuple(running.items()):
            connection, process = connections[slot], processes[slot]
            if connection not in ready and process.sentinel not in ready:
                continue
            reusable = False
            try:
                if connection not in ready and not connection.poll():
                    raise RuntimeError("physical candidate worker exited without an outcome "
                                       f"(exit code {process.exitcode})")
                try:
                    ok, outcome = connection.recv()
                except EOFError as error:
                    raise RuntimeError("physical candidate worker closed without an outcome") from error
                entry[1] = ok, outcome
                reusable = process.sentinel not in ready
            except Exception as error:
                # Transport failures belong to the assigned rank, just like
                # candidate exceptions; another worker can still finish first.
                # Parent cancellation must unwind immediately, not wait behind
                # an earlier rank or disappear when that rank is accepted.
                entry[1] = False, error
            del running[slot]
            if reusable:
                idle.append(slot)
            collected = True
        return collected

    try:
        for slot in range(workers):
            parent, child = context.Pipe()
            connections.append(parent)
            process = context.Process(target=_candidate_worker,
                args=(physical, crossing, child, tuple(connections)),
                name=f'crossing-candidate-{slot}')
            processes.append(process)
            try:
                process.start()
            finally:
                child.close()
        for connection, process in zip(connections, processes):
            ready = wait((connection, process.sentinel))
            if connection not in ready and not connection.poll():
                raise RuntimeError("physical candidate worker failed during startup")
            if connection.recv() != ("ready", None):
                raise RuntimeError("physical candidate worker sent no startup acknowledgement")
        idle.extend(range(workers))
        while pending or not exhausted:
            if pending and pending[0][1] is not None:
                request, (ok, outcome) = pending.popleft()
                if not ok:
                    raise outcome
                yield request, outcome
                # Only resume permits more input or work. In particular, an
                # already buffered next rank is yielded before any refill.
                continue
            # Always drain ready slots before refilling, even when the window
            # is full. A fast child's result pipe must not wait for rank zero.
            if collect(block=False):
                continue
            if not exhausted and idle and len(pending) < lookahead:
                submit(idle.popleft())
                continue
            if running:
                collect(block=True)
            else:
                break
    finally:
        # Untraced workers have no writes to finish. Traced workers get one
        # shared, bounded grace period to flush their child-specific token.
        # A native query that cannot handle SIGTERM promptly is still killed;
        # its unflushed .start marker remains producer-tracing debt.
        traced = callable(getattr(sys, '_vh_producer_trace_flush', None))
        if traced:
            for process in processes:
                if process.pid is not None and process.is_alive():
                    process.terminate()
            deadline = time.monotonic() + 0.5
            for process in processes:
                if process.pid is not None:
                    process.join(max(0.0, deadline - time.monotonic()))
        for process in processes:
            if process.pid is not None and process.is_alive():
                process.kill()
        for process in processes:
            if process.pid is not None:
                process.join()
                process.close()
        for connection in connections:
            connection.close()
