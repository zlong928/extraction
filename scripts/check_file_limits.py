from __future__ import annotations

import argparse
from pathlib import Path


def iter_py_files(directories: list[str]) -> list[Path]:
    paths: list[Path] = []
    for directory in directories:
        root = Path(directory)
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if any(part in {".venv", ".git", "__pycache__"} for part in path.parts):
                continue
            paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("paths", nargs="+", help="Directories to scan for .py files.")
    parser.add_argument("--max-lines", type=int, default=1500)
    args = parser.parse_args()

    violations: list[tuple[Path, int]] = []
    for path in iter_py_files(args.paths):
        lines = len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
        if lines > args.max_lines:
            violations.append((path, lines))

    if violations:
        print("File line limit violations:")
        for path, lines in violations:
            print(f"  - {path}: {lines} lines")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
