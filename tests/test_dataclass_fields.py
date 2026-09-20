"""Guard: dataclasses must not contain unannotated class-level assignments.

`@dataclass` only creates a field for an *annotated* assignment. A bare
`clob = None` inside a dataclass body is silently ignored, so the constructor
rejects that keyword and the object silently loses the attribute. That exact
mistake shipped once (PaperVenue.clob) and broke paper mode at runtime, so it is
now checked mechanically.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = [REPO_ROOT / "src", REPO_ROOT / "app"]

#: Callables intentionally rebound on the class after definition (dunders only).
ALLOWED = {"__str__", "__repr__", "__hash__", "__eq__"}


def _dataclass_classes() -> list[tuple[Path, ast.ClassDef]]:
    found: list[tuple[Path, ast.ClassDef]] = []
    for root in SOURCE_ROOTS:
        for path in sorted(root.rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                decorated = any(
                    (isinstance(d, ast.Name) and d.id == "dataclass")
                    or (isinstance(d, ast.Call) and getattr(d.func, "id", "") == "dataclass")
                    for d in node.decorator_list
                )
                if decorated:
                    found.append((path, node))
    return found


def test_there_are_dataclasses_to_check():
    assert _dataclass_classes(), "no dataclasses found - the guard is not working"


def test_no_unannotated_class_level_assignments_in_dataclasses():
    offenders: list[str] = []
    for path, node in _dataclass_classes():
        for statement in node.body:
            if (
                isinstance(statement, ast.Assign)
                and len(statement.targets) == 1
                and isinstance(statement.targets[0], ast.Name)
                and statement.targets[0].id not in ALLOWED
            ):
                offenders.append(
                    f"{path.relative_to(REPO_ROOT)}:{statement.lineno} "
                    f"{node.name}.{statement.targets[0].id}"
                )
    assert offenders == [], (
        "unannotated dataclass assignments (add a type annotation, or use "
        "field(default=...)): " + "; ".join(offenders)
    )


@pytest.mark.parametrize("module,class_name,kwargs", [
    ("src.paper.simulator", "PaperVenue", {"clob": None}),
    ("src.execution.polymarket", "PolymarketExecutor", {"client": None}),
    ("src.core.runtime", "Runtime", {"ai": None}),
])
def test_key_optional_collaborators_are_real_fields(module, class_name, kwargs):
    """The specific fields that were once silently dropped must be settable."""
    import importlib

    imported = importlib.import_module(module)
    cls = getattr(imported, class_name)
    for name in kwargs:
        assert name in cls.__dataclass_fields__, f"{class_name}.{name} is not a dataclass field"
