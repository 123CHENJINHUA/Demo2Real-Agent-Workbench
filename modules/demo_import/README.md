# 示教导入

`run(ctx)` 接收 `input.source_path`，先清点预算，再复制到本次 `output_dir/dataset`。源路径只保留溯源；下游使用项目内清单和副本。逐文件 SHA256、源变更检测、软链接/特殊文件/递归目标拒绝，进度来自文件数与字节。取消保留检查点，重试会重新复制未完成导入；完成导入重用前逐文件校验。

轻量审查支持 LeRobot JSON、视频清单、NPZ/NPY 头（不执行 pickle）。未解码视频、深度与语义明确为未验证。首版不承诺视频精度审查或解码同步已完成。默认限 100000 文件、50 GiB，可在输入中减小。

`audit(ctx)` 重新校验导入副本并列出输入缺项。统一配置 ffmpeg_executable（带同目录 ffprobe）后，对最多三段视频各采六帧，保留视频/请求时间/hash，产出真实 contact sheet。没有 ffmpeg 明确待配置，采样帧不构成完整视频理解或深度精度证明。

LeRobot v3 的 meta/episodes Parquet 在应用环境安装 pyarrow 后可读取：audit 的 episode_index（默认 0）严格绑定对应视频 chunk/file 和起止时间。未安装时明确缺依赖，只采各 RGB stream 首段并标时间范围未核验；不会假定 file_index 等于 episode_index。源深度编码说明如果声明有损/8bit，会单独列风险。
