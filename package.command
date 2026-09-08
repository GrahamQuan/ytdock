#!/bin/sh
set -eu
cd "$(dirname "$0")"
if ! command -v uv >/dev/null 2>&1; then
    echo '打包需要 uv 和 Xcode Command Line Tools；请先手动安装。' >&2
    exit 1
fi
uv sync --locked --group package
exec uv run --no-sync python scripts/package.py "$@"
