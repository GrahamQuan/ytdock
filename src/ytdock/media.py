"""Local FFmpeg planning and verification; no shell, no external media service."""

import json
import math
import os
import struct
import subprocess
from fractions import Fraction
from pathlib import Path

from .core import UserError
from .i18n import t


def run(args: list[str], pass_fds=(), *, cwd=None) -> bytes:
    try:
        result = subprocess.run(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            pass_fds=pass_fds,
            cwd=cwd,
        )
    except OSError:
        raise UserError(t("unable_to_start_ffmpeg_ffprobe_check_the_dependencies")) from None
    if result.returncode:
        if b"No space left" in result.stderr:
            raise UserError(t("not_enough_disk_space"))
        raise UserError(t("media_processing_or_output_verification_failed_no_output_was_published"))
    return result.stdout


def probe(path: Path, ffprobe: str, pass_fds=()) -> dict:
    try:
        info = json.loads(
            run(
                [ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                pass_fds,
            )
        )
        if not isinstance(info.get("streams"), list) or not isinstance(info.get("format"), dict):
            raise ValueError
        return info
    except (ValueError, KeyError, TypeError):
        raise UserError(t("ffprobe_did_not_return_valid_media_information")) from None


def rate(stream: dict) -> float:
    try:
        value = float(Fraction(stream.get("avg_frame_rate") or stream.get("r_frame_rate")))
        return value if math.isfinite(value) else 0
    except (ValueError, ZeroDivisionError, TypeError):
        return 0


def video_stream(info: dict) -> dict:
    videos = [
        s
        for s in info["streams"]
        if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
    ]
    if len(videos) != 1:
        raise UserError(t("unexpected_number_of_video_streams"))
    return videos[0]


def check_source(stream: dict, choice: dict):
    if stream.get("color_transfer") in ("smpte2084", "arib-std-b67") or any(
        "dovi" in str(s).lower() for s in stream.get("side_data_list", [])
    ):
        raise UserError(t("the_downloaded_source_is_hdr_which_is_not_supported"))
    if (
        any(float(s.get("rotation", 0)) % 360 for s in stream.get("side_data_list", []))
        or float(stream.get("tags", {}).get("rotate", 0)) % 360
    ):
        raise UserError(
            t("the_source_contains_rotation_metadata_that_cannot_currently_be_preserved")
        )
    if stream.get("width") != choice["width"] or stream.get("height") != choice["height"]:
        raise UserError(t("the_source_resolution_differs_from_the_selection_it_was_not"))
    if abs(rate(stream) - choice["fps"]) > max(0.06, choice["fps"] * 0.002):
        raise UserError(t("the_source_frame_rate_differs_from_the_selection_refresh_the"))


def process(
    video: Path,
    audio: Path | None,
    output: Path,
    choice: dict,
    duration: float,
    ffmpeg: str,
    ffprobe: str,
    emit,
    pass_fds=(),
) -> dict:
    source = probe(video, ffprobe, pass_fds)
    stream = video_stream(source)
    check_source(stream, choice)
    audio_info = probe(audio, ffprobe, pass_fds) if audio else source
    audio_streams = [s for s in audio_info["streams"] if s.get("codec_type") == "audio"]
    if bool(audio_streams) != choice["has_audio"]:
        raise UserError(t("the_actual_audio_streams_differ_from_the_selection_no_output"))
    measured_durations = [video_duration(source, stream)]
    if audio_streams:
        measured_durations.append(video_duration(audio_info, audio_streams[0]))
    measured_duration = max(measured_durations)
    # YouTube metadata is commonly rounded to whole seconds. It is a coarse
    # completeness guard, not the expected duration of the muxed output.
    metadata_tolerance = max(1.0, min(2.0, duration * 0.005))
    for track_duration in measured_durations:
        if abs(track_duration - duration) > metadata_tolerance:
            raise UserError(
                t("verification.source_duration", expected=duration, actual=track_duration)
            )
    vcopy = stream.get("codec_name") == "h264" and stream.get("pix_fmt") == "yuv420p"
    acopy = not audio_streams or audio_streams[0].get("codec_name") == "aac"
    transcode = not (vcopy and acopy)
    if transcode and not choice["transcode"]:
        raise UserError(t("the_source_codec_differs_from_the_metadata_and_requires_transcoding"))
    # Preserve actual source frame timestamps and SAR, without -r or scaling.
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-n",
        "-noautorotate",
        "-i",
        str(video),
    ]
    if audio:
        args += ["-i", str(audio)]
    args += ["-map", "0:v:0"]
    if audio_streams:
        args += ["-map", "1:a:0" if audio else "0:a:0"]
    args += ["-map_metadata", "-1", "-map_chapters", "-1", "-c:v", "copy" if vcopy else "libx264"]
    if not vcopy:
        args += [
            "-crf",
            "20",
            "-preset",
            "medium",
            "-pix_fmt",
            "yuv420p",
            "-fps_mode",
            "passthrough",
        ]
    if audio_streams:
        args += ["-c:a", "copy" if acopy else "aac"]
        if not acopy:
            args += ["-b:a", "192k"]
    args += ["-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-nostats", str(output)]
    stage = (
        "stage.transcoding_and_merging"
        if transcode and audio
        else "stage.transcoding"
        if transcode
        else "stage.merging"
        if audio
        else "stage.packaging_mp4"
    )
    emit({"stage": stage})
    with (output.parent / "ffmpeg-error.log").open("wb") as error:
        with subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=error,
            shell=False,
            pass_fds=pass_fds,
            text=True,
        ) as process:
            for line in process.stdout:
                if transcode and line.startswith("out_time_us="):
                    try:
                        emit(
                            {
                                "stage": stage,
                                "done": max(0, int(line.split("=", 1)[1])) / 1_000_000,
                                "total": duration,
                            }
                        )
                    except ValueError:
                        pass
            if process.wait():
                message = (output.parent / "ffmpeg-error.log").read_bytes()[-8192:]
                raise UserError(
                    t("not_enough_disk_space")
                    if b"No space left" in message
                    else t("merging_or_transcoding_failed_no_output_was_published")
                )
    if transcode:
        emit({"stage": stage, "finished": True})
    return dict(stream, _ytdock_duration=measured_duration)


def faststart(path: Path) -> bool:
    # Read MP4 box headers without loading the media into memory.
    with path.open("rb") as file:
        total = path.stat().st_size
        offset = 0
        while offset + 8 <= total:
            file.seek(offset)
            length, kind = struct.unpack(">I4s", file.read(8))
            header = 8
            if length == 1:
                data = file.read(8)
                if len(data) != 8:
                    return False
                length = struct.unpack(">Q", data)[0]
                header = 16
            if kind == b"moov":
                return length >= header and offset + length <= total
            if kind == b"mdat" or length < header or offset + length > total:
                return False
            offset += length
    return False


def video_duration(info, stream):
    """Use the selected picture stream's duration consistently across both entrances."""
    try:
        duration = float(stream.get("duration") or info["format"].get("duration"))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
        return duration
    except (ValueError, TypeError, KeyError):
        raise UserError(t("unable_to_determine_a_valid_duration_this_file_is_not")) from None


def verify(
    output: Path,
    choice: dict,
    duration: float,
    source: dict,
    ffmpeg: str,
    ffprobe: str,
    pass_fds=(),
    *,
    video_codec="h264",
    video_tag=None,
    subtitle_duration=False,
):
    if not output.is_file() or output.stat().st_size == 0:
        raise UserError(t("the_output_file_is_missing_or_empty"))
    info = probe(output, ffprobe, pass_fds)
    stream = video_stream(info)
    check_source(stream, choice)
    audios = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    try:
        actual_duration = float(info["format"]["duration"])
        sar = Fraction(stream.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        original_sar = Fraction(source.get("sample_aspect_ratio", "1:1").replace(":", "/"))
    except (ValueError, KeyError, ZeroDivisionError):
        raise UserError(t("invalid_output_duration_or_aspect_ratio")) from None
    expected_duration = source.get("_ytdock_duration", duration)
    checks = {
        "container": "mp4" in info["format"].get("format_name", "").split(","),
        "video_codec": stream.get("codec_name") == video_codec,
        "video_tag": video_tag is None or stream.get("codec_tag_string") == video_tag,
        "pixel_format": stream.get("pix_fmt") == "yuv420p",
        "audio": len(audios) == int(choice["has_audio"])
        and all(s.get("codec_name") == "aac" for s in audios),
        "duration": math.isfinite(actual_duration)
        and actual_duration > 0
        and abs(actual_duration - expected_duration)
        <= max(0.25, min(2.0, expected_duration * 0.005)),
        "frame_rate": abs(rate(stream) - rate(source)) <= 0.01,
        "aspect_ratio": sar == original_sar,
        "faststart": faststart(output),
    }
    failed = [t("verification." + key) for key, passed in checks.items() if not passed]
    if failed:
        details = ", ".join(failed)
        if not checks["duration"]:
            details += t(
                "verification.duration_values", expected=expected_duration, actual=actual_duration
            )
        raise UserError(t("verification.failed", details=details))
    if audios:
        try:
            if abs(float(audios[0]["duration"]) - actual_duration) > max(
                1.0, min(5.0, duration * 0.01)
            ):
                raise ValueError
        except (KeyError, ValueError):
            raise UserError(t("the_output_audio_duration_is_incomplete")) from None
    run(
        [
            ffmpeg,
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-nostdin",
            "-i",
            str(output),
            "-map",
            "0:v:0",
            "-map",
            "0:a?",
            "-f",
            "null",
            "-",
        ],
        pass_fds,
    )
    with output.open("rb") as file:
        os.fsync(file.fileno())
    return video_duration(info, stream) if subtitle_duration else actual_duration
