import asyncio
import hashlib
import shutil
import subprocess
from pathlib import Path

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock import ui
from ytdock.core import UserError
from ytdock.local_subtitles import inspect, prepare, process
from ytdock.media import probe

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
media = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="FFmpeg required")


def test_local_srt_preserves_multiline_and_segments(tmp_path):
    source = tmp_path / "翻译 字幕.srt"
    text = "This line intentionally has more than forty-two characters.\n这是第二行，保持分段。"
    source.write_text(f"8\n00:00:00,100 --> 00:00:02,000\n{text}\n", encoding="utf-8-sig")
    before = source.read_bytes()
    target = tmp_path / "captions.srt"
    events = []
    result = prepare(source, target, 1.5, events.append)
    assert result["count"] == 1 and result["adjusted"] == 1
    assert text in target.read_text() and "00:00:01,500" in target.read_text()
    assert source.read_bytes() == before and events


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\xff",
        b"1\n00:00:00,000 --> 00:00:04,001\na",
        b"1\n00:00:02,000 --> 00:00:02,100\na",
        b"1\n00:00:00,000 --> 00:00:01,000\na\n\n2\n00:00:00,900 --> 00:00:01,500\nb",
        b"1\n00:00:01,000 --> 00:00:00,000\na",
    ],
)
def test_local_invalid_srt(tmp_path, data):
    source = tmp_path / "input.srt"
    source.write_bytes(data)
    with pytest.raises(UserError):
        prepare(source, tmp_path / "captions.srt", 2, lambda e: None)
    assert source.read_bytes() == data


@media
@pytest.mark.parametrize(
    "text,fps,dimensions,audio",
    [
        ("English first line\nSecond line", 30, "640x360", "aac"),
        ("中文翻译字幕\n保留原来的时间轴", 60, "360x640", None),
        ("Esta es una traducción\nSin cambiar los segmentos", 60, "640x360", "pcm_s16le"),
        ("日本語の翻訳字幕\n元の区切りを保ちます", 30, "640x360", "aac"),
    ],
)
def test_real_local_burn(tmp_path, text, fps, dimensions, audio):
    video = tmp_path / "local 中文 video.mkv"
    args = [
        FFMPEG,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=s={dimensions}:r={fps}:d=1.2",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1.2", "-c:a", audio]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", str(video)]
    subprocess.run(args, check=True)
    captions = tmp_path / "translated.srt"
    captions.write_text(f"1\n00:00:00,100 --> 00:00:01,000\n{text}\n")
    hashes = [hashlib.sha256(p.read_bytes()).hexdigest() for p in (video, captions)]
    task = tmp_path / "task"
    task.mkdir()
    info = inspect(video, captions, task, FFMPEG, FFPROBE, lambda e: None)
    result = process(info, task, FFMPEG, FFPROBE, lambda e: None)
    streams = probe(task / "subtitled.mp4", FFPROBE)["streams"]
    assert streams[0]["codec_name"] == "hevc" and streams[0]["codec_tag_string"] == "hvc1"
    assert streams[0]["avg_frame_rate"] == f"{fps}/1"
    assert len(streams) == 1 + bool(audio)
    if audio:
        assert streams[1]["codec_name"] == "aac"
    assert result["stem"] == "local 中文 video_with-subtitle"
    assert hashes == [hashlib.sha256(p.read_bytes()).hexdigest() for p in (video, captions)]


def test_dual_input_keeps_values_and_language_picker():
    async def scenario(pipe):
        task = asyncio.create_task(
            ui.read_input("subtitles", {"video": "/tmp/a video.mp4", "captions": ""})
        )
        await asyncio.sleep(0.1)
        pipe.send_text("\x1b[B/tmp/翻译.srt\x0f")
        await asyncio.sleep(0.1)
        pipe.send_text("\x1b")
        await asyncio.sleep(0.6)
        pipe.send_text("\t")
        action, values = await task
        assert action == "switch" and values == {
            "video": "/tmp/a video.mp4",
            "captions": "/tmp/翻译.srt",
        }
        task = asyncio.create_task(ui.read_input("subtitles", values))
        await asyncio.sleep(0.1)
        pipe.send_text("\r")
        assert await task == ("submit", values)

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_navigation_active_mode_is_styled():
    for mode in ui.MODES:
        fragments = ui.navigation(mode)
        active = [text for style, text in fragments if style == "class:selected"]
        assert len(active) == 1 and active[0].startswith("[") and active[0].endswith("]")


@media
def test_glyph_and_overflow_preflight(tmp_path):
    from ytdock.burn import render_sample

    with pytest.raises(UserError):
        render_sample(tmp_path, "Hello\u0378", 640, 360, FFMPEG, 16, strict=True)
    assert not render_sample(tmp_path, "W" * 300, 640, 360, FFMPEG, 16, strict=True)


@media
@pytest.mark.parametrize(
    "cancel_stage", [None, "stage.burning_subtitles", "stage.verifying_subtitled"]
)
def test_local_worker_publication_and_cancel(tmp_path, cancel_stage):
    from ytdock.controller import Cancelled, execute
    from ytdock.storage import instance_lock

    video = tmp_path / "input.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=320x180:r=30:d=2",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )
    captions = tmp_path / "翻译.srt"
    captions.write_text("1\n00:00:00,100 --> 00:00:01,500\n这是翻译字幕\n")
    before = [p.read_bytes() for p in (video, captions)]
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    existing = downloads / "input_with-subtitle.mp4"
    existing.write_bytes(b"keep")

    async def scenario(lock):
        base = dict(ffmpeg=FFMPEG, ffprobe=FFPROBE)
        info = await execute(
            base
            | dict(operation="inspect_subtitles", path=str(video), captions_path=str(captions)),
            downloads,
            lock,
            asyncio.Event(),
            lambda e: None,
        )
        cancel = asyncio.Event()

        def update(event):
            if cancel_stage and event.get("stage") == cancel_stage:
                cancel.set()

        task = execute(
            base | dict(operation="local_subtitles", source=info), downloads, lock, cancel, update
        )
        if cancel_stage:
            with pytest.raises(Cancelled):
                await task
        else:
            result = await task
            assert Path(result["path"]).name == "input_with-subtitle (1).mp4"

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert existing.read_bytes() == b"keep"
    assert before == [p.read_bytes() for p in (video, captions)]
    assert len(list(downloads.iterdir())) == (1 if cancel_stage else 2)


@media
def test_default_audio_track_is_copied(tmp_path):
    video = tmp_path / "multi.mp4"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=blue:s=320x180:d=1.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1.2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1.2",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:a",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            str(video),
        ],
        check=True,
    )
    captions = tmp_path / "input.srt"
    captions.write_text("1\n00:00:00,100 --> 00:00:01,000\nDefault audio\n")
    task = tmp_path / "task"
    task.mkdir()
    info = inspect(video, captions, task, FFMPEG, FFPROBE, lambda e: None)
    assert info["audio_count"] == 2 and info["audio"]["index"] == 2
    process(info, task, FFMPEG, FFPROBE, lambda e: None)

    def audio_hash(path, index):
        return subprocess.check_output(
            [FFMPEG, "-v", "error", "-i", str(path), "-map", index, "-c", "copy", "-f", "hash", "-"]
        )

    assert audio_hash(video, "0:2") == audio_hash(task / "subtitled.mp4", "0:a:0")


@media
def test_changed_caption_rejected_before_burning(tmp_path):
    from ytdock.compression import identity

    video = tmp_path / "source.mp4"
    video.write_bytes(b"source")
    captions = tmp_path / "source.srt"
    captions.write_text("old")
    info = {
        "path": str(video),
        "identity": identity(video),
        "captions": {"path": str(captions), "identity": identity(captions)},
    }
    captions.write_text("changed")
    with pytest.raises(UserError):
        process(info, tmp_path, FFMPEG, FFPROBE, lambda e: None)
    assert not (tmp_path / "subtitled.mp4").exists()


def test_local_confirmation_back_and_enter(tmp_path):
    info = dict(
        title="video.mp4",
        choice=dict(width=640, height=360, fps=60),
        duration=10,
        audio=None,
        captions=dict(count=3, adjusted=1),
    )

    async def scenario(pipe):
        for key, expected in (("\x1b", False), ("\r", True)):
            task = asyncio.create_task(ui.select_local_subtitles(info, tmp_path))
            await asyncio.sleep(0.1)
            pipe.send_text(key)
            assert await task is expected

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))
