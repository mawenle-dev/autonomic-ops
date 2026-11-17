# Known gaps（已知不足）

按《施工宪法》10.14：每个版本至少保留一条"已知不足"，标出下一版入口。这里记录的是**尚未做到**的部分，不隐藏、不含糊。

## 1.0.1 / 1.0.2（信用链第一环，已本地实现，未发布）
- 端到端 `tests/selftest.sh` 的撤销两断言（F-01）需 Linux + systemd + 真实 socket，本机 Windows 未能运行；以 `tests/test_f01_quarantine.py` + `tests/test_f01_state_consistency.py` 的字节精确单测覆盖同一语义，但没有声称 selftest 通过。
- 目录级入仓（传一个目录整体入仓）尚未支持，`quarantine()` 对目录条目显式标 `skipped`；当前清理按文件粒度入仓，够用但非通用。→ 后续按需要纳入版本，不提前实现。
- 面板"置灰"标签属 N-1 对话层（2.x），按"不提前实现"留待后续版本；本版完成的是快照列表 / CLI / 账本 / manifest 四处的数据一致性，面板 UI 未接线。
- `undo.kind=copy` 降级路径的代码已实现，但本机单文件系统无法真实触发 EXDEV；未声称其端到端验证通过。

## 版本路线与闸门
- `release/versions.yaml` 已列全 120 版路线，`release/release.yaml` 保留 120 个闸门键、当前 `version: 1.0.0`、所有闸门 `false`。
- 能力按版本闸门开启：未到版本号的闸门保持关闭，运行时不注册对应能力（宪法 10 总原则）。本仓库尚未发布任何 1.0.1+ 版本，故没有开任何新闸门、没有打 tag、没有 push 发布。
