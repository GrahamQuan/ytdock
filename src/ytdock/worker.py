"""Private worker protocol: stdout contains only allowlisted JSON events."""

import copy
import json
import os
import signal
import sys
import tempfile
import threading
import time
from pathlib import Path

from .core import (
    FormatExpired,
    UserError,
    choices,
    original_language,
    provider_error,
    safe_text,
    select_audio,
)
from .i18n import set_language, t
from .media import process, verify


class QuietLogger:
    def debug(self, _message):
        pass

    info = warning = error = debug


def emit(event: dict):
    print(json.dumps(event, ensure_ascii=False), flush=True)


def options(quickjs: str):
    return {
        "quiet": True,
        "no_warnings": True,
        "logger": QuietLogger(),
        "noplaylist": True,
        "cachedir": False,
        "socket_timeout": 20,
        "retries": 3,
        "fragment_retries": 3,
        "extractor_retries": 3,
        "file_access_retries": 0,
        "continuedl": False,
        "overwrites": False,
        "skip_unavailable_fragments": False,
        "concurrent_fragment_downloads": 1,
        "js_runtimes": {"quickjs": {"path": quickjs}},
        "remote_components": set(),
        "writethumbnail": False,
        "writesubtitles": False,
        "writeinfojson": False,
        "postprocessors": [],
        "fixup": "never",
    }


def extract(url: str, quickjs: str) -> dict:
    from yt_dlp import YoutubeDL

    emit({"stage": "stage.fetching_information"})
    with YoutubeDL(options(quickjs)) as ydl:
        raw = ydl.extract_info(url, download=False, process=False)
        language = raw.get("original_language") or raw.get("language")
        info = ydl.process_ie_result(raw, download=False)
        info["_ytdock_original_language"] = language
    choices(info)  # Reject live/unusable metadata before displaying or downloading.
    return info


def download_stream(info: dict, fmt: dict, path: Path, stage: str, quickjs: str):
    from yt_dlp import YoutubeDL

    last = 0.0

    def hook(data):
        nonlocal last
        finished = data.get("status") == "finished"
        now = time.monotonic()
        if not finished and now - last < 0.1:
            return
        last = now
        emit(
            {
                "stage": stage,
                "done": data.get("downloaded_bytes", 0),
                "total": data.get("total_bytes") or data.get("total_bytes_estimate"),
                "approximate": not data.get("total_bytes")
                and bool(data.get("total_bytes_estimate")),
                "speed": data.get("speed"),
                "eta": data.get("eta"),
                "finished": finished,
                "download": True,
            }
        )

    emit({"stage": stage, "done": 0, "total": fmt.get("filesize"), "download": True})
    params = options(quickjs) | {"outtmpl": str(path), "progress_hooks": [hook]}
    details = copy.deepcopy(info)
    details.pop("requested_formats", None)
    details.pop("requested_downloads", None)
    details.update(fmt)
    with YoutubeDL(params) as ydl:
        ydl.process_info(details)
    if not path.is_file() or not path.stat().st_size:
        raise UserError(t("the_download_did_not_produce_a_complete_media_file"))


def work(request: dict):
    if request["operation"] in ("inspect_subtitles", "local_subtitles"):
        from .local_subtitles import inspect as inspect_subtitles
        from .local_subtitles import process as process_subtitles

        directory = Path(request["directory"])
        fds = tuple(request["pass_fds"])
        emit({"stage": "stage.reading_information"})
        if request["operation"] == "inspect_subtitles":
            result = inspect_subtitles(
                Path(request["path"]),
                Path(request["captions_path"]),
                directory,
                request["ffmpeg"],
                request["ffprobe"],
                emit,
                fds,
            )
        else:
            result = process_subtitles(
                request["source"], directory, request["ffmpeg"], request["ffprobe"], emit, fds
            )
        emit({"result": result})
        return
    if request["operation"] == "burn_subtitles":
        from .burn import burn

        result = burn(
            Path(request["directory"]),
            request["choice"],
            request["duration"],
            request["ffmpeg"],
            request["ffprobe"],
            emit,
            tuple(request["pass_fds"]),
        )
        emit({"result": result})
        return
    if request["operation"] in ("inspect_local", "compress"):
        from .compression import compress, inspect, unchanged

        emit({"stage": "stage.reading_information"})
        fds = tuple(request["pass_fds"])
        if request["operation"] == "inspect_local":
            emit({"result": inspect(Path(request["path"]), request["ffprobe"], fds)})
        else:
            expected = request["source"]
            unchanged(expected)
            source = inspect(Path(expected["path"]), request["ffprobe"], fds)
            if source != expected:
                raise UserError(t("source_information_has_changed_enter_the_path_again"))
            result = compress(
                source,
                request["profile"],
                Path(request["directory"]) / "verified.mp4",
                request["ffmpeg"],
                request["ffprobe"],
                emit,
                fds,
            )
            emit({"result": result})
        return
    info = extract(request["url"], request["quickjs"])
    available = choices(info)
    from .subtitles import caption_track, download_caption, prepare

    caption = caption_track(info)
    if request["operation"] == "inspect":
        subtitle_error = None
        if caption:
            from .burn import capabilities

            try:
                capabilities(
                    request["ffmpeg"],
                    request["ffprobe"],
                    Path(request["directory"]),
                    tuple(request["pass_fds"]),
                )
            except UserError as exc:
                subtitle_error = str(exc)
        emit(
            {
                "result": {
                    "title": safe_text(info.get("title", "YouTube video")),
                    "id": info["id"],
                    "duration": info["duration"],
                    "choices": [c.to_dict() for c in available],
                    "caption": caption,
                    "subtitle_error": subtitle_error,
                    "audio_language": original_language(info) if select_audio(info)[1] else None,
                }
            }
        )
        return
    selection = request["choice"]
    selected = next(
        (
            c
            for c in available
            if c.video_id == selection["video_id"]
            and c.audio_id == selection["audio_id"]
            and c.key == (selection["width"], selection["height"], selection["fps"])
            and c.transcode == selection["transcode"]
            and c.has_audio == selection["has_audio"]
        ),
        None,
    )
    if selected is None:
        raise FormatExpired(
            t("the_selected_format_has_changed_refreshing_the_options_please_select")
        )
    directory = Path(request["directory"])
    if request.get("subtitles"):
        from .burn import capabilities

        if not caption or caption != request.get("caption"):
            raise FormatExpired(t("subtitle.changed"))
        capabilities(request["ffmpeg"], request["ffprobe"], directory, tuple(request["pass_fds"]))
        raw_caption = download_caption(info, caption, directory, request["quickjs"], emit)
    formats = {str(f["format_id"]): f for f in info["formats"]}
    video = directory / "video.media"
    download_stream(
        info, formats[selected.video_id], video, "stage.downloading_video", request["quickjs"]
    )
    audio = None
    if selected.audio_id:
        audio = directory / "audio.media"
        download_stream(
            info, formats[selected.audio_id], audio, "stage.downloading_audio", request["quickjs"]
        )
    output = directory / "verified.mp4"
    fds = tuple(request["pass_fds"])
    source = process(
        video,
        audio,
        output,
        selected.to_dict(),
        float(info["duration"]),
        request["ffmpeg"],
        request["ffprobe"],
        emit,
        fds,
    )
    emit({"stage": "stage.verifying_file"})
    actual_duration = verify(
        output,
        selected.to_dict(),
        float(info["duration"]),
        source,
        request["ffmpeg"],
        request["ffprobe"],
        fds,
        subtitle_duration=bool(request.get("subtitles")),
    )
    if request.get("subtitles"):
        emit({"stage": "stage.preparing_subtitles"})
        prepare(raw_caption, directory / "captions.srt", actual_duration, emit)
    emit(
        {
            "result": {
                "title": safe_text(info.get("title", "YouTube video")),
                "id": info["id"],
                "duration": actual_duration,
            }
        }
    )


def main():
    request = json.loads(sys.stdin.readline())
    set_language(request.get("language", "en"))
    parent = request["parent_pid"]
    os.chdir(request["directory"])
    tempfile.tempdir = request["directory"]
    os.environ["TMPDIR"] = request["directory"]

    def watch_parent():
        while True:
            if os.getppid() != parent:
                # A killed UI must not leave a download/FFmpeg task behind.
                os.killpg(os.getpgrp(), signal.SIGKILL)
            time.sleep(0.2)

    threading.Thread(target=watch_parent, daemon=True).start()
    try:
        work(request)
    except Exception as exc:
        error = (
            exc
            if isinstance(exc, UserError)
            else (
                UserError(
                    t("not_enough_disk_space")
                    if isinstance(exc, OSError) and exc.errno == 28
                    else t("local_video_processing_failed_check_the_file_disk_space_and")
                )
                if request.get("operation") in ("inspect_local", "compress", "burn_subtitles")
                else provider_error(exc)
            )
        )
        emit({"error": str(error), "expired": isinstance(error, FormatExpired)})
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
