"""Migration guards: skill entry, documentation references, and CLI surface.

Structural checks for the herdr-plan-manager migration. They keep one
discoverable skill entry, make every live reference and evidence citation in
the acceptance map resolve, and confirm the documented CLI operations exist.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "SKILL.md"
# Development-only evidence. `.scratch/` is untracked and gitignored, so a fresh clone has no
# acceptance map; every check that cites it is skipped there and stays meaningful on a source
# checkout, which is the only place that keeps the record.
ACCEPTANCE_MAP = ROOT / ".scratch" / "herdr-plan-manager" / "evidence" / "09-end-to-end-and-migration" / "final-acceptance.md"
EVIDENCE_DOCS = [ACCEPTANCE_MAP] if ACCEPTANCE_MAP.is_file() else []
ENTRY_DOCS = [SKILL, ROOT / "README.md", *sorted((ROOT / "docs" / "plan-management").glob("*.md"))]
CLI_OPERATIONS = ["init-run", "start", "status", "wait", "ack", "read", "handoff", "stop", "cleanup"]

MD_LINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
CODE_SPAN = re.compile(r"`([^`\n]+)`")
PATH_PREFIXES = (".scratch/", "docs/", "bin/", "prompts/", "tests/", "README.md", "SKILL.md", "CONTEXT.md")
# The acceptance map deliberately names the removed preview entry when
# explaining what the migration replaced; it is not a live reference.
HISTORICAL_TOKENS = {"docs/plan-management/SKILL.md"}


def _documented_evidence_tokens() -> list[str]:
    tokens: list[str] = []
    for document in [*ENTRY_DOCS, *EVIDENCE_DOCS]:
        for span in CODE_SPAN.findall(document.read_text(encoding="utf-8")):
            token = span.split()[0].rstrip(".,;:")
            if token.startswith(PATH_PREFIXES) and not any(char in token for char in "<>*[]"):
                tokens.append(token)
    return tokens


def _test_node_exists(node_id: str) -> bool:
    parts = node_id.split("::")
    path = ROOT / parts[0]
    if not path.is_file():
        return False
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = tree
    for name in parts[1:]:
        name = name.split("[", 1)[0]
        found = None
        for child in getattr(scope, "body", []):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and child.name == name:
                found = child
                break
        if found is None:
            return False
        scope = found
    return True


def test_skill_entry_is_the_single_herdr_plan_manager_skill():
    text = SKILL.read_text(encoding="utf-8")
    frontmatter = text.split("---", 2)[1]
    assert re.search(r"^name: herdr-plan-manager$", frontmatter, re.MULTILINE)
    assert re.search(r"^disable-model-invocation: true$", frontmatter, re.MULTILINE)
    assert "HERDR_ENV=1" in frontmatter
    nested = [path for path in ROOT.rglob("SKILL.md") if path != SKILL]
    assert nested == [], f"duplicate skill entries: {nested}"


def test_documented_relative_references_resolve():
    missing: list[str] = []
    for document in [*ENTRY_DOCS, *EVIDENCE_DOCS]:
        for target in MD_LINK.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            path = target.split("#", 1)[0]
            if path and not (document.parent / path).resolve().exists():
                missing.append(f"{document.relative_to(ROOT)} -> {target}")
    assert missing == [], f"unresolvable references: {missing}"


def test_runtime_distribution_is_self_contained(tmp_path):
    """The installable resources work without the source checkout's evidence."""
    for name in ("SKILL.md", "README.md", "bin", "docs"):
        source = ROOT / name
        destination = tmp_path / name
        if source.is_dir():
            shutil.copytree(source, destination, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(source, destination)
    for source in ENTRY_DOCS:
        document = tmp_path / source.relative_to(ROOT)
        for target in MD_LINK.findall(document.read_text(encoding="utf-8")):
            if target.startswith(("http://", "https://", "#", "mailto:")):
                continue
            resolved = (document.parent / target.split("#", 1)[0]).resolve()
            assert resolved.is_relative_to(tmp_path), (document, target)
            assert resolved.exists(), (document, target)
    subprocess.run(
        [sys.executable, str(tmp_path / "bin" / "plan_manager.py"), "--help"],
        check=True, capture_output=True, text=True,
    )
    template = tmp_path / "docs" / "plan-management" / "plan-worker.md"
    assert template.is_file()


def test_development_assets_are_excluded_from_export():
    paths = [".scratch", "tests", "CONTEXT.md"]
    result = subprocess.run(
        ["git", "check-attr", "export-ignore", "--", *paths],
        cwd=ROOT, check=True, capture_output=True, text=True,
    )
    assert all(line.endswith(": set") for line in result.stdout.splitlines())
    assert len(result.stdout.splitlines()) == len(paths)


def test_documented_cli_operations_exist():
    proc = subprocess.run(
        [sys.executable, str(ROOT / "bin" / "plan_manager.py"), "--help"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=True,
    )
    help_text = proc.stdout
    missing = [
        operation for operation in CLI_OPERATIONS if not re.search(rf"^\s*{re.escape(operation)}\s", help_text, re.MULTILINE)
    ]
    assert missing == [], f"documented operations absent from --help: {missing}"
    # Every subcommand named in the acceptance map must be a real parser choice.
    choices = re.search(r"\{([^}]+)\}", help_text)
    assert choices, "cannot read the subcommand choices from --help"
    available = {item.strip() for item in choices.group(1).split(",")}
    assert set(CLI_OPERATIONS) <= available, f"missing from parser choices: {CLI_OPERATIONS} - {available}"


def test_acceptance_map_evidence_and_tests_exist():
    if not EVIDENCE_DOCS:
        pytest.skip("the untracked .scratch/ acceptance map is absent from this checkout")
    missing: list[str] = []
    nodes: list[str] = []
    for token in _documented_evidence_tokens():
        if "::" in token:
            nodes.append(token)
            if not _test_node_exists(token):
                missing.append(f"unknown test node: {token}")
        elif token in HISTORICAL_TOKENS:
            continue
        elif not (ROOT / token).exists():
            missing.append(f"unknown path: {token}")
    assert nodes, "the acceptance map names no test node IDs"
    assert missing == [], f"acceptance references do not resolve: {missing}"
