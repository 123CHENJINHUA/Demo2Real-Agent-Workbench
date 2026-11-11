# 隔离Worker入口

`python -m workers.run_stage --root PROJECT --run-id ID --stage REGISTERED_STAGE` 只调用application已注册模块，输出每节点result.json和worker.log。所有算法由modules实现。事件与进度写SQLite，取消检查在模块循环中调用ctx.check_cancel；进程监督另提供强制停止。入口不接受任意Python路径或shell。
