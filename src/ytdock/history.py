"""In-memory task records; terminal strings never determine lifecycle state."""

import time
from dataclasses import dataclass, field
from datetime import datetime

from .core import clock, safe_text
from .i18n import t


@dataclass
class Record:
    mode: str
    inputs: dict
    started: datetime = field(default_factory=lambda: datetime.now().astimezone())
    started_tick: float = field(default_factory=time.monotonic)
    ended_tick: float | None = None
    status: str = "reading"
    title: str = ""
    stage: str | None = None
    failure_stage: str | None = None
    reason: str = ""
    saved: dict = field(default_factory=dict)
    cleanup: dict | None = None
    notices: list = field(default_factory=list)
    result: dict = field(default_factory=dict)

    def update(self, event):
        if event.get("stage") and event["stage"] != "stage.cancelling":
            self.stage = event["stage"]
        if event.get("notice"):
            self.notices.append(event["notice"])
        self.saved.update(event.get("saved", {}))
        if outcome := event.get("task_outcome"):
            self.saved.update(outcome.get("saved", {}))
            self.cleanup = outcome.get("cleanup")
            self.failure_stage = outcome.get("failure_stage")

    def finish(self, status, reason=""):
        self.status, self.reason = status, reason
        self.ended_tick = time.monotonic()
        if status in ("failed", "cancelled") and not self.failure_stage:
            self.failure_stage = self.stage

    @property
    def elapsed(self):
        return (self.ended_tick or time.monotonic()) - self.started_tick

    def summary(self):
        symbols = {"succeeded": "✓", "failed": "✗", "cancelled": "⊘", "not_started": "–"}
        result = (
            f"{symbols.get(self.status, '…')} {t('mode.' + self.mode)} · {t('task.' + self.status)}"
        )
        if self.status != "succeeded" and self.saved:
            result += " · " + t("task.retained")
        if self.cleanup and not self.cleanup["ok"]:
            result += " · " + t("task.cleanup_failed")
        return result

    def notification(self):
        result = self.summary()
        if self.status == "cancelled" and self.cleanup and self.cleanup["ok"]:
            result += " · " + t("task.cleanup_ok")
        if self.cleanup and not self.cleanup["ok"]:
            result += "\n" + "\n".join(self.cleanup.get("residuals", []))
        return result + " · " + t("task.see_details")

    def details(self):
        lines = [
            self.summary(),
            self.title,
            t("task.started", value=self.started.strftime("%Y-%m-%d %H:%M:%S %Z")),
            t("task.elapsed", value=clock(self.elapsed)),
            t("task.inputs"),
        ]
        lines += [f"{t('task.input_' + key)}: {value}" for key, value in self.inputs.items()]
        if self.stage:
            lines.append(t("task.stage", value=t(self.failure_stage or self.stage)))
        if self.reason:
            lines += [t("task.reason"), self.reason]
        if self.saved:
            lines += [t("task.saved")] + list(self.saved.values())
        if self.cleanup:
            lines += [t("task.cleanup_ok" if self.cleanup["ok"] else "task.cleanup_failed")]
            lines += self.cleanup.get("residuals", [])
        if "input_bytes" in self.result and "output_bytes" in self.result:
            from .compression import summary

            lines.append(summary(self.result))
        if self.notices:
            lines += self.notices
        return "\n".join(safe_text(line) for text in lines for line in str(text).splitlines())
