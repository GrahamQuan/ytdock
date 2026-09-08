"""Per-user installation with ownership checks, rollback and safe uninstallation."""

import hashlib
import json
import os
import platform
import shlex
import shutil
import signal
import sys
import tempfile
from pathlib import Path

from .core import UserError
from .i18n import t
from .storage import APP_ID, instance_lock


def read_json(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2_000_000:
        raise UserError(t("invalid_installation_record", p0=path))
    try:
        value = json.loads(path.read_text())
        if not isinstance(value, dict):
            raise ValueError
        return value
    except (ValueError, OSError):
        raise UserError(t("unable_to_read_the_installation_record", p0=path)) from None


def inventory(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if relative in ("bundle.json", "installation.json") or path.name == ".DS_Store":
            continue
        if path.is_symlink():
            target = os.readlink(path)
            if os.path.isabs(target) or not path.resolve().is_relative_to(root.resolve()):
                raise UserError(t("the_package_contains_a_link_outside_its_directory", p0=relative))
            result[relative] = {"link": target}
        elif path.is_file():
            with path.open("rb") as handle:
                digest = hashlib.file_digest(handle, "sha256").hexdigest()
            result[relative] = {"sha256": digest, "executable": bool(path.stat().st_mode & 0o111)}
        elif path.is_dir():
            result[relative] = {"directory": True}
        else:
            raise UserError(t("the_package_contains_a_special_file", p0=relative))
    return result


def verify_bundle(root: Path) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise UserError(t("invalid_package_path"))
    manifest = read_json(root / "bundle.json")
    if manifest.get("app") != APP_ID or manifest.get("schema") != 1:
        raise UserError(t("unable_to_confirm_package_ownership"))
    if manifest.get("files") != inventory(root):
        raise UserError(t("the_package_is_incomplete_or_its_files_have_changed_download"))
    if manifest.get("architecture") != platform.machine():
        raise UserError(
            t(
                "this_package_is_for_the_current_architecture_is",
                p0=manifest.get("architecture"),
                p1=platform.machine(),
            )
        )
    current = tuple(map(int, platform.mac_ver()[0].split(".")))
    minimum = tuple(map(int, manifest["minimum_macos"].split(".")))
    if current < minimum:
        raise UserError(t("this_package_requires_macos_or_later", p0=manifest["minimum_macos"]))
    return manifest


def launcher_text(executable: Path) -> str:
    return f"#!/bin/sh\n# {APP_ID} launcher\nexec " + shlex.quote(str(executable)) + "\n"


def layout(prefix: Path):
    prefix = prefix.expanduser().resolve()
    return (
        prefix / "share" / APP_ID,
        prefix / "bin" / "ytdock",
        prefix / "bin" / "YTDock.command",
    )


def owned_install(destination: Path, prefix: Path):
    if destination.is_symlink() or destination.stat().st_uid != os.getuid():
        raise UserError(t("the_installation_directory_has_unsafe_ownership_no_changes_made"))
    record = read_json(destination / "installation.json")
    if record.get("app") != APP_ID or record.get("prefix") != str(prefix):
        raise UserError(t("the_existing_directory_does_not_belong_to_this_app_no"))
    verify_bundle(destination)
    return record


def install_bundle(source: Path, prefix: Path, home=None, shell="") -> Path:
    from . import shell_path

    prefix = prefix.expanduser().resolve()
    home = home.resolve() if home else None
    verify_bundle(source)
    destination, link, launcher = layout(prefix)
    if source.resolve() == destination:
        raise UserError(t("already_running_from_the_installation_directory_no_reinstall_is_needed"))
    for directory in (destination.parent, link.parent):
        if directory.is_symlink():
            raise UserError(
                t("the_installation_directory_must_not_be_a_symbolic_link", p0=directory)
            )
        directory.mkdir(parents=True, exist_ok=True)
    with instance_lock(prefix / ".ytdock-installer"):
        previous = owned_install(destination, prefix) if os.path.lexists(destination) else {}
        if os.path.lexists(link) and not (
            link.is_symlink() and os.readlink(link) == str(destination / "ytdock")
        ):
            raise UserError(t("the_command_path_is_occupied_by_another_program_and_was", p0=link))
        expected_launcher = launcher_text(destination / "ytdock")
        if os.path.lexists(launcher) and (
            launcher.is_symlink()
            or not launcher.is_file()
            or launcher.read_text() != expected_launcher
        ):
            raise UserError(t("the_launcher_path_is_occupied_by_another_file_and_was", p0=launcher))
        staging = Path(tempfile.mkdtemp(prefix=".ytdock-install-", dir=destination.parent))
        backup, new = staging / "previous", staging / "new"
        had_link, had_launcher = os.path.lexists(link), os.path.lexists(launcher)
        moved_new = added_path = False
        path_record = previous.get("path_config")
        clean_staging = True
        try:
            shutil.copytree(source, new, symlinks=True)
            verify_bundle(new)
            (new / "installation.json").write_text(
                json.dumps({"app": APP_ID, "prefix": str(prefix), "path_config": path_record})
            )
            if destination.exists():
                destination.rename(backup)
            new.rename(destination)
            moved_new = True
            if not had_link:
                link.symlink_to(destination / "ytdock")
            if not had_launcher:
                with launcher.open("x") as handle:
                    handle.write(expected_launcher)
                launcher.chmod(0o755)
            if home:
                path_record, added_path = shell_path.configure(prefix, home, shell, path_record)
                (destination / "installation.json").write_text(
                    json.dumps({"app": APP_ID, "prefix": str(prefix), "path_config": path_record})
                )
        except BaseException:
            try:
                if added_path:
                    shell_path.remove(prefix, home, path_record)
                if (
                    not had_link
                    and link.is_symlink()
                    and os.readlink(link) == str(destination / "ytdock")
                ):
                    link.unlink()
                if (
                    not had_launcher
                    and launcher.is_file()
                    and launcher.read_text() == expected_launcher
                ):
                    launcher.unlink()
                if moved_new:
                    shutil.rmtree(destination)
                if backup.exists():
                    backup.rename(destination)
            except BaseException:
                clean_staging = False
                raise UserError(t("install.rollback_failed", path=staging)) from None
            raise
        finally:
            if clean_staging:
                shutil.rmtree(staging)
    return link


def uninstall(prefix: Path, home=None):
    prefix = prefix.expanduser().resolve()
    destination, link, launcher = layout(prefix)
    with instance_lock(prefix / ".ytdock-installer"):
        if not destination.exists():
            raise UserError(t("ytdock_is_not_installed_at_this_location"))
        record = owned_install(destination, prefix)
        expected = launcher_text(destination / "ytdock")
        if home:
            from . import shell_path

            shell_path.remove(prefix, home.resolve(), record.get("path_config"))
        # Changed command links and user-created files are never removed.
        if link.is_symlink() and os.readlink(link) == str(destination / "ytdock"):
            link.unlink()
        if launcher.is_file() and not launcher.is_symlink() and launcher.read_text() == expected:
            launcher.unlink()
        shutil.rmtree(destination)


def run(action: str, prefix: str | None, media_options=None):
    old_handlers = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

    def cancel(signum, frame):
        for sig in old_handlers:
            signal.signal(sig, signal.SIG_IGN)
        raise KeyboardInterrupt

    try:
        for sig in old_handlers:
            signal.signal(sig, cancel)
        return _run(action, prefix, media_options)
    finally:
        for sig, handler in old_handlers.items():
            signal.signal(sig, handler)


def _run(action: str, prefix: str | None, media_options=None):
    if not getattr(sys, "frozen", False):
        raise UserError(t("use_the_executable_in_a_release_package_to_install_or"))
    from .storage import system_directory

    # Use the same global lock as interactive sessions so upgrades cannot remove
    # a running worker's Python libraries. Keep lock inodes after uninstall.
    with instance_lock(system_directory("support") / APP_ID):
        chosen = Path(prefix) if prefix else Path.home() / ".local"
        if prefix is None and action == "uninstall":
            record = Path(sys.executable).resolve().parent / "installation.json"
            if record.is_file():
                chosen = Path(read_json(record)["prefix"])
        if action == "install":
            command = install_bundle(
                Path(sys.executable).resolve().parent,
                chosen,
                Path.home(),
                os.environ.get("SHELL", ""),
            )
            print(
                t(
                    "installed_run_double_click_to_launch",
                    p0=command,
                    p1=command,
                    p2=command.parent / "YTDock.command",
                )
            )
            print(t("uninstall_uninstall", p0=command))
            if str(command.parent) not in os.get_exec_path():
                print(t("path.instructions", directory=shlex.quote(str(command.parent))))
            from .ffmpeg import post_install_check

            post_install_check(**(media_options or {}))
        else:
            uninstall(chosen, Path.home())
            print(t("the_app_ytdock_command_and_launcher_were_removed_your_videos"))
            print(t("path.restart_terminal"))
    return 0
