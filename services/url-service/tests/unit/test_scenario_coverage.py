"""Definition of Done check: every OpenSpec scenario has at least one linked test."""

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parents[1]
REPO = TESTS.parents[2]
SPEC_FILES = [
    *REPO.glob("openspec/specs/*/spec.md"),
    *(p for p in REPO.glob("openspec/changes/*/specs/*/spec.md") if "/archive/" not in str(p)),
]


def spec_scenarios() -> set[tuple[str, str]]:
    found = set()
    for path in SPEC_FILES:
        capability = path.parent.name
        for name in re.findall(r"^#### Scenario: (.+?)\s*$", path.read_text(), re.M):
            found.add((capability, name))
    return found


def linked_scenarios() -> set[tuple[str, str]]:
    pattern = re.compile(r'@scenario\(\s*"([^"]+)",\s*"([^"]+)"\s*\)')
    found = set()
    for path in TESTS.rglob("test_*.py"):
        found.update(pattern.findall(path.read_text()))
    return found


def test_spec_files_found():
    assert SPEC_FILES, f"no spec files under {REPO / 'openspec'}"


def test_every_scenario_has_a_test():
    missing = sorted(spec_scenarios() - linked_scenarios())
    assert not missing, "scenarios without a test:\n" + "\n".join(f"  {c}: {n}" for c, n in missing)


def test_no_test_links_to_unknown_scenario():
    unknown = sorted(linked_scenarios() - spec_scenarios())
    assert not unknown, "tests linked to scenarios not in any spec:\n" + "\n".join(
        f"  {c}: {n}" for c, n in unknown
    )
