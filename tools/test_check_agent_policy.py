"""The policy/docs consistency check: it exists because prose drifts and checks do not."""

import re
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


def _copy_with_go(tmp_path: Path) -> Path:
    root = _copy(tmp_path)
    (root / "internal/app").mkdir(parents=True)
    shutil.copy(REPO / "internal/app/verbs.go", root / "internal/app/verbs.go")
    return root


def test_the_go_registry_must_agree_with_the_policy(tmp_path):
    root = _copy_with_go(tmp_path)
    assert check_agent_policy.check(root) == []


def test_quietly_downgrading_a_mutating_verb_in_code_is_caught(tmp_path):
    # The failure this guards against: a verb that still reads world_state in the policy while
    # the code treats it as a read, so it would run without an operator ever confirming it.
    root = _copy_with_go(tmp_path)
    registry = root / "internal/app/verbs.go"
    registry.write_text(re.sub(
        r'("deploy_apply":\s*\{ID: "deploy_apply", Class: )ClassWorldState', r"\1ClassRead",
        registry.read_text()))
    problems = check_agent_policy.check(root)
    assert any("deploy_apply" in p and "verbs.go" in p for p in problems), problems


def test_a_verb_the_code_never_implements_is_caught(tmp_path):
    root = _copy_with_go(tmp_path)
    registry = root / "internal/app/verbs.go"
    registry.write_text(registry.read_text().replace(
        '"world_backup":  {ID: "world_backup", Class: ClassWorldState, Operation: "backup", NeedsWorld: true},', ""))
    problems = check_agent_policy.check(root)
    assert any("world_backup" in p and "absent from internal/app/verbs.go" in p for p in problems), problems


def _copy_with_readme(tmp_path: Path) -> Path:
    root = _copy_with_go(tmp_path)
    shutil.copy(REPO / "README.md", root / "README.md")
    return root


def test_the_readme_counts_match_the_code():
    assert check_agent_policy.check(REPO) == []


def test_a_stale_readme_count_is_caught(tmp_path):
    # An installation guide claiming more verbs work than do is the kind of number someone acts
    # on, so it is checked against the table that answers it rather than trusted.
    #
    # The current count is read out of the README rather than written here: hardcoding it made
    # this test silently stop testing anything the first time a verb was added, because the
    # corruption it applies became a no-op replace that left a correct README behind.
    root = _copy_with_readme(tmp_path)
    readme = root / "README.md"
    text = readme.read_text()
    match = re.search(r"(\d+) execute through the portal today", text)
    assert match, "the README no longer states an executing count in the form the checker reads"
    stale = int(match.group(1)) + 5
    readme.write_text(text.replace(match.group(0), f"{stale} execute through the portal today"))
    problems = check_agent_policy.check(root)
    assert any(f"README.md says {stale} executing" in p for p in problems), problems


def test_a_stale_forbidden_count_is_caught(tmp_path):
    root = _copy_with_readme(tmp_path)
    readme = root / "README.md"
    readme.write_text(readme.read_text().replace(" 4 forbidden", " 2 forbidden"))
    problems = check_agent_policy.check(root)
    assert any("forbidden" in p and "README.md says 2" in p for p in problems), problems
