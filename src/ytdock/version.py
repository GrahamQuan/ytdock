"""The project distribution metadata is the shared version authority."""

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path


def get_version():
    try:
        return version("ytdock")
    except PackageNotFoundError:
        # Allows a source checkout before an editable installation has been made.
        import sys
        import tomllib

        if getattr(sys, "frozen", False):
            raise RuntimeError("YTDock version metadata is missing") from None
        return tomllib.loads((Path(__file__).resolve().parents[2] / "pyproject.toml").read_text())[
            "project"
        ]["version"]
