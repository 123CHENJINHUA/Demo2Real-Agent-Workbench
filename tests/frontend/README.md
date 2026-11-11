# 真实浏览器验收

脚本直接访问本地 FastAPI/React 服务，没有mock API，也不直接调用设备。

- `browser_smoke.py`：首次页面/目录浏览/转换数据任务创建。会创建一个真实任务。
- `real_demo_acceptance.py`：本次历史验收run的重试和原始LeRobot示教复制审查，绑定本次run ID。
- `layout_acceptance.py`：复用已存在原始示教run，核验联系图、全部工件访问、分类/搜索/限高和移动布局。不导入新数据。
- `policy_acceptance.py`：应用当前脚本策略离线配置，核验无权重神经策略拒绝，并读取硬件状态确认未连接和发送数为0。

依赖：宿主Chrome、Python websocket-client。测试服务/调试端口为8787/9338。截图与报告位于 `runs/acceptance/frontend/`。Chrome启动参数见 `frontend/README.md`。

UI验收通过不表示任务语义已获核验、仿真成功、策略训练完成或真机可执行。没有配置多模态模型时，在该阶段阻塞是预期行为。

`diagnostics_payload.mjs`：使用Node24原生TypeScript读取纯payload构建函数，验证3个诊断模式×6个干扰的真实HTTP JSON及15种非法输入。测试接收器使用随机本机端口，不连接8787，不产生仿真/GPU任务。运行：`TMPDIR="$PWD/.runtime/tmp" node tests/frontend/diagnostics_payload.mjs`。

`simulation_preview_selection.mjs`：10项纯逻辑检查（数字排序、示教/跨run排除、缺图不造图、完成状态下保持失败告警）。

`simulation_vision_acceptance.py`：复用实际RGBD仿真run `3035e6ca201844ef`，只读验证最新面板实际加载、保持失败醒目显示、示教/仿真图严格分离及移动布局，不发起新诊断或重新导入。

`provider_components.mjs`：Codex/HTTP载荷隔离、项目登录状态映射、官方链接检查、未知型号不猜测，以及React SSR组件中Codex隐藏API密钥/HTTP地址、HTTP保留原字段。夹具仅在测试使用，不发起官方登录或实际推理。运行 `TMPDIR="$PWD/.runtime/tmp" node tests/frontend/provider_components.mjs`。

历史 `layout_acceptance.py` 的模型测试部分要求服务尚未配置/项目未登录；完成真实登录后，不应用该失败预期验证已可用模型。

`codex_live_acceptance.py`：要求用户已在本项目完成官方登录，只读验收真实Codex状态/版本/路径、空默认模型、隐藏HTTP密钥、项目登录命令和移动布局；记录浏览器API方法全部为GET，不点击登录/推理/保存。报告不包含凭据或设备验证码。

`task_scene_components.mjs`：18组任务定义/场景契约与React SSR检查，包括JSON对象、正revision、quoted etag/并发保护载荷、仅离线freeze/preview、BigInt新build排序、排除contactsheet/其他run、未知位姿和未验证范围展示。使用明确隔离夹具，不发起模型/GPU/job/API写操作，不把夹具计为真实场景通过。

`task_scene_acceptance.py SCENE_RUN_ID`：仅GET读取已完成scene_build的真实工件，检查全景/腕相机加载、实际结果revision/hash、任务概览/JSON/历史、源任务导航、stale比较、action/retention与执行权限边界和移动布局。复用独立9338 Chrome，不启动模型/GPU/登录或硬件；报告按run ID保存，workflow/局部检查可能仍失败，不能外推为动作成功。

本次实际只读验收ID：旧失败 `e648721d1192409c`、新Connector `7a2f6bd23eae482a`、程序方块 `c9784f26971a43f7`。三份报告各自保留workflow/local-validation实际状态；新Connector历史中仍有旧失败。专用Chrome验收后已关闭。

`expert_components.mjs`：23组纯逻辑/React SSR检查，覆盖scene成功+源spec版本hash核验、忙/失败/旧快照/未支持Profile禁用、精确启动载荷、独立阶段未知、对键不等于插入、保持失败不被动作/overall覆盖、报告跨run隔离、明确真值专家与无硬件边界。运行 `TMPDIR="$PWD/.runtime/tmp" node --experimental-strip-types tests/frontend/expert_components.mjs`；仅测试夹具，不对真实服务POST或启动GPU。

`expert_live_acceptance.py EXPERT_RUN_ID`：复用真实已执行专家job，只读检查scene显式启动按钮（不点击）、5节点实际状态/报告（含执行前场景复审）、真实输出版本hash、保持失败与动作成功分别显示、源场景导航及手机布局。专用Chrome在9338，报告 `runs/acceptance/frontend/expert-<ID>-report.json`；全部浏览器API必须GET，脚本不启动专家/GPU/模型/硬件。
