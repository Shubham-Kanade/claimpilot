"""Print the OpenAPI document.

Usage: `uv run python -m claimpilot.export_openapi > ../../apps/web/openapi.json`
"""

from __future__ import annotations

import json
import sys

from claimpilot.main import create_app


def main() -> None:
    sys.stdout.write(json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
