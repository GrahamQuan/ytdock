"""Single local-file compression, with read-only source identity and explicit streams."""

import json
import math
import shlex
import stat
import subprocess
import time
from fractions import Fraction
from pathlib import Path

from .core import UserError, filename, safe_text, size
from .i18n import t
from .media import probe, rate, run, verify

PROFILES = {
    "size": {
        "label": "compression.smaller_file",
        "crf": "30",
        "preset": "superfast",
        "bitrate": "128k",
    },
    "quality": {
        "label": "compression.higher_quality",
        "crf": "23",
        "preset": "medium",
        "bitrate": "192k",
    },
}
# Exclude playlists, manifests and multi-file demuxers from a single-file tool.
FORMATS = "mov,matroska,webm,avi,flv,mpeg,mpegts,ogg,asf"


def local_path(value: str) -> Path:
    value = value.strip()
    if not value or any(c in value for c in ("\x00", "\n", "\r")):
        raise UserError(t("enter_the_absolute_path_of_one_local_video_file_multiple"))
    # Literal existing paths may contain spaces, quotes and shell metacharacters.
    # Otherwise parse Finder's escaped/quoted spelling, without evaluating anything.
    try:
        literal = Path(value).expanduser()
    except RuntimeError:
        raise UserError(t("unable_to_expand_the_home_directory_use_an_absolute_path")) from None
    if literal.is_absolute() and literal.is_file():
        return literal.resolve()
    try:
        parts = shlex.split(value)
    except ValueError:
        raise UserError(t("unmatched_quotes_in_the_path_paste_the_complete_path_again")) from None
    if len(parts) != 1:
        raise UserError(t("only_one_file_is_supported_quote_paths_containing_spaces_or"))
    try:
        path = Path(parts[0]).expanduser()
    except RuntimeError:
        raise UserError(t("unable_to_expand_the_home_directory_use_an_absolute_path")) from None
    if not path.is_absolute():
        raise UserError(t("use_an_absolute_local_file_path_is_supported_not_a"))
    if not path.exists():
        raise UserError(t("file_not_found_check_the_path"))
    if not path.is_file():
        raise UserError(t("use_a_single_regular_video_file_not_a_directory_or"))
    return path.resolve()


def identity(path: Path) -> list[int]:
    s = path.stat()
    if not stat.S_ISREG(s.st_mode):
        raise UserError(t("only_regular_video_files_are_supported"))
    return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]


def unchanged(info):
    try:
        if identity(Path(info["path"])) == info["identity"]:
            return
    except OSError:
        pass
    raise UserError(t("the_source_has_changed_or_is_unreadable_enter_it_again"))


def main_stream(streams, kind):
    available = [
        s
        for s in streams
        if s.get("codec_type") == kind and not s.get("disposition", {}).get("attached_pic")
    ]
    return next(
        (s for s in available if s.get("disposition", {}).get("default")),
        available[0] if available else None,
    )


def validate_video(video):
    if video is None:
        raise UserError(t("the_file_has_no_usable_video_stream_cover_art_does"))
    side = video.get("side_data_list", [])
    if any(x in json.dumps(side).lower() for x in ("dovi", "mastering display", "content light")):
        raise UserError(t("hdr_videos_and_videos_with_hdr_color_metadata_are_not"))
    allowed = {
        "color_transfer": {None, "unknown", "bt709", "smpte170m", "smpte240m", "bt470m", "bt470bg"},
        "color_primaries": {
            None,
            "unknown",
            "bt709",
            "smpte170m",
            "smpte240m",
            "bt470m",
            "bt470bg",
        },
        "color_space": {None, "unknown", "bt709", "smpte170m", "smpte240m", "bt470bg", "fcc"},
        "color_range": {None, "unknown", "tv", "pc"},
    }
    if any(video.get(key) not in values for key, values in allowed.items()):
        raise UserError(t("hdr_or_color_information_that_cannot_be_reliably_preserved_is"))
    pixel = video.get("pix_fmt")
    eight_bit = {"yuv420p", "yuv422p", "yuv444p", "yuvj420p", "yuvj422p", "yuvj444p"}
    ten_bit = {"yuv420p10le", "yuv422p10le", "yuv444p10le"}
    if pixel not in eight_bit | ten_bit or (
        pixel in ten_bit
        and any(
            video.get(key) in (None, "unknown")
            for key in ("color_transfer", "color_primaries", "color_space")
        )
    ):
        raise UserError(t("unsupported_pixel_format_or_color_information_10_bit_sdr_requires"))
    try:
        if float(video.get("tags", {}).get("rotate", 0)) % 360:
            raise ValueError
        # Reject even a zero-rotation display matrix: it can also encode mirroring.
        if any("rotation" in s or "displaymatrix" in s for s in side):
            raise ValueError
    except (ValueError, TypeError):
        raise UserError(
            t("the_video_contains_rotation_or_display_matrix_information_that_cannot")
        ) from None
    if video.get("field_order") not in (None, "unknown", "progressive"):
        raise UserError(t("interlaced_video_is_not_supported"))
    if not video.get("codec_name") or any(
        not isinstance(video.get(k), int) or video[k] <= 0 or video[k] % 2
        for k in ("width", "height")
    ):
        raise UserError(
            t("unsupported_codec_or_dimensions_yuv420p_requires_positive_even_dimensions_the")
        )
    if rate(video) <= 0:
        raise UserError(t("unable_to_determine_a_reliable_frame_rate_this_video_is"))
    try:
        sar = Fraction(video.get("sample_aspect_ratio", "1:1").replace(":", "/"))
        if sar <= 0:
            raise ValueError
    except (ValueError, ZeroDivisionError):
        raise UserError(t("unable_to_determine_a_reliable_pixel_aspect_ratio")) from None


def inspect(path: Path, ffprobe: str, pass_fds=()) -> dict:
    try:
        before = identity(path)
        with path.open("rb"):
            pass
    except OSError:
        raise UserError(t("the_file_is_missing_or_unreadable_check_its_path_and")) from None
    if not before[2]:
        raise UserError(t("the_source_file_is_empty"))
    try:
        data = json.loads(
            run(
                [
                    ffprobe,
                    "-v",
                    "error",
                    "-protocol_whitelist",
                    "file,pipe",
                    "-format_whitelist",
                    FORMATS,
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(path),
                ],
                pass_fds,
            )
        )
    except (UserError, ValueError):
        raise UserError(t("unable_to_read_video_information_the_file_may_be_damaged")) from None
    videos = data.get("streams", [])
    video = main_stream(videos, "video")
    validate_video(video)
    audio = main_stream(videos, "audio")
    try:
        duration = float(video.get("duration") or data["format"].get("duration"))
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError
    except (ValueError, TypeError, KeyError):
        raise UserError(t("unable_to_determine_a_valid_duration_this_file_is_not")) from None
    # Unknown/absent SAR conventionally means square pixels for these containers.
    source = dict(video)
    source.setdefault("sample_aspect_ratio", "1:1")
    if source["pix_fmt"].startswith("yuvj"):
        source["color_range"] = "pc"
    result = {
        "path": str(path),
        "title": safe_text(path.name),
        "identity": before,
        "input_bytes": before[2],
        "duration": duration,
        "video": source,
        "audio": audio,
        "audio_count": sum(s.get("codec_type") == "audio" for s in videos),
        "video_count": sum(
            s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")
            for s in videos
        ),
        "choice": {
            "width": video["width"],
            "height": video["height"],
            "fps": rate(video),
            "has_audio": audio is not None,
        },
    }
    unchanged(result)
    return result


def output_name(info, profile):
    # Reuse the downloader's sanitization and UTF-8 byte limit, keeping our fixed suffix.
    base = filename(Path(info["path"]).stem, "local").removesuffix(" [local]")
    return f"{base}_compressed"


def command(info, profile, output, ffmpeg):
    if profile not in PROFILES:
        raise UserError(t("unknown_compression_mode_select_again"))
    setting = PROFILES[profile]
    video, audio = info["video"], info["audio"]
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-xerror",
        "-nostdin",
        "-n",
        "-noautorotate",
        "-protocol_whitelist",
        "file,pipe",
        "-format_whitelist",
        FORMATS,
        "-i",
        info["path"],
        "-map",
        f"0:{video['index']}",
    ]
    if audio is not None:
        args += ["-map", f"0:{audio['index']}"]
    args += [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-c:v",
        "libx264",
        "-crf",
        setting["crf"],
        "-preset",
        setting["preset"],
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "passthrough",
        "-tag:v",
        "avc1",
    ]
    if video.get("color_range") == "pc":
        args += ["-vf", "scale=in_range=pc:out_range=tv"]
    # Keep the SDR transfer and matrix; explicitly convert full to limited range.
    for key, flag in (
        ("color_range", "-color_range"),
        ("color_space", "-colorspace"),
        ("color_transfer", "-color_trc"),
        ("color_primaries", "-color_primaries"),
    ):
        if video.get(key) not in (None, "unknown"):
            args += [flag, "tv" if key == "color_range" else video[key]]
    if audio is not None:
        copy = profile == "quality" and audio.get("codec_name") == "aac"
        args += ["-c:a", "copy" if copy else "aac"]
        if not copy:
            args += ["-b:a", setting["bitrate"]]
    args += ["-movflags", "+faststart", "-f", "mp4", "-progress", "pipe:1", "-nostats", str(output)]
    return args


def compress(info, profile, output, ffmpeg, ffprobe, emit, pass_fds=()):
    unchanged(info)
    args = command(info, profile, output, ffmpeg)
    emit({"stage": "stage.compressing", "done": 0, "total": info["duration"]})
    last = 0.0
    error_path = output.parent / "ffmpeg-error.log"
    with error_path.open("wb") as error:
        with subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=error,
            shell=False,
            text=True,
            pass_fds=pass_fds,
        ) as proc:
            for line in proc.stdout:
                now = time.monotonic()
                if line.startswith("out_time_us=") and now - last >= 0.1:
                    try:
                        done = max(0, int(line.partition("=")[2])) / 1_000_000
                    except ValueError:
                        continue
                    emit({"stage": "stage.compressing", "done": done, "total": info["duration"]})
                    last = now
            if proc.wait():
                message = error_path.read_bytes()[-8192:]
                raise UserError(
                    t("not_enough_disk_space")
                    if b"No space left" in message
                    else t("compression_failed_the_source_is_unchanged_and_no_output_was")
                )
    emit({"stage": "stage.compressing", "finished": True})
    emit({"stage": "stage.verifying_file"})
    unchanged(info)
    verify(output, info["choice"], info["duration"], info["video"], ffmpeg, ffprobe, pass_fds)
    result = probe(output, ffprobe, pass_fds)
    video = main_stream(result["streams"], "video")
    if video.get("codec_tag_string") != "avc1" or len(result["streams"]) != 1 + int(
        info["audio"] is not None
    ):
        raise UserError(t("output_codec_tag_or_extra_stream_verification_failed"))
    for key in ("color_range", "color_space", "color_transfer", "color_primaries"):
        expected = info["video"].get(key)
        actual = video.get(key)
        if key == "color_range":
            expected = "tv" if expected == "pc" else expected
            actual = actual or "tv"  # H.264 defaults to limited range when omitted.
        if expected not in (None, "unknown") and actual != expected:
            raise UserError(t("output_color_information_differs_from_the_source_no_output_was"))
    unchanged(info)
    return {
        "stem": output_name(info, profile),
        "input_bytes": info["input_bytes"],
        "output_bytes": output.stat().st_size,
    }


def summary(result):
    before, after = result["input_bytes"], result["output_bytes"]
    change = abs(after - before) / before * 100
    label = (
        t("size_increased")
        if after > before
        else t("size_decreased")
        if after < before
        else t("size_unchanged")
    )
    return t(
        "compression.complete",
        p0=size(before),
        p1=size(after),
        p2=label,
        p3=change,
        p4=result["path"],
    )
