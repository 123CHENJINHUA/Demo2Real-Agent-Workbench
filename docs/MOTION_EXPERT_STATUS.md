# 运动专家里程碑与续作位置

本轮已实现：成功场景→经验复查→执行前USD/依赖复审→显式示教绑定→隔离Isaac专家执行→独立动作/保持验收。入口为成功Connector场景页面的“运行离线示教专家”。当前仅支持注册Connector专家；其他任务、改过的机器人/功能帧/判据会拒绝而非套用默认值。

已核对导入第0回合555帧时间/左右关节/夹爪反馈与显式NPZ一致。物体示教位姿仍是历史人工RGB/几何重建，不是相机真值。新专家加载实际导出USD，不重建默认场景、不重置物体；指爪接触和被动阻尼为记录在案的execution overlay。当前是真值脚本专家，不是RGBD或已训练的学生。

本轮CPU全套118 passed/1 skipped；新增计划15项、专家API/证据13项、几何3项。前端专家23组契约/SSR与生产构建通过；完整真实专家浏览器验收尚未完成。证据原文runs/acceptance/expert/test_output.txt。

首次实验8740ed10fdef4a1b在动作前导出OmniPBR失败；复用可移植材质适配修正。6c657e34fed542ad及9a196d5bae124524在截图图节点初始化失败。ffb4837f85184642、ff013090435848b8已生成initial RGB，但截图切换使Articulation tensor handle失效，仍未进入抓取。所有失败保留，不能称作动作通过。最终改为动作期间只保存真实阶段USD与轨迹，渲染与运动分开；没有把未生成的图像标成成功。

当前真实实验ID：32568d8a19874d87，已结束。独立重算：双臂抓取通过（抬升约77/74mm）、对键插入通过（深度22.82mm）、原位开爪通过；松手后保持失败，完整任务未通过。所有证据完整性检查通过。物理/视觉场景派生副本与原场景的非运行期渲染部分独立签名一致。机器可读状态和分阶段结果见runs/acceptance/expert/progress_report.json；原始数据见对应runs目录。无论工具执行节点是否succeeded，都以expert_validation的grasp/insertion/release/retention与full_task_passed为准，缺失阶段保持未知。当前图像可视化仍用已成功场景的全景/腕图；阶段USD不冒充RGB或FoundationPose结果。

涉及目录：modules/planning、modules/motion_expert、modules/expert_validation、workers/execute_expert.py、tasks/connector/components；注册/API/调度和独立前端面板已接入。原任务模板/原场景未被覆盖。

用户最新决定：暂时忽略松手后保持，把它作为记录项，不阻塞动作实验。小范围XY±10mm/yaw±5°测试及定向修复已完成，见[PLACEMENT_TEST_RESULTS.md](PLACEMENT_TEST_RESULTS.md)。下一步固定专家版本做位置留出，再接RGBD/干扰与恢复数据。阶段RGB渲染与完整浏览器验收还需独立接入，不重复消耗模型做已完成的示教分析。尚未做任务学生训练或真机测试。

资源约定：后续默认单个开发Agent，仅必要的定向检查和一次对应回归；不自动增加并行模型Agent，不重复请求已存在的示教图像分析。工作台的确定性工具Worker与语言模型请求分开显示；本地GPU运行不冒充模型推理。

所有新文件/日志/缓存在本项目，外置运行环境只读。机器人保持disconnected，硬件命令0。

最新：已在同一冻结版本完成5/5复核及10/10新位姿留出动作验证，详见[FROZEN_EXPERT_RESULTS.md](FROZEN_EXPERT_RESULTS.md)。进入RGBD对照小试阶段；当前留出轨迹仅用于评价，尚未训练学生。
