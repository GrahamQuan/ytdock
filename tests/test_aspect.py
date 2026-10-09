import asyncio
import hashlib
import shutil
import subprocess
from fractions import Fraction

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock.aspect import PRESETS, dimensions
from ytdock.compression import inspect
from ytdock.controller import execute
from ytdock.core import UserError
from ytdock.media import probe
from ytdock.storage import instance_lock
from ytdock.ui import select_aspect


@pytest.mark.parametrize("ratio", (*PRESETS, "7:5", "18:32"))
def test_dimensions(ratio):
    w, h = dimensions({"width": 1920, "height": 1080}, ratio)
    assert Fraction(w, h) == Fraction(ratio.replace(":", "/"))
    assert w % 2 == h % 2 == 0


@pytest.mark.parametrize("ratio", ("0:1", "1:0", "-1:2", "foo", "1:100", "9999:9998"))
def test_invalid(ratio):
    with pytest.raises(UserError):
        dimensions({"width": 1920, "height": 1080}, ratio)


def test_picker_custom():
    async def run():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            task = asyncio.create_task(
                select_aspect({"title": "movie", "video": {"width": 1920, "height": 1080}}, "/tmp")
            )
            await asyncio.sleep(0.05)
            pipe.send_text("\x1b[B" * 4 + "\x01\x0b7:5\r")
            assert await asyncio.wait_for(task, 3) == "7:5"

    asyncio.run(run())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_repair_exports_verified_video(tmp_path):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=320x180:rate=24",
            "-t",
            "0.5",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
    )
    before = hashlib.sha256(source.read_bytes()).digest()
    info = inspect(source, ffprobe)
    with instance_lock(tmp_path) as lock:
        result = asyncio.run(
            execute(
                {
                    "operation": "repair_aspect",
                    "source": info,
                    "ratio": "9:16",
                    "ffmpeg": ffmpeg,
                    "ffprobe": ffprobe,
                },
                tmp_path,
                lock,
                asyncio.Event(),
                lambda event: None,
            )
        )
    output = probe(result["path"], ffprobe)["streams"][0]
    assert (output["width"], output["height"]) == (180, 320)
    assert output["sample_aspect_ratio"] == "1:1"
    assert "ratio-fixed" in result["path"]
    assert hashlib.sha256(source.read_bytes()).digest() == before
