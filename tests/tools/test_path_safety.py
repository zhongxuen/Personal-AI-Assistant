"""
Path sanitization tests (§33, file 11 prompt 3).

`sanitize_path()` is the one gate every file-operation tool goes through before
touching the filesystem (`app/tools/path_safety.py`). Covers the two ways a request
can try to escape the allow-list: literal ".." traversal segments, and an
out-of-allow-list absolute path that doesn't traverse at all -- both must raise
`PathSafetyError`, never resolve silently.
"""

from __future__ import annotations

import pytest

from app.config.settings import Settings
from app.tools import path_safety
from app.tools.path_safety import PathSafetyError, sanitize_path


@pytest.fixture(autouse=True)
def _allowed_dirs(tmp_path, monkeypatch):
    """Pin the allow-list to two throwaway directories under `tmp_path` so this test
    doesn't depend on (or touch) the real Desktop/Documents fallback.
    """
    allowed = tmp_path / "allowed"
    other = tmp_path / "not_allowed"
    allowed.mkdir()
    other.mkdir()

    settings = Settings(_env_file=None, allowed_file_directories=str(allowed))
    monkeypatch.setattr(path_safety, "get_settings", lambda: settings)

    return allowed, other


def test_accepts_a_path_inside_the_allow_list(_allowed_dirs):
    allowed, _other = _allowed_dirs
    target = allowed / "notes.txt"

    resolved = sanitize_path(str(target))

    assert resolved == target.resolve()


def test_accepts_a_nested_path_inside_the_allow_list(_allowed_dirs):
    allowed, _other = _allowed_dirs
    target = allowed / "subdir" / "notes.txt"

    resolved = sanitize_path(str(target))

    assert resolved == target.resolve()


@pytest.mark.parametrize(
    "suffix",
    [
        "../secret.txt",
        "subdir/../../secret.txt",
        "../../../etc/passwd",
    ],
)
def test_rejects_traversal_attempts(_allowed_dirs, suffix):
    allowed, _other = _allowed_dirs

    with pytest.raises(PathSafetyError, match="traversal"):
        sanitize_path(str(allowed / suffix))


def test_rejects_out_of_allow_list_absolute_path(_allowed_dirs):
    _allowed, other = _allowed_dirs
    target = other / "secret.txt"

    with pytest.raises(PathSafetyError, match="outside the allowed directories"):
        sanitize_path(str(target))


def test_rejects_empty_path(_allowed_dirs):
    with pytest.raises(PathSafetyError, match="must not be empty"):
        sanitize_path("")


def test_rejects_whitespace_only_path(_allowed_dirs):
    with pytest.raises(PathSafetyError, match="must not be empty"):
        sanitize_path("   ")


def test_error_never_silently_passes_through_a_rejected_path(_allowed_dirs):
    """Belt-and-braces: a rejected path must never come back as a usable Path -- the
    caller either gets `PathSafetyError` or a resolved-and-allowed Path, nothing in
    between.
    """
    _allowed, other = _allowed_dirs

    try:
        sanitize_path(str(other / "secret.txt"))
    except PathSafetyError:
        pass
    else:
        pytest.fail("sanitize_path() should have raised PathSafetyError")
