import asyncio
import os
import subprocess
import sys
import threading
import time

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock import check_process, cli, dependencies, storage
from ytdock.core import UserError
from ytdock.i18n import set_language
from ytdock.screen import Screen
from ytdock.startup import Startup
from ytdock.version import get_version


async def until(predicate):
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Startup did not reach expected state")


@pytest.mark.parametrize("flag", ["--version", "-v"])
def test_version_has_no_dependency_or_application_side_effects(monkeypatch, capsys, flag):
    monkeypatch.setattr(sys, "argv", ["ytdock", flag])
    monkeypatch.setattr(dependencies, "check", lambda **kwargs: pytest.fail("No checks"))
    monkeypatch.setattr(Screen, "__init__", lambda *a, **k: pytest.fail("No UI"))
    assert cli.main() == 0
    assert capsys.readouterr().out == f"YTDock {get_version()}\n"


@pytest.mark.parametrize(
    "flags",
    [
        ("--check", "--upgrade"),
        ("--install", "--upgrade"),
        ("--uninstall", "--check"),
        ("--version", "--upgrade"),
    ],
)
def test_actions_are_exclusive(monkeypatch, flags):
    monkeypatch.setattr(sys, "argv", ["ytdock", *flags])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 2


def test_version_process_has_no_path_tools_or_escape_sequences():
    result = subprocess.run(
        [sys.executable, "-m", "ytdock.cli", "-v"],
        capture_output=True,
        text=True,
        env=os.environ | {"PATH": "/nonexistent"},
        timeout=10,
    )
    assert result.returncode == 0 and result.stdout == f"YTDock {get_version()}\n"
    assert "\x1b" not in result.stdout and not result.stderr


def test_development_upgrade_does_not_connect(monkeypatch, capsys):
    from ytdock import upgrade

    monkeypatch.setattr(sys, "argv", ["ytdock", "--upgrade"])
    monkeypatch.setattr(upgrade, "release", lambda *a: pytest.fail("Must not connect"))
    assert cli.main() == 1
    assert "only in an installed release" in capsys.readouterr().err


class Output(DummyOutput):
    columns, rows = 80, 24

    def get_size(self):
        return Size(rows=self.rows, columns=self.columns)


def rendered(screen):
    data = screen.app.renderer._last_screen.data_buffer
    return "\n".join(
        "".join(c.char for _, c in sorted(row.items())) for _, row in sorted(data.items())
    )


@pytest.fixture
def local_directories(tmp_path, monkeypatch):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    support = tmp_path / "support"
    monkeypatch.setattr(
        storage, "system_directory", lambda kind: downloads if kind == "downloads" else support
    )
    return downloads, support


@pytest.mark.parametrize("language", ["en", "zh-CN"])
def test_slow_startup_animates_then_enters_same_app(
    local_directories, monkeypatch, language, tmp_path
):
    entered, release = threading.Event(), threading.Event()

    def check(progress, **kwargs):
        for key in ("python", "quickjs"):
            progress(key, "checking")
            progress(key, "ready")
        progress("ffmpeg", "checking")
        entered.set()
        release.wait(4)
        progress("ffmpeg", "ready")
        progress("ffprobe", "checking")
        progress("ffprobe", "ready")
        return {}

    monkeypatch.setattr(dependencies, "check", check)

    async def scenario():
        set_language(language)
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
            screen, startup = Screen({}, None, None), Startup()
            task = asyncio.create_task(screen.run(startup=startup))
            await until(entered.is_set)
            await asyncio.sleep(0.03)
            assert startup.states["python"] == startup.states["quickjs"] == "ready"
            assert startup.states["downloads"] == "pending"
            first = startup.formatted()
            pipe.send_text("\t\x1b[Z\x0f")
            await asyncio.sleep(0.15)
            assert first != startup.formatted()
            assert screen.active == "download" and not screen.pages
            screen.app.output.columns, screen.app.output.rows = 42, 18
            screen.app._on_resize()
            await asyncio.sleep(0.05)
            assert "FFmpeg" in rendered(screen) and "Ctrl+C" in rendered(screen)
            (tmp_path / f"startup-{language}.txt").write_text(rendered(screen))
            release.set()
            await until(lambda: len(screen.pages) == 3)
            assert all(value == "ready" for value in startup.states.values())
            assert screen.app.full_screen
            pipe.send_text("\x03")
            assert await task == 130

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    with storage.instance_lock(local_directories[1] / storage.APP_ID):
        pass


def test_failed_startup_has_persistent_report(local_directories, monkeypatch):
    def check(progress, **kwargs):
        progress("python", "ready")
        progress("quickjs", "checking")
        raise UserError("Install QuickJS")

    monkeypatch.setattr(dependencies, "check", check)

    async def scenario():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
            startup = Startup()
            with pytest.raises(UserError) as caught:
                await Screen({}, None, None).run(startup=startup)
            assert "✓ Python" in str(caught.value) and "✗ QuickJS" in str(caught.value)
            assert "Install QuickJS" in str(caught.value)

    asyncio.run(scenario())


def test_cancel_waits_for_recovery_and_releases_lock(local_directories, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(dependencies, "check", lambda **kwargs: {})

    def recover(path):
        entered.set()
        release.wait(4)
        return []

    monkeypatch.setattr(storage, "recover", recover)

    async def scenario():
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=Output()):
            startup = Startup()
            task = asyncio.create_task(Screen({}, None, None).run(startup=startup))
            await until(entered.is_set)
            pipe.send_text("\x03\x03")
            await until(startup.cancel.is_set)
            assert not task.done()
            with pytest.raises(UserError):
                with storage.instance_lock(local_directories[1] / storage.APP_ID):
                    pass
            release.set()
            assert await task == 130

    try:
        asyncio.run(scenario())
    finally:
        release.set()
    with storage.instance_lock(local_directories[1] / storage.APP_ID):
        pass


def test_cancel_stops_check_and_its_child(tmp_path):
    marker = tmp_path / "pid"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); "
        "Path(sys.argv[1]).write_text(str(p.pid)); time.sleep(30)"
    )
    cancel = threading.Event()
    errors = []

    def call():
        try:
            check_process.run(cancel, [sys.executable, "-c", script, str(marker)], text=True)
        except check_process.CheckCancelled:
            errors.append("cancelled")

    thread = threading.Thread(target=call)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        cancel.set()
        thread.join(3)
        assert not thread.is_alive() and errors == ["cancelled"]
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", marker.read_text()], capture_output=True, text=True
        )
        assert not result.stdout.strip() or result.stdout.strip().startswith("Z")
    finally:
        cancel.set()
        thread.join(3)


def test_no_ffmpeg_candidates_fails_correct_stage(monkeypatch):
    from ytdock import ffmpeg

    monkeypatch.setattr(dependencies, "check_python", lambda: None)
    monkeypatch.setattr(dependencies, "find_quickjs", lambda runner: "/qjs")
    monkeypatch.setattr(ffmpeg, "search_directories", lambda: iter([]))
    for key in ("YTDOCK_FFMPEG", "YTDOCK_FFPROBE", "YTDOCK_FFMPEG_DIR"):
        monkeypatch.delenv(key, raising=False)
    startup = Startup()
    with pytest.raises(UserError):
        startup.check()
    assert startup.states["quickjs"] == "ready"
    assert startup.states["ffmpeg"] == "failed"


@pytest.mark.parametrize("failed", [False, True])
def test_real_pty_startup_and_persistent_failure(tmp_path, failed):
    import fcntl
    import pty
    import select
    import struct
    import termios

    script = tmp_path / "entry.py"
    script.write_text("""import sys,time
from ytdock import cli,dependencies
from ytdock.core import UserError
if sys.argv.pop() == 'failure':
    def check(progress,**kwargs):
        progress('python','ready')
        progress('quickjs','checking')
        time.sleep(0.15)
        raise UserError('TEST STARTUP FAILURE: install QuickJS')
    dependencies.check=check
raise SystemExit(cli.main())
""")
    home = tmp_path / "home"
    home.mkdir()
    (home / "Downloads").mkdir()
    master, slave = pty.openpty()
    fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 24, 80, 0, 0))
    before = termios.tcgetattr(slave)
    env = os.environ | {
        "HOME": str(home),
        "CFFIXED_USER_HOME": str(home),
        "TERM": "xterm-256color",
        "NO_COLOR": "1",
    }
    process = subprocess.Popen(
        [sys.executable, str(script), "failure" if failed else "success"],
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        start_new_session=True,
    )
    output = b""
    cancelled = False
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            ready, _, _ = select.select([master], [], [], 0.1)
            if ready:
                try:
                    output += os.read(master, 65536)
                except OSError:
                    break
            if not failed and b"[Download]" in output and not cancelled:
                os.write(master, b"\x03")
                cancelled = True
            if process.poll() is not None:
                break
        assert process.wait(timeout=2) == (1 if failed else 130), output.decode(errors="replace")
        # Drain any final ordinary stderr output after leaving the alternate screen.
        while select.select([master], [], [], 0)[0]:
            try:
                output += os.read(master, 65536)
            except OSError:
                break
        assert output.count(b"\x1b[?1049h") == output.count(b"\x1b[?1049l") == 1
        restored = termios.tcgetattr(slave)
        # macOS may set PENDIN (queued input retype), which is kernel-maintained.
        # Compare all actual input-mode flags, control characters and baud rates.
        restored[3] &= ~getattr(termios, "PENDIN", 0)
        before[3] &= ~getattr(termios, "PENDIN", 0)
        assert restored == before
        if failed:
            assert output.rfind(b"TEST STARTUP FAILURE") > output.rfind(b"\x1b[?1049l")
        else:
            assert cancelled
        (tmp_path / "pty-output.txt").write_bytes(output)
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        os.close(master)
        os.close(slave)


def test_check_stays_plain_and_does_not_enter_screen(local_directories, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ytdock", "--check"])
    monkeypatch.setattr(dependencies, "check", lambda **kw: {"ffmpeg": "/ffmpeg"})
    monkeypatch.setattr(Screen, "__init__", lambda *a, **k: pytest.fail("No UI"))
    assert cli.main() == 0
    text = capsys.readouterr().out
    assert "Dependencies ready" in text and "\x1b" not in text


def test_help_skips_checks(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["ytdock", "--help"])
    monkeypatch.setattr(dependencies, "check", lambda **kw: pytest.fail("No checks"))
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 0
    assert "--upgrade" in capsys.readouterr().out
