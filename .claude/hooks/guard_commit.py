"""PreToolUse hook (Bash): block `git commit` and `git push` when the staged changes contain secrets or forbidden files.

Exit code 2 blocks the tool call, and stderr is shown to Claude.
If gitleaks is on PATH it also runs. The regex scan always runs, so the guard works without it.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys

SECRET_PATTERNS = {
    "Anthropic API key": re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    "AWS access key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "GitHub token": re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),
    "Private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "Generic secret assignment": re.compile(
        r"""(?i)\b(api[_-]?key|secret|password|token)\b\s*[:=]\s*['"][A-Za-z0-9_\-]{16,}['"]"""
    ),
}
FORBIDDEN_FILES = [
    re.compile(r"(^|/)\.env$"),
    re.compile(r"(^|/)\.env\.(?!example$)[^/]+$"),
    re.compile(r"(?i)business cases for ai innovation lab"),
    re.compile(r"(?i)(^|/)email\.txt$"),
]
GIT_WRITE = re.compile(r"\bgit\b(\s+-C\s+\S+)?\s+(commit|push)\b")


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], capture_output=True, encoding="utf-8", errors="replace", check=False
    )
    return result.stdout or ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command", "")
    if not GIT_WRITE.search(command):
        return 0

    problems: list[str] = []
    staged = [f for f in git("diff", "--cached", "--name-only").splitlines() if f]
    for name in staged:
        if any(p.search(name) for p in FORBIDDEN_FILES):
            problems.append(f"forbidden file staged: {name}")

    added = [
        line[1:]
        for line in git("diff", "--cached", "--unified=0", "--no-color").splitlines()
        if line.startswith("+") and not line.startswith("+++")
    ]
    for label, pattern in SECRET_PATTERNS.items():
        if any(pattern.search(line) for line in added):
            problems.append(f"possible {label} in staged changes")

    if shutil.which("gitleaks"):
        leak = subprocess.run(
            ["gitleaks", "git", "--staged", "--no-banner", "--redact"],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if leak.returncode == 1:
            problems.append("gitleaks reported leaks:\n" + leak.stdout[-1500:])

    if problems:
        sys.stderr.write(
            "Commit/push blocked by .claude/hooks/guard_commit.py:\n- "
            + "\n- ".join(problems)
            + "\nUnstage the offending content (secrets belong in .env / GitHub secrets).\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # fail closed: a crashed guard must not let a commit through
        sys.stderr.write(f"guard_commit.py failed ({type(exc).__name__}: {exc}); blocking.\n")
        sys.exit(2)
