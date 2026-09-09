"""Discover compatible external FFmpeg/ffprobe pairs without changing the system."""

import os
import re
import subprocess
import sys
from pathlib import Path

from .core import UserError
from .i18n import t

HOMEBREW_DIRS = (
    "/opt/homebrew/bin",
    "/usr/local/bin",
    "/opt/homebrew/opt/ffmpeg/bin",
    "/usr/local/opt/ffmpeg/bin",
)
DECODERS = {
    "H.264": {"h264"},
    "VP8": {"vp8", "libvpx"},
    "VP9": {"vp9", "libvpx-vp9"},
    "AV1 software": {"libdav1d", "libaom-av1"},
    "AAC": {"aac", "aac_fixed"},
    "Opus": {"opus", "libopus"},
    "Vorbis": {"vorbis", "libvorbis"},
}
INSTALL_HINT = "install_a_complete_ffmpeg_build_including_ffprobe_for_example_brew"


def absolute(value):
    return Path(os.path.abspath(os.path.expanduser(str(value))))


def search_directories():
    seen = set()
    for value in [*os.get_exec_path(), *HOMEBREW_DIRS]:
        if not value:
            continue
        path = absolute(value)
        if str(path) not in seen:
            seen.add(str(path))
            yield path


def output(path: Path, *args, runner=None) -> str:
    try:
        result = (runner or subprocess.run)(
            [str(path), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=10,
            shell=False,
        )
    except subprocess.TimeoutExpired:
        raise UserError(t("execution_timed_out_10_seconds")) from None
    except OSError as exc:
        raise UserError(t("unable_to_execute", p0=exc.strerror or t("system_error"))) from None
    if result.returncode:
        raise UserError(
            t(
                "execution_failed_exit_code_possible_library_or_architecture_issue",
                p0=result.returncode,
            )
        )
    return result.stdout


def codec_names(text: str) -> set[str]:
    return set(re.findall(r"^\s*[VAS][A-Z.]{5}\s+(\S+)", text, re.MULTILINE))


def validate(path: Path, kind: str, runner=None):
    def read(*args):
        return output(path, *args, runner=runner) if runner else output(path, *args)

    if not path.is_file():
        raise UserError(t("executable_not_found"))
    if not os.access(path, os.X_OK):
        raise UserError(t("not_executable"))
    if not read("-version").startswith(kind + " version "):
        raise UserError(t("not_a_recognized_executable", p0=kind))
    decoders = codec_names(read("-hide_banner", "-decoders"))
    missing = [name for name, aliases in DECODERS.items() if not aliases & decoders]
    if missing:
        raise UserError(t("missing_decoders") + "、".join(missing))
    if kind == "ffmpeg":
        encoders = codec_names(read("-hide_banner", "-encoders"))
        missing = sorted({"libx264", "aac"} - encoders)
        if missing:
            raise UserError(t("missing_encoders") + "、".join(missing))
        if "-fps_mode" not in read("-hide_banner", "-h", "full"):
            raise UserError(t("missing_fps_mode_support_use_a_newer_complete_ffmpeg_build"))


def find_media_tools(
    directory=None, ffmpeg=None, ffprobe=None, warn=None, progress=None, runner=None
) -> dict:
    # Command-line choices override environment choices as a group.
    if not any((directory, ffmpeg, ffprobe)):
        directory = os.environ.get("YTDOCK_FFMPEG_DIR")
        ffmpeg = os.environ.get("YTDOCK_FFMPEG")
        ffprobe = os.environ.get("YTDOCK_FFPROBE")
    explicit = bool(directory or ffmpeg or ffprobe)
    pairs = []
    if ffmpeg or ffprobe:
        base = absolute(directory) if directory else absolute(ffmpeg or ffprobe).parent
        pairs.append(
            (
                absolute(ffmpeg) if ffmpeg else base / "ffmpeg",
                absolute(ffprobe) if ffprobe else base / "ffprobe",
                True,
            )
        )
    if directory:
        base = absolute(directory)
        pairs.append((base / "ffmpeg", base / "ffprobe", True))
    automatic = [
        (d / "ffmpeg", d / "ffprobe", False)
        for d in search_directories()
        if (d / "ffmpeg").exists() or (d / "ffprobe").exists()
    ]
    if ffmpeg and not ffprobe:
        pairs += [(absolute(ffmpeg), probe, True) for _, probe, _ in automatic]
    elif ffprobe and not ffmpeg:
        pairs += [(fm, absolute(ffprobe), True) for fm, _, _ in automatic]
    pairs += automatic
    valid = {"ffmpeg": [], "ffprobe": []}
    cache = {}
    errors, explicit_errors = [], []

    def checked(path, kind):
        key = (str(path.resolve()), kind)
        if key not in cache:
            try:
                if progress:
                    progress(kind, "checking")
                if runner:
                    validate(path, kind, runner=runner)
                else:
                    validate(path, kind)
                cache[key] = None
                valid[kind].append(path)
            except UserError as exc:
                cache[key] = f"{path}：{exc}"
                errors.append(cache[key])
        return cache[key]

    def success(fm, probe):
        if progress:
            progress("ffmpeg", "ready")
            progress("ffprobe", "ready")
        if explicit and explicit_errors:
            message = t(
                "the_specified_ffmpeg_candidate_is_unavailable_using_other_compatible_tools"
            ) + "\n".join(explicit_errors)
            (warn or (lambda text: print(text, file=sys.stderr)))(message)
        return {"ffmpeg": str(fm.resolve()), "ffprobe": str(probe.resolve())}

    for fm, probe, is_explicit in pairs:
        failures = [error for error in (checked(fm, "ffmpeg"), checked(probe, "ffprobe")) if error]
        if not failures:
            return success(fm, probe)
        if is_explicit:
            explicit_errors.extend(failures)
    # Same-directory pairs have priority. Only then pair individually verified tools.
    if valid["ffmpeg"] and valid["ffprobe"]:
        return success(valid["ffmpeg"][0], valid["ffprobe"][0])
    if progress:
        # A usable ffprobe must not be marked failed just because FFmpeg is missing
        # (or vice versa). Emit actual failed kinds last for the startup report.
        for kind in valid:
            if valid[kind]:
                progress(kind, "ready")
        for kind in valid:
            if not valid[kind]:
                progress(kind, "failed")
    details = (
        "\n".join(errors)
        if errors
        else t("no_executables_found_on_path_or_in_common_homebrew_locations")
    )
    raise UserError(
        t("no_compatible_external_ffmpeg_and_ffprobe_found") + details + "\n" + t(INSTALL_HINT)
    )


def post_install_check(**options):
    try:
        paths = find_media_tools(**options)
        print(
            t(
                "external_dependencies_ready_ffmpeg_ffprobe",
                p0=paths["ffmpeg"],
                p1=paths["ffprobe"],
            )
        )
    except UserError as exc:
        print(t("the_app_is_installed_but_cannot_process_videos_yet", p0=exc))
