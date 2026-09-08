"""Own only the exact PATH block recorded by this installer."""

import os
import shlex
import stat
import tempfile
from pathlib import Path

from .core import UserError
from .i18n import t

START = "# >>> YTDock PATH >>>"
END = "# <<< YTDock PATH <<<"


def block_for(prefix):
    return f'{START}\nexport PATH={shlex.quote(str(prefix / "bin"))}:"$PATH"\n{END}\n'


def read_config(path):
    if not os.path.lexists(path):
        return "", None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
        raise UserError(t("path.config_unsafe", path=path))
    return path.read_text(), info


def write_config(path, text, previous):
    # Compare identity/content before replacing, avoiding stale writes or link following.
    current, info = read_config(path)
    old_text, old_info = previous
    if (
        current != old_text
        or (info is None) != (old_info is None)
        or (info and (info.st_ino, info.st_mtime_ns) != (old_info.st_ino, old_info.st_mtime_ns))
    ):
        raise UserError(t("path.config_changed", path=path))
    fd, temporary = tempfile.mkstemp(prefix=".ytdock-zshrc-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as handle:
            os.fchmod(handle.fileno(), stat.S_IMODE(info.st_mode) if info else 0o600)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure(prefix, home, shell, previous=None):
    """Return (ownership record, new-block-created). Never adopt unrecorded user text."""
    if previous:
        return previous, False
    if str(prefix / "bin") in os.get_exec_path():
        return None, False
    if Path(shell).name != "zsh" or (
        os.environ.get("ZDOTDIR") and Path(os.environ["ZDOTDIR"]).expanduser().resolve() != home
    ):
        return None, False
    path = home / ".zshrc"
    old = read_config(path)
    if START in old[0] or END in old[0]:
        print(t("path.existing_block", path=path))
        return None, False
    block = ("\n" if old[0] and not old[0].endswith("\n") else "") + block_for(prefix)
    write_config(path, old[0] + block, old)
    return {"path": str(path), "block": block}, True


def remove(prefix, home, record):
    if not record:
        return
    path = home / ".zshrc"
    if record.get("path") != str(path) or record.get("block") not in (
        block_for(prefix),
        "\n" + block_for(prefix),
    ):
        print(t("path.record_changed"))
        return
    try:
        old = read_config(path)
        block = record["block"]
        if old[0].count(block) != 1 or old[0].count(START) != 1 or old[0].count(END) != 1:
            print(t("path.record_changed"))
            return
        write_config(path, old[0].replace(block, "", 1), old)
    except (OSError, UserError):
        print(t("path.record_changed"))
