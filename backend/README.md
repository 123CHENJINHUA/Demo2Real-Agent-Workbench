# 本地后端/API

启动由项目根 scripts 管理，应用入口 `backend.main:app`；默认只监听127.0.0.1。实际模块在 application 注册，API不载入Isaac、Torch或SDK。

读取 `/api/session` 获取本地会话token，所有写请求带 `X-Session-Token`。只接受localhost/127.0.0.1 Host/Origin；不提供跨域许可。凭据持久化在 `.runtime/secrets`（0600），API只返回脱敏配置。浏览器页面、任务完成和重启都不能触发机器人使能。

主要路由：

- `/api/health`、`/api/overview`、`/api/session`
- `/api/diagnostics` POST：已迁移Connector局部回归，kind为connector_bench/connector_physics/connector_rgbd；单独诊断不跳过普通新任务的分析验收门。另支持supervisor_bc，输入learning:{dataset_path,versions,epochs?}，仅监督技能分类训练，不代表闭环/硬件成功。
- `/api/runs` GET/POST，`/api/runs/{id}` GET，`.../cancel`、`.../retry` POST，`.../events?after=0` GET（可断线重放）
- `/api/fs?path=`：默认仅项目根与Downloads；额外只读导入根在 `config/import_roots.yaml` 的 `allowed_roots` 中登记。忽略隐藏项和软链接。
- `/api/settings/environments` GET/PUT：同一 environments.yaml；`/api/settings/provider` GET/PUT；`.../test` POST真实HTTP核验。
- `/api/policies/active` GET/PUT：仅离线选择兼容插件；`/api/deployments` GET列出已有包，不能触发硬件执行。
- `/api/experience`、`/api/incidents`、`/api/artifacts/{id}`、`/api/policies`。
- `/api/robots/status` 只读，`/api/robots/shadow` 仅影子计算，其他机器人POST拒绝403。

SQLite `.runtime/workbench.sqlite3` 持久保存runs/tasks/agents/events/artifacts/incidents及每次进程尝试jobs。实际worker进度写events与progress；未知耗时不伪造百分比。工件必须存在项目内并计算SHA256后才登记。

前端dist由同一后端服务。API不存在返回404，不落入SPA路由。

当前限制：单机轻量并发调度、轮询事件；单项目调度器进程锁，重型仿真/学习/评价串行预约GPU，无分布式资源锁。服务重启对running标记interrupted，需用户显式retry；不会自动恢复硬件动作。连接实际硬件、完整通用场景生成与泛化训练取决于相应插件和独立验收，不由HTTP完成状态推定。

普通run可携带learning/evaluation/deployment独立对象；任务JSON上限64KiB，真实数组通过工件引用，禁止把大数据塞进HTTP配置。
