import hashlib
import json
import os
import signal
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import pytest

from ytdock import install, storage, upgrade
from ytdock.core import UserError
from ytdock.i18n import t


def bundle(root, version):
    root.mkdir(parents=True)
    (root / "ytdock").write_text("application " + version)
    (root / "ytdock").chmod(0o755)
    (root / "bundle.json").write_text(
        json.dumps(
            dict(
                schema=1,
                app="ytdock",
                architecture="arm64",
                minimum_macos="14.0",
                version=version,
                files=install.inventory(root),
            )
        )
    )
    return root


def metadata(version="2.0.0"):
    tag = "v" + version
    name = f"ytdock-{tag}-macos-arm64.tar.gz"
    return dict(
        tag_name=tag,
        draft=False,
        prerelease=False,
        assets=[
            dict(name=n, browser_download_url=f"{upgrade.REPOSITORY}/releases/download/{tag}/{n}")
            for n in (name, name + ".sha256")
        ],
    )


@pytest.fixture
def installed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    prefix = home / "custom prefix"
    source = bundle(tmp_path / "old", "1.0.0")
    command = install.install_bundle(source, prefix, home, "/bin/zsh")
    destination = install.layout(prefix)[0]
    record = install.read_json(destination / "installation.json")
    record["preferences"] = {"language": "zh-CN", "custom": "keep"}
    (destination / "installation.json").write_text(json.dumps(record))
    (home / "video.mp4").write_bytes(b"untouched")
    state = tmp_path / "support"
    state.mkdir()
    monkeypatch.setattr(upgrade, "system_directory", lambda _: state)
    monkeypatch.setattr(upgrade.sys, "frozen", True, raising=False)
    monkeypatch.setattr(upgrade.sys, "executable", str(command.resolve()))
    monkeypatch.setattr(upgrade, "get_version", lambda: "1.0.0")
    return home, prefix, destination, state


@pytest.mark.parametrize("target", ["1.0.0", "0.9.0"])
def test_latest_or_newer_does_not_install(installed, monkeypatch, capsys, target):
    monkeypatch.setattr(upgrade, "release", lambda root: metadata(target))
    monkeypatch.setattr(upgrade, "prepare", lambda *a: pytest.fail("No download"))
    assert upgrade.run() == 0
    assert "1.0.0" in capsys.readouterr().out
    assert (installed[2] / "ytdock").read_text() == "application 1.0.0"


def test_other_instance_blocks_before_network(installed, monkeypatch):
    monkeypatch.setattr(upgrade, "release", lambda *a: pytest.fail("Must not query"))
    with storage.instance_lock(installed[3] / "ytdock"):
        with pytest.raises(UserError):
            upgrade.run()


@pytest.mark.parametrize(
    "failure", [KeyboardInterrupt, OSError(28, "disk full"), UserError("network")]
)
def test_download_failure_and_cancel_preserve_install(installed, monkeypatch, failure):
    seen = []

    def fail(root):
        seen.append(root)
        (root / "partial").write_bytes(b"partial")
        raise failure

    monkeypatch.setattr(upgrade, "release", fail)
    if failure is KeyboardInterrupt:
        assert upgrade.run() == 130
    else:
        with pytest.raises(UserError):
            upgrade.run()
    assert seen and not seen[0].exists()
    assert (installed[2] / "ytdock").read_text() == "application 1.0.0"
    assert (installed[0] / "video.mp4").read_bytes() == b"untouched"


@pytest.mark.parametrize("field", ["draft", "prerelease"])
def test_nonstable_response_never_selected(tmp_path, monkeypatch, field):
    data = metadata()
    data[field] = True
    monkeypatch.setattr(
        upgrade, "download", lambda url, path, limit: path.write_text(json.dumps(data))
    )
    with pytest.raises(UserError):
        upgrade.release(tmp_path)


@pytest.mark.parametrize("problem", ["missing", "url", "duplicate", "architecture"])
def test_invalid_assets_and_platform(tmp_path, monkeypatch, problem):
    data = metadata()
    if problem == "missing":
        data["assets"].pop()
    if problem == "url":
        data["assets"][0]["browser_download_url"] = "http://example.invalid/package"
    if problem == "duplicate":
        data["assets"].append(data["assets"][0])
    if problem == "architecture":
        monkeypatch.setattr(upgrade.platform, "machine", lambda: "x86_64")
    with pytest.raises(UserError):
        upgrade.package_urls(data)


@pytest.mark.parametrize("problem", [None, "checksum", "traversal", "version"])
def test_real_archive_verification(tmp_path, monkeypatch, problem):
    data = metadata()
    name, urls = upgrade.package_urls(data)
    source = bundle(
        tmp_path / "build" / name / "ytdock", "3.0.0" if problem == "version" else "2.0.0"
    )
    archive = tmp_path / "package.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(source.parent, arcname=name)
        if problem == "traversal":
            entry = tarfile.TarInfo("../outside")
            tar.addfile(entry)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    def fetch(url, path, limit):
        if url == urls[0]:
            path.write_bytes(archive.read_bytes())
        else:
            path.write_text(
                ("0" * 64 if problem == "checksum" else digest) + "  " + name + ".tar.gz\n"
            )

    monkeypatch.setattr(upgrade, "download", fetch)
    work = tmp_path / "work"
    work.mkdir()
    if problem:
        with pytest.raises(UserError):
            upgrade.prepare(work, data)
    else:
        assert install.verify_bundle(upgrade.prepare(work, data))["version"] == "2.0.0"
    assert not (tmp_path / "outside").exists()


@pytest.mark.parametrize(
    "code,key",
    [
        (403, "upgrade.rate_limit"),
        (429, "upgrade.rate_limit"),
        (404, "upgrade.missing_asset"),
        (500, "upgrade.network"),
    ],
)
def test_http_errors_are_clear_and_redacted(tmp_path, monkeypatch, code, key):
    class Opener:
        def open(self, *args, **kwargs):
            raise upgrade.urllib.error.HTTPError(
                "https://host/?secret=token", code, "secret", {}, None
            )

    monkeypatch.setattr(upgrade.urllib.request, "build_opener", lambda *a: Opener())
    with pytest.raises(UserError) as caught:
        upgrade.download(upgrade.LATEST, tmp_path / "file", 100)
    assert str(caught.value) == t(key) and "secret" not in str(caught.value)


def test_plain_http_never_opened(tmp_path):
    with pytest.raises(UserError):
        upgrade.download("http://example.invalid", tmp_path / "file", 100)


def test_update_worker_waits_and_keeps_lock_then_preserves_configuration(installed, tmp_path):
    home, prefix, destination, state = installed
    root = tmp_path / "ytdock-upgrade-test"
    root.mkdir(mode=0o700)
    source = bundle(root / "extracted" / "release" / "ytdock", "2.0.0")
    (root / "upgrade.json").write_text(
        json.dumps(
            dict(
                app="ytdock",
                uid=os.getuid(),
                prefix=str(prefix),
                home=str(home),
                shell="/bin/zsh",
                language="en",
                source=str(source.relative_to(root)),
            )
        )
    )
    reader, writer = os.pipe()
    # sys.executable is patched to a bundle fixture; run this controlled child with Python.
    python = Path(sys.prefix) / "bin/python"
    script = (
        "import sys; from pathlib import Path; from ytdock import upgrade; "
        "upgrade.system_directory=lambda _:Path(sys.argv[4]); "
        "sys.exit(upgrade.worker(sys.argv[1],int(sys.argv[2]),int(sys.argv[3])))"
    )
    process = None
    try:
        with storage.instance_lock(state / "ytdock") as lock:
            # Production handoff ignores SIGINT before spawning; inherit the same
            # disposition here, including while the child's Python imports run.
            previous_handler = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                process = subprocess.Popen(
                    [str(python), "-c", script, str(root), str(lock), str(reader), str(state)],
                    pass_fds=(lock, reader),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            finally:
                signal.signal(signal.SIGINT, previous_handler)
            os.close(reader)
            reader = -1
            time.sleep(0.2)
            assert process.poll() is None
            assert (destination / "ytdock").read_text() == "application 1.0.0"
        # Parent descriptor is gone; the updater must still keep the SAME flock alive.
        with pytest.raises(UserError):
            with storage.instance_lock(state / "ytdock"):
                pass
        process.send_signal(signal.SIGINT)
        os.close(writer)
        writer = -1
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode == 0, (stdout, stderr)
        assert (destination / "ytdock").read_text() == "application 2.0.0"
        assert install.read_json(destination / "installation.json")["preferences"] == {
            "language": "zh-CN",
            "custom": "keep",
        }
        assert (home / "video.mp4").read_bytes() == b"untouched"
        assert not root.exists()
        with storage.instance_lock(state / "ytdock"):
            pass
    finally:
        for fd in (reader, writer):
            if fd >= 0:
                os.close(fd)
        if process and process.poll() is None:
            process.kill()
            process.wait()


def test_install_failure_rolls_back_custom_install(installed, tmp_path, monkeypatch):
    home, prefix, destination, state = installed
    source = bundle(tmp_path / "new", "2.0.0")
    original = Path.rename

    def rename(path, target):
        if path.name == "new":
            raise OSError(28, "disk full")
        return original(path, target)

    monkeypatch.setattr(Path, "rename", rename)
    with pytest.raises(OSError):
        install.install_bundle(source, prefix, home, "/bin/zsh")
    assert (destination / "ytdock").read_text() == "application 1.0.0"
    assert install.read_json(destination / "installation.json")["preferences"]["custom"] == "keep"
    assert (home / "video.mp4").read_bytes() == b"untouched"


def test_handoff_copies_updater_and_passes_lock_and_parent_pipe(installed, tmp_path, monkeypatch):
    root = tmp_path / "ytdock-upgrade-handoff"
    root.mkdir(mode=0o700)
    source = bundle(root / "extracted" / "new" / "ytdock", "2.0.0")
    seen = []
    monkeypatch.setattr(
        upgrade.subprocess, "Popen", lambda args, **kwargs: seen.append((args, kwargs))
    )
    writers_before = len(upgrade._handoff_writers)
    try:
        with storage.instance_lock(installed[3] / "ytdock") as lock:
            upgrade.handoff(root, installed[2], source, installed[1], lock)
            args, options = seen[0]
            assert args[:2] == [str(root / "updater/ytdock"), "--upgrade-worker"]
            assert options["pass_fds"][0] == lock and options["start_new_session"]
            assert "shell" not in options
            assert install.verify_bundle(root / "updater")["version"] == "1.0.0"
            request = install.read_json(root / "upgrade.json")
            assert request["prefix"] == str(installed[1])
            assert (installed[2] / "ytdock").read_text() == "application 1.0.0"
            assert len(upgrade._handoff_writers) == writers_before + 1
    finally:
        for fd in upgrade._handoff_writers[writers_before:]:
            os.close(fd)
        del upgrade._handoff_writers[writers_before:]
