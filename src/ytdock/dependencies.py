"""Locate private bundled tools, or supported development tools on PATH."""

import importlib.metadata
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from .core import UserError
from .i18n import t


def runtime_directory() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / "runtime"
    local = Path(__file__).resolve().parents[2] / ".runtime" / "bin"
    return local if local.is_dir() else None


def candidates(name: str):
    bundled = runtime_directory()
    if bundled is not None:
        yield str(bundled / name)
        if getattr(sys, "frozen", False):
            return  # Installed builds never silently depend on the user's PATH.
    seen = set()
    for directory in os.get_exec_path():
        path = shutil.which(name, path=directory)
        if path and os.path.realpath(path) not in seen:
            seen.add(os.path.realpath(path))
            yield os.path.abspath(path)


def find_quickjs(runner=None) -> str:
    found = []
    for path in candidates("qjs"):
        try:
            # Bellard QuickJS prints --help and exits 1; this is not a failed check.
            result = (runner or subprocess.run)(
                [path, "--help"], capture_output=True, text=True, timeout=10
            )
            output = result.stdout + result.stderr
            version = re.search(r"QuickJS version (\d+)-(\d+)-(\d+)", output)
            ng = re.search(r"QuickJS-ng version (\d+)\.(\d+)\.(\d+)", output)
            if (version and tuple(map(int, version.groups())) >= (2025, 4, 26)) or (
                ng and tuple(map(int, ng.groups())) >= (0, 12, 0)
            ):
                return path
        except (OSError, subprocess.SubprocessError):
            pass
        found.append(path)
    raise UserError(
        t("quickjs_2025_04_26_or_quickjs_ng_0_12_0")
        + (t("checked_paths") + "；".join(found) if found else "")
    )


def check_python():
    if sys.platform != "darwin":
        raise UserError(t("only_macos_is_supported"))
    missing = []
    for package in ("yt-dlp", "yt-dlp-ejs", "prompt-toolkit", "pyobjc-framework-Cocoa"):
        try:
            importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            missing.append(package)
    if missing:
        raise UserError(
            t("missing_python_dependencies")
            + ", ".join(missing)
            + t("reinstall_the_app_or_run_uv_sync")
        )
    from importlib.util import find_spec

    for module in ("yt_dlp", "yt_dlp_ejs", "prompt_toolkit", "Foundation"):
        try:
            if find_spec(module) is None:
                raise ImportError(module)
        except ImportError:
            raise UserError(
                t("missing_python_dependencies") + module + t("reinstall_the_app_or_run_uv_sync")
            ) from None


def check(progress=None, runner=None, **media_options) -> dict:
    from .ffmpeg import find_media_tools

    notify = progress or (lambda *args: None)
    notify("python", "checking")
    check_python()
    notify("python", "ready")
    notify("quickjs", "checking")
    quickjs = find_quickjs(runner)
    notify("quickjs", "ready")
    notify("ffmpeg", "checking")
    extra = {"progress": progress, "runner": runner} if progress or runner else {}
    return find_media_tools(**media_options, **extra) | {"quickjs": quickjs}
