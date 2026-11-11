# 项目内Codex账户

使用官方Codex CLI的device-auth，全部运行状态放.runtime/codex-home与codex-sqlite，不读取/复制/修改全局auth.json/config.toml，也不改变系统HOME。需要在本项目内完成一次官方登录。

仅一次性设备验证码在本机前端显示，长期凭据从不返回给浏览器或模型，不加入提示词；.runtime整体git忽略。登录进程取消只终止本次流程，不调用logout。权限仍按账户和实际模型推理结果核验。

支持CLI解释器是外置只读工具，路径在environments.yaml的codex_executable。此模块只管理模型账户，不连接机器人。
