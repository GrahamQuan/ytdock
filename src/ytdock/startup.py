"""Real startup stages rendered by the same full-screen Application as the CLI."""

import asyncio
import importlib
import threading
import time
from contextlib import ExitStack
from functools import partial

from prompt_toolkit.data_structures import Point
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import HSplit, Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl

from . import check_process, dependencies, storage
from .core import UserError
from .i18n import t

STAGES = ("python", "quickjs", "ffmpeg", "ffprobe", "downloads", "lock", "recovery", "interface")


class Startup:
    def __init__(self, media_options=None):
        self.options = media_options or {}
        self.cancel = threading.Event()
        self.states = dict.fromkeys(STAGES, "pending")
        self.current = "python"
        self.errors, self.warnings = [], []
        self.resources = ExitStack()
        self.invalidate = lambda: None
        self.screen = None

    def update(self, stage, state):
        if self.states[self.current] == "checking" and stage != self.current:
            self.states[self.current] = "pending"
        self.current = stage
        self.states[stage] = state
        self.invalidate()

    def cancelled(self):
        if self.cancel.is_set():
            raise check_process.CheckCancelled

    def check(self):
        def progress(stage, state):
            self.cancelled()
            self.update(stage, state)

        try:
            paths = dependencies.check(
                progress=progress,
                runner=partial(check_process.run, self.cancel),
                warn=self.warnings.append,
                **self.options,
            )
            progress("downloads", "checking")
            downloads = storage.system_directory("downloads")
            storage.check_downloads(downloads)
            progress("downloads", "ready")
            progress("lock", "checking")
            lock = self.resources.enter_context(
                storage.instance_lock(storage.system_directory("support") / storage.APP_ID)
            )
            progress("lock", "ready")
            progress("recovery", "checking")
            # Never interrupt an ownership-checked removal halfway through cleanup.
            residuals = storage.recover(downloads)
            if residuals:
                raise UserError(
                    "\n".join(
                        t("unable_to_safely_clean_up_an_old_task_check_the", p0=p)
                        for p in residuals
                    )
                )
            progress("recovery", "ready")
            progress("interface", "checking")
            importlib.import_module("ytdock.workflow")
            progress("interface", "ready")
            return paths, downloads, lock
        except check_process.CheckCancelled:
            raise
        except Exception as exc:
            self.states[self.current] = "failed"
            self.errors.append(
                str(exc) if isinstance(exc, UserError) else t("startup.check_failed")
            )
            self.invalidate()
            raise UserError(self.report()) from None

    def report(self):
        lines = ["YTDock"]
        for stage, state in self.states.items():
            mark = {"ready": "✓", "failed": "✗", "checking": "…", "pending": " "}[state]
            lines.append(f"{mark} {t('startup.' + stage)}")
        return "\n".join(lines + self.warnings + self.errors)

    def formatted(self):
        spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"[int(time.monotonic() * 10) % 10]
        rows = [("class:title", "YTDock\n\n")]
        for stage, state in self.states.items():
            label = t("startup." + stage)
            if state == "checking":
                text = spinner + " " + t("startup.checking", item=label)
            else:
                text = {"ready": "✓ ", "failed": "✗ ", "pending": "  "}[state] + label
            rows.append(("class:hint" if state == "pending" else "", text + "\n"))
        if self.cancel.is_set():
            rows.append(("", "\n" + t("startup.cancelling")))
        for message in self.warnings + self.errors:
            rows.append(("", "\n" + message))
        return rows

    async def run(self, screen):
        self.screen = screen
        self.invalidate = screen.app.invalidate
        keys = KeyBindings()
        for key in ("c-c", "<sigint>"):

            @keys.add(key, eager=True)
            def cancel(event):
                self.cancel.set()
                self.invalidate()

        screen.app.key_bindings = keys
        screen.app.layout = Layout(
            HSplit(
                [
                    Window(
                        FormattedTextControl(
                            self.formatted,
                            focusable=True,
                            show_cursor=False,
                            get_cursor_position=lambda: Point(0, 2 + STAGES.index(self.current)),
                        ),
                        wrap_lines=True,
                    ),
                    Window(FormattedTextControl(lambda: t("startup.cancel_hint")), height=1),
                ]
            )
        )
        screen.app.invalidate()
        task = asyncio.create_task(asyncio.to_thread(self.check))
        try:
            values = await asyncio.shield(task)
            self.cancelled()
            screen.paths, screen.downloads, screen.lock = values
        except asyncio.CancelledError:
            self.cancel.set()
            try:
                await task
            except (check_process.CheckCancelled, UserError):
                pass
            raise

    def close(self):
        self.resources.close()
