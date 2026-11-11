"""输出本地视频的 ffprobe 诊断报告，供排查 Finder 快速查看问题。"""

import argparse
import json
import platform
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def resolve_path(raw):
    path = Path(raw).expanduser()
    if path.is_file():
        return path.resolve()
    # Finder 拖入交互提示时，路径可能带有 shell 转义。
    parts = shlex.split(raw.strip())
    if len(parts) == 1:
        path = Path(parts[0]).expanduser()
        if path.is_file():
            return path.resolve()
    raise ValueError("找不到视频文件，请检查路径。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", nargs="?", help="本地视频路径")
    args = parser.parse_args()
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        sys.exit("找不到 ffprobe。若使用 Homebrew，可运行 brew install ffmpeg。")
    try:
        if args.path is None:
            print("请输入或拖入视频文件，再按回车：", file=sys.stderr, flush=True)
        path = resolve_path(args.path if args.path is not None else input())
    except (ValueError, EOFError) as exc:
        sys.exit(f"无法读取文件路径：{exc}")

    entries = (
        "format=format_name,format_long_name,nb_streams,start_time,duration,size,bit_rate"
        ":format_tags=major_brand,minor_version,compatible_brands"
        ":stream:stream_tags=language,rotate:stream_disposition:stream_side_data"
    )
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        capture_output=True,
        text=True,
        errors="replace",
    )
    report = {
        "macOS": platform.mac_ver()[0],
        "architecture": platform.machine(),
        "extension": path.suffix.lower(),
        "ffprobe_exit_code": result.returncode,
    }
    if result.stderr.strip():
        report["ffprobe_messages"] = result.stderr.strip().replace(str(path), "<视频文件>")
    if result.stdout.strip():
        report.update(json.loads(result.stdout))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
