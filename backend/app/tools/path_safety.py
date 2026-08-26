"""
Path sanitization helper (§33 "Sanitize file paths", file 11 prompt 1).

Every file-operation tool (`app/tools/files.py`) must resolve a user-supplied path through
`sanitize_path()` before touching the filesystem -- it must never pass a raw path string
straight to `open()`/`Path()` (§41 Rule 6: never trust LLM/user-supplied input blindly).

The policy is deliberately restrictive: reject any literal ".." segment outright, fully
resolve the path (following symlinks, so a symlink planted inside an allowed directory
that points outside it doesn't become a bypass), then require the resolved path to sit
at or under one of `settings.allowed_file_directories_list` (§30 -- configurable via env,
not hardcoded). Anything that fails either check raises `PathSafetyError` with a clear,
user-facing message; callers turn that straight into a `ToolResult(success=False, ...)`,
never a silent pass-through.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import get_settings


class PathSafetyError(ValueError):
    """A requested path failed sanitization -- callers convert this to a ToolResult error."""


def allowed_roots() -> list[Path]:
    """The configured allow-list of base directories, each fully resolved."""
    settings = get_settings()
    return [Path(base).expanduser().resolve(strict=False) for base in settings.allowed_file_directories_list]


def sanitize_path(raw_path: str) -> Path:
    """Resolve `raw_path` and verify it falls within an allow-listed base directory.

    Raises `PathSafetyError` if `raw_path` is empty, contains a literal ".." segment,
    can't be resolved, or resolves outside every allow-listed directory. Does not require
    the path to exist -- `create_file` needs to sanitize a path that doesn't exist yet;
    callers that need existence (`open_file`, `search_files`) check that separately.
    """
    if not raw_path or not raw_path.strip():
        raise PathSafetyError("Path must not be empty.")

    # Reject literal traversal segments up front -- belt-and-braces on top of the
    # resolve()-based containment check below, since a raw ".." component is never
    # legitimate input for these tools regardless of where it'd end up resolving.
    if ".." in Path(raw_path).parts:
        raise PathSafetyError(f"Path '{raw_path}' is not allowed: '..' traversal is rejected.")

    try:
        resolved = Path(raw_path).expanduser().resolve(strict=False)
    except (OSError, RuntimeError) as exc:
        raise PathSafetyError(f"Could not resolve path '{raw_path}': {exc}") from exc

    roots = allowed_roots()
    if not any(resolved == root or root in resolved.parents for root in roots):
        allowed = ", ".join(str(root) for root in roots)
        raise PathSafetyError(f"Path '{raw_path}' is outside the allowed directories: {allowed}.")

    return resolved
