# YTDock 命名统一（0.7.1）

本次按用户明确的 run coding 指令实施，无旧名称兼容别名，不 commit、不 push、不发布 Release。

- Python 包：`src/ytdock/`，入口 `ytdock.cli:main`，工作进程 `python -m ytdock.worker`。测试、脚本、打包数据与语言文件均使用此包。
- 环境变量：`YTDOCK_FFMPEG_DIR`、`YTDOCK_FFMPEG`、`YTDOCK_FFPROBE`。不读取旧名称变量。
- 应用状态：macOS Application Support 下的 `ytdock`；下载、字幕、压缩和安装/卸载统一使用此目录中的单实例锁。
- 临时任务：`.ytdock-task-<token>`，所有权 `ytdock-v1`；安装器锁 `.ytdock-installer`，构建日志和发行包自检目录均使用 ytdock 命名。
- 安装位置仍为 `~/.local/share/ytdock`、`~/.local/bin/ytdock`、`~/.local/bin/YTDock.command`。安装器仅管理能够验证归属的 YTDock 文件，保留自身升级回滚、PATH 幂等和安全卸载机制。
- 移除旧安装迁移逻辑。历史 `download-youtube-cli` 目录、`yt` 命令与旧任务目录原样保留，第三方文件不受影响；旧名仅在历史说明及明确的保护测试中出现。
- `yt-dlp`、`yt-dlp-ejs` 和外部参考技能名称保持原名。
- 完成代码和安装包验证后，将开发目录移到 `/Users/a6677/Documents/personal-projects/ytdock`，再重新同步可编辑环境并从新目录验证命令和一键打包。历史生成产物不批量替换，当前版本重新构建。
- 更换命名空间前退出所有旧版实例。新版不提供旧锁兼容，不能用它判断旧实例是否仍在等待输入。

验证包括新包导入、工作进程、语言资源、新旧环境变量边界、旧文件保护、新锁与任务所有权、安装升级回滚卸载，以及真实下载、压缩、字幕烧录和取消清理。记录本机检查结果，不把其他平台、其他 Mac 或 Apple 公证标为通过。

## 实际验证记录（2026-09-08）

- 修改前未发现运行中的下载、压缩或构建任务；移动前结束旧路径下的 Ruff 语言服务，完成构建及在线验收后再移动目录。旧开发目录不存在，未创建兼容符号链接。
- 从新目录重建 `.venv` 并同步锁定依赖。命令 shebang、activate、可编辑安装均指向新目录；`ytdock` 导入和双语资源加载正常，旧包无法导入。
- 新目录完整回归：206 项通过；Ruff 静态检查、格式检查通过。保护测试验证旧目录、旧任务和第三方命令不被删除，旧环境变量不被读取。
- 临时 HOME 中实际执行 `uv run --no-sync ytdock`，输入页、模式切换、语言选择、草稿保留及 Ctrl+C（130）通过。
- 0.7.1 安装版在移动前通过真实在线下载、人工英文字幕、HEVC 烧录及检查、下载取消；两种压缩、60fps、字幕烧录/检查取消、安装、升级与卸载均通过。
- 移动后从新目录再次执行一键打包，并通过安装版隔离验收。ZIP/tar.gz 文件与链接内容一致，分别输出 SHA256。
- 所有安装和媒体验证均使用临时 HOME/目录，未改动用户实际安装、FFmpeg 或视频。历史生成产物保留，未批量替换其内部路径。
- 仅本机 macOS 14.8.4 / Apple Silicon 验证。未验证另一台 Mac、Intel、Gatekeeper 隔离首次打开或 Apple 公证；未 commit、push、创建标签或发布 Release。
