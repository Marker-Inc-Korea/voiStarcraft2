#!/usr/bin/env python3
"""Install the Finder-launchable voiStarcraft2 app."""

from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from starcraft_commander.local_cockpit import install_macos_application


def main() -> int:
    app_path = install_macos_application(REPO_ROOT)
    print(f"Installed: {app_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
