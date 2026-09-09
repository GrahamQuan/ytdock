# YTDock

**English** · [简体中文](README.zh-CN.md)

Download YouTube videos, compress local videos, or burn local SRT subtitles in your macOS terminal. Use **Tab / Shift+Tab** to move focus; **← / →** switches pages only while the top navigation is focused. Finished MP4 files are saved to the system Downloads folder by default.

- **Download:** choose an available resolution and frame rate for a single YouTube video or Short.
- **Compress:** choose Smaller file or Higher quality while keeping the source dimensions, aspect ratio and actual frame rate, including 60fps.
- **Protect your files:** existing files are never overwritten, compression leaves the source unchanged, and cancellation or failure cleans up the current task's temporary files.

Current version: **0.7.1**. Release packages target **macOS 14+ / Apple Silicon (arm64)**.

## Quick start

### 1. Install

Install the latest stable macOS arm64 release with the command below. If no compatible stable release is available, the script stops without installing.

```sh
curl -fsSL https://raw.githubusercontent.com/GrahamQuan/ytdock/main/install.sh | bash
```

The script downloads the latest stable Apple Silicon package over HTTPS, verifies SHA256, then runs its installer. It needs no interactive answers and does not start the CLI. Network, missing-asset or checksum failures stop installation. SHA256 checks integrity; it does not replace Apple signing or notarization.

For manual installation, obtain the ZIP from [GitHub Releases](https://github.com/GrahamQuan/ytdock/releases) or use a locally built package:

Extract the complete `ytdock-v0.7.1-macos-arm64.zip` archive and double-click `Install.command`. Installation is per user and does not require administrator privileges.

| Dependency | Required separately? |
|---|---|
| Python, QuickJS, yt-dlp, yt-dlp-ejs and UI dependencies | Bundled |
| FFmpeg and ffprobe | Provide a compatible external installation |
| uv | Not needed to use a release; required for development and packaging |

The installer checks FFmpeg after installing the app. Missing or incompatible FFmpeg does not prevent installation, but must be resolved before processing videos. If you use Homebrew:

```sh
brew install ffmpeg
```

The app does not install, update or uninstall FFmpeg automatically.

### 2. Launch

```sh
~/.local/bin/ytdock
```

If `~/.local/bin` is on PATH, run `ytdock` directly. You can also double-click the installed `~/.local/bin/YTDock.command` in Finder.

To try the app without installing it, double-click `Start.command` in the fully extracted release directory.

If PATH has just been configured, open a new terminal or run:

```sh
export PATH="$HOME/.local/bin:$PATH"
ytdock
```

The installer cannot change its parent terminal. `~/.local/bin/ytdock` works immediately. For other shells or a custom `ZDOTDIR`, add the displayed export to your shell configuration yourself. Existing shell configuration symlinks or unsafe files are not overwritten.

### 3. Use the keyboard

| Action | Key |
|---|---|
| Move focus between navigation and content | Tab / Shift+Tab |
| Cycle pages while navigation is focused | ← / → |
| Open the language picker on input and selection pages | Ctrl+O |
| Submit a URL or file path | Enter |
| Choose a resolution or compression mode | ↑ / ↓ |
| Start processing the selected option | Enter |
| Return from a selection page to its input page | Esc |
| Return from compression input to Download | Esc |
| Exit, or cancel and clean up a running task | Ctrl+C |

The interactive interface uses one full-screen application and restores the previous terminal screen on exit. Switching tabs preserves input, cursor, focus and selection, including both subtitle paths. The centered tab bar uses three spaces between tabs; the active mode is colored, bold and bracketed. A compact three-line character logo appears above it when space permits, and collapses to a single-line YTDock title in narrow or short windows. You cannot switch modes while processing. Only one instance may run per user; after a task finishes, enter the next item.

## Tasks and results

The **Tasks** tab lists this run’s records, newest first. Use **↑ / ↓**, **Enter** for details, **Esc** to return, and **Page Up / Page Down** to scroll long details. Records include saved paths, failure stage and reason, cancellation and cleanup results. They are held in memory and disappear when you exit; this is not a queue or a resume feature.

Inspection, selection and processing share one record, including format reselection. Returning before processing marks it “Not started”. Success returns to the same input page and clears the submitted input; failure or cancellation preserves it. Each mode keeps its result notice until the next task starts. Partial saves and cleanup failures are explicitly reported, with full details in Tasks. Published files remain protected.

Focus movement, page switching and language selection are disabled during processing and cleanup. Use a terminal at least **52 columns × 20 rows**; enlarge it when the resize message appears. Lists and details scroll, and layout responds to resizing and language changes. `--help`, `--check`, installation and uninstallation use ordinary terminal output.

## Interface language

The default language is **English**. Press **Ctrl+O** on an input or selection page to open the language picker. It highlights the current language. Use **↑ / ↓** to choose, **Enter** to confirm, or **Esc** to cancel without changing the language. You return to the original page with its input, cursor and selected option preserved.

```text
Select language / 选择语言

❯ English
  简体中文

↑↓ Select / 选择 · Enter Confirm / 确认 · Esc Cancel / 取消
```

You can also choose a language at launch:

```sh
~/.local/bin/ytdock --lang en
~/.local/bin/ytdock --lang zh-CN
~/.local/bin/ytdock --lang zh-CN --check
```

The setting applies to the current run; the next launch defaults to English. Language switching is disabled during processing. Progress, cancellation, completion and error messages use the language selected when the task starts.

Video titles, file paths, audio-language metadata and output filenames are not translated. **Smaller file** is the default compression option (体积优先); **Higher quality** corresponds to 画质优先.

## Download a YouTube video

1. Paste a single video URL on the default Download page and press Enter.
2. Review the title, duration, actual dimensions, frame rate and estimated size.
3. Choose a resolution with ↑ / ↓. Tab moves to subtitles (when available), then Confirm; only Enter in Confirm starts the download.

The default is the highest available resolution up to 1080p. If every option exceeds 1080p, the lowest option is selected. Different frame rates at the same resolution are listed separately, and options requiring transcoding are labeled in advance.

Regular videos, Shorts and single-video links containing playlist parameters are supported; only the current video is downloaded. Playlists without a video, channels, live streams, upcoming premieres, login/cookies, HDR, translated subtitles, cover art and download resuming are not supported.

Video, audio and post-processing stages are shown separately. Compatible streams are copied; other streams are transcoded as needed. Completion is reported only after verification and saving.

Audio selection identifies a track before comparing compatible codecs and bitrates. An explicitly marked original track wins. A single unmarked track is allowed without being called original; AAC, Opus and bitrate variants are encoding versions, not separate tracks. With multiple tracks, only an explicit original-language match to one non-dubbed track is accepted. Unknown original language, no match, multiple candidates and conflicting identities have distinct errors. Default audio, titles, interface language and region are never used to guess. Combined audio/video formats and refreshed selections follow the same rules.

Choosing a single track does not establish the original language for captions. Subtitle evidence remains independent: manual original-language captions take priority over native automatic captions, and translated tracks are excluded. SRT names use language codes such as `.en.srt`, `.es.srt` and `.ja.srt`. See the [audio selection contract and real-video verification](specs/audio-selection.md).

## Optional original-language subtitles

When captions in the original audio language are available, the resolution page shows an inline **Subtitles** panel (Tab / Shift+Tab selects a panel; ↑↓ changes its option). Select **With subtitles** to enable automatic burning; each new video defaults to no subtitles. If no usable matching track exists, the subtitle setting is hidden. Manual captions take priority over native automatic captions; the page identifies the actual source. Translated tracks, translation and speech recognition are not supported.

The app first saves and displays `Title [ID].mp4` (standard H.264/AAC) and `Title [ID].<language>.srt` (the processed, single-line, non-overlapping original-language captions, at most 42 characters per cue). It then automatically creates `Title [ID]_with-subtitle.mp4`: **H.265/HEVC, hvc1, CRF 27, medium, yuv420p and faststart**, with AAC copied and the original resolution, aspect ratio and frame rate retained. Silent videos remain silent. White text has a thin black outline and no shadow; overflowing text is reduced to fit.

Subtitle mode additionally needs external FFmpeg with **libx265, subtitles/libass, usable fonts and HEVC decoding**. Missing capabilities block this option while ordinary downloads remain available. No dependency is installed automatically.

Subtitle timing is checked against the verified source MP4 duration from ffprobe, using integer milliseconds. A cue that starts inside the video and ends up to **2 seconds** beyond it is shortened to the video end, with a notice and all text retained. Cues entirely outside the video, larger overruns and invalid intervals are rejected with their timing and the actual video duration. Empty subtitles, unparseable timestamps and segmentation errors have distinct messages.

Existing names are never overwritten. **Files already saved remain after later cancellation or failure.** If only one file was published, the error identifies it. Temporary originals, partial videos and raw captions are cleaned up. Completion is shown only after the subtitled video passes codec, duration, full decoding and sampled-frame checks. There is no resume-burning entry.

## Burn a local SRT subtitle file

Focus the top navigation with **Tab**, then select **[Subtitles]** using **← / →** and press **Tab** to enter the first field. Paste a video path and a **UTF-8 SRT** path into the two frames. Use **Tab / Shift+Tab** to move between navigation and both fields, and **Enter** to inspect. Paths support spaces, quotes, `~` and Finder drag-and-drop escaping. **Ctrl+O** opens the language picker without losing your input.

You can supply an externally AI-translated SRT in any language. This mode does not translate or upload files. It retains cue text, line breaks and segmentation rather than applying the YouTube 42-character splitting rule. Invalid UTF-8, overlapping/out-of-order cues and invalid times are rejected. End overruns up to 2 seconds are shortened with a notice; larger overruns or cues wholly outside the video are rejected. Font rendering and every distinct cue are checked; missing glyphs or overflow require fixing the font or SRT before proceeding. SRT files are limited to 20 MB; ASS overrides and control characters are unsupported.

Review video details, cue count, the retained audio track and output settings. **Enter** starts burning; **Esc** returns to both paths. The output is `original-name_with-subtitle.mp4` in Downloads: H.265 / hvc1, CRF 27, medium, yuv420p and faststart, preserving dimensions, aspect ratio and frame rate. Only the default audio track (or first if none is marked) is retained: AAC is copied, other audio becomes AAC 192 kbps. Silent sources stay silent. Multiple audio tracks are disclosed before starting.

Only the finished video is published, after media checks, full decoding and subtitle frame comparisons. Inputs stay unchanged and existing names receive a numeric suffix. During processing **Ctrl+C** waits for cancellation and cleanup, then returns to the subtitle paths; success clears them for the next item. No installation package has been rebuilt for this feature yet.

## Compress a local video

1. Focus the navigation with Tab and use ← / → to select Compress; press Tab to enter its input.
2. Paste an absolute video-file path, or drag the file from Finder into the terminal, and press Enter.
3. Review the source information and selected audio track, choose a mode, then press Enter.

Paths support spaces, Chinese characters, `~`, paired quotes and Finder's escaped path syntax. For example:

```text
"~/Movies/My Video.mp4"
```

Only one local file is accepted. Directories and batch input are not supported. Path text is never executed as a shell command.

| Mode | Tradeoff | Video settings | Audio handling |
|---|---|---|---|
| Smaller file (default) | Stronger compression; may lose visible detail | libx264 · CRF 30 · superfast | AAC 128 kbps |
| Higher quality | Preserve more detail; slower processing | libx264 · CRF 23 · medium | Copy AAC sources; otherwise AAC 192 kbps |

**The result is not guaranteed to be smaller.** The `superfast` preset favors speed rather than maximum compression efficiency. Completion shows input size, output size and percentage change. A larger output is kept and clearly labeled **Size increased**.

Both modes preserve dimensions, aspect ratio and actual frame rate, including 60fps and 59.94fps. Only the main video and default audio track are kept; the first track is used when no default is marked. For multiple audio tracks, the selection page identifies the track that will be retained. Silent sources stay silent. Subtitles, cover art and other extra streams are excluded.

### Supported sources

- Single-file containers such as MP4/MOV, MKV/WebM, AVI, FLV, MPEG/TS, Ogg and ASF. Actual codec support depends on external FFmpeg.
- Common 8-bit SDR YUV sources. 10-bit SDR requires explicit SDR color tags and is converted to 8-bit. Explicit full-range SDR is converted to limited range, preserving known color matrices and transfer characteristics.
- HDR, rotation/display matrices, interlaced video, odd dimensions and unreliable color, frame-rate, duration or aspect-ratio information are rejected. The app does not silently rotate or resize the source.

## Output and file protection

Files go to Downloads as resolved through macOS system APIs. Standard downloads and compression use **MP4 · H.264 · 8-bit yuv420p · AAC · faststart**; silent sources keep no audio track. Compression uses the `avc1` video tag.

| Source | Example filename |
|---|---|
| YouTube download | `Video title [videoID].mp4` |
| Local compression, either mode | `movie_compressed.mp4` |
| Existing compression filename | `movie_compressed (1).mp4` |

Original filename text is preserved; compression adds the English `_compressed` suffix. Invalid characters are sanitized and length is limited. Existing files are never overwritten.

Processing uses a separate hidden task directory inside Downloads. Before publication, the app checks codecs, dimensions, frame rate, aspect ratio, audio and duration, then performs a full decode check.

Ctrl+C waits for the worker and its subprocesses to stop, cleans up this task's temporary files and returns to the current mode's input page, preserving the entered URL or path. Ctrl+C on an input or selection page exits with code 130. Repeated presses do not skip cleanup. Failures also trigger cleanup; cleanup errors report the remaining path. Source videos, existing files and already published outputs are kept. After a forced termination, the next launch attempts to clean up confirmed stale task directories owned by this app.

## Upgrade and uninstall

**Exit all `ytdock` instances first, including instances waiting on an input page.** Run the new release's `Install.command` to upgrade. To uninstall:

```sh
~/.local/bin/ytdock --uninstall
```

Alternatively, double-click `Uninstall.command` in the extracted release directory. Removal only affects this app's files and unchanged launchers. Videos, external FFmpeg and other tools are kept. The installer never overwrites another tool's command or directory. It adds a marked YTDock PATH block to `~/.zshrc` only when needed for the default zsh configuration. Reinstalling does not duplicate it. Uninstall removes only the recorded block if unchanged; modified blocks and other shell settings are preserved. Reopen the terminal after uninstall to refresh PATH and command caches.

The installer manages only YTDock. Historical `download-youtube-cli` directories and `yt` commands are not migrated, recognized or removed. Exit every older instance before switching to this release: it uses the independent `ytdock` state directory and task locks. Old environment variable names and Python package aliases are not supported. Rerun the installer to upgrade YTDock; failed upgrades roll back the application.

The default app directory is `~/.local/share/ytdock`. Empty lock files remain to preserve single-instance protection.

For a custom installation prefix, run from the extracted directory:

```sh
./ytdock/ytdock --install --prefix "$HOME/my-tools"
"$HOME/my-tools/bin/ytdock" --uninstall
```

## Dependency checks and troubleshooting

Check dependencies, Downloads and the selected executable paths:

```sh
~/.local/bin/ytdock --check
```

| Problem | Action |
|---|---|
| `ytdock: command not found` | Run `~/.local/bin/ytdock`, or add `~/.local/bin` to PATH yourself |
| Another instance is running | Exit other `ytdock` instances; installation, removal and packaging smoke tests use the same protection |
| Missing ffprobe or encoder | Install a complete FFmpeg build, or select another compatible installation |
| Double-click launch cannot find custom FFmpeg | Use explicit paths from a terminal; Finder launches may not inherit terminal environment variables |
| Downloads is missing or unwritable | Restore the system Downloads directory and check permissions and free disk space |
| macOS blocks first launch | The package is not notarized; you may need to allow it in Privacy & Security |

### Custom FFmpeg paths

```sh
~/.local/bin/ytdock --ffmpeg-dir "$HOME/tools/ffmpeg/bin"
```

You can specify the executables individually or use environment variables:

```sh
~/.local/bin/ytdock --ffmpeg "$HOME/tools/ffmpeg/bin/ffmpeg" --ffprobe "$HOME/tools/ffmpeg/bin/ffprobe"

export YTDOCK_FFMPEG_DIR="$HOME/tools/ffmpeg/bin"
~/.local/bin/ytdock
```

`YTDOCK_FFMPEG` and `YTDOCK_FFPROBE` are also supported. These settings are not automatically saved to shell configuration.

Search order: **explicit paths → PATH → common Homebrew locations**. Environment variables are read only when no command-line path is supplied. Homebrew locations include `/opt/homebrew/bin`, `/usr/local/bin` and their corresponding `opt/ffmpeg/bin` directories.

A matching pair from the same directory is preferred. When only one executable is specified, the app first seeks a compatible companion for it. Automatic discovery tries complete pairs before combining individually validated tools. Incompatible candidates are skipped; fallback from an explicit path is reported. If all candidates fail, specific reasons are shown.

Both tools must run and provide H.264, VP8, VP9, software AV1, AAC, Opus and Vorbis decoding. FFmpeg also needs `libx264`, AAC encoding and `-fps_mode`. Software AV1 requires `libdav1d` or `libaom-av1`.

## Development and packaging

The development directory is `ytdock/`; the first-party Python package is `src/ytdock/`. Run these commands from the repository root. Development/build machines need Python 3.11+, uv, Xcode Command Line Tools and compatible external FFmpeg/ffprobe. Scripts do not install system software.

### Run from source

```sh
./package.command --runtime-only
uv run ytdock --check
uv run ytdock
uv run ytdock --lang zh-CN
```

### Build a release

Exit all `ytdock` instances, then run:

```sh
./package.command
```

The script synchronizes locked dependencies, verifies and builds QuickJS, bundles Python and project dependencies, and runs isolated installation, launch, compression, cancellation, upgrade and removal checks. It then creates:

```text
dist/ytdock-v0.7.1-macos-arm64.zip
dist/ytdock-v0.7.1-macos-arm64.zip.sha256
dist/ytdock-v0.7.1-macos-arm64.tar.gz
dist/ytdock-v0.7.1-macos-arm64.tar.gz.sha256
```

QuickJS versions and checksums are in [packaging/sources.json](packaging/sources.json). Third-party licenses and related source code ship in `ytdock/THIRD_PARTY` and `ytdock/SOURCES`. Build caches live in `build/` and `.runtime/`. FFmpeg/ffprobe and their dedicated codec libraries are not downloaded, built or bundled. Both READMEs and the translation catalog are included in releases.

### Validate changes

```sh
uv run pytest -q
uv run ruff check src tests scripts packaging
uv run ruff format --check src tests scripts packaging
uv run python scripts/smoke_release.py build/release/ytdock-v0.7.1-macos-arm64/ytdock --online
```

`--online` adds a public-video check for installed downloads, publication and cancellation. Media and installation checks use temporary directories. Launcher checks use a PTY and minimal PATH. Run release checks serially to avoid the single-instance lock.

The current release targets arm64. Intel builds require an Intel Mac and have not been validated. Packages use ad-hoc signing and are not Developer ID signed or Apple-notarized. See the [implementation and verification record](specs/implementation.md) for checks actually performed and their limitations.

### Translation maintenance

Each language has its own JSON catalog: [en.json](src/ytdock/locales/en.json) and [zh-CN.json](src/ytdock/locales/zh-CN.json). Both use stable English identifiers such as `download.complete` and `compression.choose_mode`; values contain the translated text. [i18n.py](src/ytdock/i18n.py) loads catalogs from `locales/`.

To add a language, copy `en.json` to `<language-code>.json`, translate its values, and set `language.name` to the native display name. Restart the app to discover it automatically in the picker and `--lang` choices. Missing messages fall back to English; unknown keys remain errors. Keep placeholder names and formatting identical to English. Translate templates before inserting user content; never translate file paths or titles. Packaging collects all locale JSON files.

## Specifications

- [YouTube download specification](specs/ytdock.md)
- [External FFmpeg contract](specs/external-ffmpeg.md)
- [Compression specification](specs/compression.md)
- [Bilingual interface specification](specs/i18n.md)

## Release preparation

The manual [GitHub Actions workflow](.github/workflows/prepare-release.yml) tests and builds arm64 artifacts without publishing. Update `pyproject.toml` and `uv.lock`, run the checks and package command, then separately create `v<version>` and upload both archives with their `.sha256` files to a stable Release. Verify public URLs, checksums and the curl installation after publication. ZIP and tar.gz contain the same program files.

Linux, WSL2 and Intel are not supported release targets. A second Mac and Apple notarization have not been validated. Current local verification is recorded in [the YTDock plan](specs/ytdock-installation.md).

- [Original-language subtitle specification / 原语言字幕规格](specs/subtitles.md)

- [YTDock naming contract / 命名统一规格](specs/ytdock-naming.md)

- [Full-screen interface and task records](specs/fullscreen-tasks.md)

## Version, upgrades and startup checks (development)

These changes are implemented in source and **are not included in the published v0.7.1 package yet**. No new package or real cross-version binary upgrade was validated in this change.

```sh
ytdock --version   # also: ytdock -v
ytdock --upgrade
```

Version queries print `YTDock <version>` without network access, FFmpeg checks or a full-screen interface. For this checkout use `uv run ytdock --version`. Development environments reject `--upgrade` without modifying anything.

In a future build containing these changes, the installed app can query the latest stable GitHub Release, show both versions, and download a matching package over HTTPS with SHA256 verification. Equal or newer local versions are not reinstalled or downgraded. Exit other instances first, including those waiting for input. The updater preserves the custom installation directory and existing configuration, and does not change videos or FFmpeg. Ctrl+C cancels downloading; replacement finishes safely or rolls back. An independent updater waits for the old process to exit while retaining the global lock, then reports completion and the launch command. Keep the terminal open; its completion message may follow the shell prompt. Ctrl+O language choice remains per-process; `--lang` controls upgrade messages.

Interactive startup displays real Python component, QuickJS, FFmpeg, ffprobe, Downloads, lock, cleanup and interface checks. Completed checks remain marked, pending checks are dimmed, and slow subprocess checks do not freeze the indicator. Ctrl+C waits for safe cleanup and restores the terminal; failures are reprinted outside the alternate screen. Help, version, `--check`, install, uninstall and upgrade keep ordinary output; action flags are mutually exclusive. Feedback begins when the program entry point executes, not before macOS or the bundled runtime loads the program.

See the [startup and upgrade contract](specs/startup-upgrade.md) for implementation and validation limits.

Subtitle timing uses the selected video stream duration consistently for YouTube downloads and local SRT imports (container duration is a fallback when absent). Downloaded SRT files are saved after timing correction; importing them with the same video does not repeat the correction. Local imports retain text and segmentation. End-time corrections within the existing 2-second tolerance report the actual milliseconds shortened.

Download verification compares the output with measured source-stream durations. YouTube metadata is only a coarse completeness check (at least one second of tolerance for rounding); output verification retains its tighter tolerance and full decoding check. Failures identify the failed check and duration mismatches include expected/actual seconds.

The local subtitle confirmation page offers “Confirm and continue” and “Cancel and go back”. Use ↑↓ to select, Enter to apply, or Esc to return; confirming is selected by default.

On the download selection page, Tab / Shift+Tab moves between Resolution, Subtitles and Confirm panels. ↑↓ changes options only in the active panel, highlighted by its border color. Enter starts downloading only from Confirm; Esc goes back. Unavailable subtitle panels are skipped.
