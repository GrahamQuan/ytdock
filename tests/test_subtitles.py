import asyncio
import shutil
import subprocess

import pytest
from prompt_toolkit.application.current import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from ytdock import ui
from ytdock.burn import burn, capabilities
from ytdock.core import Choice, UserError
from ytdock.media import probe
from ytdock.subtitles import caption_track, prepare, validate_srt


def track(url="https://example.invalid/caption?lang=en"):
    return [{"ext": "vtt", "url": url}]


def test_caption_priority_excludes_translations():
    info = {
        "language": "en",
        "subtitles": {"en-US": track(), "zh": track()},
        "automatic_captions": {"en": track()},
    }
    assert caption_track(info) == {"kind": "manual", "language": "en-US"}
    info["subtitles"]["en"] = track()
    assert caption_track(info)["language"] == "en"
    info["subtitles"] = {}
    assert caption_track(info)["kind"] == "automatic"
    info["automatic_captions"]["en"] = track("https://example.invalid/c?lang=fr&tlang=en")
    assert caption_track(info) is None
    assert caption_track({}) is None


def test_reference_segmentation_and_timeline(tmp_path):
    source = tmp_path / "original.vtt"
    source.write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n"
        "<b>Hello &amp; welcome</b>\nto this English subtitle with many words.\n\n"
        "00:00:01.800 --> 00:00:03.000\n" + "W" * 85 + "\n"
    )
    target = tmp_path / "captions.srt"
    prepare(source, target, 3)
    blocks = validate_srt(target, 3)
    assert len(blocks) >= 4
    assert "<b>" not in target.read_text()
    assert "Hello & welcome" in target.read_text()
    assert all(len(b.splitlines()[2]) <= 42 for b in blocks)


@pytest.mark.parametrize(
    "text",
    [
        "",
        "1\n00:00:01,000 --> 00:00:00,000\nhello\n",
        "1\n00:00:00,000 --> 00:00:01,000\n" + "x" * 43,
        "1\n00:00:00,000 --> 00:00:01,000\na\nb",
    ],
)
def test_invalid_srt(tmp_path, text):
    path = tmp_path / "captions.srt"
    path.write_text(text)
    with pytest.raises(UserError):
        validate_srt(path, 2)


class Output(DummyOutput):
    def __init__(self):
        self.text = ""

    def write(self, value):
        self.text += value

    def write_raw(self, value):
        self.text += value


@pytest.mark.parametrize("available", [False, True])
@pytest.mark.parametrize("locale", ["en", "zh-CN"])
def test_subtitle_visibility_and_selection(tmp_path, available, locale):
    from ytdock.i18n import set_language

    set_language(locale)
    subtitle_word = "S Settings" if locale == "en" else "S 设置"
    options = [
        Choice(str(i), None, w, h, 30, False, None, False, False).to_dict()
        for i, (w, h) in enumerate([(1920, 1080), (1280, 720)])
    ]
    info = {
        "title": "test",
        "duration": 2,
        "choices": options,
        "caption": {"kind": "manual", "language": "en"} if available else None,
    }
    output = Output()
    settings = {}

    async def scenario(pipe):
        task = asyncio.create_task(ui.select(info, tmp_path, settings))
        await asyncio.sleep(0.1)
        if not available:
            assert subtitle_word not in output.text
            pipe.send_text("s\r")
        else:
            assert subtitle_word in output.text
            pipe.send_text("\x1b[Bs")
            await asyncio.sleep(0.1)
            pipe.send_text("\x1b[B\r")
            await asyncio.sleep(0.1)
            assert not task.done()
            assert settings["subtitles"]
            pipe.send_text("s\x1b")
            await asyncio.sleep(0.6)
            pipe.send_text("\r")
        choice = await task
        assert choice.height == (720 if available else 1080)
        if available:
            task = asyncio.create_task(ui.select(info, tmp_path, settings))
            await asyncio.sleep(0.1)
            pipe.send_text("\r")
            assert (await task).height == 720 and settings["subtitles"]

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        asyncio.run(scenario(pipe))


@pytest.mark.parametrize("fps,portrait,audio", [(30, False, True), (60, True, False)])
def test_actual_subtitle_burn(tmp_path, fps, portrait, audio):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        pytest.skip("FFmpeg required")
    directory = tmp_path / "中文 ' : , [test]"
    directory.mkdir()
    width, height = (180, 320) if portrait else (320, 180)
    capabilities(ffmpeg, ffprobe, directory)
    args = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"color=black:s={width}x{height}:r={fps}:d=1",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=d=1", "-c:a", "aac"]
    args += [
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-movflags",
        "+faststart",
        str(directory / "verified.mp4"),
    ]
    subprocess.run(args, check=True, capture_output=True)
    (directory / "captions.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nA clear English subtitle for this video.\n"
    )
    events = []
    burn(
        directory,
        dict(width=width, height=height, fps=fps, has_audio=audio),
        1,
        ffmpeg,
        ffprobe,
        events.append,
    )
    info = probe(directory / "subtitled.mp4", ffprobe)
    assert info["streams"][0]["codec_name"] == "hevc"
    assert info["streams"][0]["codec_tag_string"] == "hvc1"
    pixels = subprocess.check_output(
        [
            ffmpeg,
            "-v",
            "error",
            "-i",
            str(directory / "subtitled.mp4"),
            "-frames:v",
            "1",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
    )
    assert sum(p > 100 for p in pixels) > 30
    assert events[-1]["stage"] == "stage.verifying_subtitled"


def test_missing_capability_keeps_no_subtitles(tmp_path, monkeypatch):
    applications = []
    original = ui.Application

    def application(*args, **kwargs):
        app = original(*args, **kwargs)
        applications.append(app)
        return app

    monkeypatch.setattr(ui, "Application", application)
    info = {
        "title": "test",
        "duration": 1,
        "choices": [Choice("a", None, 320, 180, 30, False, None, False, False).to_dict()],
        "caption": {"kind": "manual", "language": "en"},
        "subtitle_error": "missing libx265",
    }
    settings = {}
    output = Output()

    async def scenario(pipe):
        task = asyncio.create_task(ui.select(info, tmp_path, settings))
        await asyncio.sleep(0.1)
        pipe.send_text("s\x1b[B\r")
        await asyncio.sleep(0.1)
        assert not task.done() and not settings["subtitles"]
        screen = applications[-1].renderer._last_screen
        rendered = "".join(
            char.char
            for _, row in sorted(screen.data_buffer.items())
            for _, char in sorted(row.items())
        )
        assert "missinglibx265" in "".join(rendered.split())
        pipe.send_text("\x1b[A\r\r")
        assert await task

    with create_pipe_input() as pipe, create_app_session(input=pipe, output=output):
        asyncio.run(scenario(pipe))


def test_caption_download_failure_is_safe(tmp_path, monkeypatch):
    from yt_dlp import YoutubeDL

    from ytdock.subtitles import download_caption

    monkeypatch.setattr("ytdock.subtitles.time.sleep", lambda _: None)

    def fail(*args):
        raise RuntimeError("signed-private-url-must-not-leak")

    monkeypatch.setattr(YoutubeDL, "urlopen", fail)
    with pytest.raises(UserError) as exc:
        download_caption(
            {"subtitles": {"en": track()}},
            {"kind": "manual", "language": "en"},
            tmp_path,
            "unused",
            lambda e: None,
        )
    assert "signed-private" not in str(exc.value)
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("kind", ["manual", "automatic"])
def test_caption_download_and_processing(tmp_path, monkeypatch, kind):
    import io

    from yt_dlp import YoutubeDL

    from ytdock.subtitles import download_caption

    key = "subtitles" if kind == "manual" else "automatic_captions"
    monkeypatch.setattr(
        YoutubeDL,
        "urlopen",
        lambda *args: io.BytesIO(
            b"WEBVTT\n\n00:00:00.000 --> 00:00:01.000\nHello <b>English</b> captions\n"
        ),
    )
    raw = download_caption(
        {key: {"en": track()}}, {"kind": kind, "language": "en"}, tmp_path, "unused", lambda e: None
    )
    target = tmp_path / "captions.srt"
    prepare(raw, target, 1)
    assert "Hello English captions" in target.read_text()
    assert validate_srt(target, 1)


def test_missing_burn_capabilities_reported(tmp_path, monkeypatch):
    from ytdock import burn as module

    monkeypatch.setattr(module, "output", lambda *args: "")
    with pytest.raises(UserError, match="libx265"):
        capabilities("ffmpeg", "ffprobe", tmp_path)


def test_malformed_timestamp_rejected(tmp_path):
    raw = tmp_path / "raw.srt"
    raw.write_text("1\n00:99:00,000 --> 00:99:01,000\nEnglish\n")
    with pytest.raises(UserError):
        prepare(raw, tmp_path / "captions.srt", 10000)


@pytest.mark.parametrize("overrun", [500, 1000, 2000])
def test_tail_clamped_before_segmentation(tmp_path, overrun):
    from ytdock.subtitles import format_srt_timestamp, parse_subtitle_cues

    raw = tmp_path / "original.srt"
    text = "This long sentence keeps every word when split into multiple subtitle cues."
    raw.write_text(f"1\n00:00:09,000 --> {format_srt_timestamp(10000 + overrun)}\n{text}\n")
    target = tmp_path / "captions.srt"
    events = []
    assert prepare(raw, target, 10, events.append) == 1
    cues = parse_subtitle_cues(target)
    assert cues[-1].end_ms == 10000
    assert " ".join(c.text for c in cues) == text
    assert all(c.start_ms < c.end_ms <= 10000 for c in cues)
    assert events and "notice" in events[0]
    validate_srt(target, 10)


@pytest.mark.parametrize(
    "start,end", [(9000, 12001), (10000, 10500), (11000, 12000), (9000, 9000), (9000, 8000)]
)
def test_bad_caption_ranges_rejected_without_output(tmp_path, start, end):
    from ytdock.subtitles import format_srt_timestamp

    raw = tmp_path / "original.srt"
    raw.write_text(f"1\n{format_srt_timestamp(start)} --> {format_srt_timestamp(end)}\nWords\n")
    target = tmp_path / "captions.srt"
    with pytest.raises(UserError) as error:
        prepare(raw, target, 10)
    assert "00:00:10,000" in str(error.value)
    assert not target.exists()
    assert "Words" in raw.read_text()


def test_bad_timestamp_is_not_silently_skipped(tmp_path):
    raw = tmp_path / "original.srt"
    raw.write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nValid\n\n"
        "2\ninvalid --> 00:00:02,000\nDo not lose these words\n"
    )
    with pytest.raises(UserError):
        prepare(raw, tmp_path / "captions.srt", 10)


def test_real_video_tail_regression_with_actual_duration(tmp_path):
    # Timing-only reproduction of shhFyL2oKHw; no downloaded transcript stored in tests.
    raw = tmp_path / "original.srt"
    raw.write_text("1\n00:21:14,400 --> 00:21:16,960\nFinal caption\n")
    target = tmp_path / "captions.srt"
    prepare(raw, target, 1275.170249)
    assert "00:21:15,170" in target.read_text()
    validate_srt(target, 1275.170249)
    with pytest.raises(UserError):
        validate_srt(raw, 1275.170249)


@pytest.mark.parametrize("language", ["en-US", "es", "ja", "zh-Hans", "es-419"])
def test_subtitles_match_native_audio_without_translation(language):
    native = track(f"https://example.invalid/c?lang={language}")
    metadata = {
        "formats": [{"acodec": "aac", "language": language, "language_preference": 10}],
        "subtitles": {"fr": track("https://example.invalid/c?lang=fr")},
        "automatic_captions": {
            language: native,
            "de": track(f"https://example.invalid/c?lang={language}&tlang=de"),
        },
    }
    assert caption_track(metadata) == {"kind": "automatic", "language": language}
    metadata["subtitles"][language] = native
    assert caption_track(metadata) == {"kind": "manual", "language": language}
    metadata["subtitles"][language] = track(f"https://example.invalid/c?lang={language}&tlang=")
    assert caption_track(metadata)["kind"] == "automatic"
    metadata["automatic_captions"] = {}
    assert caption_track(metadata) is None


def test_manual_subtitle_language_does_not_override_unknown_original():
    assert caption_track({"subtitles": {"es": track("https://example.invalid/c?lang=es")}}) is None
