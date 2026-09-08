import pytest

from ytdock.core import (
    UserError,
    choices,
    default_choice,
    filename,
    percent,
    provider_error,
    youtube_url,
)
from ytdock.ui import status_text


def fmt(id="v", width=1920, height=1080, fps=30, vcodec="avc1.640028", acodec="none", **kw):
    return (
        dict(
            format_id=id,
            width=width,
            height=height,
            fps=fps,
            vcodec=vcodec,
            acodec=acodec,
            url="https://example.invalid/media",
            protocol="https",
            dynamic_range="SDR",
        )
        | kw
    )


def info(*formats):
    return dict(id="BaW_jenozKc", title="Test", duration=2, formats=list(formats))


@pytest.mark.parametrize(
    "done,total,finished,expected",
    [
        (0, 100, False, "0%"),
        (5, 100, False, "5.0%"),
        (12.3, 100, False, "12.3%"),
        (99.99, 100, False, "99.9%"),
        (100, 100, False, "99.9%"),
        (101, 100, True, "100%"),
        (0.00001, 100, False, "0.0%"),
        (5, None, False, None),
        (5, 0, False, None),
    ],
)
def test_progress(done, total, finished, expected):
    assert percent(done, total, finished) == expected


def test_unknown_total_and_estimate():
    assert "%" not in status_text(
        {"stage": "stage.downloading_video", "done": 5, "download": True}, 0
    )
    assert "约" in status_text(
        {
            "stage": "stage.downloading_video",
            "done": 5,
            "total": 100,
            "approximate": True,
            "download": True,
        },
        0,
    )
    assert "100%" not in status_text({"stage": "stage.verifying_file"}, 0)


@pytest.mark.parametrize(
    "url",
    [
        "https://youtu.be/BaW_jenozKc",
        "https://www.youtube.com/watch?v=BaW_jenozKc&list=PLtest",
        " https://www.youtube.com/shorts/BaW_jenozKc ",
    ],
)
def test_url(url):
    assert youtube_url(url) == "https://www.youtube.com/watch?v=BaW_jenozKc"


@pytest.mark.parametrize(
    "url",
    [
        "",
        "https://youtube.com/playlist?list=PLtest",
        "https://youtube.com/@name",
        "https://youtube.com/live/BaW_jenozKc",
        "https://evil.test/watch?v=BaW_jenozKc",
        "https://youtube.com.evil.test/watch?v=BaW_jenozKc",
        "file:///video",
        "https://youtu.be/BaW_jenozKc https://youtu.be/BaW_jenozKc",
        "https://user:secret@youtube.com/watch?v=BaW_jenozKc",
        "https://youtube.com/watch?v=BaW_jenozKc&v=BaW_jenozKc",
    ],
)
def test_reject_urls(url):
    with pytest.raises(UserError):
        youtube_url(url)


def test_prefer_compatible_and_audio():
    result = choices(
        info(
            fmt(vcodec="vp9", id="vp9"),
            fmt(id="h264"),
            fmt(id="opus", vcodec="none", acodec="opus", abr=192),
            fmt(id="aac", vcodec="none", acodec="mp4a.40.2", abr=128),
        )
    )
    assert len(result) == 1
    assert result[0].video_id == "h264" and result[0].audio_id == "aac"
    assert result[0].has_audio and not result[0].transcode


def test_default_portrait_fps_and_unknown_size():
    result = choices(
        info(
            fmt(id="4k", width=3840, height=2160),
            fmt(id="portrait", width=1080, height=1920, fps=60),
            fmt(id="30", width=1080, height=1920, fps=30),
            fmt(id="720", width=1280, height=720),
        )
    )
    assert result[default_choice(result)].video_id == "portrait"
    assert "1080×1920" in result[1].label()
    assert "大小未知" in result[1].label()
    assert not result[1].has_audio
    assert default_choice(result[:1]) == 0
    high = choices(info(fmt(width=3840, height=2160), fmt(id="2k", width=2560, height=1440)))
    assert high[default_choice(high)].height == 1440


def test_no_silent_audio_loss_or_hdr():
    with pytest.raises(UserError):
        choices(info(fmt(), fmt(id="unsupported", vcodec="none", acodec="opus", has_drm=True)))
    with pytest.raises(UserError):
        choices(info(fmt(dynamic_range="HDR10")))


def test_live_and_invalid_duration():
    for flags in ({"is_live": True}, {"live_status": "is_upcoming"}, {"duration": None}):
        with pytest.raises(UserError):
            choices(info(fmt()) | flags)


def test_transcode_and_sizes():
    option = choices(info(fmt(vcodec="vp9", filesize_approx=300)))[0]
    assert option.transcode and option.approximate and option.bytes == 300
    assert "需要转码" in option.label()
    assert "耗时较长" not in option.label()


def test_filename_and_redaction():
    name = filename("../../\x1b\n标题/" + "长" * 300, "BaW_jenozKc")
    assert "/" not in name and ".." not in name and "\x1b" not in name
    assert len(name.encode()) < 240
    assert "secret" not in str(provider_error(Exception("network https://host/?token=secret")))


@pytest.mark.parametrize("language", ["en-US", "es", "ja", "zh-Hans"])
def test_native_audio_wins_over_compatible_dub(language):
    from ytdock.core import original_language

    metadata = info(fmt(acodec="none"))
    metadata["formats"] += [
        dict(
            format_id="dub",
            url="https://example.invalid/dub",
            protocol="https",
            vcodec="none",
            acodec="mp4a.40.2",
            abr=256,
            language="ar",
            language_preference=-1,
        ),
        dict(
            format_id="native",
            url="https://example.invalid/native",
            protocol="https",
            vcodec="none",
            acodec="opus",
            abr=100,
            language=language,
            language_preference=10,
            format_note="original (default)",
        ),
    ]
    assert original_language(metadata) == language.lower()
    assert all(c.audio_id == "native" and c.transcode for c in choices(metadata))


def test_ambiguous_or_dubbed_only_audio_is_rejected():
    from ytdock.core import original_audio_formats

    for tracks in (
        [dict(acodec="aac", language="es"), dict(acodec="aac", language="en")],
        [dict(acodec="aac", format_note="dubbed-auto")],
    ):
        with pytest.raises(UserError):
            original_audio_formats({"formats": tracks})
