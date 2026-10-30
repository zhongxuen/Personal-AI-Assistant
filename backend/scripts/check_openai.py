"""
Build gate for the OpenAI key rules (OPENAI-RULES.md section 4.2).

Fails (exit 1) if any code file other than the guard imports the `openai` package,
calls `api.openai.com` directly, or sets `store=True` / `store: true`. Runs before
every Render build (`render.yaml` buildCommand) and in the test suite
(`tests/llm/test_openai_gate.py`); the frontend has its own copy of the same check in
`frontend/scripts/check-openai.mjs`, run by `npm run build`.

    python backend/scripts/check_openai.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# The guard and the guard's own tests -- nothing else. Adding to this list needs the
# owner's approval (OPENAI-RULES.md).
ALLOWLIST = {
    "backend/app/llm/openai_guard.py",
    "tests/llm/test_openai_guard.py",
    # Deliberately tampers with requests (store=True etc.) to prove they're refused.
    "tests/llm/test_openai_privacy.py",
}

CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
SKIP_DIRS = {"node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__", ".playwright-mcp"}

RULES = [
    (re.compile(r"^\s*(import\s+openai\b|from\s+openai\b)", re.M), "imports the openai package"),
    (re.compile(r"""(from\s+|require\(\s*|import\(\s*)['"]openai(/[^'"]*)?['"]"""), "imports the openai package"),
    (re.compile(r"api\.openai\.com"), "calls api.openai.com directly"),
    (re.compile(r"""['"]?\bstore['"]?\s*[=:]\s*(True|true)\b"""), "sets store=True"),
]


def _code_files(root: Path):
    for path in root.rglob("*"):
        if path.suffix not in CODE_SUFFIXES or not path.is_file():
            continue
        if SKIP_DIRS.intersection(path.relative_to(root).parts):
            continue
        yield path


def find_violations(root: Path = REPO_ROOT) -> list[str]:
    self_path = Path(__file__).resolve()
    violations = []
    for path in _code_files(root):
        rel = path.relative_to(root).as_posix()
        if rel in ALLOWLIST or path.resolve() == self_path or rel == "frontend/scripts/check-openai.mjs":
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern, reason in RULES:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                violations.append(f"{rel}:{line}: {reason}")
    return violations


def main() -> int:
    violations = find_violations()
    if violations:
        print("OpenAI rules check FAILED (see OPENAI-RULES.md):", file=sys.stderr)
        for violation in violations:
            print(f"  {violation}", file=sys.stderr)
        return 1
    print("OpenAI rules check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
