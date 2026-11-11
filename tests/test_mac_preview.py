import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from ytdock.core import UserError
from ytdock.preview import color_filters, export, inspect

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_media = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="requires FFmpeg + ffprobe")


def make(*args):
    subprocess.run([FFMPEG, "-v", "error", "-nostdin", *map(str, args)], check=True)


def test_dolby_vision_base_layer():
    hdr = dict(
        pix_fmt="yuv420p10le",
        color_transfer="arib-std-b67",
        color_primaries="bt2020",
        color_space="bt2020nc",
        height=2160,
    )
    dovi = {"dv_profile": 8, "dv_bl_signal_compatibility_id": 4}
    assert any("tonemap" in f for f in color_filters(hdr | {"side_data_list": [dovi]}))
    with pytest.raises(UserError, match="Dolby Vision"):
        color_filters(hdr | {"side_data_list": [dovi | {"dv_profile": 5}]})


@requires_media
def test_preview_mkv_forced_audio_copy_and_cleanup(tmp_path):
    subtitle = tmp_path / "captions.srt"
    subtitle.write_text("1\n00:00:00,050 --> 00:00:00,800\nForced caption\n")
    source = tmp_path / "电影.mkv"
    make(
        "-f",
        "lavfi",
        "-i",
        "color=size=240x120:rate=25:duration=1",
        "-f",
        "lavfi",
        "-i",
        "sine=duration=1",
        "-i",
        subtitle,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-map",
        "1:a",
        "-map",
        "2:s",
        "-disposition:a:0",
        "0",
        "-disposition:a:1",
        "default",
        "-metadata:s:a:1",
        "language=fra",
        "-disposition:s:0",
        "forced",
        "-c:v",
        "libx264",
        "-color_range",
        "tv",
        "-colorspace",
        "bt709",
        "-color_trc",
        "bt709",
        "-color_primaries",
        "bt709",
        "-c:a",
        "flac",
        source,
    )
    notices = []
    plain = export(source, tmp_path, FFMPEG, FFPROBE, notify=notices.append)
    burned = export(source, tmp_path, FFMPEG, FFPROBE, burn_forced=True)
    assert plain != burned and plain.name == "电影.preview.mp4"
    assert any("fra" in s for s in notices)

    def frame(path):
        return subprocess.check_output(
            [
                FFMPEG,
                "-v",
                "error",
                "-ss",
                "0.4",
                "-i",
                path,
                "-map",
                "0:v:0",
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-",
            ]
        )

    assert frame(source) == frame(plain)
    assert frame(plain) != frame(burned)
    assert inspect(plain, FFPROBE)["streams"][1]["profile"] == "LC"
    assert not list(tmp_path.glob(".ytdock-task-*"))


@requires_media
def test_preview_cli_rotated_and_corrupt(tmp_path):
    source = tmp_path / "high rate.mp4"
    make("-f", "lavfi", "-i", "testsrc2=s=144x80:r=60000/1001:d=0.6", "-c:v", "libx264", source)
    vertical = tmp_path / "vertical.mov"
    make("-display_rotation", "90", "-i", source, "-c", "copy", vertical)
    args = [sys.executable, "-m", "ytdock.cli", "--mac-preview", str(vertical)]
    result = subprocess.run(args, capture_output=True, text=True, check=True)
    output = Path(result.stdout.strip())
    assert output == tmp_path / "vertical.preview.mp4"
    streams = inspect(output, FFPROBE)["streams"]
    assert len(streams) == 1
    assert (streams[0]["width"], streams[0]["height"]) == (80, 144)
    assert streams[0]["r_frame_rate"] == "30000/1001"
    vertical.write_bytes(b"damaged source")
    bad = subprocess.run(args, capture_output=True)
    assert bad.returncode and vertical.read_bytes() == b"damaged source"
    assert list(tmp_path.glob("vertical.preview*.mp4")) == [output]


@requires_media
@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_hdr_exports_bt709_without_hdr_signalling(tmp_path, transfer):
    source = tmp_path / "hdr.mkv"
    make(
        "-f",
        "lavfi",
        "-i",
        "testsrc2=size=120x80:rate=24:duration=0.5",
        "-pix_fmt",
        "yuv420p10le",
        "-color_trc",
        transfer,
        "-colorspace",
        "bt2020nc",
        "-color_primaries",
        "bt2020",
        "-c:v",
        "ffv1",
        "-vf",
        "format=yuv420p10le,setparams=color_primaries=bt2020"
        f":color_trc={transfer}:colorspace=bt2020nc",
        source,
    )
    out = export(source, tmp_path, FFMPEG, FFPROBE)
    video = inspect(out, FFPROBE)["streams"][0]
    assert video["pix_fmt"] == "yuv420p"
    assert all(video[k] == "bt709" for k in ("color_primaries", "color_transfer", "color_space"))
    assert not video.get("side_data_list")


@requires_media
def test_preview_worker_reads_verifies_publishes_and_cancels(tmp_path):
    import asyncio

    from ytdock.controller import Cancelled, execute
    from ytdock.storage import instance_lock

    source = tmp_path / "原片.mov"
    make("-f", "lavfi", "-i", "testsrc2=s=64x48:r=24:d=0.5", "-c:v", "libx264", source)
    paths = dict(ffmpeg=FFMPEG, ffprobe=FFPROBE)

    async def scenario(lock):
        cancel = asyncio.Event()
        info = await execute(
            paths | {"operation": "inspect_preview", "path": str(source)},
            tmp_path,
            lock,
            cancel,
            lambda _: None,
        )
        request = paths | {"operation": "mac_preview", "source": info}
        result = await execute(request, tmp_path, lock, cancel, lambda _: None)
        assert result["path"] == str(tmp_path / "原片.preview.mp4")

        def stop(event):
            if event.get("stage") == "stage.preview":
                cancel.set()

        with pytest.raises(Cancelled):
            await execute(request, tmp_path, lock, cancel, stop)

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert len(list(tmp_path.glob("*.mp4"))) == 1
    assert not list(tmp_path.glob(".ytdock-task-*"))


def test_preview_stalled_ffmpeg_exits_even_with_repeated_heartbeats(tmp_path):
    from ytdock.preview import run_ffmpeg

    fake = tmp_path / "ffmpeg"
    fake.write_text(
        f"#!{sys.executable}\nimport time\nwhile True:\n"
        " print('out_time_us=1000', flush=True)\n time.sleep(0.02)\n"
    )
    fake.chmod(0o755)
    with pytest.raises(UserError, match="FFmpeg"):
        run_ffmpeg([str(fake)], 2, lambda _: None, stall_timeout=0.25)
