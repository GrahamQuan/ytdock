"""Build private native tools and a relocatable, user-installable macOS release."""

import argparse
import hashlib
import importlib.metadata
import inspect
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "release"
RUNTIME = ROOT / ".runtime"
SOURCES = ROOT / "build" / "sources"
MIN_MACOS = "14.0"


def run(args, cwd=ROOT, env=None, log="ytdock-build.log"):
    BUILD.mkdir(parents=True, exist_ok=True)
    path = BUILD / log
    with path.open("ab") as output:
        output.write(("\n$ " + repr([str(a) for a in args]) + "\n").encode())
        output.flush()
        result = subprocess.run(
            [str(a) for a in args],
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=output,
        )
    if result.returncode:
        tail = path.read_text(errors="replace")[-6000:]
        raise RuntimeError(f"构建失败，日志：{path}\n{tail}")


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


def unpack(name, spec):
    archive = SOURCES / spec["file"]
    if not archive.exists():
        SOURCES.mkdir(parents=True, exist_ok=True)
        partial = archive.with_suffix(archive.suffix + ".partial")
        run(
            [
                "/usr/bin/curl",
                "-fL",
                "--retry",
                "3",
                "--connect-timeout",
                "20",
                "--max-time",
                "300",
                "-o",
                partial,
                spec["url"],
            ]
        )
        if digest(partial) != spec["sha256"]:
            partial.unlink()
            raise RuntimeError(f"源码校验失败：{name}")
        partial.rename(archive)
    if digest(archive) != spec["sha256"]:
        raise RuntimeError(f"缓存源码校验失败：{archive}")
    destination = BUILD / ("source-" + name)
    if not destination.exists():
        temporary = BUILD / ("extract-" + name)
        if temporary.exists():
            shutil.rmtree(temporary)
        temporary.mkdir()
        with tarfile.open(archive) as tar:
            tar.extractall(temporary, filter="data")
        folders = list(temporary.iterdir())
        if len(folders) != 1 or not folders[0].is_dir():
            raise RuntimeError(f"源码目录结构无效：{name}")
        folders[0].rename(destination)
        temporary.rmdir()
    return destination


def native_tools():
    locked = json.loads((ROOT / "packaging" / "sources.json").read_text())
    signature = hashlib.sha256(
        (
            json.dumps(locked, sort_keys=True)
            + platform.machine()
            + MIN_MACOS
            + inspect.getsource(native_tools)
        ).encode()
    ).hexdigest()
    stamp = RUNTIME / "build.json"
    if stamp.is_file() and json.loads(stamp.read_text()).get("signature") == signature:
        if all((RUNTIME / "bin" / name).is_file() for name in ("qjs",)):
            print("复用已校验构建配置的运行时。", flush=True)
            return
    jobs = str(min(os.cpu_count() or 2, 8))
    env = os.environ | {
        "MACOSX_DEPLOYMENT_TARGET": MIN_MACOS,
        "CC": "clang",
        "CFLAGS": f"-O2 -mmacosx-version-min={MIN_MACOS}",
        "LC_ALL": "C",
    }
    print("构建内置 QuickJS（FFmpeg/ffprobe 使用外部安装）…", flush=True)
    qjs = unpack("quickjs", locked["quickjs"])
    run(["make", "-j", jobs, "qjs"], cwd=qjs, env=env)
    binaries = RUNTIME / "bin"
    binaries.mkdir(parents=True, exist_ok=True)
    target = binaries / "qjs"
    shutil.copy2(qjs / "qjs", target)
    run(["/usr/bin/strip", "-S", target])
    run(["/usr/bin/codesign", "--force", "--sign", "-", target])
    dependencies = subprocess.check_output(["/usr/bin/otool", "-L", target], text=True)
    if "/opt/" in dependencies.split("\n", 1)[-1] or "/usr/local/" in dependencies:
        raise RuntimeError(f"QuickJS 仍依赖本机库：{dependencies}")
    stamp.write_text(
        json.dumps(
            {"signature": signature, "sources": locked, "architecture": platform.machine()},
            indent=2,
        )
    )


def notices(bundle):
    target = bundle / "THIRD_PARTY"
    target.mkdir()
    installed = []
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        installed.append({"name": name, "version": distribution.version})
        for file in distribution.files or []:
            if any(
                word in Path(str(file)).name.lower() for word in ("license", "copying", "notice")
            ):
                original = Path(distribution.locate_file(file))
                if original.is_file() and original.stat().st_size < 2_000_000:
                    destination = target / name / str(file).replace("../", "")
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(original, destination)
    (target / "python-packages.json").write_text(json.dumps(installed, indent=2))
    (target / "README.txt").write_text(
        "QuickJS: MIT. FFmpeg and ffprobe are NOT distributed in this package.\n"
        "Exact corresponding native sources and this build script are in SOURCES/.\n"
        "Users supply their own compatible FFmpeg installation.\n"
        "Python's license is included in Python-LICENSE.txt.\n"
    )
    # The stdlib license printer already knows the framework's license paths.
    import builtins

    builtins.license._Printer__setup()
    (target / "Python-LICENSE.txt").write_text("\n".join(builtins.license._Printer__lines))
    corresponding = bundle / "SOURCES"
    corresponding.mkdir()
    locked = json.loads((ROOT / "packaging/sources.json").read_text())
    for spec in locked.values():
        shutil.copy2(SOURCES / spec["file"], corresponding / spec["file"])
    shutil.copy2(ROOT / "packaging/sources.json", corresponding / "sources.json")
    shutil.copy2(__file__, corresponding / "package.py")
    shutil.copy2(ROOT / "uv.lock", corresponding / "uv.lock")
    shutil.copy2(ROOT / "pyproject.toml", corresponding / "pyproject.toml")
    # Include project source and packaging scripts for rebuilding the same release.
    for name in ("src", "scripts", "packaging"):
        shutil.copytree(
            ROOT / name, corresponding / name, ignore=shutil.ignore_patterns("__pycache__")
        )
    shutil.copy2(ROOT / "package.command", corresponding / "package.command")
    shutil.copy2(ROOT / "install.sh", corresponding / "install.sh")


def package():
    if sys.platform != "darwin" or platform.machine() != "arm64":
        raise RuntimeError("Only verified macOS arm64 releases may be built.")
    from ytdock.version import get_version

    version = get_version()
    release_name = f"ytdock-v{version}-macos-{platform.machine()}"
    staging = BUILD / release_name
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir()
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--name",
        "ytdock",
        "--distpath",
        staging,
        "--workpath",
        BUILD / "pyinstaller",
        "--specpath",
        BUILD,
        "--paths",
        ROOT / "src",
        "--collect-data",
        "ytdock",
        "--collect-all",
        "yt_dlp",
        "--collect-all",
        "yt_dlp_ejs",
    ]
    for name in ("ytdock", "yt-dlp", "yt-dlp-ejs", "prompt-toolkit", "pyobjc-framework-Cocoa"):
        command += ["--copy-metadata", name]
    command += [ROOT / "packaging/entry.py"]
    print("打包 Python、UI 和 yt-dlp…", flush=True)
    run(
        command,
        env=os.environ | {"MACOSX_DEPLOYMENT_TARGET": MIN_MACOS},
        log="ytdock-pyinstaller.log",
    )
    bundle = staging / "ytdock"
    (bundle / "runtime").mkdir()
    shutil.copy2(RUNTIME / "bin/qjs", bundle / "runtime/qjs")
    notices(bundle)
    # Verify that every Mach-O load command resolves inside the bundle or to macOS.
    from macholib.MachO import MachO

    for path in bundle.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        with path.open("rb") as handle:
            magic = handle.read(4)
        if magic not in (
            b"\xcf\xfa\xed\xfe",
            b"\xfe\xed\xfa\xcf",
            b"\xca\xfe\xba\xbe",
            b"\xbe\xba\xfe\xca",
        ):
            continue
        try:
            macho = MachO(str(path))
        except (ValueError, OSError):
            continue
        for header in macho.headers:
            for _, _, dependency in header.walkRelocatables():
                if dependency.startswith("/") and not dependency.startswith(
                    ("/usr/lib/", "/System/Library/")
                ):
                    raise RuntimeError(f"发现不可移植依赖：{path}: {dependency}")
    from ytdock.install import APP_ID, inventory

    (bundle / "bundle.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "app": APP_ID,
                "version": version,
                "architecture": platform.machine(),
                "minimum_macos": MIN_MACOS,
                "files": inventory(bundle),
            },
            indent=2,
        )
    )
    for filename, flag in (
        ("Install.command", "--install"),
        ("Uninstall.command", "--uninstall"),
        ("Start.command", ""),
    ):
        script = staging / filename
        script.write_text(
            '#!/bin/sh\ncd "$(dirname "$0")" || exit 1\n"./ytdock/ytdock" '
            + flag
            + '\nstatus=$?\nprintf "\\nPress Enter to close…"\nread -r answer\nexit "$status"\n'
        )
        script.chmod(0o755)
    (staging / "README.txt").write_text(
        f"YTDock {version} / macOS {MIN_MACOS}+ / arm64\n\n"
        "Double-click Install.command to install, Start.command to try, "
        "or Uninstall.command to remove.\n"
        "Run ~/.local/bin/ytdock; once PATH is active, run ytdock.\n"
        "Tab switches Download/Compress. Ctrl+O opens the language picker.\n"
        "Python and QuickJS are bundled. Install external FFmpeg/ffprobe yourself.\n"
        "Exit all YTDock instances before installation, upgrade or removal.\n"
        "See README.md or README.zh-CN.md for PATH, upgrade and removal details.\n"
        "Ad-hoc signed; not Apple-notarized. SHA256 checks integrity, not publisher identity.\n"
    )
    shutil.copy2(ROOT / "README.md", staging / "README.md")
    shutil.copy2(ROOT / "README.zh-CN.md", staging / "README.zh-CN.md")
    shutil.copytree(ROOT / "specs", staging / "specs")
    print("验收隔离安装、外部 FFmpeg、转码、升级与卸载…", flush=True)
    run([sys.executable, ROOT / "scripts/smoke_release.py", bundle], log="ytdock-smoke.log")
    for extension in ("zip", "tar.gz"):
        output = ROOT / "dist" / (release_name + "." + extension)
        output.parent.mkdir(exist_ok=True)
        if output.exists():
            output.unlink()
        if extension == "zip":
            run(
                [
                    "/usr/bin/ditto",
                    "-c",
                    "-k",
                    "--norsrc",
                    "--noextattr",
                    "--noqtn",
                    "--keepParent",
                    staging,
                    output,
                ]
            )
        else:
            with tarfile.open(output, "w:gz", format=tarfile.PAX_FORMAT) as archive:
                archive.add(staging, arcname=release_name)
        Path(str(output) + ".sha256").write_text(f"{digest(output)}  {output.name}\n")
        print(f"Built: {output}\nSHA256: {digest(output)}", flush=True)

    # Reject archive metadata sidecars or any other difference between distribution formats.
    with tarfile.open(ROOT / "dist" / (release_name + ".tar.gz")) as tar:
        tar_files = {
            member.name: hashlib.sha256(
                member.linkname.encode() if member.issym() else tar.extractfile(member).read()
            ).hexdigest()
            for member in tar.getmembers()
            if member.isfile() or member.issym()
        }
    with zipfile.ZipFile(ROOT / "dist" / (release_name + ".zip")) as archive:
        zip_files = {
            member.filename: hashlib.sha256(archive.read(member)).hexdigest()
            for member in archive.infolist()
            if not member.is_dir()
        }
    if tar_files != zip_files:
        raise RuntimeError("ZIP and tar.gz contents differ; do not publish these artifacts.")
    print("ZIP and tar.gz contents match.", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runtime-only", action="store_true", help="只构建项目内运行时")
    args = parser.parse_args()
    if sys.platform != "darwin" or platform.machine() not in ("arm64", "x86_64"):
        raise RuntimeError("请在目标架构的 macOS 上打包。")
    if not shutil.which("clang") or not shutil.which("make"):
        raise RuntimeError("缺少 Xcode Command Line Tools，请手动安装。")
    native_tools()
    if not args.runtime_only:
        package()


if __name__ == "__main__":
    main()
