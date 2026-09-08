import json
import os
import platform
from pathlib import Path

import pytest

from ytdock.core import UserError
from ytdock.install import APP_ID, install_bundle, inventory, layout, uninstall, verify_bundle


def bundle(root, content="version one"):
    root.mkdir()
    (root / "ytdock").write_text(content)
    (root / "ytdock").chmod(0o755)
    (root / "runtime").mkdir()
    (root / "runtime/qjs").write_text("fixture")
    (root / "bundle.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "app": APP_ID,
                "architecture": platform.machine(),
                "minimum_macos": "14.0",
                "version": "test",
                "files": inventory(root),
            }
        )
    )
    return root


def test_install_upgrade_uninstall_preserves_downloads(tmp_path):
    source = bundle(tmp_path / "release")
    prefix = tmp_path / "prefix with spaces"
    command = install_bundle(source, prefix)
    assert command.read_text() == "version one"
    video = prefix / "video.mp4"
    video.write_text("user media")
    new = bundle(tmp_path / "new", "version two")
    install_bundle(new, prefix)
    assert command.read_text() == "version two"
    uninstall(prefix)
    assert not os.path.lexists(command)
    assert not layout(prefix)[0].exists()
    assert video.read_text() == "user media"


def test_foreign_command_never_overwritten(tmp_path):
    source = bundle(tmp_path / "release")
    prefix = tmp_path / "prefix"
    (prefix / "bin").mkdir(parents=True)
    command = prefix / "bin/ytdock"
    command.write_text("another program")
    with pytest.raises(UserError, match="占用"):
        install_bundle(source, prefix)
    assert command.read_text() == "another program"
    assert not layout(prefix)[0].exists()


def test_unknown_installed_files_block_deletion(tmp_path):
    source = bundle(tmp_path / "release")
    prefix = tmp_path / "prefix"
    install_bundle(source, prefix)
    extra = layout(prefix)[0] / "user.txt"
    extra.write_text("keep")
    with pytest.raises(UserError, match="变化"):
        uninstall(prefix)
    assert extra.read_text() == "keep"


def test_corrupt_release_and_outside_symlinks_rejected(tmp_path):
    source = bundle(tmp_path / "release")
    (source / "ytdock").write_text("changed")
    with pytest.raises(UserError, match="变化"):
        verify_bundle(source)
    (source / "alias").symlink_to(tmp_path)
    with pytest.raises(UserError, match="越界"):
        inventory(source)


def test_failed_upgrade_rolls_back(tmp_path, monkeypatch):
    prefix = tmp_path / "prefix"
    install_bundle(bundle(tmp_path / "v1"), prefix)
    layout(prefix)[1].unlink()
    new = bundle(tmp_path / "v2", "version two")

    def fail(*args, **kwargs):
        raise OSError("simulated link failure")

    monkeypatch.setattr(Path, "symlink_to", fail)
    with pytest.raises(OSError):
        install_bundle(new, prefix)
    assert (layout(prefix)[0] / "ytdock").read_text() == "version one"


def test_modified_link_preserved_on_uninstall(tmp_path):
    prefix = tmp_path / "prefix"
    command = install_bundle(bundle(tmp_path / "release"), prefix)
    command.unlink()
    command.write_text("replacement command")
    uninstall(prefix)
    assert command.read_text() == "replacement command"


def test_missing_ffmpeg_does_not_abort_install(tmp_path, monkeypatch, capsys):
    from ytdock import ffmpeg, install, storage

    source = bundle(tmp_path / "release")
    prefix = tmp_path / "prefix"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("SHELL", "/bin/zsh")
    monkeypatch.setattr(install.sys, "frozen", True, raising=False)
    monkeypatch.setattr(install.sys, "executable", str(source / "ytdock"))
    monkeypatch.setattr(storage, "system_directory", lambda kind: tmp_path / "support")

    def missing(**kwargs):
        raise UserError("缺少 FFmpeg；brew install ffmpeg")

    monkeypatch.setattr(ffmpeg, "find_media_tools", missing)
    assert install.run("install", str(prefix)) == 0
    assert (layout(prefix)[0] / "ytdock").exists()
    assert "程序已安装，但暂时不能下载" in capsys.readouterr().out


def test_path_upgrade_and_safe_removal(tmp_path, monkeypatch):
    from ytdock.shell_path import START

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    home = tmp_path
    config = home / ".zshrc"
    config.write_text("# user settings")
    prefix = home / "tools with spaces"
    source = bundle(tmp_path / "release")
    install_bundle(source, prefix, home, "/bin/zsh")
    install_bundle(source, prefix, home, "/bin/zsh")
    assert config.read_text().count(START) == 1
    config.write_text(config.read_text() + "# later user setting\n")
    uninstall(prefix, home)
    assert config.read_text() == "# user settings# later user setting\n"


def test_modified_path_block_is_preserved(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    prefix = tmp_path / ".local"
    install_bundle(bundle(tmp_path / "release"), prefix, tmp_path, "/bin/zsh")
    config = tmp_path / ".zshrc"
    modified = config.read_text().replace("export PATH=", "export PATH=/my/tools:")
    config.write_text(modified)
    uninstall(prefix, tmp_path)
    assert config.read_text() == modified


@pytest.mark.parametrize("case", ["already_on_path", "bash", "custom_zdotdir"])
def test_no_unowned_shell_edits(tmp_path, monkeypatch, case):
    prefix = tmp_path / ".local"
    monkeypatch.setenv("PATH", str(prefix / "bin") if case == "already_on_path" else "/bin")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    if case == "custom_zdotdir":
        monkeypatch.setenv("ZDOTDIR", str(tmp_path / "custom"))
    install_bundle(
        bundle(tmp_path / "release"),
        prefix,
        tmp_path,
        "/bin/bash" if case == "bash" else "/bin/zsh",
    )
    assert not (tmp_path / ".zshrc").exists()
    uninstall(prefix, tmp_path)


def test_shell_failure_rolls_back_and_protects_symlink(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "/bin")
    monkeypatch.delenv("ZDOTDIR", raising=False)
    target = tmp_path / "user-config"
    target.write_text("keep")
    (tmp_path / ".zshrc").symlink_to(target)
    prefix = tmp_path / ".local"
    with pytest.raises(UserError):
        install_bundle(bundle(tmp_path / "release"), prefix, tmp_path, "/bin/zsh")
    assert target.read_text() == "keep"
    assert not layout(prefix)[0].exists()


@pytest.mark.parametrize("old_command", ["file", "symlink"])
def test_old_names_and_third_party_files_are_untouched(tmp_path, old_command):
    # Historical names are fixtures only, never recognized by the current installer.
    prefix = tmp_path / ".local"
    old = prefix / "share/download-youtube-cli"
    old.mkdir(parents=True)
    (old / "yt").write_text("old application")
    (prefix / "bin").mkdir()
    link = prefix / "bin/yt"
    if old_command == "symlink":
        link.symlink_to(old / "yt")
    else:
        link.write_text("third-party tool")
    launcher = prefix / "bin/YouTube Downloader.command"
    launcher.write_text("old launcher")
    before = {p: p.read_bytes() for p in (old / "yt", link, launcher)}
    install_bundle(bundle(tmp_path / "release"), prefix)
    uninstall(prefix)
    assert all(p.read_bytes() == content for p, content in before.items())
    assert link.is_symlink() == (old_command == "symlink")


@pytest.mark.parametrize("action", ["install", "uninstall"])
def test_ytdock_instance_lock_blocks_changes(tmp_path, monkeypatch, action):
    from ytdock import install, storage

    prefix = tmp_path / ".local"
    source = bundle(tmp_path / "release")
    monkeypatch.setattr(install.sys, "frozen", True, raising=False)
    monkeypatch.setattr(install.sys, "executable", str(source / "ytdock"))
    monkeypatch.setattr(storage, "system_directory", lambda kind: tmp_path / "support")
    with storage.instance_lock(tmp_path / "support" / APP_ID):
        with pytest.raises(UserError):
            install.run(action, str(prefix))
    assert not prefix.exists()


def test_copy_disk_failure_keeps_installed_version(tmp_path, monkeypatch):
    from ytdock import install

    prefix = tmp_path / ".local"
    install_bundle(bundle(tmp_path / "initial"), prefix)

    def fail(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(install.shutil, "copytree", fail)
    with pytest.raises(OSError):
        install_bundle(bundle(tmp_path / "next", "new"), prefix)
    assert layout(prefix)[1].read_text() == "version one"
    assert not list((prefix / "share").glob(".ytdock-install-*"))
