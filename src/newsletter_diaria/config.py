from __future__ import annotations

import logging
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ENV_FILES = [PROJECT_ROOT / ".smtp.env"]

logger = logging.getLogger("newsletter_diaria")


def load_project_env() -> None:
    loaded_keys: set[str] = set()
    for env_file in PROJECT_ENV_FILES:
        if not env_file.exists():
            continue
        try:
            for raw_line in env_file.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if not key:
                    continue
                if key in os.environ and key not in loaded_keys:
                    continue

                os.environ[key] = value
                loaded_keys.add(key)
        except Exception as exc:  # pragma: no cover - defensivo
            logger.warning("Could not read %s: %s", env_file, exc)
