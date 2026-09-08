import asyncio
import hashlib
import shlex
import shutil
import subprocess

import pytest

from ytdock.compression import (
    command,
    compress,
    inspect,
    local_path,
    output_name,
    summary,
    validate_video,
)
from ytdock.controller import Cancelled, execute
from ytdock.core import UserError
from ytdock.media import probe
from ytdock.storage import instance_lock

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
media_required = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="FFmpeg required")


@pytest.mark.parametrize("spelling", ["literal", "single", "double", "finder", "tilde"])
def test_local_paths(tmp_path, monkeypatch, spelling):
    path = tmp_path / "中文 有空格.mp4"
    path.touch()
    monkeypatch.setenv("HOME", str(tmp_path))
    values = {
        "literal": str(path),
        "single": shlex.quote(str(path)),
        "double": f'"{path}"',
        "finder": str(path).replace(" ", "\\ "),
        "tilde": "~/中文\\ 有空格.mp4",
    }
    assert local_path(values[spelling]) == path.resolve()


@pytest.mark.parametrize(
    "value",
    [
        "",
        "relative.mp4",
        "https://a/b",
        "/not/existing.mp4",
        "'/unclosed",
        "/one.mp4 /two.mp4",
        "/one\n/two",
        "/dev/null",
    ],
)
def test_invalid_paths(value):
    with pytest.raises(UserError):
        local_path(value)


def test_directories_multiple_files_and_shell_text(tmp_path):
    with pytest.raises(UserError):
        local_path(str(tmp_path))
    a, b = tmp_path / "a.mp4", tmp_path / "b.mp4"
    a.touch()
    b.touch()
    with pytest.raises(UserError):
        local_path(f"{shlex.quote(str(a))} {shlex.quote(str(b))}")
    weird = tmp_path / "$(touch PWNED);'movie.mp4"
    weird.touch()
    assert local_path(str(weird)) == weird
    assert not (tmp_path / "PWNED").exists()


@pytest.mark.parametrize(
    "change",
    [
        {"color_transfer": "smpte2084"},
        {"color_transfer": "arib-std-b67"},
        {"color_primaries": "bt2020"},
        {"pix_fmt": "yuv420p10le"},
        {"tags": {"rotate": "90"}},
        {"tags": {"rotate": "oops"}},
        {"side_data_list": [{"displaymatrix": "mirror", "rotation": 0}]},
        {"side_data_list": [{"side_data_type": "DOVI configuration record"}]},
        {"width": 91},
        {"avg_frame_rate": "0/0"},
        {"field_order": "tt"},
    ],
)
def test_unsupported_source_metadata(change):
    source = {
        "width": 160,
        "height": 90,
        "pix_fmt": "yuv420p",
        "codec_name": "h264",
        "avg_frame_rate": "60/1",
    }
    with pytest.raises(UserError):
        validate_video(source | change)


def fixture(path, fps="60", audio="aac", portrait=False, crf="23"):
    args = [
        FFMPEG,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={'90x160' if portrait else '160x90'}:rate={fps}:duration=1",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=duration=1", "-c:a", audio]
    args += ["-c:v", "libx264", "-crf", crf, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)


@media_required
@pytest.mark.parametrize(
    "profile,fps,audio,portrait",
    [
        ("size", "30", "aac", False),
        ("quality", "30", "aac", False),
        ("size", "60", None, True),
        ("quality", "60", None, True),
        ("size", "60", "pcm_s16le", False),
        ("quality", "60", "pcm_s16le", False),
        ("size", "60000/1001", "aac", False),
        ("quality", "60000/1001", "aac", False),
    ],
)
def test_real_compression(tmp_path, profile, fps, audio, portrait):
    source = tmp_path / "中文 原文件.mkv"
    fixture(source, fps, audio, portrait)
    digest = hashlib.sha256(source.read_bytes()).digest()
    info = inspect(source, FFPROBE)
    events = []
    out = tmp_path / "output.mp4"
    result = compress(info, profile, out, FFMPEG, FFPROBE, events.append)
    assert result["output_bytes"] == out.stat().st_size
    assert hashlib.sha256(source.read_bytes()).digest() == digest
    stages = [e["stage"] for e in events]
    assert stages[0] == "stage.compressing" and stages[-1] == "stage.verifying_file"
    assert any(e.get("finished") for e in events)
    info_out = probe(out, FFPROBE)
    assert len(info_out["streams"]) == (2 if audio else 1)
    args = command(info, profile, out, FFMPEG)
    assert "-r" not in args and "-vf" not in args
    assert args[args.index("-crf") + 1] == ("30" if profile == "size" else "23")
    if audio:
        assert args[args.index("-c:a") + 1] == (
            "copy" if profile == "quality" and audio == "aac" else "aac"
        )


@media_required
def test_default_audio_selected(tmp_path):
    source = tmp_path / "multi.mkv"
    subprocess.run(
        [
            FFMPEG,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=30:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=880:duration=1",
            "-map",
            "0",
            "-map",
            "1",
            "-map",
            "2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-disposition:a:0",
            "0",
            "-disposition:a:1",
            "default",
            str(source),
        ],
        check=True,
    )
    info = inspect(source, FFPROBE)
    assert info["audio_count"] == 2 and info["audio"]["index"] == 2
    out = tmp_path / "out.mp4"
    compress(info, "quality", out, FFMPEG, FFPROBE, lambda e: None)

    # Copy packets from the chosen track, including its actual audio content.
    def audio_bytes(path, index):
        return subprocess.check_output(
            [
                FFMPEG,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                f"0:{index}",
                "-c:a",
                "copy",
                "-f",
                "adts",
                "-",
            ]
        )

    assert audio_bytes(source, 2) == audio_bytes(out, 1)


@media_required
def test_source_changed_after_selection(tmp_path):
    source = tmp_path / "source.mp4"
    fixture(source)
    info = inspect(source, FFPROBE)
    source.write_bytes(source.read_bytes() + b"changed")
    with pytest.raises(UserError, match="源文件已变化"):
        compress(info, "quality", tmp_path / "out.mp4", FFMPEG, FFPROBE, lambda e: None)
    assert not (tmp_path / "out.mp4").exists()


def test_larger_output_and_filename():
    result = {"input_bytes": 100, "output_bytes": 120, "path": "/output.mp4"}
    assert "体积增加 20.0%" in summary(result)
    name = output_name({"path": "/tmp/../" + "中" * 100 + ".mp4"}, "size")
    assert "/" not in name and ".." not in name and len(name.encode()) < 240
    assert name.endswith("_compressed")


@media_required
def test_worker_publish_collision_and_continuous_jobs(tmp_path):
    source = tmp_path / "源.mp4"
    fixture(source, crf="51")
    before = source.read_bytes()

    async def scenario(lock):
        base = {"ffmpeg": FFMPEG, "ffprobe": FFPROBE}
        info = await execute(
            base | {"operation": "inspect_local", "path": str(source)},
            tmp_path,
            lock,
            asyncio.Event(),
            lambda e: None,
        )
        existing = tmp_path / (output_name(info, "quality") + ".mp4")
        existing.symlink_to(source)
        results = []
        for _ in range(2):
            results.append(
                await execute(
                    base | {"operation": "compress", "source": info, "profile": "quality"},
                    tmp_path,
                    lock,
                    asyncio.Event(),
                    lambda e: None,
                )
            )
        assert results[0]["path"].endswith(" (1).mp4")
        assert results[1]["path"].endswith(" (2).mp4")
        assert results[0]["output_bytes"] > results[0]["input_bytes"]
        assert "体积增加" in summary(results[0])
        assert existing.is_symlink()

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert source.read_bytes() == before
    assert not list(tmp_path.glob(".ytdock-task-*"))


@media_required
@pytest.mark.parametrize(
    "stage", ["stage.reading_information", "stage.compressing", "stage.verifying_file"]
)
def test_cancel_compression_worker(tmp_path, stage):
    source = tmp_path / "source.mp4"
    fixture(source)
    info = inspect(source, FFPROBE)
    before = source.read_bytes()

    async def scenario(lock):
        cancel = asyncio.Event()
        seen = []

        def update(e):
            seen.append(e)
            if e.get("stage") == stage:
                cancel.set()

        with pytest.raises(Cancelled, match="已清理"):
            await execute(
                {
                    "operation": "compress",
                    "source": info,
                    "profile": "quality",
                    "ffmpeg": FFMPEG,
                    "ffprobe": FFPROBE,
                },
                tmp_path,
                lock,
                cancel,
                update,
            )
        assert any(event.get("stage") == stage for event in seen)
        assert seen[-1]["task_outcome"] == {
            "status": "cancelled",
            "saved": {},
            "cleanup": {"ok": True, "residuals": []},
            "failure_stage": stage,
        }

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert source.read_bytes() == before and not list(tmp_path.glob(".ytdock-task-*"))
    assert list(tmp_path.glob("*.mp4")) == [source]


@media_required
@pytest.mark.parametrize("failure", ["disk", "crash", "verification"])
def test_worker_failure_cleans(tmp_path, failure):
    source = tmp_path / "source.mp4"
    fixture(source)
    info = inspect(source, FFPROBE)
    wrapper = tmp_path / "ffmpeg-wrapper"
    wrapper.write_text(f"""#!/usr/bin/env python3
import os,sys
args=sys.argv[1:]
if {failure!r} == 'verification' and '-progress' in args:
    os.execv({FFMPEG!r}, [{FFMPEG!r}, *args])
if '-progress' in args:
    open(args[-1], 'wb').write(b'partial')
if {failure!r} == 'disk':
    sys.stderr.write('No space left on device')
sys.exit(1)
""")
    wrapper.chmod(0o755)

    async def scenario(lock):
        with pytest.raises(UserError):
            await execute(
                {
                    "operation": "compress",
                    "source": info,
                    "profile": "quality",
                    "ffmpeg": str(wrapper),
                    "ffprobe": FFPROBE,
                },
                tmp_path,
                lock,
                asyncio.Event(),
                lambda e: None,
            )

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert source.is_file() and not list(tmp_path.glob(".ytdock-task-*"))
    assert list(tmp_path.glob("*.mp4")) == [source]


@media_required
@pytest.mark.parametrize("kind", ["ten_bit", "full_range", "sar"])
def test_color_and_aspect_ratio(tmp_path, kind):
    source = tmp_path / "source.mkv"
    args = [
        FFMPEG,
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=160x90:rate=60:duration=1",
        "-c:v",
        "ffv1",
    ]
    if kind == "ten_bit":
        args[args.index("ffv1")] = "libx264"
        args += [
            "-vf",
            "format=yuv420p10le,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709",
        ]
        args += [
            "-pix_fmt",
            "yuv420p10le",
            "-color_trc",
            "bt709",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
        ]
    elif kind == "full_range":
        args += ["-vf", "scale=in_range=tv:out_range=pc", "-color_range", "pc"]
    else:
        args += ["-vf", "setsar=4/3"]
    subprocess.run([*args, str(source)], check=True)
    info = inspect(source, FFPROBE)
    compress(info, "quality", tmp_path / "out.mp4", FFMPEG, FFPROBE, lambda e: None)


@media_required
@pytest.mark.parametrize("kind", ["empty", "garbage", "audio"])
def test_unusable_local_media(tmp_path, kind):
    source = tmp_path / "source.mp4"
    if kind == "audio":
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=1",
                "-c:a",
                "aac",
                str(source),
            ],
            check=True,
        )
    else:
        source.write_bytes(b"" if kind == "empty" else b"not a video")
    with pytest.raises(UserError):
        inspect(source, FFPROBE)
