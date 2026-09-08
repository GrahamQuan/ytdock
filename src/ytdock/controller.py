"""UI-owned task lifecycle. Publication occurs only after worker verification."""

import asyncio
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from .core import FormatExpired, UserError, filename
from .i18n import get_language, t
from .storage import Task, publish


class Cancelled(UserError):
    pass


def group_alive(pgid: int) -> bool:
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # macOS can return EPERM for an orphan group containing only zombies.
        # Zombies have closed their descriptors and cannot access task files.
        result = subprocess.run(
            ["/bin/ps", "-axo", "pgid=,stat="], capture_output=True, text=True, timeout=5
        )
        if result.returncode:
            return True
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 2 and fields[0] == str(pgid) and not fields[1].startswith("Z"):
                return True
        return False


async def stop_group(process, grace=2.0):
    # Even if the worker exits first, FFmpeg/QuickJS may remain in its session.
    for sig, delay in ((signal.SIGTERM, grace), (signal.SIGKILL, 3.0)):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        except PermissionError:
            if not group_alive(process.pid):
                break
            raise UserError(
                t("unable_to_stop_task_subprocesses_the_temporary_directory_will_be")
            ) from None
        deadline = asyncio.get_running_loop().time() + delay
        while group_alive(process.pid) and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.05)
        if not group_alive(process.pid):
            break
    await process.wait()
    if group_alive(process.pid):
        raise UserError(t("some_subprocesses_are_still_running_the_temporary_directory_was_kept"))


def worker_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--worker"]
    return [sys.executable, "-m", "ytdock.worker"]


async def execute(request: dict, downloads: Path, lock: int, cancel: asyncio.Event, update):
    task = Task(downloads)
    process = None
    saved = {}
    error = None
    result = None
    cleanup = None
    failure_stage = None

    async def invoke(operation):
        nonlocal process, failure_stage
        if cancel.is_set():
            raise Cancelled(t("cancelled_temporary_files_have_been_cleaned_up"))
        payload = operation | {
            "language": get_language(),
            "directory": str(task.path),
            "parent_pid": os.getpid(),
            "pass_fds": [lock, task.lease],
        }
        process = await asyncio.create_subprocess_exec(
            *worker_command(),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            pass_fds=(lock, task.lease),
            limit=1024 * 1024,
        )
        process.stdin.write((json.dumps(payload) + "\n").encode())
        await process.stdin.drain()
        process.stdin.close()
        result = None
        failure = None
        pending = asyncio.create_task(process.stdout.readline())
        try:
            while True:
                if cancel.is_set():
                    update({"stage": "stage.cancelling"})
                    raise Cancelled(t("cancelled_temporary_files_have_been_cleaned_up"))
                ready, _ = await asyncio.wait({pending}, timeout=0.1)
                if not ready:
                    continue
                line = pending.result()
                if not line:
                    break
                try:
                    event = json.loads(line)
                except (ValueError, UnicodeError):
                    raise UserError(t("worker_communication_failed")) from None
                if "result" in event:
                    result = event["result"]
                elif "error" in event:
                    failure = (FormatExpired if event.get("expired") else UserError)(event["error"])
                else:
                    if event.get("stage") and event["stage"] != "stage.cancelling":
                        failure_stage = event["stage"]
                    update(event)
                pending = asyncio.create_task(process.stdout.readline())
        finally:
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await process.wait()
        if failure:
            raise failure
        if process.returncode or result is None:
            raise UserError(t("the_worker_exited_unexpectedly_no_output_was_published"))
        if cancel.is_set():
            raise Cancelled(t("cancelled_temporary_files_have_been_cleaned_up"))
        await stop_group(process)
        process = None
        return result

    def save(kind, source, stem, extension=".mp4"):
        if cancel.is_set():
            raise Cancelled(t("cancelled_temporary_files_have_been_cleaned_up"))
        try:
            target = publish(task.path / source, downloads, stem, extension)
        except (UserError, OSError) as exc:
            detail = (
                t("not_enough_disk_space")
                if isinstance(exc, OSError) and exc.errno == 28
                else str(exc)
            )
            raise UserError(
                t("subtitle.save_failed", kind=t("subtitle.file_" + kind), error=detail)
            ) from None
        saved[kind] = str(target)
        update({"saved": {kind: str(target)}})
        return target

    try:
        result = await invoke(request)
        if request["operation"] == "local_subtitles":
            from .compression import unchanged

            unchanged(request["source"])
            unchanged(request["source"]["captions"])
            target = save("subtitled", "subtitled.mp4", result["stem"])
            result["path"] = str(target)
        if request["operation"] in ("download", "compress"):
            if request["operation"] == "compress":
                from .compression import output_name, unchanged

                unchanged(request["source"])
                stem = output_name(request["source"], request["profile"])
            else:
                stem = filename(result["title"], result["id"])
            target = save("source", "verified.mp4", stem)
            result["path"] = str(target)
            if request.get("subtitles"):
                # Use the actual source basename; each later publication remains exclusive.
                stem = target.stem
                from .core import language_code

                language = language_code(request["caption"]["language"])
                if not language:
                    raise UserError(t("subtitle.unavailable"))
                save("captions", "captions.srt", stem, f".{language}.srt")
                await invoke(
                    request | {"operation": "burn_subtitles", "duration": result["duration"]}
                )
                save("subtitled", "subtitled.mp4", stem + "_with-subtitle")
                result["saved"] = dict(saved)
    except BaseException as exc:
        error = exc
    finally:
        cleanup_allowed = True
        if process:
            try:
                await stop_group(process)
            except UserError:
                cleanup_allowed = False
                cleanup = {"ok": False, "residuals": [str(task.path)]}
                os.close(task.lease)
                task.lease = -1
                kind = Cancelled if cancel.is_set() else UserError
                error = kind(
                    t("some_subprocesses_are_still_running_temporary_directory_kept", p0=task.path)
                )
        if cleanup_allowed:
            try:
                task.cleanup()
                cleanup = {"ok": True, "residuals": []}
            except UserError as exc:
                cleanup = {"ok": False, "residuals": [str(task.path)]}
                error = Cancelled(t("cancelled_but", p0=exc)) if cancel.is_set() else exc
    update(
        {
            "task_outcome": {
                "status": "cancelled"
                if isinstance(error, Cancelled)
                else "failed"
                if error
                else "succeeded",
                "saved": dict(saved),
                "cleanup": cleanup,
                "failure_stage": failure_stage if error else None,
            }
        }
    )
    if error:
        if saved:
            retained = "\n" + t("subtitle.retained") + "\n" + "\n".join(saved.values())
            kind = Cancelled if isinstance(error, Cancelled) else UserError
            raise kind(str(error) + retained) from None
        raise error
    return result
