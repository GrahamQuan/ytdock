"""The new namespace is exclusive; historical names here are protection fixtures."""

import importlib.util
import json
import sys

from ytdock import controller, i18n, install, storage


def test_package_resources_and_worker_namespace():
    assert importlib.util.find_spec("yt_cli") is None
    assert controller.worker_command() == [sys.executable, "-m", "ytdock.worker"]
    assert i18n.LOCALES_DIRECTORY.parent.name == "ytdock"
    assert {"en", "zh-CN"} <= i18n.CATALOGS.keys()
    assert install.APP_ID == storage.APP_ID == "ytdock"


def test_only_new_task_ownership_is_recovered(tmp_path):
    old = tmp_path / (".yt-cli-task-" + "a" * 32)
    old.mkdir()
    (old / "owner.json").write_text(json.dumps({"app": "yt-cli-v1", "token": "a" * 32}))
    sentinel = old / "keep.mp4"
    sentinel.write_bytes(b"old task")
    task = storage.Task(tmp_path)
    assert task.path.name.startswith(".ytdock-task-")
    assert json.loads((task.path / "owner.json").read_text())["app"] == "ytdock-v1"
    task.cleanup()
    assert storage.recover(tmp_path) == []
    assert sentinel.read_bytes() == b"old task"


def test_old_environment_variables_are_ignored(tmp_path, monkeypatch):
    from ytdock import ffmpeg

    for name in ("YTDOCK_FFMPEG_DIR", "YTDOCK_FFMPEG", "YTDOCK_FFPROBE"):
        monkeypatch.delenv(name, raising=False)
    old = tmp_path / "old"
    old.mkdir()
    current = tmp_path / "current"
    current.mkdir()
    for directory in (old, current):
        for tool in ("ffmpeg", "ffprobe"):
            (directory / tool).touch()
    monkeypatch.setenv("YT_CLI_FFMPEG_DIR", str(old))
    monkeypatch.setenv("YT_CLI_FFMPEG", str(old / "ffmpeg"))
    monkeypatch.setenv("YT_CLI_FFPROBE", str(old / "ffprobe"))
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([current]))
    checked = []
    monkeypatch.setattr(ffmpeg, "validate", lambda path, kind: checked.append(path))
    result = ffmpeg.find_media_tools()
    assert result["ffmpeg"] == str((current / "ffmpeg").resolve())
    assert all(path.parent == current for path in checked)
