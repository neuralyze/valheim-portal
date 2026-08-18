"""The upstream source registry: it exists so upstream movement cannot go unseen.

Two real failures prompted it, and both are asserted here as behaviour rather than
described in prose: a checkout silently drifting off the commit the registry pins (the
artifacts we ship are built from that tree, so its commit is part of what we published),
and a source whose upstream moved without anyone reading what changed - the container
project had even changed owner months earlier while our remote URL still named the old one.

`verify` is deliberately offline so it can run in the ordinary gate suite. The network half
lives in `status`, which is a periodic run.
"""

import json
import subprocess

import pytest

import upstream_sources as upstream


def write_registry(tmp_path, **overrides):
    entry = {
        "id": "vhvr",
        "repo": "example/vhvr-mod",
        "license": "GPL-3.0",
        "why": "The VR mod we build and ship.",
        "checkout_path": str(tmp_path / "checkout"),
        "pinned_commit": "aaaaaaaaaaaa",
        "reviewed_commit": "aaaaaaaaaaaa",
        "reviewed_at": "2026-08-18",
    }
    entry.update(overrides)
    path = tmp_path / "upstream-sources.json"
    path.write_text(json.dumps({"schema": 1, "sources": [entry]}) + "\n")
    return path


def git_checkout(path, dirty=0):
    """A real git repository, because the checks read a real HEAD."""
    path.mkdir(parents=True, exist_ok=True)
    run = lambda *args: subprocess.run(["git", "-C", str(path), *args], check=True,
                                       capture_output=True, text=True)
    run("init", "-q", "-b", "main")
    (path / "README").write_text("upstream\n")
    run("add", "README")
    run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "first")
    head = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    for index in range(dirty):
        (path / f"local-{index}.txt").write_text("changed\n")
    return head


def test_a_checkout_that_drifted_off_the_pin_is_the_failure(tmp_path):
    head = git_checkout(tmp_path / "checkout")
    registry = upstream.load(write_registry(tmp_path, pinned_commit="deadbeefdead"))

    problems = upstream.verify(registry)

    assert any("checkout is at" in problem and head[:12] in problem for problem in problems)


def test_a_checkout_on_the_pin_passes(tmp_path):
    head = git_checkout(tmp_path / "checkout")
    registry = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit=head))

    assert upstream.verify(registry) == []


def test_local_modifications_have_to_be_declared(tmp_path):
    # A patched tree is legitimate - we carry fixes for the VR mod - but an undeclared one
    # means nobody knows what we ship on top of upstream.
    head = git_checkout(tmp_path / "checkout", dirty=3)
    registry = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit=head))

    assert any("modified files and no local_changes" in problem for problem in upstream.verify(registry))

    declared = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit=head,
                                            local_changes="Two guards we offered upstream."))
    assert upstream.verify(declared) == []


def test_an_absent_checkout_is_not_a_failure(tmp_path):
    # A machine that only runs the portal has no VR mod checkout to compare.
    registry = upstream.load(write_registry(tmp_path))
    assert upstream.verify(registry) == []


def test_missing_fields_are_named_individually(tmp_path):
    registry = upstream.load(write_registry(tmp_path, why="", license=""))

    problems = upstream.verify(registry)

    assert any(problem.endswith("missing why") for problem in problems)
    assert any(problem.endswith("missing license") for problem in problems)


def test_reviewing_ahead_of_the_pin_needs_a_note(tmp_path):
    # Reading what is coming before taking it is fine; doing it silently is not.
    head = git_checkout(tmp_path / "checkout")
    registry = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit="bbbbbbbbbbbb"))

    assert any("without a note" in problem for problem in upstream.verify(registry))

    noted = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit="bbbbbbbbbbbb",
                                         reviewed_note="Read 3 commits; none affect our build."))
    assert upstream.verify(noted) == []


def test_status_reports_unreviewed_upstream_movement(tmp_path, monkeypatch):
    head = git_checkout(tmp_path / "checkout")
    registry = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit=head))
    monkeypatch.setattr(upstream, "remote_head",
                        lambda repo, token=None: ("ffffffffffff0000", "2026-07-31", "Refine gestured draw logic"))

    rows, unreviewed = upstream.status(registry)

    assert unreviewed is True
    assert rows[0]["remote"] == "ffffffffffff" and rows[0]["reviewed_up_to_date"] is False
    assert rows[0]["remote_subject"] == "Refine gestured draw logic"


def test_status_is_quiet_when_upstream_is_already_reviewed(tmp_path, monkeypatch):
    head = git_checkout(tmp_path / "checkout")
    registry = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit="ffffffffffff0000"))
    monkeypatch.setattr(upstream, "remote_head",
                        lambda repo, token=None: ("ffffffffffff0000", "2026-07-31", "Refine gestured draw logic"))

    rows, unreviewed = upstream.status(registry)

    assert unreviewed is False and rows[0]["reviewed_up_to_date"] is True


def test_an_unreachable_upstream_is_unknown_rather_than_current(tmp_path, monkeypatch):
    # A network failure must not read as "nothing changed upstream".
    head = git_checkout(tmp_path / "checkout")
    registry = upstream.load(write_registry(tmp_path, pinned_commit=head, reviewed_commit=head))

    def unreachable(repo, token=None):
        raise upstream.RegistryError("cannot read example/vhvr-mod from GitHub: offline")

    monkeypatch.setattr(upstream, "remote_head", unreachable)
    rows, unreviewed = upstream.status(registry)

    assert rows[0]["reviewed_up_to_date"] is None and unreviewed is False
    assert "offline" in rows[0]["remote_subject"]


def test_a_review_records_what_was_concluded(tmp_path):
    path = write_registry(tmp_path)
    registry = upstream.load(path)

    upstream.review(registry, "vhvr", "ffffffffffff", "Read 1 commit: bow draw refinement, take it with the next build.")
    upstream.save(registry, path)

    reloaded = upstream.load(path)["sources"][0]
    assert reloaded["reviewed_commit"] == "ffffffffffff"
    assert "bow draw" in reloaded["reviewed_note"]
    assert reloaded["reviewed_at"]


def test_a_review_without_a_conclusion_is_refused(tmp_path):
    registry = upstream.load(write_registry(tmp_path))

    with pytest.raises(upstream.RegistryError, match="needs a note"):
        upstream.review(registry, "vhvr", "ffffffffffff", "   ")


def test_an_unknown_source_names_the_ones_that_exist(tmp_path):
    registry = upstream.load(write_registry(tmp_path))

    with pytest.raises(upstream.RegistryError, match="known: vhvr"):
        upstream.source(registry, "typo")


def test_the_shipped_registry_verifies(tmp_path):
    """The real file, so a malformed edit fails the gate rather than the next run."""
    registry = upstream.load()
    assert registry["sources"], "the registry lists no sources"
    for entry in registry["sources"]:
        for field in upstream.REQUIRED_FIELDS:
            assert entry.get(field), f"{entry.get('id')}: missing {field}"
