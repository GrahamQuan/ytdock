import asyncio
import os
import sys

import pytest

from ytdock.controller import Cancelled, execute, group_alive
from ytdock.core import UserError
from ytdock.storage import Task, instance_lock

FAKE_WORKER = """
import json, os, pathlib, signal, subprocess, sys, time
r = json.loads(sys.stdin.readline())
p = pathlib.Path(r['directory'])
(p/'partial').write_bytes(b'incomplete')
if r.get('mode') == 'success':
    (p/'verified.mp4').write_bytes(b'verified-test-fixture')
    print(json.dumps({'result': {'title': 'test', 'id': 'BaW_jenozKc'}}), flush=True)
    sys.exit(0)
if r.get('mode') == 'error':
    print(json.dumps({'error': '检查失败'}), flush=True)
    sys.exit(1)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child_code = 'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(60)'
child = subprocess.Popen([sys.executable, '-c', child_code], pass_fds=tuple(r['pass_fds']))
print(json.dumps({'stage': r['stage'], 'pgid': os.getpgrp()}), flush=True)
time.sleep(60)
"""


def fake_worker(tmp_path, monkeypatch):
    script = tmp_path / "fake.py"
    script.write_text(FAKE_WORKER)
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await original(sys.executable, str(script), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)


@pytest.mark.parametrize(
    "stage",
    [
        "stage.fetching_information",
        "stage.downloading_video",
        "stage.downloading_audio",
        "stage.merging",
        "stage.transcoding",
        "stage.verifying_file",
    ],
)
def test_cancel_entire_group_and_clean(tmp_path, monkeypatch, stage):
    fake_worker(tmp_path, monkeypatch)
    download = tmp_path / "downloads"
    download.mkdir()
    existing = download / "existing.mp4"
    existing.write_bytes(b"keep")

    async def scenario(lock):
        cancel = asyncio.Event()
        pgids = []

        def update(event):
            if "pgid" in event:
                pgids.append(event["pgid"])
                cancel.set()

        with pytest.raises(Cancelled, match="已清理"):
            await execute({"operation": "download", "stage": stage}, download, lock, cancel, update)
        assert pgids and not group_alive(pgids[0])

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert list(download.iterdir()) == [existing]
    assert existing.read_bytes() == b"keep"


@pytest.mark.parametrize("mode", ["success", "error"])
def test_success_or_failure_publication(tmp_path, monkeypatch, mode):
    fake_worker(tmp_path, monkeypatch)
    download = tmp_path / "downloads"
    download.mkdir()

    async def scenario(lock):
        call = execute(
            {"operation": "download", "mode": mode}, download, lock, asyncio.Event(), lambda e: None
        )
        if mode == "error":
            with pytest.raises(UserError, match="检查失败"):
                await call
        else:
            result = await call
            assert result["path"].endswith(".mp4")

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert len(list(download.iterdir())) == (1 if mode == "success" else 0)


def test_cleanup_failure_reports_residual_on_cancel(tmp_path, monkeypatch):
    fake_worker(tmp_path, monkeypatch)
    download = tmp_path / "downloads"
    download.mkdir()
    original = Task.cleanup

    def fail(self):
        os.close(self.lease)
        self.lease = -1
        raise UserError(f"清理失败，临时文件仍位于：{self.path}")

    monkeypatch.setattr(Task, "cleanup", fail)

    async def scenario(lock):
        cancel = asyncio.Event()
        with pytest.raises(Cancelled, match="清理失败"):
            await execute(
                {"operation": "download", "stage": "stage.verifying_file"},
                download,
                lock,
                cancel,
                lambda e: cancel.set(),
            )

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert list(download.glob(".ytdock-task-*"))
    monkeypatch.setattr(Task, "cleanup", original)


def test_cancel_real_ffmpeg(tmp_path, monkeypatch):
    import shutil

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        pytest.skip("FFmpeg required")
    script = tmp_path / "ffmpeg_worker.py"
    script.write_text("""
import json, pathlib, subprocess, sys
r = json.loads(sys.stdin.readline())
p = pathlib.Path(r['directory'])
child = subprocess.Popen([r['ffmpeg'], '-loglevel', 'error', '-nostdin', '-re',
    '-f', 'lavfi', '-i', 'testsrc2=size=160x90:rate=30', '-t', '60',
    '-c:v', 'libx264', str(p/'partial.mp4')], pass_fds=tuple(r['pass_fds']))
print(json.dumps({'stage': '转码', 'started': True}), flush=True)
child.wait()
""")
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await original(sys.executable, str(script), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    async def scenario(lock):
        cancel = asyncio.Event()

        def update(event):
            if event.get("started"):
                asyncio.get_running_loop().call_later(0.2, cancel.set)

        with pytest.raises(Cancelled):
            await execute(
                dict(operation="download", ffmpeg=ffmpeg), downloads, lock, cancel, update
            )

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert list(downloads.iterdir()) == []


def test_frozen_worker_command(monkeypatch):
    from ytdock import controller

    monkeypatch.setattr(controller.sys, "frozen", True, raising=False)
    monkeypatch.setattr(controller.sys, "executable", "/installed/ytdock")
    assert controller.worker_command() == ["/installed/ytdock", "--worker"]


@pytest.mark.parametrize("state,alive", [("Z", False), ("S", True)])
def test_macos_orphan_group_permission(monkeypatch, state, alive):
    import subprocess

    from ytdock import controller

    def denied(*args):
        raise PermissionError

    monkeypatch.setattr(controller.os, "killpg", denied)
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, stdout=f"123 {state}\n"),
    )
    assert controller.group_alive(123) is alive


def test_cancel_real_quickjs(tmp_path, monkeypatch):
    import shutil
    from pathlib import Path

    qjs = Path(__file__).resolve().parents[1] / ".runtime/bin/qjs"
    if not qjs.exists():
        available = shutil.which("qjs")
        if not available:
            pytest.skip("QuickJS required")
        qjs = Path(available)
    script = tmp_path / "qjs_worker.py"
    script.write_text("""
import json, pathlib, subprocess, sys
r = json.loads(sys.stdin.readline())
p = pathlib.Path(r['directory'])
(p/'challenge.js').write_text('while (true) {}')
child = subprocess.Popen([r['quickjs'], str(p/'challenge.js')], pass_fds=tuple(r['pass_fds']))
print(json.dumps({'stage': '正在获取视频信息', 'started': True}), flush=True)
child.wait()
""")
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        return await original(sys.executable, str(script), **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    downloads = tmp_path / "downloads"
    downloads.mkdir()

    async def scenario(lock):
        cancel = asyncio.Event()

        def update(event):
            if event.get("started"):
                asyncio.get_running_loop().call_later(0.2, cancel.set)

        with pytest.raises(Cancelled):
            await execute(
                dict(operation="inspect", quickjs=str(qjs)), downloads, lock, cancel, update
            )

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert list(downloads.iterdir()) == []


SUBTITLE_WORKER = """
import json, pathlib, sys, time, subprocess, os
r=json.loads(sys.stdin.readline()); p=pathlib.Path(r['directory'])
if r['operation']=='download':
    (p/'verified.mp4').write_bytes(b'source')
    (p/'captions.srt').write_text('processed captions')
    print(json.dumps({'result':{'title':'test','id':'BaW_jenozKc','duration':1}}),flush=True)
else:
    if r.get('mode')=='error':
        print(json.dumps({'error':'burn failed'}),flush=True); sys.exit(1)
    if r.get('mode')=='cancel':
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'],
                               pass_fds=tuple(r['pass_fds']))
        print(json.dumps({'stage':r.get('stage','stage.burning_subtitles'),'pgid':os.getpgrp()}),flush=True)
        time.sleep(60)
    (p/'subtitled.mp4').write_bytes(b'hevc')
    print(json.dumps({'result':{}}),flush=True)
"""


@pytest.mark.parametrize("mode", ["success", "error", "cancel", "partial"])
@pytest.mark.parametrize("language", ["en", "es", "ja"])
def test_subtitle_publication_order_and_retention(tmp_path, monkeypatch, mode, language):
    from ytdock import controller

    script = tmp_path / "subtitle_worker.py"
    script.write_text(SUBTITLE_WORKER)
    monkeypatch.setattr(controller, "worker_command", lambda: [sys.executable, str(script)])
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    existing = downloads / "test [BaW_jenozKc].mp4"
    existing.write_bytes(b"keep")
    events = []
    original = controller.publish

    def publish(source, downloads, stem, extension=".mp4"):
        if mode == "partial" and extension == f".{language}.srt":
            raise UserError("disk full")
        return original(source, downloads, stem, extension)

    monkeypatch.setattr(controller, "publish", publish)

    async def scenario(lock):
        cancel = asyncio.Event()

        def update(event):
            events.append(event)
            if "pgid" in event:
                assert len([e for e in events if "saved" in e]) == 2
                cancel.set()

        call = execute(
            {
                "operation": "download",
                "subtitles": True,
                "mode": mode,
                "caption": {"kind": "automatic", "language": language},
            },
            downloads,
            lock,
            cancel,
            update,
        )
        if mode == "success":
            result = await call
            assert set(result["saved"]) == {"source", "captions", "subtitled"}
            assert result["saved"]["captions"].endswith(f".{language}.srt")
        else:
            with pytest.raises(Cancelled if mode == "cancel" else UserError) as exc:
                await call
            assert "已保留" in str(exc.value)
        if mode == "cancel":
            assert not group_alive(next(e["pgid"] for e in events if "pgid" in e))

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert existing.read_bytes() == b"keep"
    assert (
        len(list(downloads.iterdir()))
        == {"success": 4, "error": 3, "cancel": 3, "partial": 2}[mode]
    )
    assert not list(downloads.glob(".ytdock-task-*"))
