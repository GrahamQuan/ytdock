"""macOS directories, advisory leases, task ownership, exclusive publication."""

import errno
import fcntl
import json
import os
import shutil
import stat
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path

from .core import UserError, language_code
from .i18n import t

APP_ID = "ytdock"
PREFIX = ".ytdock-task-"


def system_directory(kind: str) -> Path:
    if sys.platform != "darwin":
        raise UserError(t("only_macos_is_supported"))
    from Foundation import (
        NSApplicationSupportDirectory,
        NSDownloadsDirectory,
        NSFileManager,
        NSUserDomainMask,
    )

    directory = NSDownloadsDirectory if kind == "downloads" else NSApplicationSupportDirectory
    url, error = (
        NSFileManager.defaultManager().URLForDirectory_inDomain_appropriateForURL_create_error_(
            directory, NSUserDomainMask, None, kind != "downloads", None
        )
    )
    if error or url is None:
        raise UserError(t("unable_to_obtain_the_user_directory_from_macos"))
    return Path(str(url.path()))


def check_downloads(path: Path):
    if not path.is_dir():
        raise UserError(t("the_system_downloads_directory_does_not_exist"))
    try:
        with tempfile.TemporaryFile(dir=path):
            pass
    except OSError as exc:
        raise UserError(
            t("not_enough_disk_space")
            if exc.errno == errno.ENOSPC
            else t("the_system_downloads_directory_is_not_writable")
        ) from None


def lock_fd(path: Path) -> int:
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise UserError(t("the_task_lock_file_is_unsafe_unable_to_start"))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def instance_lock(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid != os.getuid():
        raise UserError(t("the_application_state_directory_is_unsafe"))
    try:
        fd = lock_fd(directory / "instance.lock")
    except BlockingIOError:
        raise UserError(t("another_ytdock_instance_is_running_exit_it_before_continuing")) from None
    try:
        yield fd
    finally:
        os.close(fd)  # Never unlink: all contenders must lock the same inode.


class Task:
    def __init__(self, downloads: Path):
        self.downloads = downloads.resolve()
        self.token = uuid.uuid4().hex
        self.path = self.downloads / (PREFIX + self.token)
        self.path.mkdir(mode=0o700)
        self.lease = -1
        try:
            self.lease = lock_fd(self.path / "lease")
            (self.path / "owner.json").write_text(
                json.dumps({"app": "ytdock-v1", "token": self.token, "uid": os.getuid()})
            )
        except BaseException:
            if self.lease >= 0:
                os.close(self.lease)
            shutil.rmtree(self.path)
            raise

    def cleanup(self):
        try:
            remove_owned(self.path, self.downloads)
        finally:
            if self.lease >= 0:
                os.close(self.lease)
                self.lease = -1


def owned(path: Path, downloads: Path) -> bool:
    if path.is_symlink() or path.parent.resolve() != downloads.resolve():
        return False
    try:
        if not path.is_dir() or path.stat().st_uid != os.getuid():
            return False
        marker = path / "owner.json"
        if marker.is_symlink() or marker.stat().st_size > 1024:
            return False
        data = json.loads(marker.read_text())
        return (
            data.get("app") == "ytdock-v1"
            and data.get("uid") == os.getuid()
            and isinstance(data.get("token"), str)
            and len(data["token"]) == 32
            and path.name == PREFIX + data["token"]
        )
    except (OSError, ValueError):
        return False


def remove_owned(path: Path, downloads: Path):
    if not owned(path, downloads):
        raise UserError(t("unable_to_confirm_temporary_directory_ownership_not_deleted", p0=path))
    try:
        shutil.rmtree(path)  # Python's fd-based rmtree resists nested symlink attacks.
    except OSError:
        raise UserError(t("cleanup_failed_temporary_files_remain_at", p0=path)) from None


def recover(downloads: Path) -> list[Path]:
    residuals = []
    for path in downloads.glob(PREFIX + "*"):
        if not owned(path, downloads):
            residuals.append(path)
            continue
        try:
            fd = lock_fd(path / "lease")
        except BlockingIOError:
            continue
        except OSError:
            residuals.append(path)
            continue
        try:
            remove_owned(path, downloads)
        except UserError:
            residuals.append(path)
        finally:
            os.close(fd)
    return residuals


def publish(source: Path, downloads: Path, stem: str, extension=".mp4") -> Path:
    caption_language = language_code(extension[1:-4]) if extension.endswith(".srt") else None
    safe_extension = extension == ".mp4" or (
        caption_language is not None and extension == f".{caption_language}.srt"
    )
    if not safe_extension or Path(stem).name != stem:
        raise ValueError("Invalid output name")
    # Link is atomic and exclusive on the same filesystem. The verified file is
    # already closed/fsynced; unlinking its temporary name cannot remove the output.
    with source.open("rb") as file:
        os.fsync(file.fileno())
    for index in range(100000):
        suffix = f" ({index})" if index else ""
        target = downloads / f"{stem}{suffix}{extension}"
        try:
            os.link(source, target, follow_symlinks=False)
            return target
        except FileExistsError:
            continue
        except OSError as exc:
            raise UserError(
                t("not_enough_disk_space")
                if exc.errno == errno.ENOSPC
                else t("unable_to_save_the_output_to_downloads_existing_files_were")
            ) from None
    raise UserError(t("too_many_files_share_this_name_unable_to_choose_an"))
