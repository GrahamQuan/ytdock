"""Exercise the real Bash bootstrap with local HTTPS-response fixtures, without network."""

import hashlib
import io
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"


def fixture(tmp_path, mode="ok"):
    mock = tmp_path / "bin"
    mock.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    root = "ytdock-v0.6.0-macos-arm64"
    name = root + ".tar.gz"
    archive = tmp_path / name
    with tarfile.open(archive, "w:gz") as tar:
        body = (
            b'#!/bin/bash\n[ "$1" = --install ] || exit 1\nprintf installed > "$HOME/installed"\n'
        )
        entry = tarfile.TarInfo(root + "/ytdock/ytdock")
        entry.mode = 0o755
        entry.size = len(body)
        tar.addfile(entry, io.BytesIO(body))
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    if mode == "checksum":
        checksum = "0" * 64
    (tmp_path / (name + ".sha256")).write_text(f"{checksum}  {name}\n")
    assets = [
        {
            "name": n,
            "browser_download_url": f"https://github.com/GrahamQuan/ytdock/releases/download/v0.6.0/{n}",
        }
        for n in (name, name + ".sha256")
    ]
    (tmp_path / "release.json").write_text(
        json.dumps(
            {
                "tag_name": "v0.6.0",
                "draft": False,
                "prerelease": False,
                "assets": [] if mode == "missing" else assets,
            }
        )
    )
    curl = mock / "curl"
    curl.write_text(
        f"#!{sys.executable}\n"
        + """import os, pathlib, shutil, sys, time
args = sys.argv[1:]
root = pathlib.Path(os.environ["HOME"])
mode = os.environ["TEST_MODE"]
if mode == "cancel":
    (root / "started").touch()
    time.sleep(60)
if mode == "network": sys.exit(7)
if mode == "disk": sys.exit(23)
if mode == "rate":
    print("403", end="")
    sys.exit(0)
if mode == "404":
    print("404", end="")
    sys.exit(0)
url = args[-1]
name = "release.json" if url.endswith("/latest") else url.rsplit("/", 1)[-1]
shutil.copyfile(root / name, args[args.index("-o") + 1])
print("200", end="")
"""
    )
    curl.chmod(0o755)
    env = os.environ | {
        "HOME": str(tmp_path),
        "TMPDIR": str(scratch),
        "PATH": f"{mock}:/usr/bin:/bin:/usr/sbin:/sbin",
        "TEST_MODE": mode,
    }
    return env, scratch


@pytest.mark.parametrize(
    "mode, success",
    [
        ("ok", True),
        ("checksum", False),
        ("missing", False),
        ("network", False),
        ("rate", False),
        ("404", False),
        ("disk", False),
    ],
)
def test_bootstrap_pipeline(tmp_path, mode, success):
    env, scratch = fixture(tmp_path, mode)
    result = subprocess.run(
        ["/bin/bash"], input=SCRIPT.read_text(), text=True, capture_output=True, env=env, timeout=30
    )
    assert (result.returncode == 0) == success, result.stdout + result.stderr
    assert (tmp_path / "installed").exists() == success
    assert not list(scratch.iterdir())


def test_bootstrap_cancel(tmp_path):
    env, scratch = fixture(tmp_path, "cancel")
    with SCRIPT.open() as script:
        process = subprocess.Popen(
            ["/bin/bash"],
            stdin=script,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "started").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "started").exists()
        os.killpg(process.pid, signal.SIGINT)
        process.communicate(timeout=10)
        assert process.returncode == 130
        assert not list(scratch.iterdir())
        assert not (tmp_path / "installed").exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()


@pytest.mark.parametrize(
    "system,arch,version",
    [
        ("Linux", "arm64", "14.0"),
        ("Darwin", "x86_64", "14.0"),
        ("Darwin", "arm64", "13.6"),
    ],
)
def test_unsupported_platform_before_download(tmp_path, system, arch, version):
    env, scratch = fixture(tmp_path)
    uname = tmp_path / "bin/uname"
    uname.write_text(f'#!/bin/sh\nif [ "$1" = -s ]; then echo {system}; else echo {arch}; fi\n')
    uname.chmod(0o755)
    sw_vers = tmp_path / "bin/sw_vers"
    sw_vers.write_text(f"#!/bin/sh\necho {version}\n")
    sw_vers.chmod(0o755)
    result = subprocess.run(
        ["/bin/bash"], input=SCRIPT.read_text(), text=True, capture_output=True, env=env, timeout=10
    )
    assert result.returncode == 1
    assert not (tmp_path / "installed").exists()
    assert not list(scratch.iterdir())


def test_corrupt_archive_does_not_install(tmp_path):
    env, scratch = fixture(tmp_path)
    archive = tmp_path / "ytdock-v0.6.0-macos-arm64.tar.gz"
    archive.write_bytes(b"not an archive")
    Path(str(archive) + ".sha256").write_text(
        f"{hashlib.sha256(archive.read_bytes()).hexdigest()}  {archive.name}\n"
    )
    result = subprocess.run(
        ["/bin/bash"], input=SCRIPT.read_text(), text=True, capture_output=True, env=env, timeout=10
    )
    assert result.returncode == 1
    assert not (tmp_path / "installed").exists()
    assert not list(scratch.iterdir())
