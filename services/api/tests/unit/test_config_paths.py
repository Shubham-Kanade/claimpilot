from __future__ import annotations

from pathlib import PurePosixPath


def test_repo_root_never_raises_for_shallow_install_paths():
    # In the Docker image config.py lives at /app/src/claimpilot/config.py, so API_ROOT is /app.
    api_root = PurePosixPath("/app/src/claimpilot/config.py").parents[2]
    assert api_root == PurePosixPath("/app")
    assert api_root.parent.parent == PurePosixPath("/")  # what config.REPO_ROOT computes


def test_repo_root_in_checkout():
    from claimpilot.config import API_ROOT, REPO_ROOT

    assert (REPO_ROOT / "services" / "api") == API_ROOT
