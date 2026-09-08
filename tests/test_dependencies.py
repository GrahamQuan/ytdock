import subprocess

import pytest

from ytdock import dependencies
from ytdock.core import UserError


@pytest.fixture
def environment(monkeypatch):
    monkeypatch.setattr(dependencies.sys, "platform", "darwin")
    monkeypatch.setattr(dependencies.importlib.metadata, "version", lambda package: "test")
    monkeypatch.setattr(dependencies, "candidates", lambda name: iter([f"/tools/{name}"]))
    from ytdock import ffmpeg

    monkeypatch.setattr(
        ffmpeg,
        "find_media_tools",
        lambda **kwargs: {"ffmpeg": "/tools/ffmpeg", "ffprobe": "/tools/ffprobe"},
    )


def runtime(monkeypatch, version):
    def run(args, **kwargs):
        output = version if args[0] == "/tools/qjs" else "libx264 aac"
        return subprocess.CompletedProcess(
            args, 1 if args[0] == "/tools/qjs" else 0, stdout=output, stderr=""
        )

    monkeypatch.setattr(dependencies.subprocess, "run", run)


@pytest.mark.parametrize(
    "version",
    ["QuickJS version 2025-04-26", "QuickJS version 2026-06-04", "QuickJS-ng version 0.12.0"],
)
def test_quickjs_supported(environment, monkeypatch, version):
    runtime(monkeypatch, version)
    assert dependencies.check() == {
        "ffmpeg": "/tools/ffmpeg",
        "ffprobe": "/tools/ffprobe",
        "quickjs": "/tools/qjs",
    }


@pytest.mark.parametrize(
    "version", ["QuickJS version 2023-12-09", "QuickJS-ng version 0.11.0", "v23.10.0", "invalid"]
)
def test_quickjs_unsupported(environment, monkeypatch, version):
    runtime(monkeypatch, version)
    with pytest.raises(UserError, match="需要 QuickJS"):
        dependencies.check()


def test_missing_quickjs(environment, monkeypatch):
    monkeypatch.setattr(dependencies, "candidates", lambda name: iter([]))
    with pytest.raises(UserError, match="需要 QuickJS"):
        dependencies.find_quickjs()


def test_frozen_uses_only_bundled_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(dependencies.sys, "frozen", True, raising=False)
    monkeypatch.setattr(dependencies.sys, "executable", str(tmp_path / "ytdock"))
    assert list(dependencies.candidates("qjs")) == [str(tmp_path / "runtime/qjs")]
