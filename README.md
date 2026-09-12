# YTDock

**English** · [简体中文](README.zh-CN.md)

Download YouTube videos, compress local videos, and add subtitles from your Mac terminal. Follow the on-screen prompts; finished files go to your system **Downloads** folder.

- **Download videos**: Save regular videos and Shorts, choose a resolution, and optionally include original-language subtitles.
- **Compress videos**: Choose Smaller file or Higher quality while keeping the original resolution and frame rate.
- **Add subtitles**: Combine a local video and an SRT file into an MP4 with subtitles visible directly in the picture.

Supports **macOS 14 or later on Apple Silicon (M-series chips)**.

## Install and launch

### 1. Install YTDock

Open Terminal, paste this command, and press Enter:

```sh
curl -fsSL https://raw.githubusercontent.com/GrahamQuan/ytdock/main/install.sh | bash
```

This downloads and installs the latest stable release without administrator access. The app does not launch automatically after installation.

Alternatively, download the `macos-arm64.zip` package from [GitHub Releases](https://github.com/GrahamQuan/ytdock/releases), **extract the entire archive**, and double-click `Install.command`. To try it without installing, double-click `Start.command` in the extracted folder.

### 2. Set up video processing

YTDock needs FFmpeg to process videos. If you have [Homebrew](https://brew.sh/), run:

```sh
brew install ffmpeg
```

Skip this if a compatible FFmpeg is already installed. You do not need to install Python or other app dependencies separately. Startup checks will tell you if FFmpeg is missing.

### 3. Launch

```sh
~/.local/bin/ytdock
```

After reopening Terminal, you can usually launch with just `ytdock`. You can also press **Shift+Command+G** in Finder, enter `~/.local/bin`, and double-click `YTDock.command`.

The interface defaults to English. To change it, press **Ctrl+O** on an input or selection page, choose a language with **↑ / ↓**, and press **Enter**. The choice lasts for the current session only. To start in Chinese each time, use:

```sh
~/.local/bin/ytdock --lang zh-CN
```

## Essential controls

| What you want to do | Controls |
|---|---|
| Switch between Download, Compress, Subtitles, and Tasks | Press Tab to focus the top navigation, then ← / → |
| Enter page content or move between fields and panels | Tab / Shift+Tab |
| Choose an option | ↑ / ↓ |
| Submit input or confirm the current action | Enter |
| Go back | Esc |
| Change interface language | Ctrl+O |
| Cancel a running task | Ctrl+C, then wait for cleanup |
| Exit the app | Ctrl+C on an input or selection page |

Enlarge Terminal if a size warning appears; the interface needs at least **52 columns × 20 rows**. Pages and language cannot be changed during processing.

## Download a YouTube video

1. On the Download page, paste **one video link** and press **Enter**.
2. Review the video details and choose a resolution with **↑ / ↓**. Different frame rates at the same resolution appear separately.
3. For subtitles, press **Tab** to focus the subtitle panel and select the option to include them. This panel only appears when suitable subtitles are available.
4. Press **Tab** to focus the confirmation panel, then **Enter** to download. **Enter does not start a download from the resolution or subtitle panel.**
5. Wait for downloading and file checks to finish, then find your video in Downloads using the saved-path message.

The default is the highest available resolution up to 1080p. Choose another option before starting if you prefer.

### Include subtitles

With subtitles enabled, the app saves:

| Example file | Purpose |
|---|---|
| `Video title [ID].mp4` | Video without burned-in subtitles |
| `Video title [ID].en.srt` | Separate subtitles; the language suffix varies |
| `Video title [ID]_with-subtitle.mp4` | Video with subtitles burned into the picture |

Burning subtitles takes additional time. Wait for all processing to finish. Files already saved are kept if the later subtitle step fails or is cancelled.

Only identifiable **original-language subtitles** are offered, with human captions preferred over automatic captions. Automatic translations are not included. For translated subtitles, prepare your own SRT and follow the local subtitle guide below.

Regular videos, Shorts, and single-video links with playlist parameters are supported; only the current video is downloaded. Entire playlists, channels, live streams, upcoming premieres, login-required videos, HDR, and download resuming are not supported. The app reports an error if it cannot reliably select the audio track.

## Compress a local video

1. Press **Tab** to focus the top navigation and select Compress with **← / →**.
2. Press **Tab** to enter the input field. Drag a video from Finder into Terminal, or paste its path, then press **Enter**.
3. Review the video details and the audio track to keep. Choose a compression mode with **↑ / ↓**.
4. Press **Enter** to start. When finished, review the size change and saved path.

| Mode | When to use it |
|---|---|
| Smaller file (default) | File size matters more, and visible detail loss is acceptable |
| Higher quality | You want more detail and can accept slower processing and a larger file |

Example output: `movie_compressed.mp4`. The source stays unchanged. Resolution, aspect ratio, and frame rate are preserved, including 60fps.

**The result is not guaranteed to be smaller.** The app shows the before-and-after sizes. Larger results are labeled **Size increased** and kept so you can decide whether to use them.

Process one file at a time. Common formats such as MP4, MOV, MKV, and WebM are accepted. For videos with multiple audio tracks, only the track shown on the selection page is retained. Separate subtitle streams and cover art are not retained. Unsupported files, including HDR and rotated videos, are identified during checks.

## Add SRT subtitles to a local video

Use this when you already have subtitles or have translated them with another tool. Burned-in subtitles appear directly in the picture; they need no separate file during playback and cannot be switched off.

1. Select Subtitles in the top navigation and press **Tab** to enter the page.
2. Fill in the **video path** and **SRT subtitle path**, using **Tab / Shift+Tab** to move between the two fields. You can drag files from Finder.
3. Press **Enter** to check the files. Review the video details, subtitle count, and audio track to keep.
4. Leave “Confirm and continue” selected and press **Enter** to start, or press **Esc** to go back and make changes.
5. Find `original-name_with-subtitle.mp4` in Downloads when processing finishes.

Use a **UTF-8 SRT file** no larger than 20 MB. You can supply subtitles in different languages. This feature does not translate or upload files; it preserves subtitle text, line breaks, and cue boundaries, and leaves both input files unchanged.

If checks report invalid timing, missing characters, or text overflow, fix the subtitles or fonts as prompted and retry. Subtitle endings that extend slightly beyond the video (up to 2 seconds) are shortened automatically with a notice.

## Find results and cancel tasks

All finished files go to your system **Downloads** folder. Completion messages show their actual paths. Existing files are never overwritten: matching names receive a numeric suffix, such as `movie_compressed (1).mp4`.

Select Tasks in the top navigation to review records from **the current session**:

- Use **↑ / ↓** to choose a record and **Enter** to see saved paths, failure details, or cancellation results.
- Use **Page Up / Page Down** to scroll through details and **Esc** to return to the list.
- Records are cleared when you exit. There is no task queue or resume feature.

Press **Ctrl+C** during processing to cancel. Wait for temporary-file cleanup before continuing. Source files and already saved outputs are kept. After success, you can enter the next item; after failure or cancellation, your input remains available for retrying.

## Upgrade and uninstall

Exit all YTDock windows first, then run these commands in Terminal.

Check your version:

```sh
~/.local/bin/ytdock --version
```

Upgrade to the latest stable release:

```sh
~/.local/bin/ytdock --upgrade
```

Keep Terminal open until the upgrade completion message appears, then launch again. If an older version does not support `--upgrade`, rerun the installation command above, or download a new release and double-click `Install.command`.

Uninstall:

```sh
~/.local/bin/ytdock --uninstall
```

You can also double-click `Uninstall.command` in the extracted release folder. Downloaded and processed videos, along with external FFmpeg, are kept.

## Troubleshooting

For startup or processing problems, run the checks first:

```sh
~/.local/bin/ytdock --check
```

| Problem | What to try |
|---|---|
| `ytdock: command not found` | Reopen Terminal or use the full command `~/.local/bin/ytdock` |
| Missing FFmpeg, ffprobe, or an encoder | Install a complete FFmpeg build. With Homebrew, run `brew install ffmpeg`, or `brew upgrade ffmpeg` if already installed |
| Downloads work but subtitle burning fails | Follow the error message to check FFmpeg subtitle support and fonts, or download without subtitles first |
| Another instance is running | Exit other YTDock windows, including those waiting for input, and retry |
| macOS blocks the first launch | The package is not Apple-notarized. After confirming its source, follow the prompt in System Settings → Privacy & Security to allow it |
| A video will not download | Check your connection, confirm the video is public and supported, and try upgrading YTDock |
| A local file cannot be found | Drag it in again from Finder, or wrap paths containing spaces in paired quotes |
| Downloads is unwritable or disk space is low | Check folder permissions and free up space before retrying |
| You need the exact failure reason or saved path | Open Tasks, select the record, and press Enter |

If FFmpeg is installed in a custom location, launch with:

```sh
~/.local/bin/ytdock --ffmpeg-dir "$HOME/tools/ffmpeg/bin"
```

## Developer resources

For source usage, packaging, and implementation details, see the [project specification](specs/ytdock.md), [installation and packaging](specs/ytdock-installation.md), [implementation and verification record](specs/implementation.md), and [startup and upgrades](specs/startup-upgrade.md).
