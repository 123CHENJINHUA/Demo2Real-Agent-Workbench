# 本地后端/API

启动由项目根 scripts 管理，应用入口 `backend.main:app`；默认只监听127.0.0.1。实际模块在 application 注册，API不载入Isaac、Torch或SDK。

读取 `/api/session` 获取本地会话token，所有写请求带 `X-Session-Token`。只接受localhost/127.0.0.1 Host/Origin；不提供跨域许可。凭据持久化在 `.runtime/secrets`（0600），API只返回脱敏配置。

主要路由：

- `/api/health`、`/api/overview`、`/api/session`
- `/api/diagnostics` POST：已迁移Connector局部回归，kind为connector_bench/connector_physics/connector_rgbd；单独诊断不跳过普通新任务的分析验收门。另支持supervisor_bc，输入learning:{dataset_path,versions,epochs?}，仅监督技能分类训练，不代表闭环/硬件成功。
- `/api/runs` GET/POST，`/api/runs/{id}` GET，`.../cancel`、`.../retry` POST，`.../events?after=0` GET（可断线重放）
- `/api/fs?path=` 浏览服务进程可读的本机目录，初始打开用户主目录。父级导航可到根目录，列表忽略隐藏项和软链接。
- `/api/settings/environments` GET/PUT：同一 environments.yaml；`/api/settings/provider` GET/PUT；`.../test` POST真实HTTP核验。
- `/api/policies/active` GET/PUT 读取和保存离线插件配置。`/api/deployments` GET 读取已归档的离线策略包清单。
- `/api/experience`、`/api/incidents`、`/api/artifacts/{id}`、`/api/policies`。
- `/api/experience?cases_only=true` 浏览经验案例。`/api/incidents?page=1` 搜索、筛选并分页浏览问题组。`GET /api/incidents/{id}` 读取单次问题、证据校验和修订。`POST /api/incidents/{id}/revisions` 使用当前修订号追加跟进。

SQLite `.runtime/workbench.sqlite3` 持久保存runs/tasks/agents/events/artifacts/incidents及每次进程尝试jobs。实际worker进度写events与progress；未知耗时不伪造百分比。工件必须存在项目内并计算SHA256后才登记。

运行记录的 `output_directory` 是项目内相对目录。示教导入位于 `runs/<示教ID>/`，绑定示教的新派生运行位于其 `derived/<运行ID>/`，批次清单位于 `experiments/<批次ID>/`。既有运行地址保持可读，经验模块继续管理 `knowledge/`。

前端dist由同一后端服务。API不存在返回404，不落入SPA路由。

当前采用单机调度和轮询事件，重型仿真、学习与评价串行预约 GPU。服务重启将运行中的离线任务标为中断，可显式重试。通用场景生成与策略泛化取决于相应插件和独立验收。

普通run可携带learning/evaluation/deployment独立对象；任务JSON上限64KiB，真实数组通过工件引用，禁止把大数据塞进HTTP配置。
