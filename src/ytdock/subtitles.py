"""Original-language caption selection and reference single-line segmentation.

Segmentation adapted from download-yt-video-with-subtitles/scripts/
download_youtube_with_subtitles.py; application lifecycle stays in YTDock.
"""

import html
import re
import time
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .core import UserError, language_code, language_matches, original_language
from .i18n import t

HTML_TAG_RE = re.compile(r"<[^>]+>")
ASS_OVERRIDE_RE = re.compile(r"\{\\[^}]+\}")
TIMING_LINE_RE = re.compile(
    r"^\s*(?P<start>(?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{2,3})"
    r"\s+-->\s+(?P<end>(?:\d{1,3}:)?\d{2}:\d{2}[.,]\d{2,3})(?=\s|$)"
)


TAIL_TOLERANCE_MS = 2000


class DownloadError(UserError):
    def __init__(self, *_args, key="subtitle.segmentation_failed", **params):
        super().__init__(t(key, **params))


@dataclass
class SubtitleCue:
    start_ms: int
    end_ms: int
    text: str


def clean_subtitle_text(value: str) -> str:
    value = html.unescape(value)
    value = HTML_TAG_RE.sub("", value)
    value = ASS_OVERRIDE_RE.sub("", value)
    value = value.replace("\\N", " ").replace("\\n", " ")
    return re.sub(r"\s+", " ", value).strip()


def parse_subtitle_timestamp(value: str) -> int:
    parts = value.strip().replace(",", ".").split(":")
    if len(parts) == 2:
        hours = 0
        minutes, seconds = parts
    elif len(parts) == 3:
        hours, minutes, seconds = parts
    else:
        raise DownloadError(key="subtitle.timestamp_invalid")
    try:
        whole, fraction = seconds.split(".")
        if (
            int(hours) < 0
            or not 0 <= int(minutes) < 60
            or not 0 <= int(whole) < 60
            or len(fraction) not in (2, 3)
            or not fraction.isdigit()
        ):
            raise ValueError
        return (int(hours) * 3600 + int(minutes) * 60 + int(whole)) * 1000 + int(
            fraction.ljust(3, "0")
        )
    except ValueError as exc:
        raise DownloadError(key="subtitle.timestamp_invalid") from exc


def format_srt_timestamp(milliseconds: int) -> str:
    milliseconds = max(0, milliseconds)
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"


def parse_subtitle_cues(subtitle_path: Path, *, normalize=True) -> list[SubtitleCue]:
    try:
        content = subtitle_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise DownloadError(f"Could not read subtitle file: {exc}") from exc

    content = content.replace("\r\n", "\n").replace("\r", "\n")
    cues: list[SubtitleCue] = []

    if subtitle_path.suffix.lower() == ".ass":
        for raw_line in content.splitlines():
            if not raw_line.startswith("Dialogue:"):
                continue
            fields = raw_line.split(",", 9)
            if len(fields) == 10:
                text = clean_subtitle_text(fields[9])
                if text:
                    cues.append(
                        SubtitleCue(
                            parse_subtitle_timestamp(fields[1]),
                            parse_subtitle_timestamp(fields[2]),
                            text,
                        )
                    )
    else:
        for block in re.split(r"\n\s*\n", content):
            block_lines = [line.strip() for line in block.splitlines()]
            timing_index = -1
            timing_match: re.Match[str] | None = None
            for index, line in enumerate(block_lines):
                match = TIMING_LINE_RE.match(line)
                if "-->" in line and not match:
                    raise DownloadError(key="subtitle.timestamp_invalid")
                if match:
                    timing_index = index
                    timing_match = match
                    break
            if timing_match is None:
                continue
            text = clean_subtitle_text(" ".join(block_lines[timing_index + 1 :]))
            if text:
                cues.append(
                    SubtitleCue(
                        parse_subtitle_timestamp(timing_match.group("start")),
                        parse_subtitle_timestamp(timing_match.group("end")),
                        text,
                    )
                )

    if not cues:
        raise DownloadError(key="subtitle.empty")
    return normalize_subtitle_timeline(cues) if normalize else cues


def normalize_subtitle_timeline(
    cues: list[SubtitleCue],
) -> list[SubtitleCue]:
    normalized: list[SubtitleCue] = []
    for cue in sorted(cues, key=lambda item: (item.start_ms, item.end_ms)):
        if cue.end_ms <= cue.start_ms or cue.start_ms < 0:
            raise DownloadError(key="subtitle.timestamp_invalid")
        if not cue.text:
            continue
        current = SubtitleCue(cue.start_ms, cue.end_ms, cue.text)
        if (
            normalized
            and current.text == normalized[-1].text
            and current.start_ms <= normalized[-1].end_ms
        ):
            normalized[-1].end_ms = max(
                normalized[-1].end_ms,
                current.end_ms,
            )
            continue
        if normalized and current.start_ms < normalized[-1].end_ms:
            normalized[-1].end_ms = current.start_ms
            if normalized[-1].end_ms <= normalized[-1].start_ms:
                raise DownloadError()
        normalized.append(current)
    return normalized


def split_caption_text(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for word in text.split():
        pieces = [word[index : index + max_chars] for index in range(0, len(word), max_chars)]
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


def resegment_subtitle_cues(
    cues: list[SubtitleCue],
    max_chars: int,
) -> list[SubtitleCue]:
    segmented: list[SubtitleCue] = []
    for cue in cues:
        chunks = split_caption_text(cue.text, max_chars)
        if not chunks:
            continue
        duration = cue.end_ms - cue.start_ms
        weights = [max(1, len(chunk)) for chunk in chunks]
        total_weight = sum(weights)
        elapsed_weight = 0
        segment_start = cue.start_ms
        for index, (chunk, weight) in enumerate(zip(chunks, weights)):
            elapsed_weight += weight
            if index == len(chunks) - 1:
                segment_end = cue.end_ms
            else:
                segment_end = (
                    cue.start_ms + (duration * elapsed_weight + total_weight // 2) // total_weight
                )
            if segment_end <= segment_start:
                segment_end = min(cue.end_ms, segment_start + 1)
            if segment_end > segment_start:
                segmented.append(SubtitleCue(segment_start, segment_end, chunk))
            segment_start = segment_end
    if not segmented:
        raise DownloadError("The subtitle could not be split into timed cues.")
    return segmented


def caption_track(info):
    """Match original audio language; never use YouTube translation tracks."""
    language = original_language(info)
    if not language:
        # Metadata without audio language can still identify a single native caption language.
        native = {
            language_code(code)
            for key in ("automatic_captions",)
            for code, formats in (info.get(key) or {}).items()
            if language_code(code) and caption_format(formats, code)
        }
        if len({code.split("-")[0] for code in native}) != 1:
            return None
        language = sorted(native)[0]
    for kind, key in (("manual", "subtitles"), ("automatic", "automatic_captions")):
        tracks = info.get(key) or {}
        for code in sorted(tracks, key=lambda code: (language_code(code) != language, code)):
            if language_matches(code, language) and caption_format(tracks[code], language):
                return {"kind": kind, "language": code}
    return None


def caption_format(formats, language=None):
    for extension in ("srt", "vtt"):
        for fmt in formats:
            url = fmt.get("url", "")
            query = parse_qs(urlsplit(url).query, keep_blank_values=True)
            original = query.get("lang", [""])[0]
            if (
                fmt.get("ext") == extension
                and urlsplit(url).scheme == "https"
                and "tlang" not in query
                and (not original or not language or language_matches(original, language))
            ):
                return fmt
    return None


def download_caption(info, track, directory, quickjs, emit):
    from yt_dlp import YoutubeDL
    from yt_dlp.networking import Request

    from .worker import options

    key = "subtitles" if track["kind"] == "manual" else "automatic_captions"
    fmt = caption_format((info.get(key) or {}).get(track["language"], []), track["language"])
    if not fmt:
        raise UserError(t("subtitle.unavailable"))
    target = directory / ("original." + fmt["ext"])
    emit({"stage": "stage.downloading_subtitles"})
    # Bound caption size; yt-dlp's networking retains extractor headers without logging URLs.
    for attempt in range(3):
        try:
            with YoutubeDL(options(quickjs)) as ydl:
                with ydl.urlopen(
                    Request(fmt["url"], headers=info.get("http_headers") or {})
                ) as response:
                    with target.open("wb") as output:
                        count = 0
                        while chunk := response.read(65536):
                            count += len(chunk)
                            if count > 20_000_000:
                                raise UserError(t("subtitle.invalid"))
                            output.write(chunk)
            return target
        except UserError:
            raise
        except OSError as exc:
            if exc.errno == 28:
                raise UserError(t("not_enough_disk_space")) from None
            if attempt == 2:
                raise UserError(t("subtitle.download_failed")) from None
        except Exception:
            if attempt == 2:
                raise UserError(t("subtitle.download_failed")) from None
        time.sleep(attempt + 1)


def duration_milliseconds(duration):
    try:
        value = Decimal(str(duration))
        if not value.is_finite() or value <= 0:
            raise ValueError
        milliseconds = int((value * 1000).to_integral_value())
        if milliseconds <= 0:
            raise ValueError
        return milliseconds
    except (InvalidOperation, ValueError, OverflowError):
        raise DownloadError(key="subtitle.timestamp_invalid") from None


def bound_timeline(cues, duration_ms):
    adjusted = 0
    for cue in cues:
        if cue.start_ms < 0 or cue.end_ms <= cue.start_ms:
            raise DownloadError(
                key="subtitle.range_invalid",
                start=format_srt_timestamp(cue.start_ms),
                end=format_srt_timestamp(cue.end_ms),
                duration=format_srt_timestamp(duration_ms),
            )
        if cue.start_ms >= duration_ms or cue.end_ms > duration_ms + TAIL_TOLERANCE_MS:
            raise DownloadError(
                key="subtitle.out_of_bounds",
                start=format_srt_timestamp(cue.start_ms),
                end=format_srt_timestamp(cue.end_ms),
                duration=format_srt_timestamp(duration_ms),
            )
        if cue.end_ms > duration_ms:
            cue.end_ms = duration_ms
            adjusted += 1
    return adjusted


def prepare(source, target, duration, emit=None):
    try:
        original = parse_subtitle_cues(source, normalize=False)
        duration_ms = duration_milliseconds(duration)
        adjusted = bound_timeline(original, duration_ms)
        original = normalize_subtitle_timeline(original)
        cues = resegment_subtitle_cues(original, 42)
        # Millisecond rounding must not silently discard subtitle words.
        if "".join(c.text for c in cues).replace(" ", "") != "".join(
            c.text for c in original
        ).replace(" ", ""):
            raise DownloadError()
        target.write_text(
            "\n\n".join(
                f"{i}\n{format_srt_timestamp(c.start_ms)} --> "
                f"{format_srt_timestamp(c.end_ms)}\n{c.text}"
                for i, c in enumerate(cues, 1)
            )
            + "\n",
            encoding="utf-8",
        )
        validate_srt(target, duration)
        if adjusted and emit:
            emit(
                {
                    "notice": t(
                        "subtitle.tail_adjusted",
                        count=adjusted,
                        duration=format_srt_timestamp(duration_ms),
                    )
                }
            )
        return adjusted
    except (UnicodeError, ValueError):
        raise DownloadError(key="subtitle.timestamp_invalid") from None


def validate_srt(path, duration):
    content = path.read_text(encoding="utf-8")
    if not content.strip():
        raise DownloadError(key="subtitle.empty")
    duration_ms = duration_milliseconds(duration)
    blocks = content.strip().split("\n\n")
    end = 0
    for index, block in enumerate(blocks, 1):
        lines = block.splitlines()
        if len(lines) != 3 or lines[0] != str(index):
            raise DownloadError()
        timing = TIMING_LINE_RE.fullmatch(lines[1])
        if not timing or not lines[2].strip() or len(lines[2]) > 42:
            raise DownloadError()
        start = parse_subtitle_timestamp(timing["start"])
        stop = parse_subtitle_timestamp(timing["end"])
        if start < end or stop <= start or stop > duration_ms:
            raise DownloadError()
        if clean_subtitle_text(lines[2]) != lines[2]:
            raise DownloadError()
        end = stop
    return blocks
