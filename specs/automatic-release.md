# main 自动发布

触发：push main 或在 main workflow_dispatch。版本唯一来源 pyproject.toml；稳定三段版本，已发布跳过，新版必须高于历史稳定版本。不自动增加版本。

工作流按仓库串行运行，不取消进行中的发布。plan 只读查询；macos-14 ARM64 构建任务只读，完整测试后执行 package.command。使用本次运行的四个精确版本文件，GitHub artifact 传递给独立 publish job。发布任务才有 contents:write；发布前重新检查版本、校验 SHA256、确认标签属于当前已测试提交。先建草稿，全部上传成功后公开。上传失败可在相同提交重跑，已发布版本绝不覆盖；标签冲突停止。

保留原有仅构建手动工作流。CI 不执行联网 YouTube 下载，不宣称完成公证或真实跨版本自升级。本次通过 Node 受控 API 测试与 YAML 解析，实际 Actions 运行结果见交付记录。
