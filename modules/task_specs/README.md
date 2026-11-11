# 结构化任务定义

`generate/get/save/freeze/pin/load_snapshot` 提供纯离线task-spec/v1。输入模型提案、已注册profile、原始几何/标定/图像证据，所有引用在项目内且绑定SHA256。模型不能凭名称选择实现；仅明确任务资产的完整几何审计mesh path+hash唯一匹配时可绑定（共享资产库存扫描不绑定）带声明的模板草案。未知量保持null和unresolved。

每次修改在 `runs/<id>/task_spec/revisions/rNNNNNN.json` 追加新文件。expected_revision或ETag必需，冲突拒绝；规范化内容hash覆盖语义、证据和来源，排除revision/state/timestamp。freeze明确scope=offline_contract，在frozen/hash.json保存不可变快照，不代表物理/硬件验证。编辑冻结内容生成新draft，已有构建必须重新核对精确hash/revision。

字段来源在field_provenance，profile值始终标为模板假设，模型提案和用户编辑保持未验证。原始失败、geometry candidates和独立action/retention判据不得合并为成功。模块不导入Isaac/pxr/SDK，不执行运动。
