"""Local MP4 export for Finder Quick Look."""

import heapq
import json
import math
import re
import subprocess
from fractions import Fraction
from pathlib import Path
from tempfile import TemporaryFile

from .compression import FORMATS, identity, main_stream, unchanged
from .core import UserError, filename, safe_text
from .i18n import t
from .media import faststart
from .storage import Task, publish

LOCAL_INPUT = ["-protocol_whitelist", "file,pipe", "-format_whitelist", FORMATS]
COLOR = ("color_primaries", "color_transfer", "color_space")
BITMAP_SUBTITLES = {"dvd_subtitle", "hdmv_pgs_subtitle", "dvb_subtitle", "xsub"}


def invoke(args, cwd=None):
    result = subprocess.run(args, capture_output=True, cwd=cwd, stdin=subprocess.DEVNULL)
    if result.returncode:
        detail = result.stderr[-4096:].decode(errors="replace").strip()
        raise UserError(f"{Path(args[0]).name}: {detail}")
    return result.stdout


def inspect(path, ffprobe):
    return json.loads(
        invoke(
            [
                ffprobe,
                "-v",
                "error",
                *LOCAL_INPUT,
                "-show_streams",
                "-show_format",
                "-of",
                "json",
                str(path),
            ]
        )
    )


def rational(value):
    try:
        return Fraction(str(value).replace(":", "/"))
    except (ValueError, ZeroDivisionError):
        return Fraction(0)


def rates(video):
    return [rational(video.get(key)) for key in ("avg_frame_rate", "r_frame_rate")]


def packet_stats(path, ffprobe, video, audio):
    """Read timing in bounded memory, including containers without per-track durations."""
    indexes = [video["index"]] + ([audio["index"]] if audio else [])
    bounds = {index: [math.inf, -math.inf] for index in indexes}
    pending = []
    last_pts = last_dts = None
    buffered = 0.0
    peak_ok = cadence_ok = True
    # Allow timestamp quantization (e.g. Matroska's millisecond time base).
    minimum_gap = 1 / 30 - min(0.001, float(rational(video.get("time_base"))))

    def observe(pts):
        nonlocal last_pts, cadence_ok
        if last_pts is not None and pts - last_pts < minimum_gap - 0.000002:
            cadence_ok = False
        last_pts = pts

    args = [
        ffprobe,
        "-v",
        "error",
        *LOCAL_INPUT,
        "-show_packets",
        "-show_entries",
        "packet=stream_index,pts_time,dts_time,duration_time,size",
        "-of",
        "compact=p=0:nk=0",
        str(path),
    ]
    with (
        TemporaryFile() as errors,
        subprocess.Popen(
            args, stdout=subprocess.PIPE, stderr=errors, stdin=subprocess.DEVNULL, text=True
        ) as process,
    ):
        for line in process.stdout:
            row = dict(field.split("=", 1) for field in line.strip().split("|") if "=" in field)
            index = int(row.get("stream_index", -1))
            if index not in bounds or row.get("pts_time") in (None, "N/A"):
                continue
            pts = float(row["pts_time"])
            duration = (
                float(row["duration_time"]) if row.get("duration_time") not in (None, "N/A") else 0
            )
            start, end = bounds[index]
            bounds[index] = [min(start, pts), max(end, pts + duration)]
            if index != video["index"]:
                continue
            heapq.heappush(pending, pts)
            if len(pending) > 32:
                observe(heapq.heappop(pending))
            if row.get("dts_time") in (None, "N/A"):
                continue
            dts = float(row["dts_time"])
            if last_dts is not None:
                buffered = max(0, buffered - max(0, dts - last_dts) * 20_000_000)
            buffered += int(row["size"]) * 8
            last_dts = dts
            peak_ok &= buffered <= 25_000_000
        if process.wait() or errors.tell():
            errors.seek(0)
            raise UserError(errors.read(4096).decode(errors="replace") or t("preview.timeline"))
    while pending:
        observe(heapq.heappop(pending))
    if any(not math.isfinite(start) or end < start for start, end in bounds.values()):
        raise UserError(t("preview.timeline"))
    return list(bounds.values()), cadence_ok, peak_ok


def dimensions(video):
    width, height = video["width"], video["height"]
    sar = rational(video.get("sample_aspect_ratio")) or 1
    width += width % 2
    height += height % 2
    if sar < 1:
        width *= sar
    else:
        height /= sar
    angle = float(video.get("tags", {}).get("rotate", 0))
    for side in video.get("side_data_list", []):
        if matrix := side.get("displaymatrix"):
            rows = [
                list(map(int, line.split(":")[1].split()))
                for line in matrix.splitlines()
                if line.strip()
            ]
            axes = [row[:2] for row in rows[:2]]
            if (
                sorted(map(abs, axes[0])) != [0, 65536]
                or sorted(map(abs, axes[1])) != [0, 65536]
                or bool(axes[0][0]) == bool(axes[1][0])
            ):
                raise UserError(t("preview.matrix"))
            angle = 90 if axes[0][0] == 0 else 0
    if angle % 90:
        raise UserError(t("preview.matrix"))
    if angle % 180:
        width, height = height, width
    scale = min(1, 1920 / max(width, height), 1080 / min(width, height))
    return tuple(max(2, 2 * round(side * scale / 2)) for side in (width, height))


def compatible_video(video):
    avg, nominal = rates(video)
    return (
        video.get("codec_name") == "h264"
        and video.get("profile") in {"Constrained Baseline", "Baseline", "Main", "High"}
        and 0 < video.get("level", 0) <= 41
        and video.get("pix_fmt") == "yuv420p"
        and video.get("field_order") == "progressive"
        and video.get("sample_aspect_ratio") in ("1:1", None)
        and not video.get("side_data_list")
        and not video.get("tags", {}).get("rotate")
        and dimensions(video) == (video["width"], video["height"])
        and 0 < (avg or nominal) <= 30
        and all(video.get(key) == "bt709" for key in COLOR)
        and video.get("color_range") == "tv"
    )


def reference_frames_fit(path, ffmpeg, video):
    # Inspect SPS rather than trusting the level label on a remux candidate.
    args = [
        ffmpeg,
        "-v",
        "info",
        "-nostdin",
        *LOCAL_INPUT,
        "-i",
        str(path),
        "-map",
        f"0:{video['index']}",
        "-c:v",
        "copy",
        "-frames:v",
        "1",
        "-bsf:v",
        "trace_headers",
        "-f",
        "null",
        "-",
    ]
    result = subprocess.run(args, capture_output=True)
    refs = re.findall(rb"max_num_ref_frames\s+[01]+ = (\d+)", result.stderr)
    macroblocks = math.ceil(video["width"] / 16) * math.ceil(video["height"] / 16)
    return result.returncode == 0 and refs and max(map(int, refs)) <= min(16, 32768 // macroblocks)


def compatible_audio(audio):
    return (
        audio.get("codec_name") == "aac"
        and audio.get("profile") == "LC"
        and audio.get("channels") in (1, 2)
        and audio.get("sample_rate") in ("44100", "48000")
    )


def color_filters(video):
    primaries, transfer, matrix = [video.get(k) for k in COLOR]
    sides = video.get("side_data_list", [])
    for side in sides:
        if "dv_profile" in side:
            base = {1: "smpte2084", 4: "arib-std-b67"}.get(
                side.get("dv_bl_signal_compatibility_id")
            )
            if side["dv_profile"] != 8 or not base or base != transfer:
                raise UserError(t("preview.dovi", profile=side["dv_profile"]))
    hdr = transfer in ("smpte2084", "arib-std-b67")
    pixel = video.get("pix_fmt", "")
    ambiguous = (
        hdr
        or primaries == "bt2020"
        or any(k in pixel for k in ("10", "12", "16"))
        or any("mastering" in str(side).lower() for side in sides)
    )
    if ambiguous and any(
        k in (None, "unknown", "unspecified") for k in (primaries, transfer, matrix)
    ):
        raise UserError(t("preview.color"))
    if not hdr and any("mastering" in str(side).lower() for side in sides):
        raise UserError(t("preview.color"))
    if (video.get("bits_per_raw_sample") or "").isdigit() and int(video["bits_per_raw_sample"]) > 8:
        if not all((primaries, transfer, matrix)):
            raise UserError(t("preview.color"))
    # Untagged conventional 8-bit YUV follows SD/HD encoding conventions.
    default = "smpte170m" if video["height"] <= 576 else "bt709"
    rgb = any(k in pixel for k in ("rgb", "bgr", "gbr"))

    def known(value, fallback):
        return fallback if value in (None, "unknown", "unspecified") else value

    primaries = known(primaries, "bt709" if rgb else default)
    transfer = known(transfer, "iec61966-2-1" if rgb else default)
    matrix = known(matrix, "gbr" if rgb else default)
    full = video.get("color_range") == "pc" or pixel.startswith("yuvj") or rgb
    inputs = f"pin={primaries}:tin={transfer}:rin={'full' if full else 'limited'}"
    # zscale infers the RGB matrix from the input pixel format.
    if not rgb:
        inputs += f":min={matrix}"
    output = "p=bt709:t=bt709:m=bt709:r=limited:dither=error_diffusion"
    if hdr:
        return [
            f"zscale={inputs}:t=linear:npl=100",
            "format=gbrpf32le",
            "zscale=p=bt709",
            "tonemap=tonemap=mobius",
            f"zscale={output}",
            "format=yuv420p",
            "sidedata=mode=delete",
        ]
    return [f"zscale={inputs}:{output}", "format=yuv420p"]


def read_source(path, ffprobe):
    path = Path(path).resolve()
    source = {"path": str(path), "identity": identity(path)}
    data = inspect(path, ffprobe)
    streams = data["streams"]
    video = main_stream(streams, "video")
    if not video:
        raise UserError(t("the_file_has_no_usable_video_stream_cover_art_does"))
    audio = main_stream(streams, "audio")
    forced = next(
        (
            s
            for s in streams
            if s["codec_type"] == "subtitle" and s.get("disposition", {}).get("forced")
        ),
        None,
    )
    unchanged(source)
    return source | {
        "video": video,
        "audio": audio,
        "forced": forced,
        "title": safe_text(path.name),
    }


def convert(source, directory, ffmpeg, ffprobe, burn_forced=False):
    path = Path(source["path"])
    video, audio, forced = source["video"], source["audio"], source["forced"]
    if burn_forced and not forced:
        raise UserError(t("preview.no_forced"))
    selected = [s for s in (video, audio, forced if burn_forced else None) if s]
    codecs = invoke([ffmpeg, "-v", "error", "-codecs"]).decode()
    decodable = set(re.findall(r"^ D[.A-Z]{5} (\S+)", codecs, re.MULTILINE))
    for track in selected:
        if track.get("codec_name") not in decodable:
            raise UserError(
                t(
                    "preview.decoder",
                    path=source["title"],
                    index=track["index"],
                    codec=track.get("codec_name", "?"),
                )
            )
    width, height = dimensions(video)
    unchanged(source)
    before, cadence_ok, peak_ok = packet_stats(path, ffprobe, video, audio)
    out = directory / "verified.mp4"
    write_preview(
        path,
        directory,
        ffmpeg,
        video,
        audio,
        forced if burn_forced else None,
        width,
        height,
        cadence_ok,
        peak_ok,
    )
    verify_preview(out, ffmpeg, ffprobe, width, height, audio, before)
    unchanged(source)
    return filename(path.stem, "local").removesuffix(" [local]") + ".preview"


def write_preview(
    path, directory, ffmpeg, video, audio, forced, width, height, cadence_ok, peak_ok
):
    vcopy = (
        compatible_video(video)
        and cadence_ok
        and peak_ok
        and not forced
        and reference_frames_fit(path, ffmpeg, video)
    )
    acopy = audio is not None and compatible_audio(audio)
    out = directory / "verified.mp4"
    args = [ffmpeg, "-v", "error", "-xerror", "-nostdin", "-n", *LOCAL_INPUT, "-i", str(path)]
    source_label = f"0:{video['index']}"
    if vcopy:
        args += ["-map", source_label, "-c:v", "copy"]
    else:
        filters = []
        if video.get("field_order") in ("tt", "bb", "tb", "bt"):
            filters.append("yadif=mode=send_frame")
        avg, nominal = rates(video)
        if max(avg, nominal) <= 0:
            raise UserError(t("unable_to_determine_a_reliable_frame_rate_this_video_is"))
        if avg > 30 or not cadence_ok:
            cadence = nominal if nominal > 30 else avg
            target = (
                Fraction(30000, 1001)
                if cadence
                in (Fraction(60000, 1001), Fraction(120000, 1001), Fraction(240000, 1001))
                else 30
            )
            filters.append(f"fps={target}")
        # FFmpeg autorotation applies the display matrix before these filters.
        if video["width"] % 2 or video["height"] % 2:
            filters.append("pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0")
        filters += [f"scale={width}:{height}", "setsar=1", *color_filters(video)]
        needed = {entry.partition("=")[0] for entry in filters}
        if forced:
            needed.add("overlay" if forced["codec_name"] in BITMAP_SUBTITLES else "ass")
        available = invoke([ffmpeg, "-v", "error", "-filters"]).decode()
        available_filters = set(
            re.findall(r"^[ \t]*[.A-Z]+[ \t]+(\w+)[ \t]+\S+->\S+", available, re.MULTILINE)
        )
        missing = sorted(needed - available_filters)
        if missing:
            raise UserError(t("preview.filters", filters=", ".join(missing)))
        if forced:
            if forced["codec_name"] in BITMAP_SUBTITLES:
                # Composite in the source canvas, before resizing.
                prefix = f"[{source_label}][0:{forced['index']}]overlay,"
            else:
                invoke(
                    [
                        ffmpeg,
                        "-v",
                        "error",
                        "-nostdin",
                        *LOCAL_INPUT,
                        "-i",
                        str(path),
                        "-map",
                        f"0:{forced['index']}",
                        "-c:s",
                        "ass",
                        "forced.ass",
                    ],
                    directory,
                )
                filters.append("ass=forced.ass")
                prefix = f"[{source_label}]"
        else:
            prefix = f"[{source_label}]"
        args += [
            "-filter_complex",
            prefix + ",".join(filters) + "[preview]",
            "-map",
            "[preview]",
            "-c:v",
            "libx264",
            "-profile:v",
            "high",
            "-level:v",
            "4.1",
            "-refs",
            "3",
            "-maxrate",
            "20M",
            "-bufsize",
            "25M",
            "-crf",
            "20",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            "yuv420p",
            "-color_range",
            "tv",
            "-colorspace",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_primaries",
            "bt709",
        ]
    if audio:
        args += ["-map", f"0:{audio['index']}", "-c:a", "copy" if acopy else "aac"]
        if not acopy:
            sample_rate = audio.get("sample_rate")
            args += [
                "-profile:a",
                "aac_low",
                "-b:a",
                "160k",
                "-ac",
                str(min(audio["channels"], 2)),
                "-ar",
                sample_rate if sample_rate in ("44100", "48000") else "48000",
            ]
    args += [
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-tag:v",
        "avc1",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        str(out),
    ]
    invoke(args, directory)


def verify_preview(out, ffmpeg, ffprobe, width, height, audio, before):
    result = inspect(out, ffprobe)
    picture = main_stream(result["streams"], "video")
    sound = main_stream(result["streams"], "audio")
    if (
        not picture
        or not compatible_video(picture)
        or picture["codec_tag_string"] != "avc1"
        or (picture["width"], picture["height"]) != (width, height)
        or len(result["streams"]) != 1 + int(bool(audio))
        or (audio and (not sound or not compatible_audio(sound)))
        or not faststart(out)
    ):
        raise UserError(t("preview.verify"))
    after, actual_cadence_ok, actual_peak_ok = packet_stats(out, ffprobe, picture, sound)
    if not actual_cadence_ok or not actual_peak_ok:
        raise UserError(t("preview.verify"))
    # Compare both track boundaries against the same video origin. AAC priming
    # and rounding during frame-rate conversion can shift an endpoint slightly.
    for original, converted in zip(before, after, strict=True):
        for src, dst in zip(original, converted, strict=True):
            if abs((src - before[0][0]) - (dst - after[0][0])) > 0.15:
                raise UserError(t("preview.timeline"))
    invoke(
        [
            ffmpeg,
            "-v",
            "error",
            "-xerror",
            "-err_detect",
            "explode",
            "-nostdin",
            "-i",
            str(out),
            "-map",
            "0",
            "-f",
            "null",
            "-",
        ]
    )


def export(path, destination, ffmpeg, ffprobe, burn_forced=False, notify=print):
    source = read_source(path, ffprobe)
    if audio := source["audio"]:
        notify(
            t(
                "preview.audio",
                index=audio["index"],
                language=safe_text(audio.get("tags", {}).get("language", "und")),
            )
        )
    if forced := source["forced"]:
        notify(t("preview.forced", index=forced["index"]))
    task = Task(destination)
    try:
        stem = convert(source, task.path, ffmpeg, ffprobe, burn_forced)
        return publish(task.path / "verified.mp4", destination, stem)
    finally:
        task.cleanup()
