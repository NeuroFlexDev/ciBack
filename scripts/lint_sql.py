"""Lint standalone SQL and complete static SQL statements embedded in Python.

Fragments (e.g. index predicates) and dynamic expressions are intentionally not
parsed as standalone statements. Their counts are printed to make coverage clear.
"""

import ast
import re
from pathlib import Path

from sqlfluff.core import FluffConfig, Linter

ROOT = Path(__file__).resolve().parents[1]
STATEMENT = re.compile(r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH)\b", re.I)


def _lint_embedded_sql(linter):
    checked = skipped = errors = 0
    for directory in ("app", "migrations", "scripts"):
        for path in sorted((ROOT / directory).rglob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not node.args:
                    continue
                name = (
                    node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else node.func.id
                    if isinstance(node.func, ast.Name)
                    else ""
                )
                if name not in {"text", "execute", "exec_driver_sql"}:
                    continue
                argument = node.args[0]
                # execute(text(...)) is checked once at the inner text() call.
                if isinstance(argument, ast.Call):
                    continue
                if not isinstance(argument, ast.Constant) or not isinstance(argument.value, str):
                    skipped += 1
                    continue
                if not STATEMENT.match(argument.value):
                    skipped += 1
                    continue
                checked += 1
                result = linter.lint_string(argument.value)
                for violation in result.get_violations():
                    errors += 1
                    print(
                        f"{path.relative_to(ROOT)}:{argument.lineno}: "
                        f"{violation.rule_code()} {violation.desc()}"
                    )
    return checked, skipped, errors


def main() -> int:
    linter = Linter(config=FluffConfig.from_root())
    checked, skipped, errors = _lint_embedded_sql(linter)
    for path in sorted(ROOT.rglob("*.sql")):
        if any(
            part
            in {
                ".venv",
                ".venv-security",
                ".lint-tools",
                "lint-reports",
                "venv",
                "node_modules",
                ".git",
                "uploads",
                "mlcourse",
            }
            for part in path.relative_to(ROOT).parts
        ):
            continue
        checked += 1
        for violation in linter.lint_string(path.read_text(encoding="utf-8")).get_violations():
            errors += 1
            print(
                f"{path.relative_to(ROOT)}:{violation.line_no}: "
                f"{violation.rule_code()} {violation.desc()}"
            )
    print(
        f"SQLFluff: {checked} statements/files checked; "
        f"{skipped} dynamic expressions/fragments skipped; {errors} violations"
    )
    return int(errors > 0)


if __name__ == "__main__":
    raise SystemExit(main())
