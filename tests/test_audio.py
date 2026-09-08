"""Track identity, encoding choice and independent subtitle-language evidence."""

import pytest

from ytdock.core import UserError, choices, original_language, select_audio
from ytdock.i18n import t
from ytdock.subtitles import caption_track


def audio(id="140", **kwargs):
    return (
        dict(
            format_id=id,
            acodec="mp4a.40.2",
            vcodec="none",
            language="en",
            abr=128,
            url="https://example.invalid/audio",
            protocol="https",
        )
        | kwargs
    )


def video(id="137", **kwargs):
    return (
        dict(
            format_id=id,
            acodec="none",
            vcodec="avc1",
            width=1920,
            height=1080,
            fps=60,
            url="https://example.invalid/video",
            protocol="https",
        )
        | kwargs
    )


def info(*tracks, **kwargs):
    return dict(duration=202, formats=[video(), *tracks]) | kwargs


def test_unmarked_single_track_encodings_and_no_original_claim():
    tracks = [
        audio("139", acodec="mp4a.40.5", abr=48, format_note="low"),
        audio("140", format_note="medium"),
        audio("251", acodec="opus", abr=160, format_note="medium"),
    ]
    for track in tracks:
        track["language_preference"] = -1
    data = info(*tracks, language="en")  # Processed/default format language is not evidence.
    assert select_audio(data) == (tracks, False)
    assert original_language(data) is None
    assert choices(data)[0].audio_id == "140"


def test_marked_track_precedes_codec_and_language_fallback():
    native = audio("251", audio_track_id="native", acodec="opus", format_note="original")
    data = info(audio(audio_track_id="dub", language="es", format_note="dubbed-auto"), native)
    assert select_audio(data) == ([native], True)
    assert choices(data)[0].audio_id == "251"
    assert original_language(data) == "en"


def test_explicit_language_unique_track_and_dub_exclusion():
    selected = audio("140", audio_track_id="en-main", language_preference=-1)
    data = info(
        selected,
        audio("141", audio_track_id="en-dub", format_note="dubbed"),
        audio("251", audio_track_id="es-main", language="es"),
        original_language="en",
    )
    assert select_audio(data) == ([selected], True)
    assert choices(data)[0].audio_id == "140"


@pytest.mark.parametrize(
    "tracks,extra,error",
    [
        (
            [audio(audio_track_id="a"), audio("251", audio_track_id="b", language="es")],
            {"language": "en"},
            "audio.original_unknown",
        ),
        (
            [audio(audio_track_id="a"), audio("251", audio_track_id="b", language="es")],
            {"original_language": "ja"},
            "audio.no_match",
        ),
        (
            [audio(audio_track_id="a"), audio("251", audio_track_id="b")],
            {"original_language": "en"},
            "audio.multiple_candidates",
        ),
        ([audio(format_note="dubbed-auto")], {}, "audio.no_match"),
        ([audio(format_note="original dubbed-auto")], {}, "audio.identity_ambiguous"),
        (
            [audio(audio_track_id="same"), audio("251", audio_track_id="same", language="es")],
            {},
            "audio.identity_ambiguous",
        ),
        (
            [
                audio(audio_track_id="a", format_note="original"),
                audio("251", audio_track_id="b", format_note="original"),
            ],
            {},
            "audio.multiple_candidates",
        ),
        ([audio(format_note="original")], {"original_language": "es"}, "audio.identity_ambiguous"),
        ([audio("140-0"), audio("140-1")], {}, "audio.identity_ambiguous"),
    ],
)
def test_ambiguity_and_missing_evidence(tracks, extra, error):
    with pytest.raises(UserError) as caught:
        choices(info(*tracks, **extra))
    assert str(caught.value) == t(error)


def test_same_explicit_track_multiple_rates_and_combined_formats():
    aac = audio("140", audio_track_id="a", format_note="original")
    data = info(
        aac,
        audio("141", audio_track_id="a", abr=256, format_note="original"),
        audio("251", audio_track_id="a", acodec="opus", format_note="original"),
        video(
            "18",
            height=720,
            audio_track_id="a",
            acodec="mp4a.40.2",
            language="en",
            format_note="original",
        ),
        video(
            "22",
            height=480,
            audio_track_id="b",
            acodec="mp4a.40.2",
            language="es",
            format_note="dubbed-auto",
        ),
    )
    selected = choices(data)
    assert {c.video_id for c in selected} == {"137", "18"}
    assert next(c for c in selected if c.video_id == "137").audio_id == "141"
    assert next(c for c in selected if c.video_id == "18").audio_id is None
    assert all(c.has_audio for c in selected)


def test_silent_and_combined_only():
    assert not choices(info())[0].has_audio
    assert select_audio(info()) == ([], False)
    data = dict(duration=2, formats=[video("18", acodec="mp4a.40.2")])
    assert choices(data)[0].has_audio
    assert not select_audio(data)[1]


def test_unmarked_single_audio_does_not_authorize_translated_captions():
    data = info(
        audio(),
        language="en",
        subtitles={"en": [{"ext": "srt", "url": "https://example.invalid/sub?lang=en"}]},
        automatic_captions={
            "en": [{"ext": "srt", "url": "https://example.invalid/sub?lang=es&tlang=en"}]
        },
    )
    assert caption_track(data) is None
    data["original_language"] = "es"
    data["automatic_captions"]["es"] = [
        {"ext": "srt", "url": "https://example.invalid/sub?lang=es"}
    ]
    assert caption_track(data) == {"kind": "automatic", "language": "es"}


def test_extractor_language_is_captured_before_default_format(monkeypatch):
    from yt_dlp import YoutubeDL

    from ytdock.worker import extract

    raw = info(audio())

    def get(self, url, download, process):
        assert not download and not process
        return raw

    def process(self, data, download):
        data["language"] = "en"
        return data

    monkeypatch.setattr(YoutubeDL, "extract_info", get)
    monkeypatch.setattr(YoutubeDL, "process_ie_result", process)
    assert original_language(extract("https://youtu.be/O16af0iRs44", "/unused")) is None


def test_refreshed_audio_identity_is_checked_before_download(monkeypatch):
    from ytdock import worker

    old = info(audio())
    selection = choices(old)[0].to_dict()
    refreshed = info(audio(audio_track_id="a"), audio("251", audio_track_id="b", language="es"))
    monkeypatch.setattr(worker, "extract", lambda *args: refreshed)
    monkeypatch.setattr(worker, "download_stream", lambda *args: pytest.fail("Must not download"))
    with pytest.raises(UserError) as caught:
        worker.work(dict(operation="download", url="unused", quickjs="unused", choice=selection))
    assert str(caught.value) == t("audio.original_unknown")
