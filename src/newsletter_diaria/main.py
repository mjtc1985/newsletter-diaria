from __future__ import annotations

from newsletter_diaria.app import run
from newsletter_diaria.cli import parse_args
from newsletter_diaria.config import load_project_env


def main(argv: list[str] | None = None) -> int:
    load_project_env()
    return run(parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
