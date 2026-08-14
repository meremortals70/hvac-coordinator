"""Static check: every instance attribute is assigned before it is used.

This exists because it was missed. `_unavailable` was read in a method and
never initialised, which passed lint and passed type checking and then failed
at runtime in Home Assistant with "object has no attribute". Nothing in the
pure-module test suite could catch it, because the fault was in a file that
needs Home Assistant to import.

Parsing the source finds it without importing anything.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "custom_components" / "hvac_coordinator"


def _self_attributes(cls: ast.ClassDef) -> tuple[set[str], set[str]]:
    """Attributes assigned on self, and attributes read from self."""
    assigned: set[str] = set()
    used: set[str] = set()
    for node in ast.walk(cls):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "self"
        ):
            if isinstance(node.ctx, ast.Store):
                assigned.add(node.attr)
            else:
                used.add(node.attr)
    return assigned, used


#: Names provided by the Home Assistant base classes this project subclasses.
#: Anything here is inherited, not local state, so its absence is not a fault.
INHERITED = {
    "add_suggested_values_to_schema",
    "async_abort",
    "async_create_entry",
    "async_show_form",
    "async_show_menu",
    "async_request_refresh",
    "async_set_updated_data",
    "async_on_remove",
    "async_write_ha_state",
    "config_entry",
    "coordinator",
    "trace",
    "_room_id",
    "data",
    "defer",
    "entity_description",
    "hass",
    "last_update_success",
    "logger",
    "name",
    "update_interval",
}


def _declared_names(cls: ast.ClassDef) -> set[str]:
    """Methods, annotated attributes and class-level names."""
    names: set[str] = set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            names.add(node.name)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, ast.Assign):
            names.update(
                target.id for target in node.targets if isinstance(target, ast.Name)
            )
    return names


class TestNoUninitialisedAttributes(unittest.TestCase):
    def test_every_private_attribute_is_assigned_somewhere(self):
        problems: list[str] = []
        for path in sorted(SRC.glob("*.py")):
            tree = ast.parse(path.read_text())
            for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                assigned, used = _self_attributes(cls)
                declared = _declared_names(cls)
                for attr in sorted(used - assigned - declared - INHERITED):
                    if attr.startswith("__"):
                        continue
                    # Methods defined on a sibling class in a mixin pair.
                    if any(
                        attr in _declared_names(other)
                        for other in ast.walk(tree)
                        if isinstance(other, ast.ClassDef)
                    ):
                        continue
                    problems.append(f"{path.name}:{cls.name}.{attr}")
        self.assertEqual(problems, [], f"attributes used but never assigned: {problems}")


if __name__ == "__main__":
    unittest.main()
