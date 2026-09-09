from ytdock import worker


def test_real_ytdlp_single_stream_download(tmp_path, monkeypatch):
    source = tmp_path / "source.bin"
    source.write_bytes(b"local fixture" * 1000)
    target = tmp_path / "video.media"
    original = worker.options
    monkeypatch.setattr(
        worker, "options", lambda quickjs: original(quickjs) | {"enable_file_urls": True}
    )
    events = []
    monkeypatch.setattr(worker, "emit", events.append)
    info = dict(
        id="BaW_jenozKc",
        title="Local fixture",
        duration=1,
        extractor="test",
        webpage_url="https://youtube.com/watch?v=BaW_jenozKc",
    )
    fmt = dict(
        format_id="test",
        url=source.as_uri(),
        protocol="file",
        ext="mp4",
        vcodec="avc1.640028",
        acodec="none",
    )
    worker.download_stream(info, fmt, target, "stage.downloading_video", "/unused/qjs")
    assert target.read_bytes() == source.read_bytes()
    assert events[-1]["finished"]
    assert all(event["stage"] == "stage.downloading_video" for event in events)


def test_changed_selection_requires_reselection(monkeypatch):
    import pytest

    from ytdock.core import FormatExpired

    metadata = dict(
        id="BaW_jenozKc",
        title="test",
        duration=1,
        formats=[
            dict(
                format_id="v",
                url="https://example.invalid",
                protocol="https",
                width=1280,
                height=720,
                fps=30,
                vcodec="avc1.640028",
                acodec="none",
            ),
        ],
    )
    monkeypatch.setattr(worker, "extract", lambda url, quickjs: metadata)
    monkeypatch.setattr(worker, "download_stream", lambda *a: pytest.fail("Must not download"))
    with pytest.raises(FormatExpired):
        worker.work(
            dict(
                operation="download",
                url="unused",
                quickjs="unused",
                choice=dict(
                    video_id="v",
                    audio_id=None,
                    width=1920,
                    height=1080,
                    fps=30,
                    transcode=False,
                    has_audio=False,
                ),
            )
        )


def test_subtitles_use_verified_duration_instead_of_metadata(tmp_path, monkeypatch):
    from ytdock import burn, subtitles
    from ytdock.core import Choice

    choice = Choice("v", None, 320, 180, 30, False, None, False, False)
    track = {"kind": "automatic", "language": "en"}
    metadata = dict(id="test", title="Test", duration=9, formats=[{"format_id": "v"}])
    monkeypatch.setattr(worker, "extract", lambda *args: metadata)
    monkeypatch.setattr(worker, "choices", lambda *args: [choice])
    monkeypatch.setattr(subtitles, "caption_track", lambda *args: track)
    monkeypatch.setattr(burn, "capabilities", lambda *args: None)
    raw = tmp_path / "original.srt"
    raw.write_text("1\n00:00:09,500 --> 00:00:10,500\nRetained beyond metadata duration\n")
    monkeypatch.setattr(subtitles, "download_caption", lambda *args: raw)
    monkeypatch.setattr(worker, "download_stream", lambda *args: None)
    monkeypatch.setattr(worker, "process", lambda *args: {})

    def verified_duration(*args, subtitle_duration=False):
        assert subtitle_duration is True
        return 10.25

    monkeypatch.setattr(worker, "verify", verified_duration)
    events = []
    monkeypatch.setattr(worker, "emit", events.append)
    worker.work(
        dict(
            operation="download",
            url="unused",
            quickjs="unused",
            directory=str(tmp_path),
            pass_fds=[],
            ffmpeg="unused",
            ffprobe="unused",
            choice=choice.to_dict(),
            subtitles=True,
            caption=track,
        )
    )
    assert "00:00:10,250" in (tmp_path / "captions.srt").read_text()
    assert events[-1]["result"]["duration"] == 10.25
    assert any("notice" in event for event in events)
