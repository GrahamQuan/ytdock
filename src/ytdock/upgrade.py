"""Verified release download and a lock-preserving out-of-process replacement."""

import hashlib
import http.client
import json
import os
import platform
import re
import shutil
import signal
import ssl
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .core import UserError
from .i18n import get_language, set_language, t
from .install import install_bundle, layout, owned_install, read_json, verify_bundle
from .storage import APP_ID, instance_lock, system_directory
from .version import get_version

REPOSITORY = "https://github.com/GrahamQuan/ytdock"
LATEST = "https://api.github.com/repos/GrahamQuan/ytdock/releases/latest"
_handoff_writers = []  # Deliberately held until the old process actually exits.


class HTTPSOnly(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if urllib.parse.urlsplit(newurl).scheme != "https":
            raise UserError(t("upgrade.unsafe_url"))
        return super().redirect_request(request, fp, code, msg, headers, newurl)


def download(url, destination, limit):
    if urllib.parse.urlsplit(url).scheme != "https":
        raise UserError(t("upgrade.unsafe_url"))
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "YTDock",
            "Accept": "application/vnd.github+json"
            if url == LATEST
            else "application/octet-stream",
        },
    )
    try:
        with urllib.request.build_opener(HTTPSOnly()).open(request, timeout=20) as response:
            if response.status != 200:
                raise UserError(t("upgrade.network"))
            total = 0
            with destination.open("xb") as file:
                while chunk := response.read(1024 * 256):
                    total += len(chunk)
                    if total > limit:
                        raise UserError(t("upgrade.invalid_release"))
                    file.write(chunk)
    except urllib.error.HTTPError as exc:
        key = (
            "upgrade.rate_limit"
            if exc.code in (403, 429)
            else "upgrade.missing_asset"
            if exc.code == 404
            else "upgrade.network"
        )
        raise UserError(t(key)) from None
    except (
        urllib.error.URLError,
        TimeoutError,
        ConnectionError,
        ssl.SSLError,
        http.client.HTTPException,
    ):
        raise UserError(t("upgrade.network")) from None


def version_tuple(value):
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", str(value))
    if not match:
        raise UserError(t("upgrade.invalid_release"))
    return tuple(map(int, match.groups()))


def release(root):
    path = root / "release.json"
    download(LATEST, path, 2_000_000)
    try:
        data = json.loads(path.read_text())
        if (
            not isinstance(data, dict)
            or data.get("draft") is not False
            or data.get("prerelease") is not False
        ):
            raise ValueError
        version_tuple(data["tag_name"])
        if not str(data["tag_name"]).startswith("v"):
            raise ValueError
        return data
    except (KeyError, ValueError, TypeError):
        raise UserError(t("upgrade.invalid_release")) from None


def package_urls(data):
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise UserError(t("upgrade.platform"))
    try:
        if int(platform.mac_ver()[0].split(".")[0]) < 14:
            raise UserError(t("upgrade.platform"))
    except ValueError:
        raise UserError(t("upgrade.platform")) from None
    root = f"ytdock-{data['tag_name']}-macos-arm64"
    archive = root + ".tar.gz"
    wanted = (archive, archive + ".sha256")
    assets = data.get("assets")
    if not isinstance(assets, list):
        raise UserError(t("upgrade.missing_asset"))
    urls = []
    for name in wanted:
        matches = [a for a in assets if isinstance(a, dict) and a.get("name") == name]
        expected = f"{REPOSITORY}/releases/download/{data['tag_name']}/{name}"
        if len(matches) != 1 or matches[0].get("browser_download_url") != expected:
            raise UserError(t("upgrade.missing_asset"))
        urls.append(expected)
    return root, urls


def prepare(root, data):
    name, urls = package_urls(data)
    archive = root / (name + ".tar.gz")
    checksum = root / (archive.name + ".sha256")
    print(t("upgrade.downloading"), flush=True)
    download(urls[0], archive, 512 * 1024 * 1024)
    download(urls[1], checksum, 1024)
    try:
        checksum_text = checksum.read_text(encoding="ascii")
    except UnicodeError:
        raise UserError(t("upgrade.checksum")) from None
    match = re.fullmatch(r"([a-fA-F0-9]{64})  " + re.escape(archive.name) + r"\n?", checksum_text)
    with archive.open("rb") as file:
        digest = hashlib.file_digest(file, "sha256").hexdigest()
    if not match or digest != match[1].lower():
        raise UserError(t("upgrade.checksum"))
    extracted = root / "extracted"
    extracted.mkdir()
    try:
        with tarfile.open(archive) as tar:
            members = tar.getmembers()
            if len(members) > 50000 or sum(m.size for m in members) > 2 * 1024**3:
                raise ValueError
            for member in members:
                parts = Path(member.name).parts
                if (
                    not parts
                    or parts[0] != name
                    or ".." in parts
                    or not (member.isfile() or member.isdir() or member.issym())
                ):
                    raise ValueError
            tar.extractall(extracted, filter="data")
    except (tarfile.TarError, ValueError):
        raise UserError(t("upgrade.invalid_archive")) from None
    source = extracted / name / "ytdock"
    try:
        manifest = verify_bundle(source)
    except (KeyError, TypeError, ValueError):
        raise UserError(t("upgrade.invalid_archive")) from None
    if manifest.get("version") != data["tag_name"].removeprefix("v"):
        raise UserError(t("upgrade.invalid_release"))
    return source


def cleanup(root):
    try:
        shutil.rmtree(root)
    except OSError:
        print(t("upgrade.cleanup_failed", path=root), file=sys.stderr, flush=True)


def handoff(root, installed, source, prefix, lock):
    helper = root / "updater"
    shutil.copytree(installed, helper, symlinks=True)
    verify_bundle(helper)
    (root / "upgrade.json").write_text(
        json.dumps(
            {
                "app": APP_ID,
                "uid": os.getuid(),
                "prefix": str(prefix),
                "home": str(Path.home()),
                "shell": os.environ.get("SHELL", ""),
                "language": get_language(),
                "source": str(source.relative_to(root)),
            }
        )
    )
    reader, writer = os.pipe()
    try:
        subprocess.Popen(
            [str(helper / "ytdock"), "--upgrade-worker", str(root), str(lock), str(reader)],
            pass_fds=(lock, reader),
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except BaseException:
        os.close(writer)
        raise
    finally:
        os.close(reader)
    _handoff_writers.append(writer)
    # The inherited open-file description keeps the global flock continuously held.
    # The updater waits for pipe EOF, so replacement starts only after this process exits.


def run():
    if not getattr(sys, "frozen", False):
        raise UserError(t("upgrade.release_only"))
    installed = Path(sys.executable).resolve().parent
    record = read_json(installed / "installation.json")
    prefix = Path(record.get("prefix", "")).resolve()
    if layout(prefix)[0] != installed:
        raise UserError(t("upgrade.not_owned"))
    with instance_lock(system_directory("support") / APP_ID) as lock:
        owned_install(installed, prefix)
        root = Path(tempfile.mkdtemp(prefix="ytdock-upgrade-")).resolve()
        transferred = False
        old = {sig: signal.getsignal(sig) for sig in (signal.SIGINT, signal.SIGTERM)}

        def cancel(signum, frame):
            for sig in old:
                signal.signal(sig, signal.SIG_IGN)
            raise KeyboardInterrupt

        try:
            for sig in old:
                signal.signal(sig, cancel)
            data = release(root)
            current, target = get_version(), data["tag_name"].removeprefix("v")
            print(t("upgrade.versions", current=current, target=target), flush=True)
            if version_tuple(current) >= version_tuple(target):
                print(t("upgrade.current"), flush=True)
                return 0
            source = prepare(root, data)
            print(t("upgrade.replacing"), flush=True)
            # From this point interruption must not strand the handoff or replacement.
            for sig in old:
                signal.signal(sig, signal.SIG_IGN)
            handoff(root, installed, source, prefix, lock)
            transferred = True
            return 0
        except KeyboardInterrupt:
            print(t("upgrade.cancelled"), flush=True)
            return 130
        except OSError as exc:
            raise UserError(
                t("not_enough_disk_space") if exc.errno == 28 else t("upgrade.install_failed")
            ) from None
        finally:
            if not transferred:
                cleanup(root)
            for sig, handler in old.items():
                signal.signal(sig, handler)


def worker(root, lock, parent):
    """Private inherited-FD entry. Never acquire a second global lock description."""
    root = Path(root)
    cleanup_allowed = False
    try:
        if (
            root.is_symlink()
            or root.stat().st_uid != os.getuid()
            or stat.S_IMODE(root.stat().st_mode) != 0o700
            or not root.name.startswith("ytdock-upgrade-")
        ):
            raise UserError(t("upgrade.not_owned"))
        data = read_json(root / "upgrade.json")
        if data.get("app") != APP_ID or data.get("uid") != os.getuid():
            raise UserError(t("upgrade.not_owned"))
        set_language(data["language"])
        expected = system_directory("support") / APP_ID / "instance.lock"
        actual, expected_stat = os.fstat(lock), expected.stat()
        if (actual.st_dev, actual.st_ino) != (
            expected_stat.st_dev,
            expected_stat.st_ino,
        ) or not stat.S_ISFIFO(os.fstat(parent).st_mode):
            raise UserError(t("upgrade.not_owned"))
        source = (root / data["source"]).resolve()
        if not source.is_relative_to(root.resolve() / "extracted"):
            raise UserError(t("upgrade.not_owned"))
        cleanup_allowed = True
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, signal.SIG_IGN)
        while os.read(parent, 1):
            pass
        os.close(parent)
        command = install_bundle(source, Path(data["prefix"]), Path(data["home"]), data["shell"])
        print(t("upgrade.complete", command=command), flush=True)
        return 0
    except Exception as exc:
        print(
            str(exc)
            if isinstance(exc, UserError)
            else t("not_enough_disk_space")
            if isinstance(exc, OSError) and exc.errno == 28
            else t("upgrade.install_failed"),
            file=sys.stderr,
            flush=True,
        )
        return 1
    finally:
        if cleanup_allowed:
            cleanup(root)
        os.close(lock)
