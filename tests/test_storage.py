import os

import pytest

from ytdock.core import UserError
from ytdock.storage import Task, instance_lock, publish, recover


def test_exclusive_publish_and_cleanup(tmp_path):
    task = Task(tmp_path)
    source = task.path / "verified.mp4"
    source.write_bytes(b"verified")
    existing = tmp_path / "video.mp4"
    existing.write_bytes(b"existing")
    target = publish(source, tmp_path, "video")
    assert target.name == "video (1).mp4"
    task.cleanup()
    assert target.read_bytes() == b"verified"
    assert existing.read_bytes() == b"existing"
    assert not task.path.exists()


def test_single_instance(tmp_path):
    with instance_lock(tmp_path):
        with pytest.raises(UserError, match="已有"):
            with instance_lock(tmp_path):
                pass
    with instance_lock(tmp_path):
        pass


def test_recovery_preserves_active_and_unowned(tmp_path):
    active = Task(tmp_path)
    stale = Task(tmp_path)
    os.close(stale.lease)
    stale.lease = -1
    unknown = tmp_path / ".ytdock-task-unknown"
    unknown.mkdir()
    assert unknown in recover(tmp_path)
    assert active.path.exists()
    assert not stale.path.exists()
    assert unknown.exists()
    active.cleanup()


def test_symlinks_never_followed(tmp_path):
    task = Task(tmp_path)
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "file").write_text("keep")
    (task.path / "alias").symlink_to(protected)
    task.cleanup()
    assert (protected / "file").read_text() == "keep"
    (tmp_path / ".ytdock-task-alias").symlink_to(protected)
    recover(tmp_path)
    assert protected.exists()


def test_publish_symlink_conflict(tmp_path):
    source = tmp_path / "source"
    source.write_text("data")
    (tmp_path / "result.mp4").symlink_to(tmp_path / "missing")
    assert publish(source, tmp_path, "result").name == "result (1).mp4"
    assert not (tmp_path / "missing").exists()


def test_disk_full_never_publishes(tmp_path, monkeypatch):
    import errno

    source = tmp_path / "source"
    source.write_bytes(b"verified")

    def disk_full(*args, **kwargs):
        raise OSError(errno.ENOSPC, "disk full")

    monkeypatch.setattr(os, "link", disk_full)
    with pytest.raises(UserError, match="空间不足"):
        publish(source, tmp_path, "out")
    assert not (tmp_path / "out.mp4").exists()
    assert source.read_bytes() == b"verified"


@pytest.mark.parametrize("extension", [".es.srt", ".ja.srt", ".es-419.srt", ".zh-hans.srt"])
def test_native_caption_publication_keeps_existing(tmp_path, extension):
    source = tmp_path / "captions.srt"
    source.write_text("subtitle")
    output = tmp_path / "output"
    output.mkdir()
    existing = output / ("video" + extension)
    existing.write_text("keep")
    result = publish(source, output, "video", extension)
    assert result.name == "video (1)" + extension
    assert existing.read_text() == "keep"


@pytest.mark.parametrize(
    "extension", [".../../es.srt", ".en/xx.srt", ".srt", ".en.srt/evil", ".en-orig.srt"]
)
def test_caption_extension_rejects_unsafe_values(tmp_path, extension):
    with pytest.raises(ValueError):
        publish(tmp_path / "absent", tmp_path, "video", extension)
