"""The approval watcher: it exists so waiting on a gate costs nobody a round trip.

The payloads below are the shapes the bridge actually answers with - a `messages`
list whose outcome is stated in prose, plus `awaiting_approval` entries - so a
change to how outcomes are worded fails here rather than silently going quiet.
"""

import portal_approval_watch as watch


def test_a_succeeded_call_is_announced_once():
    payload = {
        "messages": [{"text": "succeeded: mod_add world=Hrafnheim Smoothbrain-Mining 1.1.6"}],
        "awaiting_approval": [],
        "cursor": 12,
    }
    announced: set[str] = set()

    first = watch.watch_lines(payload, announced)
    assert first == ["WATCH:SETTLED succeeded: mod_add world=Hrafnheim Smoothbrain-Mining 1.1.6"]

    # A replayed cursor must not re-announce a decision the caller already saw.
    assert watch.watch_lines(payload, announced) == []


def test_denied_and_failed_are_settled_too():
    for word in ("denied", "failed", "refused"):
        lines = watch.watch_lines({"messages": [{"text": f"{word}: deploy_apply world=Asgard"}]}, set())
        assert lines and lines[0].startswith("WATCH:SETTLED"), word


def test_ordinary_chatter_is_not_a_decision():
    payload = {"messages": [
        {"text": "Awaiting approval: mod_add world=Hrafnheim Smoothbrain-Farming 2.2.2"},
        {"text": "plan only; rerun with --apply after stopping server"},
    ]}
    assert watch.watch_lines(payload, set()) == []


def test_a_pending_call_is_reported_once_while_it_waits():
    payload = {"awaiting_approval": [{"id": "abc123", "verb": "deploy_apply",
                                      "summary": "deploy_apply world=Hrafnheim"}]}
    announced: set[str] = set()

    assert watch.watch_lines(payload, announced) == ["WATCH:PENDING verb=deploy_apply id=abc123"]
    # Still waiting on the next tick is not news.
    assert watch.watch_lines(payload, announced) == []


def test_a_message_with_no_text_field_still_yields_its_outcome():
    # Field naming has moved before; the outcome must survive that.
    payload = {"messages": [{"detail": "succeeded:  deploy_apply   world=Hrafnheim  deployed=true"}]}
    lines = watch.watch_lines(payload, set())
    assert lines == ["WATCH:SETTLED succeeded: deploy_apply world=Hrafnheim deployed=true"]


def test_an_unknown_shape_is_summarized_rather_than_dropped():
    payload = {"messages": [{"kind": "verb", "outcome": "failed", "verb": "world_backup"}]}
    lines = watch.watch_lines(payload, set())
    assert len(lines) == 1
    assert "failed" in lines[0] and "world_backup" in lines[0]


def test_long_text_is_truncated_so_one_event_stays_one_line():
    payload = {"messages": [{"text": "failed: " + "x" * 900}]}
    line = watch.watch_lines(payload, set())[0]
    assert len(line) <= len("WATCH:SETTLED ") + 400


def test_an_empty_payload_says_nothing():
    assert watch.watch_lines({}, set()) == []
    assert watch.watch_lines({"messages": [], "awaiting_approval": []}, set()) == []
