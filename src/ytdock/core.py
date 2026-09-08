"""Pure selection, validation and presentation contracts."""

import math
import re
import unicodedata
from dataclasses import asdict, dataclass
from urllib.parse import parse_qs, urlparse

from .i18n import t


class UserError(Exception):
    """An intentionally safe, user-facing error; never wrap raw provider messages."""


class FormatExpired(UserError):
    pass


def youtube_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise UserError(t("paste_a_youtube_video_url"))
    if len(value.split()) != 1 or len(re.findall(r"https?://", value)) > 1:
        raise UserError(t("only_one_video_url_is_supported_at_a_time"))
    try:
        parsed = urlparse(value)
        if parsed.scheme not in ("https", "http") or parsed.username or parsed.password:
            raise ValueError
        if parsed.port not in (None, 80, 443):
            raise ValueError
        host = (parsed.hostname or "").lower()
        parts = parsed.path.strip("/").split("/")
        if host in ("youtu.be", "www.youtu.be") and len(parts) == 1:
            video_id = parts[0]
        elif host in ("youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"):
            if parsed.path == "/watch":
                ids = parse_qs(parsed.query).get("v", [])
                if len(ids) != 1:
                    raise ValueError
                video_id = ids[0]
            elif len(parts) == 2 and parts[0] in ("shorts", "embed"):
                video_id = parts[1]
            else:
                raise ValueError
        else:
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id):
            raise ValueError
    except ValueError:
        raise UserError(t("invalid_or_unsupported_url_use_a_single_youtube_video_link")) from None
    return f"https://www.youtube.com/watch?v={video_id}"


def number(value, default=0.0):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else default
    except (TypeError, ValueError):
        return default


def percent(done: float, total: float | None, finished=False) -> str | None:
    if finished:
        return "100%"
    if not total or total <= 0:
        return None
    if done == 0:
        return "0%"
    return f"{min(99.9, max(0, done / total * 100)):.1f}%"


def size(value: float | None) -> str:
    if value is None:
        return t("size_unknown")
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1000 or unit == "TB":
            return f"{value:.1f} {unit}"
        value /= 1000
    raise AssertionError


def clock(value: float) -> str:
    seconds = int(max(0, value))
    return (
        f"{seconds // 3600}:{seconds % 3600 // 60:02}:{seconds % 60:02}"
        if seconds >= 3600
        else f"{seconds // 60:02}:{seconds % 60:02}"
    )


def safe_text(text: str) -> str:
    return "".join(c for c in str(text) if not unicodedata.category(c).startswith("C"))


def filename(title: str, video_id: str) -> str:
    title = re.sub(r'[<>:"/\\|?*]', "_", safe_text(title)).replace("..", "_").strip(" .")
    title = title or "YouTube video"
    while len(title.encode("utf-8")) > 180:
        title = title[:-1]
    video_id = re.sub(r"[^A-Za-z0-9_-]", "", video_id)[:32] or "video"
    return f"{title} [{video_id}]"


def compatible_video(fmt: dict) -> bool:
    codec = fmt.get("vcodec") or ""
    pixel = fmt.get("pix_fmt")
    # YouTube's avc1 baseline/main/high profiles use 8-bit 4:2:0. Other profiles
    # and uncertain metadata are conservatively labelled for transcoding.
    avc420 = bool(re.match(r"avc1\.(42|4d|58|64)", codec, re.I))
    return (codec == "h264" or avc420) and (pixel == "yuv420p" or (pixel is None and avc420))


def compatible_audio(fmt: dict) -> bool:
    return (fmt.get("acodec") or "").startswith(("mp4a", "aac"))


def language_code(value):
    value = str(value or "").lower().removesuffix("-orig")
    return value if re.fullmatch(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*", value) else None


def language_matches(left, right):
    left, right = language_code(left), language_code(right)
    return bool(left and right and left.split("-")[0] == right.split("-")[0])


def is_dubbed(fmt):
    return "dubbed" in str(fmt.get("format_note", "")).lower()


def audio_preference(fmt):
    try:
        value = float(fmt.get("language_preference", 0))
        return value if math.isfinite(value) else 0
    except (TypeError, ValueError):
        return 0


def audio_original_mark(fmt):
    return audio_preference(fmt) >= 10 or bool(
        re.search(r"\boriginal\b", str(fmt.get("format_note", "")), re.I)
    )


def audio_groups(info):
    """Identify tracks before ranking encodings; absent preference is not a dub flag."""
    groups = {}
    for fmt in info.get("formats", []):
        if fmt.get("acodec") in (None, "none"):
            continue
        language = language_code(fmt.get("language"))
        raw_track = fmt.get("audioTrack") or {}
        identity = fmt.get("audio_track_id") or raw_track.get("id")
        # yt-dlp retains displayName in format_note, but not the raw track ID.
        # Remove only known encoding/role annotations, never arbitrary track names.
        note = str(fmt.get("format_note") or "").lower()
        label = re.sub(r"\b(original|default|dubbed-auto|dubbed|drc)\b", "", note)
        label = re.sub(r"\b(low|medium|high|tiny)\b", "", label)
        label = re.sub(r"[(),\s]+", " ", label).strip()
        if (
            not identity
            and not label
            and re.fullmatch(r"\d+(?:-drc)?-\d+", str(fmt.get("format_id") or ""))
        ):
            raise UserError(t("audio.identity_ambiguous"))
        key = ("id", str(identity)) if identity else ("metadata", language, label)
        groups.setdefault(key, []).append(fmt)
    for key, formats in groups.items():
        languages = {language_code(f.get("language")) for f in formats}
        if len(languages) > 1 or (
            any(audio_original_mark(f) for f in formats) and any(is_dubbed(f) for f in formats)
        ):
            raise UserError(t("audio.identity_ambiguous"))
        if key[0] == "metadata":
            # Repeated versions with no track identity can hide distinct same-language
            # tracks. Do not infer a shared identity from language alone in that case.
            versions = set()
            for fmt in formats:
                format_id = str(fmt.get("format_id") or "")
                itag = re.fullmatch(r"(\d+)(?:-drc)?(?:-\d+)?", format_id)
                version = (
                    ("itag", itag[1], "drc" in format_id)
                    if itag
                    else (
                        fmt.get("acodec"),
                        fmt.get("abr"),
                        fmt.get("audio_channels"),
                        fmt.get("vcodec") not in (None, "none"),
                    )
                )
                if version in versions:
                    raise UserError(t("audio.identity_ambiguous"))
                versions.add(version)
    return list(groups.values())


def explicit_original_language(info):
    # YoutubeDL's final `language` can be copied from the selected/default format.
    # Only extractor-level language captured before format selection is trusted.
    return language_code(info.get("original_language") or info.get("_ytdock_original_language"))


def select_audio(info):
    groups = audio_groups(info)
    if not groups:
        return [], False
    marked = [g for g in groups if any(audio_original_mark(f) for f in g)]
    if len(marked) > 1:
        raise UserError(t("audio.multiple_candidates"))
    if marked:
        language = explicit_original_language(info)
        track_language = language_code(marked[0][0].get("language"))
        if language and track_language and not language_matches(language, track_language):
            raise UserError(t("audio.identity_ambiguous"))
        return marked[0], True
    eligible = [g for g in groups if not any(is_dubbed(f) for f in g)]
    if len(groups) == 1:
        if not eligible:
            raise UserError(t("audio.no_match"))
        return eligible[0], False
    language = explicit_original_language(info)
    if not language:
        raise UserError(t("audio.original_unknown"))
    matching = [g for g in eligible if language_matches(g[0].get("language"), language)]
    if not matching:
        raise UserError(t("audio.no_match"))
    if len(matching) != 1:
        raise UserError(t("audio.multiple_candidates"))
    return matching[0], True


def original_audio_formats(info):
    return select_audio(info)[0]


def original_language(info):
    tracks, confirmed = select_audio(info)
    if confirmed:
        return language_code(tracks[0].get("language")) or explicit_original_language(info)
    return explicit_original_language(info)


@dataclass(frozen=True)
class Choice:
    video_id: str
    audio_id: str | None
    width: int
    height: int
    fps: float
    transcode: bool
    bytes: float | None
    approximate: bool
    has_audio: bool

    @property
    def key(self):
        return self.width, self.height, self.fps

    @property
    def resolution(self):
        return min(self.width, self.height)

    def label(self):
        estimate = (t("approx") if self.approximate else "") + size(self.bytes)
        convert = t("transcoding.required") if self.transcode else ""
        return (
            f"{self.resolution}p · {self.width}×{self.height} · "
            f"{self.fps:g}fps · {estimate}{convert}"
        )

    def to_dict(self):
        return asdict(self)


def choices(info: dict) -> list[Choice]:
    if (
        info.get("is_live")
        or info.get("is_upcoming")
        or info.get("live_status") in ("is_live", "is_upcoming", "post_live")
    ):
        raise UserError(t("live_streams_streams_still_being_processed_and_upcoming_premieres_are"))
    if info.get("_type", "video") != "video" or not number(info.get("duration")):
        raise UserError(t("unable_to_determine_a_valid_single_video_duration_this_video"))
    formats = [
        f
        for f in info.get("formats", [])
        if f.get("url")
        and f.get("format_id")
        and not f.get("has_drm")
        and f.get("protocol") in ("https", "http", "m3u8_native", "http_dash_segments")
    ]
    audios = [
        f for f in formats if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")
    ]
    native = original_audio_formats(info)
    audios = [f for f in audios if f in native]
    audio = max(audios, key=lambda f: (compatible_audio(f), number(f.get("abr"))), default=None)
    source_has_audio = any(f.get("acodec") not in (None, "none") for f in info.get("formats", []))
    grouped = {}
    hdr_seen = False
    for f in formats:
        if f.get("vcodec") in (None, "none"):
            continue
        if f.get("dynamic_range") not in (None, "SDR"):
            hdr_seen = True
            continue
        if not (number(f.get("width")) and number(f.get("height")) and number(f.get("fps"))):
            continue
        # FFmpeg/libx264 yuv420p cannot preserve odd dimensions.
        w, h = int(f["width"]), int(f["height"])
        if w % 2 or h % 2:
            continue
        embedded = f.get("acodec") not in (None, "none")
        if embedded and f not in native:
            continue
        selected_audio = f if embedded else audio
        if source_has_audio and selected_audio is None:
            continue  # Never silently discard a source's audio.
        streams = [f] + ([audio] if not embedded and audio else [])
        sizes = [number(s.get("filesize") or s.get("filesize_approx")) for s in streams]
        option = Choice(
            str(f["format_id"]),
            str(audio["format_id"]) if not embedded and audio else None,
            w,
            h,
            float(f["fps"]),
            not compatible_video(f)
            or bool(selected_audio and not compatible_audio(selected_audio)),
            sum(sizes) if all(sizes) else None,
            any(not s.get("filesize") for s in streams) if all(sizes) else False,
            selected_audio is not None,
        )
        score = (not option.transcode, compatible_video(f), number(f.get("tbr")))
        if option.key not in grouped or score > grouped[option.key][0]:
            grouped[option.key] = (score, option)
    result = sorted(
        (x[1] for x in grouped.values()), key=lambda c: (c.resolution, c.fps, c.width), reverse=True
    )
    if not result:
        raise UserError(
            t("only_hdr_or_other_unsupported_formats_are_available_only_sdr")
            if hdr_seen
            else t("no_supported_downloadable_formats_are_available")
        )
    return result


def default_choice(options: list[Choice]) -> int:
    return next((i for i, c in enumerate(options) if c.resolution <= 1080), len(options) - 1)


def provider_error(exc: Exception) -> UserError:
    # Classify internally, never print provider text: it may contain signed URLs.
    message = str(exc).lower()
    if "no space" in message or "errno 28" in message:
        return UserError(t("not_enough_disk_space_free_up_space_and_retry"))
    if "403" in message or "410" in message or "requested format" in message:
        return FormatExpired(
            t("the_download_address_or_selected_format_has_expired_refreshing_the")
        )
    if any(
        x in message
        for x in ("private", "sign in", "login", "members-only", "age-restricted", "age restricted")
    ):
        return UserError(t("the_video_is_private_or_requires_login_login_and_cookies"))
    if any(x in message for x in ("country", "region", "geo")):
        return UserError(t("the_video_is_unavailable_in_your_region"))
    if any(x in message for x in ("unavailable", "removed", "not found", "404")):
        return UserError(t("the_video_does_not_exist_was_removed_or_is_unavailable"))
    if any(x in message for x in ("timeout", "timed out", "network", "connection", "resolve")):
        return UserError(t("the_connection_timed_out_or_was_interrupted_after_limited_retries"))
    return UserError(t("fetching_or_downloading_failed_check_your_connection_confirm_the_video"))
