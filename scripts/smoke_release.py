"""Validate a relocated release with an isolated install prefix and system-only PATH."""

import argparse
import asyncio
import os
import pty
import select
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from ytdock import controller
from ytdock.core import Choice
from ytdock.media import process, verify
from ytdock.storage import instance_lock


def run(args, env):
    result = subprocess.run(
        [str(x) for x in args], env=env, capture_output=True, text=True, timeout=120
    )
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    return result.stdout


def terminal_smoke(executable, env):
    master, slave = pty.openpty()
    process = subprocess.Popen(
        [str(executable)], stdin=slave, stdout=slave, stderr=slave, env=env, start_new_session=True
    )
    os.close(slave)
    output = b""
    try:
        deadline = time.monotonic() + 30
        while b"YTDock" not in output and time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.2)
            if ready:
                output += os.read(master, 65536)
            if process.poll() is not None:
                break
        if b"YTDock" not in output:
            raise RuntimeError("冻结界面启动失败：" + output.decode(errors="replace"))

        def wait_for(text):
            received = b""
            deadline = time.monotonic() + 10
            while text.encode() not in received and time.monotonic() < deadline:
                ready, _, _ = select.select([master], [], [], 0.1)
                if ready:
                    received += os.read(master, 65536)
            if text.encode() not in received:
                raise RuntimeError("冻结界面切换失败：" + received.decode(errors="replace"))

        os.write(master, b"draft-url\t")
        wait_for("Local Video File Path")
        os.write(master, b"\x0f")
        wait_for(" / 选择语言")
        os.write(master, b"\x1b[B\r")
        wait_for("本地视频文件路径")
        os.write(master, b"\x0f")
        wait_for(" / 选择语言")
        os.write(master, b"\x1b[A\r")
        wait_for("Local Video File Path")
        os.write(master, b"\x1b")
        wait_for("draft-url")
        os.write(master, b"\x03")
        if process.wait(timeout=15) != 130:
            raise RuntimeError("终端 Ctrl+C 退出码错误")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


def smoke(bundle, online=False):
    bundle = bundle.resolve()
    env = os.environ.copy()
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "DYLD_LIBRARY_PATH", "ZDOTDIR"):
        env.pop(name, None)
    env["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
    with tempfile.TemporaryDirectory(prefix="ytdock-release-test-") as temporary:
        root = Path(temporary).resolve()
        home = root / "home"
        home.mkdir()
        (home / "Downloads").mkdir()
        env.update(HOME=str(home), CFFIXED_USER_HOME=str(home), SHELL="/bin/zsh")
        prefix = home / ".local"
        downloads = root / "downloads"
        downloads.mkdir()
        run([bundle / "ytdock", "--install", "--prefix", prefix], env)
        executable = prefix / "bin/ytdock"
        installed = prefix / "share/ytdock"
        result = run([executable, "--check"], env)
        if str(installed / "runtime/qjs") not in result:
            raise RuntimeError("安装版没有使用内置 QuickJS")
        assert run(["/bin/zsh", "-ic", "command -v ytdock"], env).strip() == str(executable)
        terminal_smoke(executable, env)
        terminal_smoke(prefix / "bin/YTDock.command", env)
        selected_paths = dict(line.split("：", 1) for line in result.splitlines() if "：" in line)
        ffmpeg, ffprobe = [Path(selected_paths[name]) for name in ("ffmpeg", "ffprobe")]
        if any(path.is_relative_to(installed) for path in (ffmpeg, ffprobe)):
            raise RuntimeError("发布包意外使用了内置 FFmpeg")
        if set(p.name for p in (installed / "runtime").iterdir()) != {"qjs"}:
            raise RuntimeError("发布包包含 QuickJS 以外的原生运行时")
        external_stats = [(p.stat().st_size, p.stat().st_mtime_ns) for p in (ffmpeg, ffprobe)]
        custom = root / "custom media tools"
        custom.mkdir()
        (custom / "ffmpeg").symlink_to(ffmpeg)
        (custom / "ffprobe").symlink_to(ffprobe)
        run([executable, "--check", "--ffmpeg-dir", custom], env)
        occupied = subprocess.check_output(["/usr/bin/du", "-sk", str(installed)], text=True)
        print("安装目录实际占用：" + occupied.split()[0] + " KiB", flush=True)
        fixture = root / "fixture.mkv"
        run(
            [
                ffmpeg,
                "-loglevel",
                "error",
                "-nostdin",
                "-f",
                "lavfi",
                "-i",
                "testsrc2=size=160x90:rate=60:duration=1",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=1",
                "-c:v",
                "mpeg4",
                "-c:a",
                "pcm_s16le",
                fixture,
            ],
            env,
        )
        choice = dict(width=160, height=90, fps=60, has_audio=True, transcode=True)
        output = root / "verified.mp4"
        source = process(
            fixture, None, output, choice, 1, str(ffmpeg), str(ffprobe), lambda e: None
        )
        verify(output, choice, 1, source, str(ffmpeg), str(ffprobe))
        original = controller.worker_command
        controller.worker_command = lambda: [str(executable), "--worker"]

        async def check_compression(lock):
            base = {"ffmpeg": str(ffmpeg), "ffprobe": str(ffprobe)}
            before_source = fixture.read_bytes()
            metadata = await controller.execute(
                base | {"operation": "inspect_local", "path": str(fixture)},
                downloads,
                lock,
                asyncio.Event(),
                lambda e: None,
            )
            if metadata["choice"]["fps"] != 60:
                raise RuntimeError("冻结工作进程未读取实际 60fps")
            for profile in ("size", "quality"):
                result = await controller.execute(
                    base | {"operation": "compress", "source": metadata, "profile": profile},
                    downloads,
                    lock,
                    asyncio.Event(),
                    lambda e: None,
                )
                if not Path(result["path"]).is_file():
                    raise RuntimeError("冻结压缩工作进程没有发布成品")
            published = set(downloads.iterdir())
            for stage in ("stage.compressing", "stage.verifying_file"):
                cancel = asyncio.Event()
                try:
                    await controller.execute(
                        base | {"operation": "compress", "source": metadata, "profile": "quality"},
                        downloads,
                        lock,
                        cancel,
                        lambda event: cancel.set() if event.get("stage") == stage else None,
                    )
                except controller.Cancelled:
                    pass
                else:
                    raise RuntimeError("冻结压缩取消未生效")
                if set(downloads.iterdir()) != published:
                    raise RuntimeError("冻结压缩取消误删成品或遗留临时文件")
            if fixture.read_bytes() != before_source:
                raise RuntimeError("压缩修改了源文件")
            print("冻结工作进程：两种压缩、60fps、取消及成品保护通过。", flush=True)

        try:
            with instance_lock(root / "compression-lock") as lock:
                asyncio.run(check_compression(lock))
        finally:
            controller.worker_command = original

        async def check_subtitle_burn(lock):
            # A verified local source replaces network download; the installed executable
            # performs the actual burn, decode/frame checks and controller publication.
            fixture_worker = """
import json,pathlib,shutil,sys
request=json.loads(sys.stdin.readline()); directory=pathlib.Path(request['directory'])
shutil.copyfile(sys.argv[1],directory/'verified.mp4')
(directory/'captions.srt').write_text('1\\n00:00:00,000 --> 00:00:01,000\\nEnglish subtitles\\n')
print(json.dumps({'result':{'title':'subtitle fixture','id':'fixture','duration':1}}),flush=True)
"""
            for stage in (None, "stage.burning_subtitles", "stage.verifying_subtitled"):
                commands = iter(
                    [
                        [sys.executable, "-c", fixture_worker, str(output)],
                        [str(executable), "--worker"],
                    ]
                )
                controller.worker_command = lambda: next(commands)
                events = []
                cancel = asyncio.Event()
                before = set(downloads.iterdir())

                def update(event):
                    events.append(event)
                    if stage and event.get("stage") == stage:
                        cancel.set()

                request = dict(
                    operation="download",
                    subtitles=True,
                    caption={"kind": "manual", "language": "en"},
                    choice=choice,
                    ffmpeg=str(ffmpeg),
                    ffprobe=str(ffprobe),
                )
                try:
                    result = await controller.execute(request, downloads, lock, cancel, update)
                    if stage:
                        raise RuntimeError("Subtitle cancellation did not take effect")
                    assert len(result["saved"]) == 3
                except controller.Cancelled:
                    if not stage:
                        raise
                added = set(downloads.iterdir()) - before
                assert len(added) == (2 if stage else 3)
                assert not any(path.name.startswith(".ytdock-task-") for path in added)
                first_burn = next(
                    i for i, e in enumerate(events) if e.get("stage") == "stage.burning_subtitles"
                )
                assert len([e for e in events[:first_burn] if "saved" in e]) == 2
            print(
                "Frozen subtitles: HEVC, frames, early publication and cancellation passed.",
                flush=True,
            )

        original = controller.worker_command
        try:
            with instance_lock(root / "subtitle-lock") as lock:
                asyncio.run(check_subtitle_burn(lock))
        finally:
            controller.worker_command = original
        if online:
            original = controller.worker_command
            controller.worker_command = lambda: [str(executable), "--worker"]

            # The parent orchestration is exercised against the actual installed worker.
            async def check_download(lock):
                paths = {
                    "ffmpeg": str(ffmpeg),
                    "ffprobe": str(ffprobe),
                    "quickjs": str(installed / "runtime/qjs"),
                }
                base = paths | {"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}
                metadata = await controller.execute(
                    base | {"operation": "inspect"},
                    downloads,
                    lock,
                    asyncio.Event(),
                    lambda e: None,
                )
                selected = metadata["choices"][-1]
                print("冻结工作进程解析成功：" + Choice(**selected).label(), flush=True)
                result = await controller.execute(
                    base | {"operation": "download", "choice": selected},
                    downloads,
                    lock,
                    asyncio.Event(),
                    lambda e: None,
                )
                if not Path(result["path"]).is_file():
                    raise RuntimeError("冻结工作进程未发布成品")
                print("冻结工作进程下载、验证和发布成功。", flush=True)
                if metadata.get("caption") and not metadata.get("subtitle_error"):
                    subtitled = await controller.execute(
                        base
                        | {
                            "operation": "download",
                            "choice": selected,
                            "subtitles": True,
                            "caption": metadata["caption"],
                        },
                        downloads,
                        lock,
                        asyncio.Event(),
                        lambda e: None,
                    )
                    assert len(subtitled["saved"]) == 3
                    print("Frozen online captions and HEVC subtitles passed.", flush=True)
                before = set(downloads.iterdir())
                cancel = asyncio.Event()

                def update(event):
                    if event.get("stage") == "stage.downloading_video":
                        cancel.set()

                try:
                    await controller.execute(
                        base | {"operation": "download", "choice": selected},
                        downloads,
                        lock,
                        cancel,
                        update,
                    )
                except controller.Cancelled:
                    pass
                else:
                    raise RuntimeError("打包工作进程取消未生效")
                if set(downloads.iterdir()) != before:
                    raise RuntimeError("取消后遗留半成品或删除了已有成品")
                print("冻结工作进程取消和清理成功。", flush=True)

            try:
                with instance_lock(root / "test-lock") as lock:
                    asyncio.run(check_download(lock))
            finally:
                controller.worker_command = original
        # Reinstall exercises upgrade; uninstall is invoked from the installed executable itself.
        run([bundle / "ytdock", "--install", "--prefix", prefix], env)
        sentinel = prefix / "keep.mp4"
        sentinel.write_bytes(b"existing user file")
        run([executable, "--uninstall"], env)
        if installed.exists() or os.path.lexists(executable) or not sentinel.exists():
            raise RuntimeError("卸载检查失败")
        if "YTDock PATH" in (home / ".zshrc").read_text():
            raise RuntimeError("卸载遗留自身 PATH 块")
        if external_stats != [(p.stat().st_size, p.stat().st_mtime_ns) for p in (ffmpeg, ffprobe)]:
            raise RuntimeError("安装/卸载修改了外部媒体工具")
    print("安装、外部依赖、终端与启动器、真实转码、升级和卸载验证通过。", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()
    smoke(args.bundle, args.online)
