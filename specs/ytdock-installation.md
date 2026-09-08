# YTDock 品牌与一键安装计划

## 1. 目标

将现有下载与压缩 CLI 统一命名为 YTDock，并通过 GitHub 提供一条命令下载安装的体验。

仓库：https://github.com/GrahamQuan/ytdock

目标安装命令：

curl -fsSL https://raw.githubusercontent.com/GrahamQuan/ytdock/main/install.sh | bash

安装完成后运行：

ytdock

直接进入现有交互界面，保留下载、压缩和语言选择功能。

## 2. 统一命名

- 产品名：YTDock。
- GitHub 仓库名：ytdock。
- 主终端命令：ytdock。
- 更新 CLI 标题、帮助、Python 命令入口、打包脚本和双语 README。
- 安装包采用明确的版本、系统和架构命名：
  - ytdock-v<version>-macos-arm64.tar.gz
  - ytdock-v<version>-macos-arm64.zip
- 初期只发布经过验证的 macOS 14+ / Apple Silicon 包。
- Intel 支持完成构建与实际验证后，再发布 x86_64 包。
- 不宣称支持 Linux 或 WSL2。

## 3. 依赖边界

内置：Python、QuickJS、yt-dlp 和 yt-dlp-ejs、UI 依赖与全部语言文件。
外部依赖：FFmpeg 和 ffprobe。
用户无需安装 Python、QuickJS、uv 或 Node.js。
继续复用外部 FFmpeg 的发现和兼容性检查。缺失时允许安装 YTDock，提示用户安装 FFmpeg；不自动安装、更新或卸载 FFmpeg。

## 4. 安装目录与终端入口

- 程序：~/.local/share/ytdock。
- 命令：~/.local/bin/ytdock。
- 双击启动器：~/.local/bin/YTDock.command。
- 普通用户安装，无需 sudo。绝对路径连接命令与程序，支持空格目录。不覆盖其他工具占用的命令或目录。
- 必要时向默认 zsh 的 ~/.zshrc 添加带 YTDock 标记的幂等 PATH 块；保留其他配置。其他 shell 给出手动说明。
- 安装脚本不能改变父终端环境；提示重新打开终端或执行 export PATH="$HOME/.local/bin:$PATH"，并展示可立即运行的绝对路径。

## 5. GitHub 一键安装脚本

根目录 install.sh 兼容 macOS 自带 Bash。检查系统、版本、架构，通过 GitHub Releases 查询最新稳定版本，确认平台包与 SHA256 文件存在。在独立临时目录下载、验证后解压并调用包内安装器，配置入口与 PATH，检查 FFmpeg，显示启动方法，清理临时目录。

所有下载 HTTPS；明确处理网络失败、限流、产物缺失、校验失败。校验失败不安装；下载/解压失败不破坏已有安装。Ctrl+C 清理；结构化参数，不执行拼接字符串。管道运行不从标准输入询问，不自动启动 CLI。SHA256 仅验证完整性，不替代 Apple 签名/公证。

## 6. GitHub Releases 发布

一键打包输出同内容 ZIP、tar.gz 和各自 SHA256。发布顺序：更新版本、测试构建、验证安装/启动/语言/压缩/卸载、上传 Release、验证公开地址和校验、最后将 README 命令标为可用。

准备 GitHub Actions 工作流。实际推送、标签和 Release 单独执行。发布前不把未存在的入口描述为已可用。本次用户明确要求不 commit、不 push。

## 7. 升级与旧名称边界

重复安装可升级；先退出所有旧实例。安装器只管理 YTDock，使用 ytdock 状态目录及统一的新锁；不迁移、识别或删除历史 download-youtube-cli、yt 目录及命令，不保留旧名称别名。失败回滚仍适用于 YTDock 自身。

## 8. 卸载

支持 ytdock --uninstall 和 Uninstall.command。只删除本工具拥有的程序/命令/启动器，保留视频、FFmpeg 和其他工具。PATH 块仅在与记录一致时移除，不删除用户原 PATH 或整个 ~/.local/bin。活动实例阻止卸载。提示重开终端清除缓存或残留 PATH。

## 9. 双语文档

README.md 英语、README.zh-CN.md 简体中文并互链。包含产品、系统要求、一键安装和手动安装、启动/PATH、下载/压缩/Ctrl+O、FFmpeg、自定义路径、升级、旧名称保护与卸载、排错、签名/平台验证状态、开发打包。

## 10. 验证

隔离安装目录、临时 HOME 和 shell 配置，保护真实环境。覆盖全新/重复/升级/回滚、旧名称/第三方保护、PATH 幂等和改动保护、下载失败/缺产物/校验失败/磁盘不足/取消、FFmpeg 缺失/不完整/兼容、新终端和双击/精简 PATH、默认英语/Ctrl+O/语言文件、安装版真实下载压缩取消卸载，保留视频和外部工具。准确记录未验证的 Intel、另一台 Mac、公证。

## 11. 验收标准

公开安装命令实际可用；支持平台无需 Python、QuickJS、uv、Node.js；启动指引可执行，PATH 生效后 ytdock 进入 UI；原功能正常；安装升级失败保护程序与视频；卸载仅移除自有内容；README 与实际发布/验证一致。

## 历史验证记录（0.6.0，2026-09-08）

以下迁移与旧锁兼容记录仅描述历史版本，已被当前无兼容别名的命名规格取代。

- 本机 macOS 14.8.4 / arm64；版本 0.6.0。
- `uv run pytest -q`：183 项通过；Ruff 检查、格式检查与 `bash -n install.sh` 通过。
- 隔离 HOME、CFFIXED_USER_HOME、安装前缀和 Downloads：全新安装、重复安装、升级、卸载，新 zsh 的 PATH，精简 PATH 下终端及 `.command` 启动器，默认英语、Ctrl+O 双语选择通过。
- 实际安装版：公开视频解析、下载、检查发布、下载取消；两种压缩、60fps、压缩与检查期间取消、源文件与成品保护通过。
- 使用真实 0.5.1 包安装后迁移至 YTDock，通过后继续启动、压缩、升级、卸载；旧锁兼容另有测试覆盖。
- 失败模拟覆盖网络、GitHub 403/404、产物缺失、校验不匹配、损坏归档、磁盘写入失败、取消、升级回滚，以及第三方命令和已修改 shell 配置的保护。未通过耗尽真实磁盘来测试磁盘不足。
- 打包入口验证 ZIP/tar.gz 内文件与链接的内容一致，并为两个归档分别生成 SHA256；ZIP 禁止附加 macOS 资源旁文件。
- GitHub 最新稳定 Release 接口查询为 404。尚未验证公开一键安装；本次未 commit、push、创建标签或发布 Release，Actions 未远程运行。
- 只验证本机；未验证 Intel、另一台 Mac、下载隔离属性下的 Finder/Gatekeeper 首次打开或 Apple 公证。启动器通过 PTY 和精简 PATH 执行验证，并非实际 Finder 双击验证。包为 ad-hoc 签名，未 Developer ID 签名或公证。

最终归档检查：ZIP 与 tar.gz 的 1,316 个文件/链接内容一致，两份 SHA256 均匹配，ZIP 解压后通过包清单校验。使用真实 tar.gz、模拟 GitHub HTTPS 响应，在临时 HOME 中以 `bash` 标准输入运行安装脚本；首次安装、重复安装、PATH 幂等、新 zsh 中 `ytdock --check`、卸载及视频保留全部通过。这不等于已验证公开 GitHub 下载。

产物大小：ZIP 29,297,971 字节；tar.gz 28,517,678 字节；安装目录实际占用 57,956 KiB。构建产物位于 `dist/`，未上传。
