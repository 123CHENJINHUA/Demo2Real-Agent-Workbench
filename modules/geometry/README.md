# 轻量资产审查

`run(ctx)` 审查项目内 `input.asset_paths`（空缺时 assets），支持 OBJ、二进制/ASCII STL 的数量/AABB、URDF 引用。USD 保留给配置的 Isaac Worker 检查，不在 API 导入 Isaac。

工作台可为新示教或等待资产审查的任务选择零个、一个或多个 STL/OBJ。选择的文件由应用复制到该运行的 `geometry/models/`，审查报告记录源路径、副本路径和 SHA-256。留空时该任务直接进入可用示教和代理几何的流程。场景记录未能绑定到对象的模型。

单位、功能轴、键位、夹持面、孔槽/碰撞可用性均不能从 AABB 宣称成立。输出明确未验证，不自动改缩放/孔径/锁止肩。外部资产需先复制到项目，拒绝软链接。

## 功能候选审查（CPU）

`modules.geometry.functional:run_functional(ctx)` 为独立功能审查入口；基础 `run(ctx)` 仅在 `input.functional_analysis=true` 时嵌入调用。

输入可用 `assets`（对象 id、asset_path、unit、scale、collision、functional_probe），或 `dependencies.task_spec.spec_path` 的项目内规范对象。已知 `box + size_m` 在内存构造解析三角面，标记 procedural/synthetic fixture；缺少资产和尺寸的对象保持 unresolved/needs_assets，不生成成功的几何结果。

计算直接读取 OBJ/STL 三角面：按表面积加权的精确二阶矩 PCA、闭边拓扑、三角面/截平面求交、闭轮廓嵌套和孔洞候选，以及薄层裁剪后的对向抓持面候选。生成 JSON 和 SVG；SVG 用青色显示外轮廓、橙色显示孔候选，包含截面位置。PCA 不是装配坐标；闭边不代表无自交；若采样截面有孔，也不能宣称贯通孔/真实锁止成立。未知单位保留 asset_units_unknown，绝不按数值大小猜米。

显式传入/对象绑定的工程碰撞 profile 保留路径/hash/未实测声明。Connector 的探针角色和位置来自任务模板的 functional_probe，按现有截面证据核验双特征线；不会给其他对象套 ±90°、插深、弹性或尺寸。对置特征只确定无向直线，不能证明 180° 机械对称。面积等效半径差异保留为待核验问题，不改原模型。

依赖通过 ctx.dependencies.experience 提供历史检查表；本模块不 import experience。报告逐项记录采用/不采用的原因和未解决来源。所有候选的抓取可达性、外障碍、摩擦稳定性、实际夹持姿态、功能接触和真实标定仍需后续验证。

运行边界：每次最多32对象、每文件256MiB、OBJ 50万点/100万三角面，显式取消检查，不启动GPU或硬件。依赖仅 NumPy；契约不依赖本模块。
