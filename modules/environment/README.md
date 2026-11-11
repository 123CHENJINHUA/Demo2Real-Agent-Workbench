# 环境服务

唯一外部路径入口为 config/environments.yaml。支持 `${PROJECT_ROOT}` 重定位；保存不修改共享环境。`validate` 检查路径，重型 import 与 GPU 验证由独立 Worker 执行。`local_environment` 强制可控缓存写入本项目。
