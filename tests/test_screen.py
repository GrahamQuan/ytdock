import asyncio
from contextlib import asynccontextmanager

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock import ui
from ytdock.controller import Cancelled
from ytdock.core import Choice, FormatExpired, UserError
from ytdock.i18n import set_language
from ytdock.screen import Screen

URL = "https://youtu.be/BaW_jenozKc"


async def until(predicate):
    for _ in range(300):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Screen did not reach the expected state")


@asynccontextmanager
async def running(tmp_path, output=None):
    set_language("en")
    with (
        create_pipe_input() as pipe,
        create_app_session(input=pipe, output=output or DummyOutput()),
    ):
        screen = Screen({}, tmp_path, 0)
        task = asyncio.create_task(screen.run())
        try:
            await until(lambda: len(screen.pages) == 3 or task.done())
            if task.done():
                await task
            yield screen, pipe
        finally:
            screen.app.terminate(result=130)
            await asyncio.wait_for(task, 5)


def metadata():
    return dict(
        id="BaW_jenozKc",
        title="Sample video",
        duration=2,
        choices=[Choice("v", None, 640, 360, 30, False, None, False, False).to_dict()],
        caption={"kind": "automatic", "language": "en"},
    )


def outcome(update, status="succeeded", saved=None, cleanup=True):
    update(
        {
            "task_outcome": {
                "status": status,
                "saved": saved or {},
                "cleanup": {"ok": cleanup, "residuals": [] if cleanup else ["/tmp/task-residual"]},
                "failure_stage": "stage.verifying_file" if status != "succeeded" else None,
            }
        }
    )


def test_one_application_four_tabs_cursor_and_language(tmp_path, monkeypatch):
    monkeypatch.setattr(
        ui, "ToolkitApplication", lambda **kwargs: pytest.fail("Nested Application")
    )

    async def scenario():
        async with running(tmp_path) as (screen, pipe):
            pipe.send_text("draft\x1b[D\x1b[D\t")
            await until(lambda: screen.active == "compress")
            pipe.send_text("compress-path\t")
            await until(lambda: screen.active == "subtitles")
            pipe.send_text("video-path\x1b[Bcaption-path\t")
            await until(lambda: screen.active == "tasks")
            pipe.send_text("\t")
            await until(lambda: screen.active == "download")
            buffer = screen.app.layout.current_control.buffer
            assert buffer.text == "draft" and buffer.cursor_position == 3
            # Backward wrap and every reverse hop retain each original page.
            for mode in ("tasks", "subtitles", "compress", "download"):
                pipe.send_text("\x1b[Z")
                await until(lambda: screen.active == mode)
                if mode == "subtitles":
                    assert screen.app.layout.current_control.buffer.text == "caption-path"
                if mode == "compress":
                    assert screen.app.layout.current_control.buffer.text == "compress-path"
            assert screen.app.layout.current_control.buffer is buffer
            assert buffer.cursor_position == 3
            pipe.send_text("\x1b[C")
            await until(lambda: buffer.cursor_position == 4)
            assert screen.active == "download"
            pipe.send_text("\x1b[D")
            await until(lambda: buffer.cursor_position == 3)
            pipe.send_text("\t\t")
            await until(lambda: screen.active == "subtitles")
            assert screen.app.layout.current_control.buffer.text == "caption-path"
            pipe.send_text("\x0f\x0f")
            await until(lambda: screen.modal is not None)
            assert len(screen.modal_stack) == 1
            pipe.send_text("\x1b[B\r")
            await until(lambda: screen.modal is None)
            assert screen.app.layout.current_control.buffer.text == "caption-path"
            assert screen.app.full_screen

    asyncio.run(scenario())


@pytest.mark.parametrize("finish", ["success", "error", "cancel", "partial", "cleanup"])
def test_task_outcomes_records_and_sticky_input(tmp_path, monkeypatch, finish):
    entered = asyncio.Event()

    async def execute(request, downloads, lock, cancel, update):
        if request["operation"] == "inspect":
            outcome(update)
            return metadata()
        update({"stage": "stage.verifying_file"})
        entered.set()
        if finish == "cancel":
            await cancel.wait()
            await asyncio.sleep(0.08)
            outcome(update, "cancelled")
            raise Cancelled("cancelled")
        await asyncio.sleep(0.1)
        if finish in ("partial", "cleanup"):
            update({"saved": {"source": "/tmp/source.mp4"}})
        if finish != "success":
            outcome(update, "failed", cleanup=finish != "cleanup")
            raise UserError("specific failure")
        outcome(update)
        return {"path": "/tmp/output.mp4"}

    monkeypatch.setattr(ui, "execute", execute)

    async def scenario():
        async with running(tmp_path) as (screen, pipe):
            pipe.send_text(URL + "\r")
            await until(lambda: screen.records and screen.records[0].status == "choosing")
            pipe.send_text("\r")
            await entered.wait()
            pipe.send_text("\t\x1b[Z\x0f")
            await asyncio.sleep(0.03)
            assert screen.active == "download" and screen.modal is None
            if finish == "cancel":
                pipe.send_text("\x03\x03\x03\t\x1b[Z")
                await asyncio.sleep(0.02)
                assert screen.processing and screen.active == "download"
            await until(lambda: screen.records[0].ended_tick is not None)
            record = screen.records[0]
            assert len(screen.records) == 1
            assert record.status == (
                "succeeded"
                if finish == "success"
                else "cancelled"
                if finish == "cancel"
                else "failed"
            )
            assert record.cleanup["ok"] == (finish != "cleanup")
            assert record.saved if finish in ("success", "partial", "cleanup") else not record.saved
            await until(lambda: hasattr(screen.app.layout.current_control, "buffer"))
            assert screen.app.layout.current_control.buffer.text == (
                "" if finish == "success" else URL
            )
            assert screen.notices["download"] is record
            pipe.send_text("\t\t\t")
            await until(lambda: screen.active == "tasks")
            pipe.send_text("\r")
            await asyncio.sleep(0.1)
            assert "Elapsed" in screen.task_view[0].current_control.record.details()
            pipe.send_text("\x1b")
            await asyncio.sleep(0.6)
            pipe.send_text("\t")
            await until(lambda: screen.active == "download")
            assert screen.notices["download"] is record

    asyncio.run(scenario())


def test_reselection_and_back_share_record(tmp_path, monkeypatch):
    downloads = 0

    async def execute(request, *args):
        nonlocal downloads
        if request["operation"] == "inspect":
            return metadata()
        downloads += 1
        raise FormatExpired("select again")

    monkeypatch.setattr(ui, "execute", execute)

    async def scenario():
        async with running(tmp_path) as (screen, pipe):
            pipe.send_text(URL + "\r")
            await until(lambda: screen.records and screen.records[0].status == "choosing")
            pipe.send_text("s")
            await until(lambda: screen.modal is not None)
            pipe.send_text("\x0f")
            await until(lambda: len(screen.modal_stack) == 2)
            pipe.send_text("\x1b")
            await until(lambda: len(screen.modal_stack) == 1)
            pipe.send_text("\x1b[B\r")
            await until(lambda: screen.modal is None)
            selected_control = screen.app.layout.current_control
            selected_text = selected_control.text()
            for key, modes in (
                ("\t", ("compress", "subtitles", "tasks", "download")),
                ("\x1b[Z", ("tasks", "subtitles", "compress", "download")),
            ):
                for mode in modes:
                    pipe.send_text(key)
                    await until(lambda: screen.active == mode)
                assert screen.app.layout.current_control is selected_control
                assert selected_control.text() == selected_text
            await until(lambda: screen.active == "download")
            pipe.send_text("\r")
            await until(lambda: downloads == 1 and screen.records[0].status == "choosing")
            assert len(screen.records) == 1
            pipe.send_text("\x1b")
            await until(lambda: screen.records[0].status == "not_started")
            pipe.send_text("\r")
            await until(lambda: len(screen.records) == 2)
            assert screen.records[1].status == "not_started"
            assert not screen.notices.get("download")

    asyncio.run(scenario())


def test_small_terminal_and_task_detail_scroll(tmp_path):
    class Output(DummyOutput):
        columns, rows = 40, 12

        def get_size(self):
            return Size(rows=self.rows, columns=self.columns)

    output = Output()

    def text(screen):
        return "\n".join(
            "".join(c.char for _, c in sorted(row.items()))
            for _, row in sorted(screen.app.renderer._last_screen.data_buffer.items())
        )

    async def scenario():
        async with running(tmp_path, output) as (screen, pipe):
            await asyncio.sleep(0.1)
            assert "Terminal too small" in text(screen)
            output.columns, output.rows = 80, 24
            screen.app._on_resize()
            await asyncio.sleep(0.1)
            record = screen.begin("download", {"url": URL})
            record.finish("failed", "long-path/" * 200 + "END-OF-DETAIL")
            screen.activate("tasks")
            pipe.send_text("\r")
            await asyncio.sleep(0.1)
            pipe.send_text("\x1b[6~" * 30)
            await asyncio.sleep(0.2)
            assert "END-OF-DETAIL" in text(screen)

    asyncio.run(scenario())


@pytest.mark.parametrize("fatal", [False, True])
def test_pty_alternate_screen_and_terminal_restore(tmp_path, fatal):
    import fcntl
    import os
    import pty
    import select
    import struct
    import subprocess
    import sys
    import termios
    import time

    script = tmp_path / "screen_pty.py"
    script.write_text("""import asyncio, os
from pathlib import Path
from ytdock import ui
from ytdock.core import UserError
from ytdock.screen import Screen
async def execute(*args):
    if os.environ.get("INJECT_FATAL"): raise RuntimeError("injected failure")
    raise UserError("injected task error")
ui.execute=execute
print("BEFORE-SCREEN", flush=True)
try:
    result=asyncio.run(Screen({}, Path("/tmp"), 0).run())
    print("AFTER-SCREEN", result, flush=True)
except RuntimeError:
    print("AFTER-SCREEN error", flush=True)
""")
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    env = os.environ.copy() | {"TERM": "xterm-256color", "NO_COLOR": "1"}
    if fatal:
        env["INJECT_FATAL"] = "1"
    process = subprocess.Popen(
        [sys.executable, str(script)], stdin=slave, stdout=slave, stderr=slave, env=env
    )
    os.close(slave)
    captured = b""

    def read_until(marker):
        nonlocal captured
        end = time.monotonic() + 8
        while marker not in captured and time.monotonic() < end:
            ready, _, _ = select.select([master], [], [], 0.05)
            if ready:
                try:
                    captured += os.read(master, 65536)
                except OSError:
                    break
        assert marker in captured, captured.decode(errors="replace")

    try:
        read_until(b"Download")
        os.write(master, (URL + "\r").encode())
        if not fatal:
            read_until(b"Details in Tasks")
            os.write(master, b"\t\t\t")
            time.sleep(0.1)
            os.write(master, b"\x03")
        read_until(b"AFTER-SCREEN")
        assert process.wait(timeout=5) == 0
        assert captured.count(b"\x1b[?1049h") == 1
        assert captured.count(b"\x1b[?1049l") == 1
        assert captured.index(b"\x1b[?1049l") < captured.index(b"AFTER-SCREEN")
        assert termios.tcgetattr(master) == original
        assert b"\x1b[?25h" in captured
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)


@pytest.mark.parametrize("mode", ["compress", "subtitles"])
@pytest.mark.parametrize("finish", ["success", "error", "cancel", "back"])
def test_feature_results_keep_or_clear_inputs(tmp_path, monkeypatch, mode, finish):
    video = tmp_path / "source.mp4"
    video.touch()
    captions = tmp_path / "source.srt"
    captions.touch()
    entered = asyncio.Event()

    async def execute(request, downloads, lock, cancel, update):
        if request["operation"].startswith("inspect"):
            outcome(update)
            return dict(
                title="source.mp4",
                path=str(video),
                duration=2,
                input_bytes=100,
                choice=dict(width=640, height=360, fps=30),
                video=dict(index=0, codec_name="h264"),
                audio=None,
                audio_count=0,
                captions=dict(count=1, adjusted=0),
            )
        entered.set()
        if finish == "cancel":
            await cancel.wait()
            outcome(update, "cancelled")
            raise Cancelled("cleaned")
        if finish == "error":
            outcome(update, "failed")
            raise UserError("failed")
        outcome(update)
        return dict(path="/tmp/result.mp4", input_bytes=100, output_bytes=90)

    monkeypatch.setattr(ui, "execute", execute)

    async def scenario():
        async with running(tmp_path) as (screen, pipe):
            screen.activate(mode)
            pipe.send_text(str(video))
            if mode == "subtitles":
                pipe.send_text("\x1b[B" + str(captions))
            pipe.send_text("\r")
            await until(lambda: screen.records and screen.records[0].status == "choosing")
            pipe.send_text("\x1b" if finish == "back" else "\r")
            if finish == "cancel":
                await entered.wait()
                pipe.send_text("\x03\x03")
            await until(lambda: screen.records[0].ended_tick is not None)
            assert (
                screen.records[0].status
                == dict(
                    success="succeeded", error="failed", cancel="cancelled", back="not_started"
                )[finish]
            )
            await until(lambda: hasattr(screen.app.layout.current_control, "buffer"))
            from prompt_toolkit.layout.controls import BufferControl

            fields = [
                c.buffer.text
                for c in screen.pages[mode].layout.find_all_controls()
                if isinstance(c, BufferControl)
            ]
            assert fields == (
                [""] * (2 if mode == "subtitles" else 1)
                if finish == "success"
                else [str(video)] + ([str(captions)] if mode == "subtitles" else [])
            )

    asyncio.run(scenario())


def test_centered_header_resize_languages_and_monochrome(tmp_path):
    from prompt_toolkit.output import ColorDepth
    from prompt_toolkit.utils import get_cwidth

    class Output(DummyOutput):
        columns, rows = 80, 30

        def get_size(self):
            return Size(rows=self.rows, columns=self.columns)

        def get_default_color_depth(self):
            return ColorDepth.DEPTH_1_BIT

    output = Output()

    def rows(screen):
        data = screen.app.renderer._last_screen.data_buffer
        return [
            "".join(data[y][x].char for x in range(output.columns)).rstrip()
            for y in range(output.rows)
        ]

    def centered(line):
        left = len(line) - len(line.lstrip())
        assert abs(left - (output.columns - get_cwidth(line.strip())) // 2) <= 1

    async def scenario():
        async with running(tmp_path, output) as (screen, pipe):
            for language in ("en", "zh-CN"):
                set_language(language)
                for mode in ui.MODES:
                    screen.activate(mode)
                    await asyncio.sleep(0.06)
                    rendered = rows(screen)
                    assert not rendered[0]
                    assert rendered[1].strip() == "█▄█ ▀█▀ █▀▄ █▀█ █▀▀ █▄▀"
                    assert not rendered[4] and not rendered[6]
                    for row in rendered[1:4] + [rendered[5]]:
                        centered(row)
                    assert rendered[5].count("[") == rendered[5].count("]") == 1
                    assert "/" not in rendered[5]
                    assert "   " in rendered[5].strip()
                    assert "Ctrl+C" in "\n".join(rendered)
                    (tmp_path / f"{language}-{mode}.txt").write_text("\n".join(rendered))
            for width, height in ((52, 20), (65, 30), (100, 20), (80, 30)):
                output.columns, output.rows = width, height
                screen.app._on_resize()
                await asyncio.sleep(0.08)
                rendered = rows(screen)
                if width < 70 or height < 24:
                    assert rendered[0].strip() == "YTDock"
                    centered(rendered[0])
                    centered(rendered[1])
                else:
                    assert rendered[1].strip().startswith("█▄█")
                assert "Ctrl+C" in "\n".join(rendered)
                assert "Terminal too small" not in "\n".join(rendered)

    asyncio.run(scenario())
