# v0.7.1 发布验收

首次公开发布，目标 macOS 14+ / Apple Silicon；内置 Python、QuickJS、yt-dlp/ejs 和 UI，FFmpeg/ffprobe 为外部依赖。ad-hoc 签名；未进行 Apple 公证，未验证 Intel 或另一台 Mac。

2026-09-08 本机检查：

- 当前源码完整回归：304 passed；Ruff 静态与格式检查通过。
- 冻结程序在隔离 HOME、精简 PATH 下安装及启动；终端和双击启动器入口、Ctrl+O 中英文切换、输入保留、退出均通过 PTY 检查。没有从 Finder 人工双击验收。
- 外部 FFmpeg 发现和自定义路径、内置 QuickJS 使用、两种压缩、60fps、转码与成品检查通过。
- 字幕 HEVC 烧录、抽帧检查、源视频与 SRT 先发布、烧录/检查取消后成品保留通过临时生成样本验证。
- 安装版真实下载 jNQXAC9IVRw：解析、下载、检查与发布通过；下载取消及半成品清理通过。该次没有执行在线视频字幕烧录；本地样本已验证烧录。
- 隔离升级、卸载及保留已有文件、外部媒体工具通过。
- PTY 检查适配字符标题与 ANSI 增量重绘，退出时持续读取输出，避免测试自身阻塞终端。

## 公开发布与一键安装

已发布 https://github.com/GrahamQuan/ytdock/releases/tag/v0.7.1 ，标签对应源码 f4051dfe1371f05e4648804a3fda9ef5527d2ab0。

- ZIP：29,408,522 字节；SHA256 `1b8edd46da4bc24bf767f384ccc47f44fcf14d9529fb73439e8f52b02398b1c4`。
- tar.gz：28,619,652 字节；SHA256 `6920ece06b5477222d33cfcd82e204839f511013a8dde402f4c3ce4e33f2af8b`。
- 两种归档内容一致；公开 ZIP 地址返回 HTTP 200。
- 实际执行公开 `curl -fsSL https://raw.githubusercontent.com/GrahamQuan/ytdock/main/install.sh | bash`，退出码 0，SHA256 校验成功。
- 测试使用临时 HOME、精简 PATH 和 macOS 可用 UTF-8 locale；安装后 `--check`、新 zsh 中定位命令、PTY 启动与语言切换通过。
- 卸载成功，测试视频保留，bootstrap 临时目录已清理；未改动开发者现有安装。
- 初次公开测试的后续路径断言因 macOS `/var` 与 `/private/var` 别名失败；改用规范化路径及文件身份比较后，整条链路通过。
