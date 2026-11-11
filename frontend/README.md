# 本地前端

React 19 + TypeScript + Vite。所有页面数据来自同源后端 `/api`，没有预置 Agent、虚构进度或模拟训练成功。

## 构建与开发

在项目根目录运行（npm 缓存与临时文件均在项目内）：

```bash
TMPDIR="$PWD/.runtime/tmp" npm_config_cache="$PWD/.runtime/npm-cache" npm ci --prefix frontend
TMPDIR="$PWD/.runtime/tmp" npm_config_cache="$PWD/.runtime/npm-cache" npm run build --prefix frontend
```

后端直接提供 `frontend/dist`。本地开发运行 `npm run dev --prefix frontend`，开发代理目标为 `127.0.0.1:8787`。

## 页面

- 工作台：读取真实任务/Agent 状态；创建数据复制导入；展示阶段、错误、事件与工件；取消及显式重试。
- 经验：检索真实案例与功能特征别名；按类别过滤；展示原始完整证据结构；记录类别/阶段/现场类型及实际观察。未知根因保留未知。
- 设置：统一外部环境路径加载、保存与自检；服务 URL/模型/API 密钥配置及真实模型请求测试。密钥仅发送后端，不持久化浏览器。
- 策略与真机：读取实际插件目录、可用状态与部署包；按高/低层单独应用离线配置，神经策略须有已训练权重和冻结签名。只显示执行服务状态，没有硬件使能/运动按钮。
- 三种 Connector 离线诊断：机构台架、位姿代理、RGBD 回归。它们是迁移旧任务的真实 Isaac 工作流，不表示通用任务自动搭建或策略训练通过。

## API 与会话

写操作先 GET `/api/session`，使用 `X-Session-Token`；后端核验本机 Origin。API 与 UI 默认同源。轮询仅读取状态，不触发重试、恢复或机器人动作。

状态来自实际工作流。Agent 卡片区分模型 Agent (`kind=llm`) 与工具 Worker；进度仅显示 Worker 报告的处理数量，不推算假百分比。

## 浏览器验收

`tests/frontend/browser_smoke.py` 使用宿主 Chrome DevTools 与 Python `websocket-client`（测试依赖），连接 `127.0.0.1:9338`。后端位于 `127.0.0.1:8787`。所有截图与报告写在 `runs/acceptance/frontend`。

验收会创建一个明确标记的真实转换数据导入任务，不调用任何硬件接口。它检查 API 连接、跨名称特征检索、环境显示、策略目录、无使能控制、目录浏览、任务创建及移动布局；它不把 UI 测试通过当成任务/仿真/策略通过。

Chrome 使用项目内 `.runtime/browser-test` profile 与项目内缓存，示例启动参数：

```bash
TMPDIR="$PWD/.runtime/tmp" XDG_CACHE_HOME="$PWD/.runtime/cache" XDG_CONFIG_HOME="$PWD/.runtime/config" google-chrome --headless=new --disable-gpu --disable-dev-shm-usage --no-first-run --no-default-browser-check --disable-background-networking --disable-component-update --disable-sync --disable-breakpad --user-data-dir="$PWD/.runtime/browser-test" --disk-cache-dir="$PWD/.runtime/browser-cache" --crash-dumps-dir="$PWD/.runtime/browser-crashes" --remote-debugging-port=9338 about:blank
PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.runtime/tmp" python3 tests/frontend/browser_smoke.py
```

在受限容器中启动 Chrome 可能还需 `--no-sandbox`；仅供本地验收，不作为用户日常浏览器配置。

实际验收记录：

- `runs/acceptance/frontend/report.json`：真实浏览器连接、页面和创建任务检查，0 浏览器异常。
- `runs/acceptance/frontend/real-demo-report.json`：保留首次故障并显式重试；完整原始 LeRobot 示教真实复制和前四阶段通过。未配置模型时，语义阶段明确待配置，后续阶段保持阻塞。
- 首次 worker 语法错误由自动问题归档保留；测试没有删除或覆盖原始失败案例。
- `tests/frontend/real_demo_acceptance.py` 是本次已存在历史 run 的续验脚本，绑定验收 run ID；新环境应先运行 smoke 脚本或修改要核验的 run。

最新只读布局验收：`layout-report.json` 检查81项真实工件均可访问、默认排除原始dataset、固定高度滚动和3相机预览；`policy-report.json` 检查无权重神经策略被拒绝、脚本离线应用及后端实际硬件命令数为0。

离线诊断参数现可在工作台折叠面板配置：`seed=712`（0–2147483647整数）、`spread_m=.025`（0–.05）、`yaw_spread_deg=8`（0–30）、`pose_noise_m=0`（0–.005）、`pose_delay_s=0`（0–.5）与 `none/push/drop/relocate/occlude/remove`。三种回归通过同一JSON接口提交，台架不使用变位/噪声/干扰，位姿代理和真实RGBD分别标记。

`tests/frontend/diagnostics_payload.mjs` 用隔离随机端口HTTP接收器验收18种有效组合及15类非法参数。它没有调用真实工作台诊断API，也没有启动GPU任务。报告 `runs/acceptance/frontend/diagnostic-payload-report.json`。

仿真RGBD结果预览：只从当前run的 `simulation/isaac_<ns>/…/vision/visualization/frame_<time>/panels.png` 工件选择，按ns（BigInt）和帧时间数值取最新。原始示教contact_sheet不参与。没有真实面板时不显示。仿真结果内 `post_release_retained=false` 会产生醒目的保持失败提示，即使工作流状态是succeeded。只读浏览器验收报告 `simulation-vision-report.json` 保存选中工件和实际图像加载证据。

## 本机 Codex（ChatGPT 登录）

模型设置可选 `codex_cli` 或原有HTTP服务。Codex程序路径统一登记在 `config/environments.yaml` 的 `codex_executable`。Codex模式不展示或提交HTTP地址/API密钥；模型可留空使用CLI默认，具体型号只有实际返回后才显示。

前端读取本项目独立 `CODEX_HOME` 的实际状态、CLI版本和路径。点击“开始官方设备登录”才发起官方登录；官方链接/一次性代码只在本地页面展示，状态轮询仅显示后端返回值。可取消本次项目登录，不调用全局logout。项目登录命令由后端按项目环境生成，前端不拼接CLI参数或访问凭据文件。

[官方Codex认证说明](https://learn.chatgpt.com/docs/auth)介绍ChatGPT登录、设备登录以及CODEX_HOME下文件凭据存储。工作台按用户的独立性要求使用本项目登录，不复制或修改全局登录，也不继承当前聊天上下文。

配置保存和实际模型调用测试分开。未登录时明确显示待登录；真实模型调用成功只代表本次调用完成，任务结论仍需独立核验。模型接入仅用于任务分析，不开放Shell或机器人控制。

相关API：`GET/PUT /api/settings/provider`、`POST /api/settings/provider/test`、`GET /api/settings/provider/codex/status`、`GET /api/settings/provider/codex/login`、`POST …/login/start`及`…/login/cancel`。登录及推理都不会在页面打开时自动触发。

`tests/frontend/provider_components.mjs` 使用隔离夹具完成22项载荷/状态/链接/型号检查及SSR组件检查。它不会启动官方登录、发起模型推理或读取全局凭据；报告 `runs/acceptance/frontend/codex-components-report.json`。

真实本机验收：用户完成项目内官方登录后，`tests/frontend/codex_live_acceptance.py` 仅以GET读取并刷新状态。实际检测 `codex-cli 0.166.0`、`/usr/bin/codex`、项目 `.runtime/codex-home` 已登录；Codex表单不含URL/API密钥，模型留空显示CLI默认。报告 `codex-live-report.json`、截图 `18-codex-project-authenticated.png` 和 `19-mobile-codex-settings.png`。本次浏览器没有发起登录、模型调用或配置保存。

## 任务定义与自动场景预览

运行详情有“运行与证据”及“任务定义 / 场景”页签。后者通过真实API读取已注册Profile和版本化task_spec：

- 由已存在的真实分析提案生成草案；选择其他Profile时，以revision/etag保护重建新草案，不直接修改Profile标签。不会重新调用模型。
- 结构概览显示实际对象资产/位姿、机械臂与相机标定、功能frames、阶段、action/retention判据、未知项和来源hash。未知位置或姿态保持null并明确显示。
- JSON编辑先做语法检查，完整字段/路径/权限由后端校验。保存新revision时发送expected_revision与原始quoted If-Match；冲突保留本地内容，可读取最新版本后合并。
- 离线冻结只生成不可覆盖的契约快照。草案也可构建仅预览；未保存编辑或未应用Profile选择时禁止冻结/构建。
- 构建按钮创建真实四节点job：经验检索、功能几何审查、scene_build、scene_validation。它不请求动作执行，不解锁接触或真机。新job可在原Agent/事件/工件视图跟踪，并返回源任务。
- 场景图只匹配当前run的 `scene_build/build_<ns>/scene.png` 及同目录 `camera_0_rgb.png / camera_1_rgb.png`，与示教contactsheet和旧simulation面板分开。可打开USD、task_spec快照、manifest和局部检查报告；坐标示意diagram单独标明。
- 场景run显示绑定的revision/hash，源任务编辑后，历史构建明确标记旧快照；旧结果不验证新草案。完整动作与松手后保持警告仍独立保留。

组件/契约测试：`TMPDIR="$PWD/.runtime/tmp" node tests/frontend/task_scene_components.mjs`，报告 `runs/acceptance/frontend/task-scene-components-report.json`。18组检查使用明确测试夹具，不构成真实Isaac场景结果。实际渲染验收需使用已完成的真实scene job，不由组件测试启动GPU。

真实浏览器验收使用 `PYTHONDONTWRITEBYTECODE=1 TMPDIR="$PWD/.runtime/tmp" python3 tests/frontend/task_scene_acceptance.py SCENE_RUN_ID`。先确保该真实job的scene_build已完成；脚本不新建构建。实际输出hash/revision优先于请求显示，不一致会警告。即使导出失败但已有真实渲染，预览也明确注明构建尚未完成。程序几何及合成测试夹具单独标识，不作为第二条录制示教。

2026-11-11只读真实场景验收：Connector `7a2f6bd23eae482a` 与程序方块 `c9784f26971a43f7` 的全景、实际腕部相机图、USD/manifest/task_spec/build_report均可访问；任务版本/实际输出hash匹配、源任务/历史导航、JSON读取和移动布局通过。程序方块明确标为合成夹具，不是第二条录制示教。旧Connector预览 `e648721d1192409c` 保留局部检查失败，失败UI已验收。报告分别为 `runs/acceptance/frontend/task-scene-<run_id>-report.json` 和同前缀4张截图。浏览器仅发GET，0模型请求、0新GPU任务、0硬件指令、0浏览器异常；完成后已关闭专用Chrome。这些结果只验证场景/页面，不代表插接、策略泛化或真机通过。

## 场景绑定的离线示教专家

成功Connector场景的运行详情提供“运行离线示教专家”。该控件显式标注Ground truth脚本专家，无RGBD感知、学习策略或硬件执行。页面只读取启动条件；场景构建完成不会自动运行专家。

前端对比实际scene_build输出的revision/hash与源任务当前定义，忙、失败、缺版本、旧快照、不一致或不支持Profile时禁用。点击后再次GET复核，再向 `POST /api/runs/{scene_run_id}/run-expert` 提交 `{expected_revision,spec_hash}`，并切到真实新job；服务端仍独立核验兼容性。

专家job复用真实Agent阶段、进度、日志与工件视图，并提供源场景导航。结果区将抓取、插接、松手、松手后保持、动作完成和完整任务分别展示，优先读取本次独立expert_validation。未知保持未验证，对键布尔量不等同于插入成功；保持失败不会被动作完成或工作流状态掩盖。专家执行报告、独立报告、轨迹仅链接实际当前run工件。

`tests/frontend/expert_components.mjs` 的23组契约/SSR夹具检查不请求真实API、不启动GPU、不构成专家实际动作通过。报告 `runs/acceptance/frontend/expert-components-report.json`。
