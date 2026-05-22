"""Load project .env and override system environment variables."""

from __future__ import annotations

import os
from pathlib import Path

from pipeline.paths import ROOT


def load_dotenv(
    env_path: Path | None = None,
    *,
    override: bool = True,
) -> dict[str, str]:
    """
    Load key=value pairs from .env into os.environ.

    When override=True (default), values from .env replace any existing
    system/user environment variables so the project config wins.
    """
    path = env_path or (ROOT / ".env")
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        loaded[key] = value
        if override or key not in os.environ:
            os.environ[key] = value

    # Gemini SDK prefers GOOGLE_API_KEY when both exist. If .env defines
    # GEMINI_API_KEY, use only that project key and drop a stale global GOOGLE key.
    if "GEMINI_API_KEY" in loaded:
        if "GOOGLE_API_KEY" not in loaded:
            os.environ.pop("GOOGLE_API_KEY", None)
        else:
            os.environ["GOOGLE_API_KEY"] = loaded["GOOGLE_API_KEY"]

    return loaded
