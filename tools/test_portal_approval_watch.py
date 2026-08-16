"""The approval watcher: it exists so waiting on a gate costs nobody a round trip.

The payloads below are the shapes the bridge actually answers with - a `messages`
list whose outcome is stated in prose and whose window is NOT narrowed by the
cursor, plus `awaiting_approval` entries. The history-replay test is the one that
matters: announcing on text alone made a waiter match an older decision and call
a still-pending deploy green.
"""

import portal_approval_watch as watch


def test_a_succeeded_call_above_the_floor_is_announced_once():
    payload = {
        "messages": [{"id": 7, "body": "succeeded: mod_add world=Hrafnheim Smoothbrain-Mining 1.1.6"}],
        "awaiting_approval": [],
        "cursor": 7,
    }
    state = watch.new_state(floor=6)

    assert watch.watch_lines(payload, state) == [
        "WATCH:SETTLED succeeded: mod_add world=Hrafnheim Smoothbrain-Mining 1.1.6"
    ]
    # The same window arriving again is not news.
    assert watch.watch_lines(payload, state) == []


def test_history_below_the_floor_is_never_announced():
    # The bug this file exists for: the inbox hands back old messages, so an
    # earlier deploy_apply looked like the one being waited on.
    payload = {"messages": [
        {"id": 3, "body": "succeeded: deploy_apply world=Hrafnheim deployed=true"},
        {"id": 4, "body": "succeeded: mod_add world=Hrafnheim cybrp-ItemDrawers 1.2.6"},
    ]}
    assert watch.watch_lines(payload, watch.new_state(floor=4)) == []


def test_a_call_is_settled_when_it_leaves_the_awaiting_list():
    waiting = {"awaiting_approval": [{"id": "abc123", "verb": "deploy_apply",
                                     "summary": "deploy_apply world=Hrafnheim"}]}
    state = watch.new_state()

    assert watch.watch_lines(waiting, state) == ["WATCH:PENDING verb=deploy_apply id=abc123"]
    assert watch.watch_lines(waiting, state) == []

    decided = {"awaiting_approval": []}
    assert watch.watch_lines(decided, state) == [
        "WATCH:SETTLED verb=deploy_apply id=abc123 left the approval queue"
    ]
    # Settled once; it must not keep firing.
    assert watch.watch_lines(decided, state) == []


def test_a_missing_awaiting_key_does_not_settle_pending_calls():
    # A payload that simply omits the key says nothing about what is waiting.
    state = watch.new_state()
    watch.watch_lines({"awaiting_approval": [{"id": "x", "verb": "world_stop"}]}, state)
    assert watch.watch_lines({"messages": []}, state) == []
    assert "x" in state["pending"]


def test_denied_and_failed_are_settled_too():
    for word in ("denied", "failed", "refused"):
        lines = watch.watch_lines({"messages": [{"id": 9, "body": f"{word}: deploy_apply world=Asgard"}]},
                                  watch.new_state())
        assert lines and lines[0].startswith("WATCH:SETTLED"), word


def test_ordinary_chatter_is_not_a_decision():
    payload = {"messages": [
        {"id": 10, "body": "Awaiting approval: mod_add world=Hrafnheim Smoothbrain-Farming 2.2.2"},
        {"id": 11, "body": "plan only; rerun with --apply after stopping server"},
    ]}
    assert watch.watch_lines(payload, watch.new_state()) == []


def test_an_unnumbered_message_still_yields_its_outcome_once():
    payload = {"messages": [{"detail": "succeeded:  deploy_apply   world=Hrafnheim  deployed=true"}]}
    state = watch.new_state(floor=99)
    assert watch.watch_lines(payload, state) == [
        "WATCH:SETTLED succeeded: deploy_apply world=Hrafnheim deployed=true"
    ]
    assert watch.watch_lines(payload, state) == []


def test_an_unknown_shape_is_summarized_rather_than_dropped():
    payload = {"messages": [{"id": 12, "kind": "verb", "outcome": "failed", "verb": "world_backup"}]}
    lines = watch.watch_lines(payload, watch.new_state())
    assert len(lines) == 1
    assert "failed" in lines[0] and "world_backup" in lines[0]


def test_long_text_is_truncated_so_one_event_stays_one_line():
    payload = {"messages": [{"id": 13, "body": "failed: " + "x" * 900}]}
    line = watch.watch_lines(payload, watch.new_state())[0]
    assert len(line) <= len("WATCH:SETTLED ") + 400


def test_the_floor_advances_to_the_highest_id_seen():
    state = watch.new_state()
    watch.watch_lines({"messages": [{"id": 41, "body": "hello"}, {"id": 42, "body": "world"}]}, state)
    assert state["floor"] == 42


def test_an_empty_payload_says_nothing():
    assert watch.watch_lines({}, watch.new_state()) == []
    assert watch.watch_lines({"messages": [], "awaiting_approval": []}, watch.new_state()) == []
