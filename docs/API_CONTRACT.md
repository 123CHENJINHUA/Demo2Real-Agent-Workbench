# 首版接口约定

API `/api/session` 获取本地会话 token；写请求携带 `X-Session-Token`。运行同源前端。后端 factory 为 `backend.api:create_app`，服务入口 `backend.main:app`。worker 接受 `contracts.module.ModuleContext` 字典，输出 JSON 产物。

各运行阶段有独立 worker、真实进度与证据。无模型服务时语义阶段必须 `needs_configuration`；仿真、训练、部署分别有独立验收门。高层规则与 MLP 按 skill-decision/v1 替换，运动策略按 joint-position/v1 替换；不能跨角色互换。
