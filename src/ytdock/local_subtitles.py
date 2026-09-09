"""Local UTF-8 SRT validation and burning without translation or resegmentation."""

import re
from pathlib import Path

from .compression import identity, unchanged
from .compression import inspect as inspect_video
from .core import UserError, filename
from .i18n import t
from .subtitles import (
    TIMING_LINE_RE,
    SubtitleCue,
    bound_timeline,
    duration_milliseconds,
    format_srt_timestamp,
    parse_subtitle_timestamp,
)


def prepare(path, target, duration, emit):
    if path.suffix.lower() != ".srt":
        raise UserError(t("local_subtitles.invalid"))
    before = identity(path)
    if before[2] > 20_000_000:
        raise UserError(t("local_subtitles.invalid"))
    try:
        content = path.read_text(encoding="utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except (UnicodeError, OSError):
        raise UserError(t("local_subtitles.invalid")) from None
    blocks = re.split(r"\n[ \t]*\n", content.strip())
    cues = []
    previous = 0
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3 or not lines[0].strip().isdigit():
            raise UserError(t("local_subtitles.invalid"))
        timing = TIMING_LINE_RE.fullmatch(lines[1].strip())
        text = "\n".join(lines[2:])
        if (
            not timing
            or not text.strip()
            or "{\\" in text
            or any(ord(c) < 32 and c != "\n" for c in text)
        ):
            raise UserError(t("local_subtitles.invalid"))
        start, end = (
            parse_subtitle_timestamp(timing["start"]),
            parse_subtitle_timestamp(timing["end"]),
        )
        if start < previous:
            raise UserError(t("local_subtitles.overlap"))
        cues.append(SubtitleCue(start, end, text))
        previous = end
    last_end = max((cue.end_ms for cue in cues), default=0)
    adjusted = bound_timeline(cues, duration_milliseconds(duration))
    shortened_ms = max(0, last_end - duration_milliseconds(duration))
    target.write_text(
        "\n\n".join(
            f"{i}\n{format_srt_timestamp(c.start_ms)} --> "
            f"{format_srt_timestamp(c.end_ms)}\n{c.text}"
            for i, c in enumerate(cues, 1)
        )
        + "\n",
        encoding="utf-8",
    )
    if identity(path) != before:
        raise UserError(t("the_source_has_changed_or_is_unreadable_enter_it_again"))
    if adjusted:
        emit(
            {
                "notice": t(
                    "subtitle.tail_adjusted",
                    count=adjusted,
                    milliseconds=shortened_ms,
                    duration=format_srt_timestamp(duration_milliseconds(duration)),
                )
            }
        )
    return {
        "path": str(path),
        "identity": before,
        "count": len(cues),
        "adjusted": adjusted,
        "shortened_ms": shortened_ms,
    }


def preflight(directory, info, ffmpeg, ffprobe, pass_fds=()):
    from .burn import capabilities, render_sample

    capabilities(ffmpeg, ffprobe, directory, pass_fds)
    blocks = (directory / "captions.srt").read_text().strip().split("\n\n")
    # Every distinct cue is checked, including CJK, multiline text and long translated lines.
    for text in dict.fromkeys("\n".join(b.splitlines()[2:]) for b in blocks):
        if not render_sample(
            directory,
            text,
            info["choice"]["width"],
            info["choice"]["height"],
            ffmpeg,
            16,
            pass_fds,
            strict=True,
        ):
            raise UserError(t("local_subtitles.overflow"))


def inspect(video, captions, directory, ffmpeg, ffprobe, emit, pass_fds=()):
    info = inspect_video(video, ffprobe, pass_fds)
    info["captions"] = prepare(captions, directory / "captions.srt", info["duration"], emit)
    preflight(directory, info, ffmpeg, ffprobe, pass_fds)
    unchanged(info)
    unchanged(info["captions"])
    return info


def process(info, directory, ffmpeg, ffprobe, emit, pass_fds=()):
    from .burn import burn

    unchanged(info)
    unchanged(info["captions"])
    prepare(Path(info["captions"]["path"]), directory / "captions.srt", info["duration"], emit)
    preflight(directory, info, ffmpeg, ffprobe, pass_fds)
    burn(directory, info["choice"], info["duration"], ffmpeg, ffprobe, emit, pass_fds, local=info)
    unchanged(info)
    unchanged(info["captions"])
    base = filename(Path(info["path"]).stem, "local").removesuffix(" [local]")
    return {"stem": base + "_with-subtitle"}
