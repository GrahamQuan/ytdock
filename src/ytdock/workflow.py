"""One suspended interaction flow per feature, with strictly serialized processing."""

from .controller import Cancelled
from .core import FormatExpired, UserError, youtube_url
from .i18n import t
from .path_utils import local_path


async def run_mode(screen, mode):
    from . import ui

    drafts = {"video": "", "captions": ""} if mode == "subtitles" else ""
    subtitle_video = None
    subtitle_settings = {}
    while True:
        action, text = await ui.read_input(mode, drafts)
        drafts = text
        if action in ("switch", "back"):
            screen.activate(
                "download"
                if action == "back"
                else ui.MODES[(ui.MODES.index(mode) + 1) % len(ui.MODES)]
            )
            continue
        if not text or (
            isinstance(text, dict) and not all(value.strip() for value in text.values())
        ):
            continue
        inputs = (
            text.copy()
            if isinstance(text, dict)
            else {"url" if mode == "download" else "video": text}
        )
        record = screen.begin(mode, inputs)

        async def busy(request):
            record.status = (
                "reading" if request["operation"].startswith("inspect") else "processing"
            )
            record.cleanup = None
            record.failure_stage = None
            record.stage = (
                "stage.fetching_information" if mode == "download" else "stage.reading_information"
            )
            return await ui.busy(screen.paths | request, screen.downloads, screen.lock)

        try:
            if mode == "download":
                url = youtube_url(text)
                while True:
                    info = await busy({"operation": "inspect", "url": url})
                    record.title, record.status = info["title"], "choosing"
                    if info["id"] != subtitle_video:
                        subtitle_video, subtitle_settings = info["id"], {}
                    selected = await ui.select(info, screen.downloads, subtitle_settings)
                    if selected is None:
                        result = None
                        break
                    try:
                        result = await busy(
                            {
                                "operation": "download",
                                "url": url,
                                "choice": selected.to_dict(),
                                "subtitles": subtitle_settings.get("subtitles", False),
                                "caption": info.get("caption"),
                            }
                        )
                        break
                    except FormatExpired as exc:
                        record.notices.append(str(exc))
                        screen.notices[mode] = str(exc)
                        screen.app.invalidate()
            elif mode == "compress":
                info = await busy({"operation": "inspect_local", "path": str(local_path(text))})
                record.title, record.status = info["title"], "choosing"
                profile = await ui.select_compression(info, screen.downloads)
                result = (
                    await busy({"operation": "compress", "source": info, "profile": profile})
                    if profile
                    else None
                )
            else:
                info = await busy(
                    {
                        "operation": "inspect_subtitles",
                        "path": str(local_path(text["video"])),
                        "captions_path": str(local_path(text["captions"])),
                    }
                )
                record.title, record.status = info["title"], "choosing"
                selected = await ui.select_local_subtitles(info, screen.downloads)
                result = (
                    await busy({"operation": "local_subtitles", "source": info})
                    if selected
                    else None
                )
            if result is None:
                record.finish("not_started")
            else:
                record.result = result
                record.saved.update(result.get("saved", {}))
                if result.get("path") and not record.saved:
                    record.saved["output"] = result["path"]
                record.finish("succeeded")
                drafts = {"video": "", "captions": ""} if mode == "subtitles" else ""
                if mode == "download":
                    subtitle_video, subtitle_settings = None, {}
        except Cancelled as exc:
            record.finish("cancelled", str(exc))
        except UserError as exc:
            record.finish("failed", str(exc))
        except OSError:
            record.finish(
                "failed",
                t("local_file_operation_failed_check_disk_space_and_downloads_permissions"),
            )
        screen.notices[mode] = record
        screen.app.invalidate()
