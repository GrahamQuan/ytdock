"""HEVC subtitle rendering, capability checks, progress and verification."""

import subprocess
import time
from pathlib import Path

from .core import UserError
from .ffmpeg import codec_names, output
from .i18n import t
from .media import probe, run, verify, video_stream
from .subtitles import validate_srt

STYLE = (
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BorderStyle=1,"
    "Outline=0.7,Shadow=0,WrapStyle=2,FontName=Arial"
)


def subtitle_filter(path, font_size=16):
    # Use fixed relative filenames in the private task cwd; user directory text never
    # enters FFmpeg's filter parser (which has multiple independent escaping layers).
    if path.name not in ("render-check.srt", "captions.srt"):
        raise ValueError("Unexpected subtitle filename")
    return f"subtitles=filename='{path.name}':force_style='{STYLE},FontSize={font_size:g}'"


def render_sample(directory, text, width, height, ffmpeg, font_size, pass_fds=(), *, strict=False):
    sample = directory / "render-check.srt"
    sample.write_text(f"1\n00:00:00,000 --> 00:00:01,000\n{text}\n", encoding="utf-8")
    args = [
        ffmpeg,
        "-v",
        "info" if strict else "error",
        "-nostdin",
        "-f",
        "lavfi",
        "-i",
        f"color=black:s={width}x{height}:d=1",
        "-vf",
        subtitle_filter(sample, font_size),
        "-frames:v",
        "1",
        "-pix_fmt",
        "gray",
        "-f",
        "rawvideo",
        "-",
    ]
    result = subprocess.run(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        pass_fds=pass_fds,
        cwd=directory,
    )
    if result.returncode or (
        strict
        and (
            b"failed to find any fallback" in result.stderr.lower()
            or b"fontselect: failed" in result.stderr.lower()
        )
    ):
        raise UserError(t("local_subtitles.glyphs") if strict else t("subtitle.render_failed"))
    pixels = result.stdout

    bright = [i for i, value in enumerate(pixels) if value > 100]
    if len(pixels) != width * height or not bright:
        raise UserError(t("local_subtitles.glyphs") if strict else t("subtitle.render_failed"))
    xs = [i % width for i in bright]
    ys = [i // width for i in bright]
    return min(xs) > 1 and max(xs) < width - 2 and min(ys) > 1 and max(ys) < height - 2


def capabilities(ffmpeg, ffprobe, directory, pass_fds=()):
    if "libx265" not in codec_names(output(Path(ffmpeg), "-hide_banner", "-encoders")):
        raise UserError(t("subtitle.missing_encoder"))
    for tool in (ffmpeg, ffprobe):
        if "hevc" not in codec_names(output(Path(tool), "-hide_banner", "-decoders")):
            raise UserError(t("subtitle.missing_decoder"))
    try:
        if not render_sample(directory, "English subtitles", 320, 180, ffmpeg, 16, pass_fds):
            raise UserError(t("subtitle.render_failed"))
    except UserError:
        raise UserError(t("subtitle.render_failed")) from None


def burn(directory, choice, duration, ffmpeg, ffprobe, emit, pass_fds=(), *, local=None):
    captions = directory / "captions.srt"
    blocks = (
        captions.read_text().strip().split("\n\n") if local else validate_srt(captions, duration)
    )
    # Inspect representative longest text on a black frame, including narrow portrait frames.
    font_size = 16.0
    longest = max(("\n".join(b.splitlines()[2:]) for b in blocks), key=len)
    for _ in range(1 if local else 8):
        if render_sample(
            directory, longest, choice["width"], choice["height"], ffmpeg, font_size, pass_fds
        ):
            break
        font_size *= 0.8
    else:
        raise UserError(t("subtitle.render_failed"))
    source = Path(local["path"]) if local else directory / "verified.mp4"
    output_path = directory / "subtitled.mp4"
    source_stream = local["video"] if local else video_stream(probe(source, ffprobe, pass_fds))
    args = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-nostdin",
        "-n",
        "-noautorotate",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-map_metadata",
        "-1",
        "-map_chapters",
        "-1",
        "-vf",
        subtitle_filter(captions, font_size),
        "-c:v",
        "libx265",
        "-crf",
        "27",
        "-preset",
        "medium",
        "-tag:v",
        "hvc1",
        "-pix_fmt",
        "yuv420p",
        "-fps_mode",
        "passthrough",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        "-f",
        "mp4",
        "-progress",
        "pipe:1",
        "-nostats",
        str(output_path),
    ]
    if local:
        from .compression import FORMATS

        input_at = args.index("-i")
        args[input_at:input_at] = ["-protocol_whitelist", "file,pipe", "-format_whitelist", FORMATS]
        args[args.index("0:v:0")] = f"0:{local['video']['index']}"
        audio = local["audio"]
        if audio is None:
            index = args.index("0:a?")
            del args[index - 1 : index + 1]
        else:
            args[args.index("0:a?")] = f"0:{audio['index']}"
            if audio.get("codec_name") != "aac":
                args[args.index("-c:a") + 1] = "aac"
                args[-1:-1] = ["-b:a", "192k"]
        if source_stream.get("color_range") == "pc":
            args[args.index("-vf") + 1] = "scale=in_range=pc:out_range=tv," + subtitle_filter(
                captions, font_size
            )
        for key, flag in (
            ("color_range", "-color_range"),
            ("color_space", "-colorspace"),
            ("color_transfer", "-color_trc"),
            ("color_primaries", "-color_primaries"),
        ):
            value = source_stream.get(key)
            if value not in (None, "unknown"):
                args[-1:-1] = [flag, "tv" if key == "color_range" else value]
    emit({"stage": "stage.burning_subtitles"})
    started = last = time.monotonic()
    with (directory / "burn-error.log").open("wb") as error:
        with subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=error,
            text=True,
            shell=False,
            pass_fds=pass_fds,
            cwd=directory,
        ) as process:
            for line in process.stdout:
                now = time.monotonic()
                if line.startswith("out_time_us=") and now - last >= 0.1:
                    try:
                        done = max(0, int(line.split("=", 1)[1])) / 1_000_000
                        event = {
                            "stage": "stage.burning_subtitles",
                            "done": done,
                            "total": duration,
                        }
                        if done > 0:
                            event["eta"] = max(0, (now - started) * (duration - done) / done)
                        emit(event)
                        last = now
                    except ValueError:
                        pass
            if process.wait():
                message = (directory / "burn-error.log").read_bytes()[-8192:]
                raise UserError(
                    t("not_enough_disk_space")
                    if b"No space left" in message
                    else t("subtitle.burn_failed")
                )
    emit({"stage": "stage.burning_subtitles", "finished": True})
    emit({"stage": "stage.verifying_subtitled"})
    verify(
        output_path,
        choice,
        duration,
        source_stream,
        ffmpeg,
        ffprobe,
        pass_fds,
        video_codec="hevc",
        video_tag="hvc1",
    )
    verify_rendered_frames(
        directory,
        choice,
        blocks,
        ffmpeg,
        font_size,
        pass_fds,
        source=source,
        source_index=source_stream.get("index", 0),
    )
    return {"font_size": font_size}


def verify_rendered_frames(
    directory, choice, blocks, ffmpeg, font_size, pass_fds=(), *, source=None, source_index=0
):
    """Compare decoded output samples against both plain and subtitle-rendered source frames."""
    import math

    from .compression import FORMATS
    from .subtitles import parse_subtitle_timestamp

    source = source or directory / "verified.mp4"
    samples = []
    for block in blocks:
        start, end = block.splitlines()[1].split(" --> ")
        start, end = parse_subtitle_timestamp(start) / 1000, parse_subtitle_timestamp(end) / 1000
        stamp = math.ceil(start * choice["fps"]) / choice["fps"] + 0.00001
        if stamp < end:
            samples.append(stamp)
    if not samples:
        raise UserError(t("subtitle.render_failed"))
    stamps = sorted({samples[0], samples[len(samples) // 2], samples[-1]})

    def frame(path, stamp, rendered=False):
        args = [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-ss",
            f"{stamp:.6f}",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            FORMATS,
            "-noautorotate",
            "-i",
            str(path),
        ]
        if rendered:
            args += ["-vf", subtitle_filter(directory / "captions.srt", font_size)]
        args += [
            "-map",
            f"0:{source_index}" if path == source else "0:v:0",
            "-frames:v",
            "1",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
        return run(args, pass_fds, cwd=directory)

    checked = 0
    for stamp in stamps:
        plain = frame(source, stamp)
        # Preserve subtitle times after input seeking; reset only after rendering.
        args = [
            ffmpeg,
            "-v",
            "error",
            "-nostdin",
            "-copyts",
            "-ss",
            f"{stamp:.6f}",
            "-protocol_whitelist",
            "file,pipe",
            "-format_whitelist",
            FORMATS,
            "-noautorotate",
            "-i",
            str(source),
            "-map",
            f"0:{source_index}",
            "-vf",
            subtitle_filter(directory / "captions.srt", font_size),
            "-frames:v",
            "1",
            "-pix_fmt",
            "gray",
            "-f",
            "rawvideo",
            "-",
        ]
        expected = run(args, pass_fds, cwd=directory)
        actual = frame(directory / "subtitled.mp4", stamp)
        if not (len(plain) == len(expected) == len(actual) == choice["width"] * choice["height"]):
            raise UserError(t("subtitle.render_failed"))
        mask = [i for i, (a, b) in enumerate(zip(plain, expected)) if abs(a - b) > 40]
        if not mask:
            continue
        rendered_error = sum(abs(actual[i] - expected[i]) for i in mask)
        plain_error = sum(abs(actual[i] - plain[i]) for i in mask)
        if rendered_error >= plain_error:
            raise UserError(t("subtitle.render_failed"))
        checked += 1
    if not checked:
        raise UserError(t("subtitle.render_failed"))
