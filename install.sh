#!/bin/bash
# YTDock release bootstrap. Compatible with macOS Bash 3.2.
main() (
    set -eu
    umask 077
    temporary='' child=''
    cleanup() { if [ -n "$temporary" ]; then rm -rf -- "$temporary"; fi; }
    cancel() {
        trap '' INT TERM
        if [ -n "$child" ]; then kill -TERM "$child" 2>/dev/null || true; wait "$child" 2>/dev/null || true; fi
        exit 130
    }
    trap cleanup EXIT
    trap cancel INT TERM
    fail() { printf 'YTDock: %s\n' "$*" >&2; exit 1; }
    run() { "$@" & child=$!; result=0; wait "$child" || result=$?; child=''; return "$result"; }
    [ "$(uname -s)" = Darwin ] || fail 'Only macOS is supported.'
    [ "$(uname -m)" = arm64 ] || fail 'Only native Apple Silicon is supported.'
    version=$(sw_vers -productVersion)
    [ "${version%%.*}" -ge 14 ] || fail 'macOS 14 or later is required.'
    temporary=$(mktemp -d "${TMPDIR:-/tmp}/ytdock-install.XXXXXXXX") || fail 'Cannot create temporary directory.'
    fetch() {
        run curl --proto '=https' --proto-redir '=https' --tlsv1.2 -sSL --retry 2 --connect-timeout 20 --max-time 300 -o "$2" -w '%{http_code}' "$1" > "$temporary/status" || fail 'Download failed. Check your network and available disk space.'
        status=$(cat "$temporary/status")
        case "$status" in
            200) ;;
            403|429) fail 'GitHub denied the request or its API rate limit was reached. Try again later.' ;;
            404) fail 'No published release or requested release asset was found.' ;;
            *) fail "Download failed (HTTP $status)." ;;
        esac
    }
    fetch 'https://api.github.com/repos/GrahamQuan/ytdock/releases/latest' "$temporary/release.json"
    field() { plutil -extract "$1" raw -o - "$temporary/release.json" 2>/dev/null; }
    tag=$(field tag_name) || fail 'Invalid GitHub release response.'
    [[ "$tag" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] || fail 'Unsupported release version.'
    [ "$(field draft)" = false ] && [ "$(field prerelease)" = false ] || fail 'A stable release is required.'
    root="ytdock-$tag-macos-arm64"
    archive="$root.tar.gz"
    checksum="$archive.sha256"
    archive_url='' checksum_url='' index=0
    while [ "$index" -lt 1000 ]; do
        name=$(field "assets.$index.name") || break
        case "$name" in
            "$archive"|"$checksum")
                url=$(field "assets.$index.browser_download_url") || fail 'Missing asset URL.'
                [ "$url" = "https://github.com/GrahamQuan/ytdock/releases/download/$tag/$name" ] || fail 'Unexpected release asset URL.'
                if [ "$name" = "$archive" ]; then archive_url=$url; else checksum_url=$url; fi ;;
        esac
        index=$((index + 1))
    done
    [ -n "$archive_url" ] && [ -n "$checksum_url" ] || fail 'This release has no Apple Silicon package and checksum.'
    fetch "$archive_url" "$temporary/$archive"
    fetch "$checksum_url" "$temporary/$checksum"
    cd "$temporary"
    read -r digest filename < "$checksum" || fail 'Invalid checksum file.'
    [[ "$digest" =~ ^[a-fA-F0-9]{64}$ ]] && [ "$filename" = "$archive" ] || fail 'Invalid checksum record.'
    [ "$(wc -l < "$checksum" | tr -d ' ')" = 1 ] || fail 'Invalid checksum file.'
    shasum -a 256 -c "$checksum" || fail 'SHA256 mismatch. Nothing was installed.'
    run tar -tzf "$archive" > members || fail 'Cannot read archive.'
    awk -v root="$root" '($0 != root && $0 != root "/" && index($0, root "/") != 1) || $0 ~ /(^|\/)\.\.(\/|$)/ {exit 1}' members || fail 'Unsafe archive paths.'
    mkdir extracted
    run tar -xzf "$archive" -C extracted || fail 'Extraction failed. Check available disk space.'
    executable="$temporary/extracted/$root/ytdock/ytdock"
    [ -f "$executable" ] && [ -x "$executable" ] && [ ! -L "$executable" ] || fail 'Installer is missing.'
    run "$executable" --install || fail 'Installation failed; see the diagnostic above.'
)
main "$@"
