"""Deterministic CPU scheduler fixtures; NEVER measured-world evidence.

Like crossing_search_cases, parent-run functions return matches_expected rows.
Events enforce scheduling order; timeouts only bound deadlocks in broken code.
No real physical scans, world files, servers, or external writes are involved.
"""
from __future__ import annotations

from contextlib import closing
import multiprocessing as mp
import os
import threading

from crossing_parallel import ordered_candidates
from crossing_physical import CoverageError


class _CPUQuery:
    """Synthetic query behavior tests the scheduler, not physical feasibility."""
    def __init__(self, *, later_done=None, completion=None, count=None,
                 failure=None, blocked=None, release=None, worker_pids=None):
        self.later_done = later_done
        self.completion = completion
        self.count = count
        self.failure = failure
        self.blocked = blocked
        self.release = release
        self.worker_pids = worker_pids
        # The context must be inherited, not pickled along with every request.
        self.native_fixture = lambda: None

    def candidate(self, crossing, near_bank, far_bank, datum):
        rank = near_bank['rank']
        if self.worker_pids is not None:
            self.worker_pids[rank] = os.getpid()
        if self.blocked is not None:
            if rank == 1:
                self.blocked.set()
                self.release.wait()
            elif rank == 0 and not self.blocked.wait(10):
                raise AssertionError('later worker never entered blocked query')
        if rank == 0 and self.later_done is not None:
            if not self.later_done.wait(10):
                raise AssertionError('later worker never completed before rank zero')
        if self.completion is not None:
            with self.count.get_lock():
                self.completion[self.count.value] = rank
                self.count.value += 1
        if rank == 1 and self.failure is not None:
            try:
                raise self.failure
            finally:
                if self.later_done is not None:
                    self.later_done.set()
        if rank == 1 and self.later_done is not None:
            self.later_done.set()
        return dict(ok=rank == 0, stage='constructed_scheduler_only', rank=rank,
                    crossing=crossing['id'], far=far_bank['rank'], datum=datum,
                    cpu=sum(k*k for k in range(100 + rank)))


class _LookaheadQuery(_CPUQuery):
    """Rank zero needs work beyond the initial workers, not just rank one."""
    def __init__(self, head_started, independent_done, **kwargs):
        super().__init__(**kwargs)
        self.head_started = head_started
        self.independent_done = independent_done

    def candidate(self, crossing, near_bank, far_bank, datum):
        rank = near_bank['rank']
        if rank == 0:
            self.head_started.set()
            if not self.independent_done.wait(10):
                raise AssertionError('idle worker never advanced beyond its first rank')
        elif not self.head_started.wait(10):
            raise AssertionError('earliest worker never entered its blocked query')
        proof = super().candidate(crossing, near_bank, far_bank, datum)
        if rank == 1:
            # Exceed pipe capacity: collecting only rank zero would strand this
            # child in send(), not merely leave a small completed result unread.
            proof['transfer_fixture'] = bytes(1024 * 1024)
        if rank == 2:
            self.independent_done.set()
        return proof


class _ExitingQuery(_CPUQuery):
    def __init__(self, exit_code, **kwargs):
        super().__init__(**kwargs)
        self.exit_code = exit_code

    def candidate(self, crossing, near_bank, far_bank, datum):
        if near_bank['rank'] == 1:
            self.later_done.set()
            os._exit(self.exit_code)
        return super().candidate(crossing, near_bank, far_bank, datum)


class _UnsendableDatum:
    def __init__(self, release):
        self.release = release

    def __reduce__(self):
        self.release.set()
        raise TypeError('constructed payload serialization failure')


def _requests(n):
    return [dict(near_bank={'rank': rank}, far_bank={'rank': rank + 10},
                 datum=30.6 + rank, parent_only=lambda: None)
            for rank in range(n)]


def _children():
    return {p.pid for p in mp.active_children()}


def rank_order_cases():
    context = mp.get_context('fork')
    head_started, independent_done = context.Event(), context.Event()
    completion, count = context.Array('i', 9), context.Value('i', 0)
    requests, pulled = _requests(9), []

    def lazy_requests():
        for request in requests:
            pulled.append(request['near_bank']['rank'])
            yield request
    worker_pids = context.Array('i', 9)
    physical = _LookaheadQuery(head_started, independent_done,
        completion=completion, count=count, worker_pids=worker_pids)
    baseline = _children()
    identities, ranks = [], []
    with closing(ordered_candidates(physical, {'id': 'constructed'}, lazy_requests(), 2)) as candidates:
        for request, proof in candidates:
            rank = proof['rank']
            identities.append(request is requests[rank])
            ranks.append(rank)
    return [dict(case='fork_completion_order_differs_but_results_retain_rank',
                 completion_order=list(completion), result_order=ranks,
                 worker_pids=list(worker_pids),
                 matches_expected=ranks == list(range(len(requests)))
                    and list(completion).index(2) < list(completion).index(0)
                    and head_started.is_set() and independent_done.is_set()
                    and all(identities) and pulled == ranks and _children() == baseline
                    and len(set(worker_pids)) == 2 and os.getpid() not in worker_pids)]


def close_after_acceptance_cases():
    context = mp.get_context('fork')
    blocked, release = context.Event(), context.Event()
    close_done, deadline = threading.Event(), threading.Event()
    physical = _CPUQuery(blocked=blocked, release=release)
    baseline = _children()
    pulled = []

    def lazy_requests():
        for request in _requests(9):
            pulled.append(request['near_bank']['rank'])
            yield request

    candidates = ordered_candidates(physical, {'id': 'constructed'}, lazy_requests(), 2)
    # Start the watchdog only AFTER fork: production requires a single-threaded
    # forking caller. A broken close is released after the deadline, not hung.
    watchdog = None
    owned = []
    try:
        request, proof = next(candidates)
        owned = [p for p in mp.active_children() if p.pid not in baseline]

        def release_on_deadlock():
            if not close_done.wait(5):
                deadline.set()
                release.set()

        watchdog = threading.Thread(target=release_on_deadlock, daemon=True)
        watchdog.start()
        pulled_at_acceptance = tuple(pulled)
        candidates.close()
        closed_while_blocked = not release.is_set()
        no_refill = tuple(pulled) == pulled_at_acceptance
        no_children = _children() == baseline
        close_done.set()
    finally:
        close_done.set()
        candidates.close()
        if watchdog is not None:
            watchdog.join()
        for process in owned:
            # Process handles can already be closed by the scheduler.
            try:
                if process.is_alive():
                    process.kill()
                process.join()
            except ValueError:
                pass
    return [dict(case='accepted_rank_closes_without_waiting_for_blocked_later_query',
                 matches_expected=proof['ok'] and request['near_bank']['rank'] == 0
                    and blocked.is_set() and closed_while_blocked and no_children
                    and no_refill and not deadline.is_set())]


def ordered_exception_cases():
    context = mp.get_context('fork')
    rows = []
    for error in (CoverageError('constructed missing lattice'),
                  SystemExit('constructed physical refusal'),
                  ValueError('constructed ordinary failure')):
        baseline = _children()
        later_done = context.Event()
        physical = _CPUQuery(later_done=later_done, failure=error)
        with closing(ordered_candidates(physical, {'id': 'constructed'}, _requests(2), 2)) as candidates:
            first = next(candidates)
            try:
                next(candidates)
            except BaseException as actual:
                preserved = type(actual) is type(error) and actual.args == error.args
            else:
                preserved = False
        rows.append(dict(case='later_' + type(error).__name__ + '_surfaces_only_at_its_rank',
            matches_expected=first[1]['ok'] and later_done.is_set()
                and preserved and _children() == baseline))
        # The same already-completed error must remain irrelevant on acceptance.
        later_done = context.Event()
        physical = _CPUQuery(later_done=later_done, failure=error)
        with closing(ordered_candidates(physical, {'id': 'constructed'}, _requests(2), 2)) as candidates:
            first = next(candidates)
        rows.append(dict(case='closing_accepted_result_ignores_later_' + type(error).__name__,
            matches_expected=first[1]['ok'] and later_done.is_set() and _children() == baseline))
    return rows


def parity_and_lazy_failure_cases():
    requests = _requests(4)
    crossing = {'id': 'constructed'}
    with closing(ordered_candidates(_CPUQuery(), crossing, requests, 1)) as candidates:
        serial = list(candidates)
    with closing(ordered_candidates(_CPUQuery(), crossing, requests, 3)) as candidates:
        parallel = list(candidates)
    rows = [dict(case='serial_and_fork_proofs_and_original_metadata_agree',
                 matches_expected=serial == parallel
                    and all(r is requests[k] for k, (r, _) in enumerate(parallel)))]
    for workers in (1, 2):
        def lazy_failure():
            yield requests[0]
            raise ValueError('constructed lazy enumeration failure')
        baseline = _children()
        with closing(ordered_candidates(_CPUQuery(), crossing, lazy_failure(), workers)) as candidates:
            first = next(candidates)
            try:
                next(candidates)
            except ValueError as error:
                expected = str(error) == 'constructed lazy enumeration failure'
            else:
                expected = False
        rows.append(dict(case=f'lazy_input_error_follows_accepted_prefix_workers_{workers}',
                         matches_expected=first[1]['ok'] and expected and _children() == baseline))
    for error in (CoverageError('constructed coverage parity'), SystemExit(17), ValueError('parity')):
        outcomes = []
        for workers in (1, 2):
            prefix = []
            with closing(ordered_candidates(_CPUQuery(failure=error), crossing, requests, workers)) as candidates:
                try:
                    for _, proof in candidates:
                        prefix.append(proof)
                except BaseException as actual:
                    outcomes.append((prefix, type(actual), actual.args))
        rows.append(dict(case=type(error).__name__ + '_serial_process_error_parity',
            matches_expected=len(outcomes) == 2 and outcomes[0] == outcomes[1]
                and len(outcomes[0][0]) == 1 and outcomes[0][1] is type(error)))
    return rows


def buffered_head_cases():
    context = mp.get_context('fork')
    rows = []
    for error in (None, CoverageError('constructed lookahead coverage failure'),
                  SystemExit('constructed lookahead exit'),
                  ValueError('constructed lookahead candidate failure')):
        baseline = _children()
        requests, pulled = _requests(9), []
        head_started, independent_done = context.Event(), context.Event()

        def lazy_requests():
            for request in requests:
                pulled.append(request['near_bank']['rank'])
                yield request

        physical = _LookaheadQuery(head_started, independent_done, failure=error)
        with closing(ordered_candidates(physical, {'id': 'constructed'},
                                        lazy_requests(), 2)) as candidates:
            first = next(candidates)
            pulled_at_first = tuple(pulled)
            try:
                second = next(candidates)
            except BaseException as actual:
                second_expected = (error is not None and type(actual) is type(error)
                                   and actual.args == error.args)
            else:
                second_expected = (error is None and second[0] is requests[1]
                                   and second[1]['rank'] == 1
                                   and len(second[1]['transfer_fixture']) == 1024 * 1024)
            no_refill_before_second = tuple(pulled) == pulled_at_first
        rows.append(dict(case='buffered_' + ('result' if error is None else type(error).__name__)
                         + '_precedes_refill_and_lookahead_unblocks_earliest',
            matches_expected=first[0] is requests[0] and first[1]['rank'] == 0
                and head_started.is_set() and independent_done.is_set()
                and second_expected and no_refill_before_second
                and tuple(pulled) == pulled_at_first and _children() == baseline))
    return rows


def prefetched_input_failure_cases():
    context = mp.get_context('fork')
    rows = []
    for failure in ('iteration', 'request_fields', 'serialization'):
        for consume_error in (False, True):
            baseline = _children()
            head_started, release = context.Event(), context.Event()
            requests, pulled = _requests(3), []
            physical = _LookaheadQuery(head_started, release)

            def lazy_failure():
                for request in requests[:2]:
                    pulled.append(request['near_bank']['rank'])
                    yield request
                pulled.append(2)
                if failure == 'iteration':
                    release.set()
                    raise ValueError('constructed prefetched input failure')
                if failure == 'request_fields':
                    release.set()
                    yield dict(far_bank={}, datum=0)
                else:
                    request = dict(requests[2], datum=_UnsendableDatum(release))
                    yield request
                raise AssertionError('enumerated beyond failed submission')

            expected_type = dict(iteration=ValueError, request_fields=KeyError,
                                 serialization=TypeError)[failure]
            prefix, expected_error = [], not consume_error
            with closing(ordered_candidates(physical, {'id': 'constructed'},
                                            lazy_failure(), 2)) as candidates:
                first_request, first_proof = next(candidates)
                prefix.append(first_proof['rank'])
                pulled_at_acceptance = tuple(pulled)
                if consume_error:
                    prefix.append(next(candidates)[1]['rank'])
                    try:
                        next(candidates)
                    except BaseException as actual:
                        expected_error = type(actual) is expected_type
                    else:
                        expected_error = False
            rows.append(dict(case='prefetched_' + failure + '_failure_'
                             + ('follows_prefix' if consume_error else 'ignored_on_acceptance'),
                matches_expected=first_request is requests[0] and release.is_set()
                    and prefix == ([0, 1] if consume_error else [0])
                    and pulled_at_acceptance == (0, 1, 2) and expected_error
                    and tuple(pulled) == pulled_at_acceptance and _children() == baseline))
    return rows


def worker_exit_cases():
    context = mp.get_context('fork')
    rows = []
    for exit_code in (0, 23):
        for consume_error in (False, True):
            baseline = _children()
            later_done = context.Event()
            physical = _ExitingQuery(exit_code, later_done=later_done)
            requests = _requests(2)
            expected_error = not consume_error
            with closing(ordered_candidates(physical, {'id': 'constructed'},
                                            requests, 3)) as candidates:
                first_request, first_proof = next(candidates)
                if consume_error:
                    try:
                        next(candidates)
                    except RuntimeError as actual:
                        expected_error = 'worker' in str(actual) and 'outcome' in str(actual)
                    else:
                        expected_error = False
            rows.append(dict(case=f'worker_exit_{exit_code}_'
                             + ('eof_follows_earlier_result' if consume_error
                                else 'ignored_on_acceptance'),
                matches_expected=first_request is requests[0] and first_proof['rank'] == 0
                    and later_done.is_set() and expected_error and _children() == baseline))
    return rows


def _unsupported_method_probe(connection, method):
    mp.set_start_method(method, force=True)
    try:
        with closing(ordered_candidates(_CPUQuery(), {'id': 'constructed'}, _requests(1), 2)) as candidates:
            next(candidates)
    except ValueError as error:
        connection.send("require multiprocessing start method 'fork'" in str(error))
    else:
        connection.send(False)
    finally:
        connection.close()


def unsupported_method_cases():
    context = mp.get_context('fork')
    rows = []
    for method in mp.get_all_start_methods():
        if method == 'fork':
            continue
        parent, child = context.Pipe()
        process = context.Process(target=_unsupported_method_probe, args=(child, method))
        try:
            process.start()
            child.close()
            expected = parent.poll(10) and parent.recv()
            process.join(10)
            rows.append(dict(case=method + '_is_explicitly_refused',
                             matches_expected=expected and process.exitcode == 0))
        finally:
            if process.is_alive():
                process.kill()
            process.join()
            process.close()
            parent.close()
            child.close()
    return rows


def parent_cancellation_cases():
    from multiprocessing.connection import Connection
    from unittest.mock import patch

    context = mp.get_context("fork")
    baseline, parent_pid = _children(), os.getpid()
    head_started, release = context.Event(), context.Event()
    receive = Connection.recv
    interrupted = False

    def cancelling_receive(connection):
        nonlocal interrupted
        outcome = receive(connection)
        if (os.getpid() == parent_pid and not interrupted
                and type(outcome[0]) is bool):
            interrupted = True
            release.set()
            raise KeyboardInterrupt("constructed parent cancellation")
        return outcome

    propagated = False
    with patch.object(Connection, "recv", cancelling_receive):
        with closing(ordered_candidates(_LookaheadQuery(head_started, release),
                {"id": "constructed"}, _requests(2), 2)) as candidates:
            try:
                next(candidates)
            except KeyboardInterrupt:
                propagated = True
    return [dict(case="parent_cancellation_cannot_be_buffered_or_discarded",
        matches_expected=interrupted and propagated and _children() == baseline)]


def parallel_cases():
    return (rank_order_cases() + close_after_acceptance_cases()
            + ordered_exception_cases() + parity_and_lazy_failure_cases()
            + buffered_head_cases() + prefetched_input_failure_cases()
            + worker_exit_cases() + unsupported_method_cases()
            + parent_cancellation_cases())
