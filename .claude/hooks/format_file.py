"""PostToolUse hook: format the file Claude just edited.

Python files go through ruff (via uv, in the nearest pyproject). Web files go through prettier in apps/web.
This hook never blocks: formatter failures are swallowed (exit 0).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

WEB_EXTS = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".json", ".css", ".md"}


def nearest(start: Path, marker: str) -> Path | None:
    for parent in [start, *start.parents]:
        if (parent / marker).exists():
            return parent
    return None


def run(cmd: list[str], cwd: Path) -> None:
    try:
        subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    file_path = (payload.get("tool_input") or {}).get("file_path")
    if not file_path:
        return 0
    path = Path(file_path)
    if not path.is_file():
        return 0

    if path.suffix == ".py" and shutil.which("uv"):
        project = nearest(path.parent, "pyproject.toml")
        if project:
            run(["uv", "run", "--quiet", "ruff", "format", str(path)], project)
            run(["uv", "run", "--quiet", "ruff", "check", "--fix", "--quiet", str(path)], project)
    elif path.suffix in WEB_EXTS and "apps" in path.parts and "web" in path.parts:
        web = nearest(path.parent, "package.json")
        if web and (web / "node_modules" / "prettier").exists():
            npx = shutil.which("npx") or shutil.which("npx.cmd")
            if npx:
                run([npx, "--no-install", "prettier", "--write", "--log-level", "silent", str(path)], web)
    return 0


if __name__ == "__main__":
    sys.exit(main())
