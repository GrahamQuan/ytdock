# YTDock

[English](README.md) · **简体中文**

在 macOS 终端中下载 YouTube 视频、压缩本地视频，或烧录本地 SRT 字幕。按 **Tab / Shift+Tab** 切换焦点，导航获得焦点时用 **← / →** 切页，成品统一保存为 MP4，默认存入系统 Downloads。

- **下载**：支持单个普通视频和 Shorts，可选择实际可用的分辨率与帧率。
- **压缩**：提供「体积优先」和「画质优先」，保留源尺寸、宽高比和实际帧率，包括 60fps。
- **文件保护**：不覆盖已有文件，不修改压缩源文件；取消或失败时清理本次任务的半成品。

当前版本 **0.7.1**，提供 **macOS 14+ / Apple Silicon（arm64）** 安装包。

## 快速开始

### 1. 安装程序

以下命令安装最新稳定版 macOS arm64 安装包。如果没有兼容的稳定 Release，脚本会停止，不执行安装。

```sh
curl -fsSL https://raw.githubusercontent.com/GrahamQuan/ytdock/main/install.sh | bash
```

脚本通过 HTTPS 下载最新稳定 Apple Silicon 包，验证 SHA256 后调用安装器，无需输入回答，也不会自动启动 CLI。网络失败、产物缺失或校验失败时停止安装。SHA256 只检查完整性，不能替代 Apple 签名或公证。

手动安装可从 [GitHub Releases](https://github.com/GrahamQuan/ytdock/releases) 获取 ZIP，或使用本地构建的安装包：

完整解压 `ytdock-v0.7.1-macos-arm64.zip`，双击 `Install.command`。程序安装到当前用户目录，无需管理员权限。

| 依赖 | 是否需要自行安装 |
|---|---|
| Python、QuickJS、yt-dlp、yt-dlp-ejs、UI 依赖 | 已内置 |
| FFmpeg 和 ffprobe | 需要外部提供 |
| uv | 使用安装包不需要；开发和打包需要 |

安装后会检查 FFmpeg。缺失或不兼容时，程序本身仍可安装，但需要补齐依赖才能使用。已使用 Homebrew 的用户可运行：

```sh
brew install ffmpeg
```

安装器不会自动安装、更新或卸载 FFmpeg。

### 2. 启动

```sh
~/.local/bin/ytdock
```

如果 PATH 已包含 `~/.local/bin`，可直接运行 `ytdock`。也可以在 Finder 中双击安装后的 `~/.local/bin/YTDock.command`。

想先试用而不安装，可在完整解压的目录中双击 `Start.command`。

首次写入 PATH 后，请重新打开终端，或执行：

```sh
export PATH="$HOME/.local/bin:$PATH"
ytdock
```

安装脚本不能改变父终端环境，`~/.local/bin/ytdock` 可立即运行。其他 shell 或自定义 `ZDOTDIR` 请手动将提示的 export 加入对应配置。安装器不会覆盖符号链接或不安全的 shell 配置文件。

### 3. 选择操作

| 操作 | 按键 |
|---|---|
| 在导航与页面内容之间循环焦点 | Tab / Shift+Tab |
| 导航获得焦点时循环切页 | ← / → |
| 打开语言选择页（输入页、选择页） | Ctrl+O |
| 提交 URL 或文件路径 | Enter |
| 选择分辨率或压缩方式 | ↑ / ↓ |
| 开始处理当前选项 | Enter |
| 选择页返回输入页 | Esc |
| 压缩输入页返回下载模式 | Esc |
| 退出，或取消任务并清理 | Ctrl+C |

交互界面使用一个全屏应用，退出时恢复原终端画面。切换主页面会保留各页输入、光标、焦点和选项，包括字幕页的两个路径。Tab 栏水平居中，各项间隔三个空格，当前模式以强调色、加粗和方括号标记。上方使用三行字符 Logo；窄矮窗口自动改为单行 YTDock，并收起装饰留白。处理期间不能切换模式；同一用户只能运行一个实例。完成后可继续输入下一项。

## 界面语言

默认显示英语。在输入页或选择页按 **Ctrl+O** 打开语言选择页，默认选中当前语言。使用 **↑ / ↓** 选择，**Enter** 确认，或 **Esc** 放弃修改。返回原页面后，输入内容、光标位置和当前选项都会保留。

```text
Select language / 选择语言

❯ English
  简体中文

↑↓ Select / 选择 · Enter Confirm / 确认 · Esc Cancel / 取消
```

也可启动时指定：

```sh
~/.local/bin/ytdock --lang en
~/.local/bin/ytdock --lang zh-CN
~/.local/bin/ytdock --lang zh-CN --check
```

语言设置仅作用于本次运行，下次启动默认英语。处理期间不能切换语言；进度、取消、完成和错误提示沿用开始任务时的语言。视频标题、文件路径、音轨语言标签及成品文件名不会翻译。

英语界面中的 **Smaller file** 对应默认的「体积优先」，**Higher quality** 对应「画质优先」。

## 下载 YouTube 视频

1. 在默认下载页粘贴一个视频 URL，按 Enter。
2. 查看标题、时长、实际宽高、帧率和预计大小。
3. 使用 ↑ / ↓ 选择分辨率，Tab 移至字幕框（有字幕时）及确认框；仅在确认框按 Enter 下载。

默认选择不超过 1080p 的最高可用分辨率；如果全部高于 1080p，则选最低档。同一分辨率的不同帧率会分别列出，需要转码的选项会提前标明。

支持普通视频、Shorts，以及带播放列表参数的单视频链接（只下载当前视频）。暂不支持纯播放列表、频道、直播、未开始的首播、登录/Cookie、HDR、翻译字幕、封面或断点续传。

下载时分别显示视频、音频及后处理阶段。兼容的流直接复制，其余流按需转码；完成检查和保存后才提示完成。


音轨先确定身份，再比较兼容编码和码率。明确原声优先；只有一个音轨时允许自动选择，但没有原声依据时不称为原声。AAC、Opus 和不同码率属于编码版本，不算多个音轨。多个音轨时，仅使用明确的原始语言，排除明确翻译配音后必须唯一匹配。原始语言未知、无匹配、多候选及身份或标记冲突分别报错；不根据默认音轨、标题、界面语言或地区猜测。音视频合一格式及格式刷新也遵循同一规则。

选择规则及真实视频验证见[音轨规格](specs/audio-selection.md)。单音轨自动选择不会成为原语言字幕的判断依据。字幕仍要求独立的原语言证据，人工同语言字幕优先、原生自动字幕后备，拒绝自动翻译轨；文件使用 `.en.srt`、`.es.srt`、`.ja.srt` 等语言后缀。

## 可选原语言字幕

视频有可用的原声语言字幕时，分辨率页显示 **字幕选择框**（Tab / Shift+Tab 切换框，↑↓ 调整框内选项），可选择「有字幕」；每个新视频默认「不带字幕」。没有可用的同语言字幕轨道时隐藏字幕入口。原声同语言的人工字幕优先、原生自动字幕后备，界面显示实际来源；不使用自动翻译轨、不做翻译或语音识别。

程序先保存并通知 `标题 [ID].mp4`（标准 H.264/AAC）和 `标题 [ID].<语言代码>.srt`（实际烧录所用的处理后字幕，单行、每条最多 42 字符、时间不重叠），然后自动生成 `标题 [ID]_with-subtitle.mp4`。烧录成品使用 **H.265/HEVC、hvc1、CRF 27、medium、yuv420p、faststart**，AAC 直接复制，保留源分辨率、宽高比和实际帧率，无音频不添加音轨。字幕为白字、薄黑描边、无阴影；过宽文字会缩小以适应画面。

字幕模式额外要求外部 FFmpeg 提供 **libx265、subtitles/libass、可用字体和 HEVC 解码**。缺少能力时说明原因，可返回选择普通下载，不自动安装依赖。

重名不会覆盖。**已保存文件不会因后续失败或取消删除**，部分发布成功时明确报告实际路径。原始字幕和半成品只留在临时任务目录并随任务清理。带字幕成品通过编码、时长、完整解码和抽帧对比检查后才显示全部完成；没有继续烧录入口。

字幕时间以 ffprobe 测得、检查通过的源 MP4 实际时长为准，统一使用整数毫秒。字幕在视频内开始、结束时间最多超出 **2 秒** 时，截短到视频结尾并提示，保留全部文字。完全位于视频外、超出容差或无效的时间段会被拒绝，错误包含字幕时间和实际视频时长；字幕为空、时间无法解析及重分段失败分别提示。

## 烧录本地 SRT 字幕

将焦点移至顶部导航，用 **← / →** 切换到 **[字幕]**，再按 **Tab** 进入内容，在两个输入框中分别填写视频和 **UTF-8 SRT** 路径。**Tab / Shift+Tab** 在导航及两个输入框间切换，**Enter** 检查。支持空格、中文、配对引号、`~` 和 Finder 拖入路径；**Ctrl+O** 切换界面语言时保留输入。

可以使用外部 AI 翻译后的任意语言 SRT。本模式不翻译、不上传文件，保留字幕文字、换行和分段，不套用 YouTube 字幕的 42 字符重分段规则。无效编码、时间重叠/乱序及无效时间段会被拒绝；末尾最多超出 2 秒时提示并截短，明显越界或完全位于视频外则报错。预检字体和每个不同字幕块，缺字或溢出时需修正字体或 SRT。文件最大 20 MB，不支持 ASS 指令及控制字符。

检查页显示视频信息、字幕条数、保留音轨和输出参数。**Enter** 直接烧录，**Esc** 返回并保留两个路径。输出为 Downloads 下的 `原文件名_with-subtitle.mp4`：H.265 / hvc1、CRF 27、medium、yuv420p、faststart，保留尺寸、宽高比和实际帧率。仅保留默认音轨（没有默认标记时取第一轨），AAC 复制，其他编码转为 AAC 192 kbps；无音频不添加音轨，多音轨会提前提示。

完成媒体检查、完整解码及字幕抽帧对比后，只发布带字幕成品。输入文件保持不变，重名自动加数字后缀。处理中 **Ctrl+C** 等待取消和清理后返回字幕输入页；成功后清空路径，可继续处理下一项。

## 压缩本地视频

1. 将焦点移至导航，用 ← / → 选择「压缩」，Tab 进入输入框。
2. 粘贴一个视频文件的绝对路径，或从 Finder 拖入终端，按 Enter。
3. 查看源信息和将保留的音轨，选择压缩方式，按 Enter 开始。

支持中文、空格、`~`、配对引号和 Finder 拖入产生的转义路径，例如：

```text
"~/Movies/My Video.mp4"
```

只处理单个本地文件，不支持目录或批量输入。路径文本不会作为 shell 命令执行。

| 方式 | 适用取向 | 视频参数 | 音频处理 |
|---|---|---|---|
| 体积优先（默认） | 压缩较强，可能明显损失细节 | libx264 · CRF 30 · superfast | AAC 128 kbps |
| 画质优先 | 尽量保留细节，处理较慢 | libx264 · CRF 23 · medium | AAC 源复制，否则转为 AAC 192 kbps |

**压缩后不保证比原文件小。** `superfast` 偏向处理速度，并非最佳压缩效率。完成后会显示输入大小、输出大小和体积变化；成品更大时仍保留，并明确提示“体积增加”。

两种方式都保留源尺寸、宽高比和实际帧率，包括 60fps、59.94fps。只保留主视频和默认音轨；没有默认标记时选首轨，多音轨文件会提前提示保留哪一轨。无音频源不添加静音，不保留字幕、封面或其他附加流。

### 支持范围

- 接受 MP4/MOV、MKV/WebM、AVI、FLV、MPEG/TS、Ogg、ASF 等单文件容器，实际解码能力取决于外部 FFmpeg。
- 支持常见 8-bit SDR YUV；10-bit SDR 需有明确的 SDR 色彩标记，输出转为 8-bit。明确的全范围 SDR 转为有限范围，保留已知色彩矩阵和传递特性。
- HDR、旋转/显示矩阵、隔行、奇数尺寸，以及无法可靠确认色彩、帧率、时长或宽高比的源会报错，不会擅自旋转或缩放。

## 输出与文件保护

默认保存到通过系统接口获取的 Downloads 文件夹。输出为 **MP4 · H.264 · 8-bit yuv420p · AAC · faststart**；无音频源保持无音频。压缩输出使用 `avc1` 视频标记。

| 来源 | 文件名示例 |
|---|---|
| YouTube 下载 | `视频标题 [视频ID].mp4` |
| 本地压缩（两种方式相同） | `movie_compressed.mp4` |
| 压缩文件重名 | `movie_compressed (1).mp4` |

原文件名中的文字会保留；压缩只添加英文 `_compressed` 后缀。文件名会清理非法字符并限制长度，重名不会覆盖。

处理在 Downloads 下的独立隐藏临时目录中进行。成品经过编码、尺寸、帧率、宽高比、音频、时长检查及完整解码后，才发布到最终路径。

Ctrl+C 会等待工作进程及子进程停止，再清理本次任务临时文件，返回当前模式的输入页并保留已输入的 URL 或路径。输入页或选择页按 Ctrl+C 才退出程序，退出码为 130；清理期间重复按键不会跳过清理。失败也会清理；清理失败会报告残留路径。源视频、已有文件和已发布成品不会被删除。强制结束进程后，下次启动会尝试清理确认已失效的本工具任务目录。

## 升级与卸载

**先退出所有 `ytdock` 实例，包括停留在输入页的实例。** 再运行新版 `Install.command` 升级，或运行以下命令卸载：

```sh
~/.local/bin/ytdock --uninstall
```

也可双击解压目录中的 `Uninstall.command`。卸载仅删除本工具拥有的程序和未被改动的启动器，保留视频、外部 FFmpeg 和其他工具。安装器不会覆盖其他工具占用的命令或目录。默认 zsh 下，仅在需要时向 `~/.zshrc` 添加带 YTDock 标记的 PATH 块，重复安装不会重复写入。卸载只移除记录中且未被修改的块，保留用户修改过的块及其他配置。卸载后重新打开终端，以刷新 PATH 和命令缓存。

安装器只管理 YTDock，不识别、迁移或删除历史 `download-youtube-cli` 目录和 `yt` 命令。切换本版前请退出所有旧实例：本版使用独立的 `ytdock` 状态目录和任务锁，不兼容旧环境变量和 Python 包名。重新运行安装器可升级 YTDock；升级失败会回滚程序。

默认程序目录为 `~/.local/share/ytdock`。空锁文件会保留，以维护单实例保护。

需要自定义安装位置时，在解压目录中运行：

```sh
./ytdock/ytdock --install --prefix "$HOME/my-tools"
"$HOME/my-tools/bin/ytdock" --uninstall
```

## 依赖检查与排错

先运行以下命令，查看依赖状态、Downloads 和最终选择的可执行文件路径：

```sh
~/.local/bin/ytdock --check
```

| 情况 | 处理方式 |
|---|---|
| `ytdock: command not found` | 使用 `~/.local/bin/ytdock`，或自行将 `~/.local/bin` 加入 PATH |
| 提示已有实例运行 | 退出其他 `ytdock` 实例后重试；安装、卸载和打包自检也受此限制 |
| 缺少 ffprobe 或编码器 | 安装完整 FFmpeg，或指定另一套兼容程序 |
| 双击启动找不到自定义 FFmpeg | 从终端使用下方路径参数；双击不一定继承终端环境变量 |
| Downloads 不存在或不可写 | 恢复系统 Downloads 目录并检查权限、磁盘空间 |
| macOS 阻止首次打开 | 当前包未完成 Apple 公证，可能需要在系统“隐私与安全性”中允许打开 |

### 自定义 FFmpeg 路径

```sh
~/.local/bin/ytdock --ffmpeg-dir "$HOME/tools/ffmpeg/bin"
```

也可分别指定两个程序，或使用环境变量：

```sh
~/.local/bin/ytdock --ffmpeg "$HOME/tools/ffmpeg/bin/ffmpeg" --ffprobe "$HOME/tools/ffmpeg/bin/ffprobe"

export YTDOCK_FFMPEG_DIR="$HOME/tools/ffmpeg/bin"
~/.local/bin/ytdock
```

环境变量也支持 `YTDOCK_FFMPEG` 和 `YTDOCK_FFPROBE`。路径设置不会自动保存到 shell 配置。

查找优先级为：**显式路径 → PATH → 常见 Homebrew 位置**。只有未提供命令行路径时，才读取环境变量。Homebrew 位置包括 `/opt/homebrew/bin`、`/usr/local/bin` 及其对应的 `opt/ffmpeg/bin`。

优先选择同目录配套程序；只指定一个程序时，先为它寻找配套程序。自动搜索先尝试完整配套安装，最后才组合分别通过检查的工具。不兼容候选会继续回退；显式路径回退会提示，全部失败会列出具体原因。

启动检查要求两个程序可执行，并具备 H.264、VP8、VP9、AV1 软件、AAC、Opus、Vorbis 解码能力。FFmpeg 还需 `libx264`、AAC 编码器及 `-fps_mode`；AV1 软件解码要求 `libdav1d` 或 `libaom-av1`。

## 开发与打包

开发目录为 `ytdock/`，第一方 Python 包位于 `src/ytdock/`。以下命令从仓库根目录运行。开发/构建机需要 Python 3.11+、uv、Xcode Command Line Tools 和兼容的外部 FFmpeg/ffprobe。脚本不安装系统软件。

### 从源码运行

```sh
./package.command --runtime-only
uv run ytdock --check
uv run ytdock
```

### 一键打包

先退出所有 `ytdock` 实例，再运行：

```sh
./package.command
```

脚本同步锁定依赖，校验并构建 QuickJS，封装 Python 和项目依赖，执行隔离安装、启动、压缩、取消、升级和卸载自检，最后生成 ZIP、tar.gz 和各自的 SHA256：

```text
dist/ytdock-v0.7.1-macos-arm64.zip
dist/ytdock-v0.7.1-macos-arm64.zip.sha256
dist/ytdock-v0.7.1-macos-arm64.tar.gz
dist/ytdock-v0.7.1-macos-arm64.tar.gz.sha256
```

QuickJS 版本及校验值见 [packaging/sources.json](packaging/sources.json)。许可证和相关源码随包放在 `ytdock/THIRD_PARTY`、`ytdock/SOURCES`。构建缓存位于 `build/` 和 `.runtime/`；不会下载、构建或内置 FFmpeg/ffprobe 及其专用编码库。

### 验证

```sh
uv run pytest -q
uv run ruff check src tests scripts packaging
uv run ruff format --check src tests scripts packaging
uv run python scripts/smoke_release.py build/release/ytdock-v0.7.1-macos-arm64/ytdock --online
```

`--online` 额外使用公开短视频验证安装版下载、发布和取消。媒体与安装验收使用临时目录；启动器在 PTY 和精简 PATH 下验证。请串行运行安装包自检，避免触发单实例保护。

当前提供 arm64 包；Intel 需要在 Intel Mac 构建，尚无 Intel 或其他 Mac 的人工验收结果。安装包采用 ad-hoc 签名，尚未完成 Developer ID 签名及 Apple 公证。各版本实际执行的检查与限制见 [实现与验证记录](specs/implementation.md)。

### 翻译维护

每种语言使用独立 JSON：[en.json](src/ytdock/locales/en.json) 和 [zh-CN.json](src/ytdock/locales/zh-CN.json)。共用稳定的英文标识键，例如 `download.complete`、`compression.choose_mode`，值为对应语言的文案。[i18n.py](src/ytdock/i18n.py) 从 `locales/` 目录加载。

新增语言时，将 `en.json` 复制为 `<语言代码>.json`，翻译其中的值，并将 `language.name` 设置为该语言的原生名称。重启后会自动出现在语言选择页和 `--lang` 参数中。缺失文案回退英语；未知键仍报错。占位符及格式须与英语保持一致；先翻译模板再填入用户内容，不翻译标题或路径。打包会收集全部语言 JSON。

## 规格

- [YouTube 下载规格](specs/ytdock.md)
- [外部 FFmpeg 契约](specs/external-ffmpeg.md)
- [压缩模式规格](specs/compression.md)
- [中英双语规格](specs/i18n.md)

## 发布准备

手动触发的 [GitHub Actions 工作流](.github/workflows/prepare-release.yml) 运行测试并构建 arm64 产物，不发布 Release。先更新 `pyproject.toml` 和 `uv.lock`，测试并打包；之后单独创建 `v<version>` 标签，将两个归档及各自的 `.sha256` 上传到稳定 Release。发布后验证公开地址、校验文件和 curl 安装。ZIP 与 tar.gz 包含相同程序内容。

不发布 Linux、WSL2 或 Intel 安装包。另一台 Mac 和 Apple 公证尚未验证；本次实际验证记录见 [YTDock 计划](specs/ytdock-installation.md)。

- [Original-language subtitle specification / 原语言字幕规格](specs/subtitles.md)

- [YTDock naming contract / 命名统一规格](specs/ytdock-naming.md)

## 任务与结果

「任务」页按最新在前显示本次运行的记录。↑↓ 选择、Enter 查看详情、Esc 返回列表；详情支持 Page Up / Page Down 滚动，包含保存路径、失败阶段和原因、取消及清理结果。记录仅保存在内存，退出即清空；不提供队列、并发或继续处理功能。

读取信息、选择及处理属于同一条记录，格式失效后重新选择不会新增记录。尚未处理就返回输入页时标为「未开始」。成功后返回当前输入页并清空本次输入；失败或取消后保留输入。各模式的结果提示保留到下一次任务开始，部分保存和清理失败会明确提示，完整信息见任务页。已保存成品保持安全。

处理和清理期间禁止切换焦点、主页面或语言。终端至少需要 **52 列 × 20 行**，出现尺寸提示时请继续扩大窗口；列表和详情支持滚动，缩放及切换语言后重新布局。`--help`、`--check`、安装和卸载继续使用普通终端输出。

- [全屏界面与任务记录规格](specs/fullscreen-tasks.md)

## 版本、升级与启动检查（开发版）

以下功能已在源码实现，**尚未包含在公开 v0.7.1 安装包中**。本次未重新打包，也未验证真实跨版本二进制升级。

```sh
ytdock --version   # 同样支持 ytdock -v
ytdock --upgrade
```

版本查询输出 `YTDock <version>`，不联网、不检查 FFmpeg、不进入全屏。当前检出可执行 `uv run ytdock --version`；开发环境执行 `--upgrade` 会明确拒绝，不修改开发环境。

未来包含这些改动的安装版可查询 GitHub 最新稳定 Release，显示本地与目标版本，通过 HTTPS 下载并校验 SHA256。本地版本相同或更高时不重装、不降级。升级前先退出其他实例，包括停留在输入页的实例。保留自定义安装目录和已有配置，不改视频或 FFmpeg。下载阶段 Ctrl+C 可取消；替换阶段完成安全收尾或回滚。独立更新进程继承全局锁，等待旧进程退出后才替换，并输出完成提示与启动命令；请保持终端打开，完成提示可能出现在 shell 提示符之后。Ctrl+O 语言选择仍是进程内状态，升级提示语言由 `--lang` 控制。

交互启动显示真实的 Python 组件、QuickJS、FFmpeg、ffprobe、Downloads、锁、清理和界面加载检查。成功项保留，待检查项弱化，慢速子进程不阻塞活动指示器。Ctrl+C 等待安全清理并恢复终端；失败报告在退出备用屏幕后重新打印。帮助、版本、`--check`、安装、卸载、升级保持普通输出，各操作参数互斥。反馈从程序入口执行后开始，不包含 macOS 或打包运行时加载程序之前的等待。

实现与验证限制见[启动与升级规格](specs/startup-upgrade.md)。

YouTube 下载与本地 SRT 导入统一使用所选视频流时长校验字幕，缺失时才回退至容器时长。下载的 SRT 在时间修正后保存，同一视频再次导入无需重复修正。本地导入继续保留文字与分段；沿用 2 秒末尾容差，并显示实际截短的毫秒数。

下载成品按源音视频流的实测时长校验。YouTube 元数据仅用于粗粒度完整性检查（为整秒精度保留至少 1 秒容差）；成品仍采用较严格的时长容差及完整解码检查。失败提示明确指出失败项，时长错误显示预计与实际秒数。

本地字幕确认页提供“确认继续”和“取消返回”，默认选中确认。↑↓ 选择，Enter 执行当前选项，Esc 直接返回。

下载选择页中，Tab / Shift+Tab 在导航、分辨率、字幕、确认框间循环，↑↓ 只调整当前框内选项。当前内容框仅以边框颜色标识焦点；仅在确认框按 Enter 开始下载，Esc 返回。无字幕时跳过字幕框。
