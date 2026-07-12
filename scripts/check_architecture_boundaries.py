from __future__ import annotations

import ast
import sys
from pathlib import Path


PRODUCTION_ROOTS = ("app", "content_pipeline")
FORBIDDEN_API_IMPORTS = (
    "app.services.mineru",
    "app.services.agent",
    "content_pipeline.orchestration",
    "content_pipeline.llm",
)


def check_boundaries(root: Path) -> list[str]:
    violations: list[str] = []
    for package in PRODUCTION_ROOTS:
        package_root = root / package
        for path in sorted(package_root.rglob("*.py")):
            violations.extend(_check_python_file(root, path, package))
    violations.extend(_check_frontend(root / "frontend" / "src"))
    return violations


def _check_python_file(root: Path, path: Path, package: str) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [f"{path.relative_to(root)}: syntax error: {exc}"]

    violations: list[str] = []
    module = ".".join(path.relative_to(root).with_suffix("").parts)
    for node in ast.walk(tree):
        imported_modules = _imported_modules(node)
        if not imported_modules:
            if package == "content_pipeline" and isinstance(node, ast.Call):
                if _is_dynamic_app_import(node):
                    violations.append(f"{path.relative_to(root)}: content_pipeline dynamically imports {node.args[0].value}")
            continue
        for imported in imported_modules:
            if imported.startswith(("tests", "scripts")):
                violations.append(f"{path.relative_to(root)}: production module imports {imported}")
            if package == "content_pipeline" and imported.startswith("app"):
                violations.append(f"{path.relative_to(root)}: content_pipeline imports {imported}")
            if module.startswith("content_pipeline.contracts") and (
                imported.startswith("app")
                or imported.startswith("content_pipeline.orchestration")
                or imported.startswith("content_pipeline.llm")
                or imported.startswith("content_pipeline.visual")
            ):
                violations.append(f"{path.relative_to(root)}: contracts imports high-level module {imported}")
            if module.startswith("app.api") and imported.startswith(FORBIDDEN_API_IMPORTS):
                violations.append(f"{path.relative_to(root)}: API route imports forbidden implementation {imported}")
    return violations


def _imported_modules(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        return [node.module or ""]
    return []


def _is_dynamic_app_import(node: ast.Call) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "importlib"
        and node.func.attr == "import_module"
        and bool(node.args)
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
        and node.args[0].value.startswith("app")
    )


def _check_frontend(src_root: Path) -> list[str]:
    violations: list[str] = []
    if not src_root.exists():
        return violations
    forbidden = ("sqlite", "redis://", "/data/", "../data", "../extraction.db")
    for path in sorted(src_root.rglob("*.ts")) + sorted(src_root.rglob("*.tsx")):
        text = path.read_text(encoding="utf-8").lower()
        for token in forbidden:
            if token in text:
                violations.append(f"{path.relative_to(src_root.parent.parent)}: frontend references forbidden storage token {token}")
    return violations


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    violations = check_boundaries(root)
    if violations:
        print("Architecture boundary violations:", file=sys.stderr)
        for violation in violations:
            print(f"- {violation}", file=sys.stderr)
        return 1
    print("Architecture boundary check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
