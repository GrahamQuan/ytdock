import asyncio

from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock import ui
from ytdock.core import Choice


def test_url_paste_edit_and_empty_submission():
    async def scenario(pipe):
        task = asyncio.create_task(ui.read_url())
        await asyncio.sleep(0.05)
        pipe.send_text("\r")
        await asyncio.sleep(0.05)
        assert not task.done()
        pipe.send_text("https://youtu.be/BaW_jenozKcX\x7f\r")
        assert await task == "https://youtu.be/BaW_jenozKc"

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_resolution_keyboard_and_escape(tmp_path):
    options = [
        Choice(str(i), None, w, h, 30, False, None, False, False).to_dict()
        for i, (w, h) in enumerate([(3840, 2160), (1920, 1080), (1280, 720)])
    ]
    metadata = dict(title="Test", duration=20, choices=options)

    async def scenario(pipe):
        task = asyncio.create_task(ui.select(metadata, tmp_path))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b[B\r")
        assert (await task).height == 720
        task = asyncio.create_task(ui.select(metadata, tmp_path))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")
        assert await task is None

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_repeated_ctrl_c_waits_for_cleanup(tmp_path, monkeypatch):
    from ytdock.controller import Cancelled

    cleaned = False

    async def fake_execute(request, downloads, lock, cancel, update):
        nonlocal cleaned
        await cancel.wait()
        update({"stage": "stage.cancelling"})
        await asyncio.sleep(0.2)
        cleaned = True
        raise Cancelled("已取消，临时文件已清理")

    monkeypatch.setattr(ui, "execute", fake_execute)

    async def scenario(pipe):
        task = asyncio.create_task(ui.busy({}, tmp_path, 0))
        await asyncio.sleep(0.05)
        pipe.send_text("\x03\x03\x03")
        try:
            await task
        except Cancelled:
            pass
        assert cleaned

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_input_switch_preserves_draft_and_escape():
    async def scenario(pipe):
        task = asyncio.create_task(ui.read_input("download", "https://youtu.be/"))
        await asyncio.sleep(0.05)
        pipe.send_text("\x05draft\t")
        assert await task == ("switch", "https://youtu.be/draft")
        task = asyncio.create_task(ui.read_input("compress", "/tmp/a b.mp4"))
        await asyncio.sleep(0.05)
        pipe.send_text("\x1b")
        assert await task == ("back", "/tmp/a b.mp4")
        task = asyncio.create_task(ui.read_input("download", "https://youtu.be/draft"))
        await asyncio.sleep(0.05)
        pipe.send_text("\r")
        assert await task == ("submit", "https://youtu.be/draft")

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_compression_selection_default_down_and_escape(tmp_path):
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
        for keys, expected in [("\r", "size"), ("\x1b[B\r", "quality"), ("\x1b", None)]:
            task = asyncio.create_task(ui.select_compression(info, tmp_path))
            await asyncio.sleep(0.05)
            pipe.send_text(keys)
            assert await task == expected

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_busy_tab_cannot_switch_mode(tmp_path, monkeypatch):
    gate = asyncio.Event()

    async def execute(*args):
        await gate.wait()
        return {"ok": True}

    monkeypatch.setattr(ui, "execute", execute)

    async def scenario(pipe):
        task = asyncio.create_task(ui.busy({"operation": "compress"}, tmp_path, 0))
        await asyncio.sleep(0.05)
        pipe.send_text("\t\x0f\x1b")
        await asyncio.sleep(0.05)
        assert not task.done()
        gate.set()
        assert await task == {"ok": True}

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        asyncio.run(scenario(pipe))


def test_resolution_layout_resize_and_scroll(tmp_path, monkeypatch):
    from prompt_toolkit.data_structures import Size

    from ytdock.i18n import get_language, set_language

    class Terminal(DummyOutput):
        columns = 80
        rows = 24

        def get_size(self):
            return Size(rows=self.rows, columns=self.columns)

    output = Terminal()
    applications = []
    original = ui.Application

    def application(*args, **kwargs):
        app = original(*args, **kwargs)
        applications.append(app)
        return app

    monkeypatch.setattr(ui, "Application", application)
    options = [
        Choice(str(i), None, 1920, 1080, 30 + i, True, None, False, False) for i in range(20)
    ]
    info = dict(
        title="Long video title " * 4,
        duration=20,
        choices=[c.to_dict() for c in options],
        caption={"kind": "automatic", "language": "en"},
    )

    def screen_text(app):
        screen = app.renderer._last_screen
        return "\n".join(
            "".join(row[x].char for x in range(output.columns))
            for _, row in sorted(screen.data_buffer.items())
        )

    async def scenario(pipe):
        task = asyncio.create_task(ui.select(info, tmp_path / ("long directory " * 4)))
        await asyncio.sleep(0.1)
        app = applications[-1]
        for width, height in ((80, 24), (40, 30), (52, 22), (100, 20)):
            output.columns, output.rows = width, height
            app._on_resize()
            await asyncio.sleep(0.1)
            pipe.send_text("\x1b[B" * 19)
            await asyncio.sleep(0.1)
            text = screen_text(app)
            assert "49fps" in text
            assert "Ctrl+C Exit" in text
            assert "Subtitles · S Settings" in text
            assert "Format: MP4" in text
        pipe.send_text("\r")
        assert (await task).fps == 49

    language = get_language()
    set_language("en")
    try:
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
            asyncio.run(scenario(pipe))
    finally:
        set_language(language)


def test_choice_height_matches_wrapped_content():
    control = ui.FormattedTextControl("One\nTwo")
    window = ui.choice_window(control)
    assert window.preferred_height(80, 24).preferred == 2
    window = ui.choice_window(ui.FormattedTextControl("x" * 60 + "\nTwo"))
    assert window.preferred_height(40, 24).preferred == 3


def test_shortcuts_wrap_between_actions():
    from prompt_toolkit.formatted_text import fragment_list_to_text

    control = ui.ShortcutControl(
        lambda: "↑↓ Select · Enter Download · Esc Back\nCtrl+C Exit · Ctrl+O Language"
    )
    content = control.create_content(30, 20)
    lines = [fragment_list_to_text(content.get_line(i)) for i in range(content.line_count)]
    assert lines == ["↑↓ Select · Enter Download", "Esc Back", "Ctrl+C Exit · Ctrl+O Language"]


def test_download_frames_order_and_optional_subtitles(tmp_path, monkeypatch):
    from ytdock.i18n import get_language, set_language, t

    original = ui.Application
    applications = []

    def application(*args, **kwargs):
        app = original(*args, **kwargs)
        applications.append(app)
        return app

    monkeypatch.setattr(ui, "Application", application)
    choice = Choice("v", None, 1920, 1080, 60, False, None, False, False)

    async def scenario(pipe):
        for language in ("en", "zh-CN"):
            set_language(language)
            for caption, enabled in (
                (None, False),
                ({"kind": "automatic", "language": "en"}, False),
                ({"kind": "manual", "language": "en"}, True),
            ):
                info = dict(title="Video", duration=60, choices=[choice.to_dict()], caption=caption)
                task = asyncio.create_task(ui.select(info, tmp_path, {"subtitles": enabled}))
                await asyncio.sleep(0.1)
                app = applications[-1]
                screen = app.renderer._last_screen
                text = "\n".join(
                    "".join(char.char for _, char in sorted(row.items()))
                    for _, row in sorted(screen.data_buffer.items())
                )
                assert t("download.resolution_frame") in text
                assert "1920×1080" in text
                if caption:
                    assert text.index(t("download.resolution_frame")) < text.index(
                        t("subtitle.frame")
                    )
                    assert text.index(t("subtitle.frame")) < text.index(str(tmp_path)[:25])
                    assert t("subtitle." + caption["kind"]) in text
                else:
                    assert t("subtitle.frame") not in text
                pipe.send_text("\r")
                assert (await task).key == choice.key

    previous = get_language()
    try:
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            asyncio.run(scenario(pipe))
    finally:
        set_language(previous)
