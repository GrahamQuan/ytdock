"""Frozen executable entry point, including its private worker mode."""

import sys

if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        from ytdock.worker import main
    else:
        from ytdock.cli import main
    raise SystemExit(main())
