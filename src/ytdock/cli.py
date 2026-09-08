"""Public entry point. Ordinary task errors return to the input form."""

import argparse
import asyncio
import sys

from .core import UserError
from .i18n import LANGUAGES, set_language, t


async def session(paths, downloads, lock):
    from .screen import run

    return await run(paths, downloads, lock)


def main():
    # Read the locale before constructing translated help and startup diagnostics.
    language_parser = argparse.ArgumentParser(add_help=False)
    language_parser.add_argument("--lang", choices=LANGUAGES, default="en")
    language, _ = language_parser.parse_known_args()
    set_language(language.lang)
    parser = argparse.ArgumentParser(
        prog="ytdock", description=t("youtube_downloads_and_local_video_compression_macos")
    )
    parser.add_argument(
        "--lang", choices=LANGUAGES, default="en", help=t("interface_language_default_en")
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=t("check_dependencies_and_the_system_downloads_directory_then_exit"),
    )
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument(
        "--install", action="store_true", help=t("install_the_release_package_for_the_current_user")
    )
    actions.add_argument(
        "--uninstall", action="store_true", help=t("uninstall_the_app_and_keep_your_videos")
    )
    parser.add_argument("--prefix", help=t("installation_prefix_default_local"))
    parser.add_argument("--ffmpeg-dir", help=t("preferred_directory_containing_ffmpeg_ffprobe"))
    parser.add_argument("--ffmpeg", help=t("preferred_ffmpeg_executable"))
    parser.add_argument("--ffprobe", help=t("preferred_ffprobe_executable"))
    args = parser.parse_args()
    media_options = dict(directory=args.ffmpeg_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    if args.prefix and not (args.install or args.uninstall):
        parser.error(t("prefix_is_only_available_for_installation_or_removal"))
    try:
        if args.install or args.uninstall:
            from .install import run

            return run("install" if args.install else "uninstall", args.prefix, media_options)
        from .dependencies import check
        from .storage import APP_ID, check_downloads, instance_lock, recover, system_directory

        paths = check(**media_options)
        downloads = system_directory("downloads")
        check_downloads(downloads)
        if args.check:
            print(t("dependencies_ready_downloads", p0=downloads))
            for name, path in paths.items():
                print(f"{name}：{path}")
            return 0
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise UserError(t("run_ytdock_in_an_interactive_terminal"))
        with instance_lock(system_directory("support") / APP_ID) as lock:
            for path in recover(downloads):
                print(t("unable_to_safely_clean_up_an_old_task_check_the", p0=path))
            return asyncio.run(session(paths, downloads, lock))
    except (KeyboardInterrupt, EOFError):
        print(t("exited"))
        return 130
    except UserError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except OSError:
        print(
            t("local_file_operation_failed_check_disk_space_and_directory_permissions"),
            file=sys.stderr,
        )
        return 1
    except Exception:
        print(t("the_app_failed_to_run_check_its_dependencies_and_retry"), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
