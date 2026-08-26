"""
Frontend-bundle secret-leak guard.

docs/deployment.md's "Secrets boundary" section documents a *manual* check to run
before every deploy:

    cd frontend && npm run build && grep -ri "gemini_api_key\\|auth_secret_key" dist/assets/*.js

This test automates that check so a leak fails the test suite instead of relying on
someone remembering to run the grep by hand. It builds the frontend fresh (`npm run
build`, the same command Vercel and docs/deployment.md use) and greps every emitted
JS/CSS asset for the names of secret-bearing backend env vars
(`backend/app/config/settings.py`'s secret fields / `render.yaml`'s `sync: false`
keys) -- none of them should ever reach client-side code, since Vite only inlines
`import.meta.env.VITE_*` names into the bundle (docs/deployment.md, §31: "The only
environment variable the frontend build reads is VITE_API_BASE_URL").

This is a *name* check, not a value check: it can't know what the real secret values
are (they're never committed), so it looks for the env var names themselves leaking
in --  e.g. a stray `import.meta.env.GEMINI_API_KEY` reference, or someone hardcoding
`process.env.GEMINI_API_KEY` in frontend code by mistake. Vite would inline that as a
literal `undefined` today (non-`VITE_`-prefixed vars aren't exposed), but the mistake
itself -- frontend code reaching for a backend secret at all -- is exactly what this
guards against regressing on.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_DIR = REPO_ROOT / "frontend"

# Kept in sync with render.yaml's `sync: false` keys / backend/app/config/settings.py's
# secret-bearing fields. Deliberately NOT the full settings/env-var list -- things like
# CORS_ORIGINS or VITE_API_BASE_URL are plain config the frontend is expected to know
# about (VITE_API_BASE_URL literally is the frontend's own build-time env var).
SECRET_ENV_VAR_NAMES = [
    "GEMINI_API_KEY",
    "AUTH_SECRET_KEY",
    "AUTH_SEED_PASSWORD",
]

npm = shutil.which("npm")


@pytest.mark.skipif(npm is None, reason="npm not available in this environment")
def test_build_output_contains_no_backend_secret_env_var_names():
    result = subprocess.run(
        [npm, "run", "build"],
        cwd=FRONTEND_DIR,
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, f"frontend build failed:\n{result.stdout}\n{result.stderr}"

    dist_assets = FRONTEND_DIR / "dist" / "assets"
    bundle_files = list(dist_assets.glob("*.js")) + list(dist_assets.glob("*.css"))
    assert bundle_files, f"no built assets found under {dist_assets} -- build produced nothing to check"

    leaks: dict[str, list[str]] = {}
    for path in bundle_files:
        lower_text = path.read_text(encoding="utf-8", errors="replace").lower()
        for name in SECRET_ENV_VAR_NAMES:
            if name.lower() in lower_text:
                leaks.setdefault(name, []).append(path.name)

    assert not leaks, (
        "secret env var name(s) found in the built frontend bundle -- this would ship "
        f"a backend secret's *name* (and likely its value) to every browser that loads "
        f"the site: {leaks}"
    )


def test_secret_env_var_name_list_is_a_meaningful_positive_control():
    """Guards against the check above passing for the wrong reason (an empty
    SECRET_ENV_VAR_NAMES list, a typo'd name, or a glob that silently matches nothing)
    by proving the same case-insensitive membership check actually flags a string when
    the secret name IS present in it.
    """
    haystack = "some bundle text mentioning GEMINI_API_KEY by name".lower()
    assert any(name.lower() in haystack for name in SECRET_ENV_VAR_NAMES)
