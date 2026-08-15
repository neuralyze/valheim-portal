"""The per-frame lint: the bug it exists for, and the ways it must not cry wolf."""

import subprocess
import sys
from pathlib import Path

import check_perframe_work as lint

TOOL = Path(__file__).resolve().parent / "check_perframe_work.py"

# The shape that shipped on 13 Aug: a sweep on a frame path whose only stop condition
# was success, in a world where success is impossible.
SHIPPED_BUG = """
internal static class FastLinkTitle
{
    private static void Tick()
    {
        if (_dead || _done) return;
        foreach (Text label in Resources.FindObjectsOfTypeAll<Text>())
        {
            if (!Matches(label.text)) continue;
            label.gameObject.SetActive(false);
            _done = true;
        }
    }
}
internal class Plugin
{
    private void Update()
    {
        FastLinkTitle.Tick();
    }
}
"""


def _repo(tmp_path: Path, source: str, baseline: str | None = None) -> Path:
    (tmp_path / "tools/vrfixes").mkdir(parents=True)
    (tmp_path / "tools/vrfixes/Thing.cs").write_text(source)
    if baseline is not None:
        (tmp_path / "tools/perframe-baseline.txt").write_text(baseline)
    return tmp_path


def test_it_catches_the_bug_it_was_written_for(tmp_path):
    problems = lint.check(_repo(tmp_path, SHIPPED_BUG))
    assert problems, "a scene sweep reached from Update must be reported"
    assert "FindObjectsOfTypeAll" in problems[0]
    assert "Update -> Tick" in problems[0]


def test_a_stated_bound_is_accepted(tmp_path):
    bounded = SHIPPED_BUG.replace(
        "if (_dead || _done) return;",
        "if (_dead || _done) return;\n        // lint:per-frame bounded by MaxLooks and the InWorld guard",
    )
    assert lint.check(_repo(tmp_path, bounded)) == []


def test_work_off_the_frame_path_is_ignored(tmp_path):
    off_path = SHIPPED_BUG.replace("private void Update()", "private void OnEnable()")
    assert lint.check(_repo(tmp_path, off_path)) == []


def test_allocation_directly_in_update_is_reported(tmp_path):
    source = """
internal class Plugin
{
    private void Update()
    {
        var names = new List<string>();
        Report(string.Format("{0}", names.Count));
    }
    private void Report(string s) { }
}
"""
    problems = lint.check(_repo(tmp_path, source))
    assert any("allocation on every frame" in p for p in problems)


def test_a_baselined_site_does_not_fail_the_build(tmp_path):
    repo = _repo(tmp_path, SHIPPED_BUG, baseline="Thing.cs:Tick:Resources.FindObjectsOfTypeAll\n")
    result = subprocess.run([sys.executable, str(TOOL), str(repo)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "no new violations" in result.stdout


def test_a_site_missing_from_the_baseline_fails_the_build(tmp_path):
    repo = _repo(tmp_path, SHIPPED_BUG, baseline="SomethingElse.cs:Tick:GameObject.Find\n")
    result = subprocess.run([sys.executable, str(TOOL), str(repo)], capture_output=True, text=True)
    assert result.returncode == 1
    assert "NEW unbounded" in result.stderr


def test_the_fingerprint_ignores_line_numbers(tmp_path):
    a = "tools/vrfixes/Thing.cs:31: Tick(): scene-wide search: FindObjectsOfType [reached by Update -> Tick]."
    b = "tools/vrfixes/Thing.cs:99: Tick(): scene-wide search: FindObjectsOfType [reached by Update -> Tick]."
    assert lint.fingerprint(a) == lint.fingerprint(b) == "Thing.cs:Tick:FindObjectsOfType"
