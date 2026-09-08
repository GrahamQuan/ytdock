import shutil
import subprocess

import pytest

from ytdock.core import UserError
from ytdock.media import faststart, probe, process, verify

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
pytestmark = pytest.mark.skipif(not FFMPEG or not FFPROBE, reason="FFmpeg required")


def fixture(path, video_codec="libx264", audio_codec=None, portrait=False):
    args = [
        FFMPEG,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={'90x160' if portrait else '160x90'}:rate=30000/1001:duration=1",
    ]
    if audio_codec:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-c:a", audio_codec]
    args += ["-c:v", video_codec, "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(args, check=True, capture_output=True)


@pytest.mark.parametrize(
    "video_codec,audio_codec,portrait",
    [
        ("libx264", "aac", False),
        ("libx264", None, False),
        ("libvpx-vp9", "libopus", True),
        ("libx264", "libopus", False),
    ],
)
def test_real_output(tmp_path, video_codec, audio_codec, portrait):
    video = tmp_path / "source.mkv"
    fixture(video, video_codec, audio_codec, portrait)
    output = tmp_path / "out.mp4"
    choice = dict(
        width=90 if portrait else 160,
        height=160 if portrait else 90,
        fps=29.97,
        has_audio=audio_codec is not None,
        transcode=video_codec != "libx264" or audio_codec == "libopus",
    )
    events = []
    source = process(video, None, output, choice, 1, FFMPEG, FFPROBE, events.append)
    verify(output, choice, 1, source, FFMPEG, FFPROBE)
    assert faststart(output)
    assert probe(output, FFPROBE)["streams"][0]["codec_name"] == "h264"
    assert (
        "stage.transcoding" in events[0]["stage"]
        if choice["transcode"]
        else events[0]["stage"] == "stage.packaging_mp4"
    )


def test_separate_audio_and_corrupt_output(tmp_path):
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.m4a"
    fixture(video)
    subprocess.run(
        [
            FFMPEG,
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "sine=duration=1",
            "-c:a",
            "aac",
            str(audio),
        ],
        check=True,
    )
    choice = dict(width=160, height=90, fps=29.97, has_audio=True, transcode=False)
    output = tmp_path / "out.mp4"
    events = []
    source = process(video, audio, output, choice, 1, FFMPEG, FFPROBE, events.append)
    assert events[0]["stage"] == "stage.merging"
    verify(output, choice, 1, source, FFMPEG, FFPROBE)
    output.write_bytes(output.read_bytes()[:200])
    with pytest.raises(UserError):
        verify(output, choice, 1, source, FFMPEG, FFPROBE)


def test_dimensions_and_audio_mismatch_rejected(tmp_path):
    video = tmp_path / "source.mp4"
    fixture(video)
    for choice in [
        dict(width=320, height=90, fps=29.97, has_audio=False, transcode=False),
        dict(width=160, height=90, fps=29.97, has_audio=True, transcode=False),
    ]:
        with pytest.raises(UserError):
            process(video, None, tmp_path / "out.mp4", choice, 1, FFMPEG, FFPROBE, lambda e: None)
    assert not (tmp_path / "out.mp4").exists()
