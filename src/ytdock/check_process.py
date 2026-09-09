"""Cancellable dependency subprocesses, with a private process group per check."""

import os
import signal
import subprocess
import time


class CheckCancelled(Exception):
    pass


def run(cancel, args, *, timeout=10, **kwargs):
    if cancel.is_set():
        raise CheckCancelled
    kwargs.pop("capture_output", None)
    kwargs.pop("shell", None)
    kwargs.pop("stdin", None)
    process = subprocess.Popen(
        args,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=True,
        **kwargs,
    )
    end = time.monotonic() + timeout
    try:
        while True:
            if cancel.is_set():
                raise CheckCancelled
            if time.monotonic() >= end:
                raise subprocess.TimeoutExpired(args, timeout)
            try:
                stdout, stderr = process.communicate(
                    timeout=min(0.1, max(0.001, end - time.monotonic()))
                )
                return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)
            except subprocess.TimeoutExpired:
                continue
    finally:
        # A check may have spawned descendants even if its group leader exited.
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=0.3)
        except subprocess.TimeoutExpired:
            pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate()
