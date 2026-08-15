"""The beads identity guard: it exists because the failure it catches happened twice."""

import json
from pathlib import Path

import check_beads_workspace

OURS = "9242930c-3c25-4ec5-a293-27b628595724"
GAME_PROJECT = "bb30f1b5-12a7-4d57-949e-75f590fe0963"


def _workspace(tmp_path: Path, metadata: dict | None = None, config: str | None = None) -> Path:
    beads = tmp_path / ".beads"
    beads.mkdir()
    (beads / "expected-project.json").write_text(
        json.dumps({"project_id": OURS, "prefix": "vhp", "sync_remote": ""})
    )
    if metadata is not None:
        (beads / "metadata.json").write_text(json.dumps(metadata))
    if config is not None:
        (beads / "config.yaml").write_text(config)
    return tmp_path


def test_our_own_workspace_passes(tmp_path):
    root = _workspace(tmp_path, metadata={"project_id": OURS}, config='sync.remote: ""\n')
    assert check_beads_workspace.check(root) == []


def test_a_fresh_clone_with_no_database_passes(tmp_path):
    # CI checks out the repo without a local Dolt database; that is not a failure.
    assert check_beads_workspace.check(_workspace(tmp_path)) == []


def test_another_projects_tracker_is_rejected(tmp_path):
    root = _workspace(tmp_path, metadata={"project_id": GAME_PROJECT})
    problems = check_beads_workspace.check(root)
    assert len(problems) == 1
    assert GAME_PROJECT in problems[0]
    assert "--remote ''" in problems[0]


def test_an_inherited_sync_remote_is_rejected(tmp_path):
    root = _workspace(
        tmp_path,
        metadata={"project_id": OURS},
        config='sync.remote: "git+https://source.neuralyze.com/scm/repo/Neuralyze-Stack/vibeheim"\n',
    )
    problems = check_beads_workspace.check(root)
    assert len(problems) == 1
    assert "vibeheim" in problems[0]


def test_a_commented_remote_is_not_read_as_configuration(tmp_path):
    root = _workspace(
        tmp_path,
        metadata={"project_id": OURS},
        config='# sync.remote: "git+https://example.invalid/other"\nsync.remote: ""\n',
    )
    assert check_beads_workspace.check(root) == []


def test_a_missing_expectation_file_is_itself_a_failure(tmp_path):
    (tmp_path / ".beads").mkdir()
    problems = check_beads_workspace.check(tmp_path)
    assert problems and "expected-project.json" in problems[0]
