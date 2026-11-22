# teach_02 实物标定配置

用户已明确接受当前精度，使用原始工厂内参及SDK名义FK的teach_02求解结果。未采用诊断内参或关节偏差模型。运行配置validation_passed已按用户要求设为true，validation_basis=user_override；原始误差报告和阈值未修改，原测量未通过的状态另存measured_validation_passed=false。

- bundle.json：双腕手眼、双臂基座关系、中间相机外参、RGB/深度外参及真实误差。
- left_wrist.json / right_wrist.json：分别对应每台腕部相机，沿用现有相机配置的intrinsics和hand_eye.matrix格式。
- center.json：中间相机工厂内参、T_world_color及到左右SDK基座的变换。
- config/robots/nero.yaml 已引用本目录，并填写左can1、右can0、v121及三相机引用。

所有T_parent_child将child坐标映射到parent，矩阵按行存储，平移单位米。腕部相机动态位姿仍须用拍摄时刻T_base_flange @ T_flange_color。
这里world定义为固定ChArUco板坐标系，尚未注册到已有Isaac场景Z-up坐标系；不能直接覆盖旧场景的基座/物体位置。基座变换是SDK基座定义，不能未经确认当成URDF根链接偏置。

TCP按用户要求直接采用现有模型夹持中心：URDF gripper_base中的[0,0,0.130] m，方向与gripper_base一致。
通过profile已有T_gripper_base_sdk_flange换算，左右两侧SDK法兰到TCP位置均为[0.1675,0,-0.0235] m，RPY=[-90,0,-90]度。
left_tcp.json和right_tcp.json记录完整矩阵、四元数、模型来源及hash；physically_calibrated=false。missing已清空，complete=true表示配置齐备。
mode仍为disconnected，allow_real_motion仍为false。
已有Connector历史资产、profile、冻结task spec及实验锁未修改；它们绑定的旧标定不会因本配置更新而自动替换。后续新草案需要显式引用本目录左右相机文件，并完成场景坐标注册；本次只填实物配置。

来源和用户接受记录均在bundle及相机文件中；原始结果位于calibration_workbench/runs/teach_02/left|right/result.json。
