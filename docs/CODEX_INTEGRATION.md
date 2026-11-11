# 直接接入本机Codex

用户于2026-11-11要求让Codex作为工作台模型。已增加独立codex_cli提供者，原HTTP提供者保留。

## 实际连接方式

前端→后端Provider→本机登记的Codex CLI→账户可用的Codex模型→结构化JSON与事件→任务分析模块。采用官方支持的非交互调用，附上本项目的真实示教图像、时间表和历史经验，校验JSON后生成任务提案。

当前聊天不会自动成为HTTP服务，也不自动继承本次对话、开发子Agent、工具权限和全部上下文。当前接入范围是示教任务分析；场景构建/规划Agent和真实硬件仍按各自里程碑实现。

## 项目内登录

为满足所有运行文件留在新目录、不干扰其他项目的要求，使用本项目独立CODEX_HOME：`.runtime/codex-home`；SQLite状态在`.runtime/codex-sqlite`。系统HOME不改变，全球Codex auth.json/config.toml不读取、不复制、不修改。

本机全局Codex登录状态与项目内状态分开。前端设置选择“本机Codex”后，点击官方设备登录，打开界面显示的OpenAI官方URL，输入当次短期验证码。不要把密码/长期令牌发到聊天中。凭据仅由官方CLI写入项目本地目录，.runtime整体git忽略。

命令行也可登录（不会修改父shell的Codex目录）：

```bash
cd /home/cjh/Downloads/Demo2Real-Agent-Workbench
env CODEX_HOME="$PWD/.runtime/codex-home" CODEX_SQLITE_HOME="$PWD/.runtime/codex-sqlite" TMPDIR="$PWD/.runtime/tmp" XDG_CACHE_HOME="$PWD/.runtime/cache" XDG_CONFIG_HOME="$PWD/.runtime/config" XDG_DATA_HOME="$PWD/.runtime/data" /usr/bin/codex --no-daemon -c 'cli_auth_credentials_store="file"' login --device-auth
```

CLI路径可在config/environments.yaml的codex_executable修改。登录完成后，在前端做一次真实模型请求测试，再对原始示教任务点击重试。模型为空表示使用CLI默认；指定名称则按请求模型调用。是否有访问权限以真实推理完成为准，未知型号不伪称与本会话完全相同。

## 执行边界

- 子进程禁用shell/代码执行、浏览器、插件、MCP应用、子Agent和设备工具，read-only sandbox、never approval；保留既有执行规则。
- 模型只读取显式提供的图像和文本，不能发送机器人命令。
- 图像必须是本项目实际文件；引用必须匹配已给证据。输出严格schema，失败/取消/超时不是成功。
- CLI支持JSONL事件、图像附件及结构化输出；事件/结果/模型明确报告的型号和token用量保存到任务目录。
- 工具Worker不因此变成LLM Agent。当前只有任务语义阶段调用Codex；未完成的物理验证和泛化训练不会继承为成功。

## 官方依据

- [Codex非交互模式：JSONL、结构化输出、自动化认证](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex认证与官方设备登录](https://learn.chatgpt.com/docs/auth?surface=cli)
- [CODEX_HOME及状态位置](https://learn.chatgpt.com/docs/config-file/environment-variables)

真实登录、模型调用与验收结果以本项目后续报告为准；假CLI测试只验证进程/解析/取消机制，不证明已连上模型。

## 本次真实验收

用户已完成本项目官方登录。真实文本调用成功；随后对原始示教episode0的12张三视角图像实际调用Codex，task_analysis阶段成功，输出5个阶段、物体角色、图像引用和不确定项。所有phase证据均经过schema校验；不把稀疏采样当精确动作边界，也不把影像当卡扣保持证明。

CLI没有明确回传具体模型名，显示CLI default / model_source=not_reported；不声称与当前聊天具体型号相同。真实用量保存在任务model_response.json。

当前完整workflow在simulation阶段等待任务资产/功能坐标/冻结判据配置，学习/评价/部署仍阻塞；没有因接入模型自动完成这些能力。真实模型接入报告：runs/acceptance/codex/real_demo_analysis_report.json。所有硬件命令数0。

接入后全套离线测试51 passed/1 skipped；Codex模拟可执行文件仅验证边界，另有本段真实推理证据。前端22项组件/逻辑测试及真实浏览器只读验收通过。
