import asyncio
import string
import subprocess
import sys

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock import ui
from ytdock.compression import summary
from ytdock.controller import execute
from ytdock.core import Choice, UserError
from ytdock.i18n import CATALOGS, get_language, set_language, t
from ytdock.storage import instance_lock


class RecordingOutput(DummyOutput):
    def __init__(self):
        self.text = ""

    def write(self, data):
        self.text += data

    def write_raw(self, data):
        self.text += data


def test_catalog_placeholders_and_default():
    formatter = string.Formatter()
    assert set(CATALOGS["en"]) == set(CATALOGS["zh-CN"])
    for key, original in CATALOGS["en"].items():
        assert key.isascii() and " " not in key
        translated = CATALOGS["zh-CN"][key]
        assert translated

        def fields(value):
            return {
                (name, spec, conversion)
                for _, name, spec, conversion in formatter.parse(value)
                if name is not None
            }

        assert fields(original) == fields(translated), original
    result = subprocess.check_output(
        [sys.executable, "-c", "from ytdock.i18n import get_language; print(get_language())"],
        text=True,
    )
    assert result.strip() == "en"


@pytest.mark.parametrize("locale,expected", [("en", "Choose resolution"), ("zh-CN", "选择分辨率")])
def test_language_messages_preserve_user_text(locale, expected):
    set_language(locale)
    assert t("download.choose_resolution") == expected
    path = "/tmp/中文 {p0} Downloads 文件.mp4"
    assert path in summary(dict(input_bytes=100, output_bytes=120, path=path))
    option = Choice("a", None, 1920, 1080, 60, True, None, False, False)
    assert ("Size unknown" if locale == "en" else "大小未知") in option.label()
    for stage in (
        "stage.downloading_video",
        "stage.downloading_audio",
        "stage.compressing",
        "stage.verifying_file",
        "stage.cancelling",
    ):
        display = ui.status_text(dict(stage=stage, done=5, total=100), 0)
        assert "5.0%" in display and t(stage) in display


def test_language_picker_input_keeps_text_and_renders_language():
    set_language("en")
    output = RecordingOutput()

    async def wait_for(predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(0.01)

    async def scenario(pipe):
        task = asyncio.create_task(ui.read_input("compress", "/tmp/中文 file.mp4"))
        await wait_for(lambda: "Local Video File Path" in output.text)
        pipe.send_text("\x0f\x1b[B\r")
        await wait_for(lambda: get_language() == "zh-CN" and "本地视频文件路径" in output.text)
        pipe.send_text("\x0f\x1b[A\r")
        await wait_for(lambda: get_language() == "en")
        pipe.send_text("\r")
        assert await task == ("submit", "/tmp/中文 file.mp4")

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        asyncio.run(scenario(pipe))


def test_language_picker_compression_keeps_selection(tmp_path):
    set_language("en")
    output = RecordingOutput()
    info = dict(
        title="中文.mp4",
        choice=dict(width=1920, height=1080, fps=60),
        duration=20,
        input_bytes=1000,
        video=dict(index=0, codec_name="h264"),
        audio=dict(index=2, codec_name="aac"),
        audio_count=2,
    )

    async def scenario(pipe):
        task = asyncio.create_task(ui.select_compression(info, tmp_path))
        await asyncio.sleep(0.1)
        assert "Smaller file" in output.text and "Default" in output.text
        assert "2 audio tracks; keeping only" in output.text
        pipe.send_text("\x1b[B\x0f\x1b[B\r")
        await asyncio.sleep(0.1)
        assert "画质优先" in output.text and "选择压缩方式" in output.text
        pipe.send_text("\r")
        assert await task == "quality"

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        asyncio.run(scenario(pipe))


def test_language_picker_resolution_keeps_selection(tmp_path):
    set_language("en")
    options = [
        Choice(str(i), None, w, h, 60, False, None, False, False).to_dict()
        for i, (w, h) in enumerate([(1920, 1080), (1280, 720)])
    ]

    async def scenario(pipe):
        task = asyncio.create_task(
            ui.select(dict(title="中文 title", duration=10, choices=options), tmp_path)
        )
        await asyncio.sleep(0.1)
        pipe.send_text("\x1b[B\x0f\x1b[B\r")
        await asyncio.sleep(0.1)
        assert get_language() == "zh-CN"
        pipe.send_text("\t\r")
        assert (await task).height == 720

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize(
    "locale,text", [("en", "The source file is empty"), ("zh-CN", "源文件为空")]
)
def test_worker_inherits_language(tmp_path, locale, text):
    set_language(locale)
    source = tmp_path / "source.mp4"
    source.touch()

    async def scenario(lock):
        with pytest.raises(UserError, match=text):
            await execute(
                dict(operation="inspect_local", path=str(source), ffprobe="unused"),
                tmp_path,
                lock,
                asyncio.Event(),
                lambda event: None,
            )

    with instance_lock(tmp_path / "state") as lock:
        asyncio.run(scenario(lock))
    assert not list(tmp_path.glob(".ytdock-task-*"))


def test_cli_language_help():
    for args, text in [([], "Interface language"), (["--lang", "zh-CN"], "界面语言")]:
        result = subprocess.run(
            [sys.executable, "-m", "ytdock.cli", *args, "--help"], capture_output=True, text=True
        )
        assert result.returncode == 0 and text in result.stdout
    result = subprocess.run(
        [sys.executable, "-m", "ytdock.cli", "--lang", "fr"], capture_output=True
    )
    assert result.returncode == 2


@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_picker_current_language_cancel_and_cursor(locale):
    set_language(locale)
    output = RecordingOutput()

    async def scenario(pipe):
        task = asyncio.create_task(ui.read_input("compress", "/tmp/ab.mp4"))
        await asyncio.sleep(0.1)
        pipe.send_text("\x1b[D\x0f")
        await asyncio.sleep(0.1)
        assert " / 选择语言" in output.text
        assert ("❯ English" if locale == "en" else "❯ 简体中文") in output.text
        pipe.send_text("\x1b[B" if locale == "en" else "\x1b[A")
        await asyncio.sleep(0.1)
        assert get_language() == locale  # Highlighting is not confirmation.
        pipe.send_text("\x1b")
        await asyncio.sleep(0.6)
        assert not task.done()  # Esc closes only the language page.
        assert get_language() == locale
        pipe.send_text("X\x0f\r")  # Confirm the current language, preserving the cursor.
        await asyncio.sleep(0.1)
        assert not task.done() and get_language() == locale
        pipe.send_text("\r")
        assert await task == ("submit", "/tmp/ab.mpX4")

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        asyncio.run(scenario(pipe))


def test_picker_does_not_submit_or_switch_parent(tmp_path):
    set_language("en")
    options = [Choice("a", None, 1920, 1080, 60, False, None, False, False).to_dict()]

    async def scenario(pipe):
        task = asyncio.create_task(
            ui.select(dict(title="Video", duration=10, choices=options), tmp_path)
        )
        await asyncio.sleep(0.1)
        pipe.send_text("\x0f\t\x0f\x1b[B")
        await asyncio.sleep(0.1)
        assert not task.done() and get_language() == "en"
        pipe.send_text("\r")
        await asyncio.sleep(0.1)
        assert not task.done() and get_language() == "zh-CN"
        pipe.send_text("\t\r")
        assert (await task).height == 1080

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_new_locale_discovery_and_english_fallback(tmp_path):
    import json
    import shutil
    from pathlib import Path

    from ytdock import i18n

    module = tmp_path / "i18n.py"
    shutil.copy2(i18n.__file__, module)
    shutil.copytree(Path(i18n.__file__).with_name("locales"), tmp_path / "locales")
    (tmp_path / "locales/fr.json").write_text(
        json.dumps(
            {
                "language.name": "Français",
                "compression.choose_mode": "Choisir la compression",
            }
        )
    )
    code = """
import runpy,sys
ns=runpy.run_path(sys.argv[1])
assert ns['LANGUAGES']==('en','fr','zh-CN')
ns['set_language']('fr')
assert ns['language_name']('fr')=='Français'
assert ns['t']('compression.choose_mode')=='Choisir la compression'
expected='Download complete\\nSaved to: /中文/{path}.mp4'
assert ns['t']('download.complete',p0='/中文/{path}.mp4')==expected
try:
 ns['t']('missing.key')
except KeyError:
 pass
else:
 raise AssertionError('Unknown message keys must fail')
"""
    subprocess.run([sys.executable, "-c", code, str(module)], check=True)
