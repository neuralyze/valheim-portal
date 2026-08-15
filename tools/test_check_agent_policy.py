"""The policy/docs consistency check: it exists because prose drifts and checks do not."""

import shutil
from pathlib import Path

import yaml

import check_agent_policy

REPO = Path(__file__).resolve().parent.parent


def _copy(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    shutil.copy(REPO / "policy.yaml", tmp_path / "policy.yaml")
    shutil.copy(REPO / "docs/agent-harness.md", tmp_path / "docs/agent-harness.md")
    return tmp_path


def test_the_repository_is_consistent():
    assert check_agent_policy.check(REPO) == []


def test_a_verb_missing_from_the_docs_fails(tmp_path):
    root = _copy(tmp_path)
    policy = root / "policy.yaml"
    # Written through the parser: appending text lands inside the trailing mapping and only
    # proves that invalid YAML is rejected, which is a different test.
    document = yaml.safe_load(policy.read_text())
    document["verbs"].append(
        {"id": "world_nuke", "class": "world_state", "maps_to": "something drastic",
         "purpose": "Undocumented on purpose."}
    )
    policy.write_text(yaml.safe_dump(document, sort_keys=False))
    problems = check_agent_policy.check(root)
    assert any("world_nuke" in p and "not in the documented table" in p for p in problems)


def test_a_class_mismatch_between_file_and_docs_fails(tmp_path):
    root = _copy(tmp_path)
    docs = root / "docs/agent-harness.md"
    docs.write_text(docs.read_text().replace(
        "| `publish_profile` | player_facing |", "| `publish_profile` | read |"))
    problems = check_agent_policy.check(root)
    assert any("publish_profile" in p and "player_facing" in p for p in problems)


def test_making_a_mutating_class_automatic_fails(tmp_path):
    root = _copy(tmp_path)
    policy = root / "policy.yaml"
    policy.write_text(policy.read_text().replace(
        "  world_state:\n    approval: every_invocation", "  world_state:\n    approval: none"))
    problems = check_agent_policy.check(root)
    assert any("every_invocation" in p for p in problems)


def test_a_player_facing_verb_without_evidence_fails(tmp_path):
    root = _copy(tmp_path)
    policy = root / "policy.yaml"
    text = policy.read_text().replace(
        """    evidence: >
      release_status afterwards reports nothing pending for that world, read from the cutover
      record rather than from the confirmation's own exit code.
""", "")
    policy.write_text(text)
    problems = check_agent_policy.check(root)
    assert any("release_confirm" in p and "evidence" in p for p in problems)


def test_the_approval_class_table_is_not_read_as_verbs(tmp_path):
    # The document has two tables of the same shape; only the verb table describes verbs.
    root = _copy(tmp_path)
    assert check_agent_policy.check(root) == []
