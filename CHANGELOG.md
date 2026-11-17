# Changelog

本文件记录项目的所有重要变更，格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。
自 1.0.1 起按《施工宪法》逐版施工：一版一件事，先有失败测试再实现，未发布版本标注状态，闸门键在发布时才开启。

## [1.0.2] - 施工中（未发布，闸门 `fix_snapshot_state` 未开启）

承接 1.0.1，把快照三态在读侧"四处一致"接线补齐（A2/A3）。

### Fixed — T-1 时间仓
- `list()` 递归纳入隔离区快照（`quarantine/<日期>/<id>/`），使 `trash` 态出现在快照列表中，不再"入仓后即从列表消失"。
- 快照列表统一透出 `kind` 与 `restorable`，与 manifest 同源，CLI / 账本 / 快照列表不再各自重算口径。
- 账本记录：`meta` 快照携带 `restorable=false` 且不写 `rollback_point`（同一真值源 `manifest.restorable`）。

### 验证（本地真实输出，2026-11-17）
- `tests/test_f01_state_consistency.py` 3 项：实现前 2 errors（`list` 漏 trash 快照），实现后 3/3 `OK`。
- A2：三类快照在 `list()` 中 `kind/restorable` 一致；A3：`meta` 无 `rollback_point` 且 `restore()` 明确拒绝。
- 全量单测 34/34 `OK`，无基线回归。

## [1.0.1] - 施工中（未发布，闸门 `fix_quarantine` 未开启）

信用链第一环 / 缺陷 F-01：删除操作此前只留"仅元数据"快照却登记为可回滚点，撤销永远失败却对外宣称可撤销。

### Fixed — T-1 时间仓
- 删除语义改为**入仓**：文件整体 `rename` 到 `vault/quarantine/<日期>/<快照id>/`，原路径消失、字节完整保留；撤销为反向 `rename`，天然字节精确。
- 快照引入三态 `full / trash / meta`，新增 `restorable` 字段并作为唯一真值源；`snapshot()` 可按 `kind` 显式构造。
- `meta` 快照明确 `restorable=false`，`restore()` 直接拒绝并给出明确错误，不再尝试读取未保留的内容；`meta` 不写入 `rollback_point`。
- 跨文件系统 `rename` 失败时降级为 `copy2 + unlink`，回执标注 `undo.kind=copy` 与字节成本。
- 清理执行器 `_do_cleanup` 改用 `quarantine()`；入仓不可用时**拒绝删除**（没有撤销句柄就不动手），不再"先删再说"。
- `list()` 透出 `kind` 与 `restorable`，使 CLI / 面板 / 账本口径一致的数据源就位。

### 验证（本地真实输出，2026-11-17）
- `tests/test_f01_quarantine.py` 4 项：实现前 1 error + 2 fail（`quarantine` 不存在、三态字段缺失），实现后 4/4 `OK`。
- A1：5 个文件入仓→撤销→sha256 逐一相等（真实断言通过）。
- A2/A3：`meta` 快照 `restorable=false` 且不产生 `rollback_point`。
- 全量单测 31/31 `OK`（原 27 项基线无回归）；`compileall -q src` 通过。
- 端到端 `tests/selftest.sh` 第 7 段（真实 daemon + socket + `touch -d`）需 Linux，本机 Windows 无法运行，故未声称其通过——以 Python 单测覆盖同一字节精确语义。

## [1.0.0] - 2026-11-07

首个正式版本，代号 `aegis`。

### Added

- 常驻守护进程 + 可配置 Worker 池；11 通道文件日志、Unix socket IPC、轻量 CLI。
- 指标采集（CPU / 负载 / 内存 / 磁盘 / 端口 / 进程 / systemd unit / 连接 / SSH / 流量）与 9 类内置检测器，支持连续样本防抖。
- 插件检测器：日志错误突增、证书过期、备份新鲜度、失败单元、站点健康、inode 压力、OOM 观察、时间漂移、fd 压力、磁盘增长预测。
- YAML 规则引擎、优先级调度（aging、抢占后重新入队、去重、限流）与重启恢复。
- 策略引擎：白名单、风险分级、人工审批、禁止路径、速率限制、核心代码禁自改。
- 执行器 15+ 类动作；内容寻址快照、字节级回滚、哈希链审计与完整性校验。
- 安全防护：SSH 暴破 / 连接洪泛 / 流量突增检测，nft / iptables / recorder 三档后端与显式降级。
- 配置/脚本生成：确定性模板 + 语法校验、危险模式扫描、沙箱预跑、快照后原子写入。
- 插件热加载、能力门与错误熔断；人机对话通道（确定性关键词路由）；远程部署（含 `--simulate` 假根验证）。
- 看门狗（心跳、RSS / fd、核心哈希漂移、退出码 99 触发 systemd 重启）。
- 只读 Web 面板（`autonomic-ops serve`）：标准库实现、GET-only、默认只绑 127.0.0.1，展示状态 / 指标 / 封禁 / 任务 / 待审批 / 审计尾，并暴露 Prometheus `/metrics`。
- systemd 单元整机资源上限（`LimitNOFILE` / `LimitNPROC` / `MemoryMax` / `TasksMax`），与每任务 rlimit 互补。
- `PolicyEngine.check_restart_command()`：配置里的重启配方在执行前做危险模式与控制字符校验。
- 2 个内置插件：`failed_units`、`site_health`。
- 端到端自测（`tests/selftest.sh`，50 项断言）与单元测试（含零 PyYAML 依赖路径）。
- GitHub Actions CI：Python 3.9 / 3.12 双版本，覆盖语法、shell、配置校验与两条单元测试路径。

### Changed

- 沙箱在非 POSIX 开发机上显式降级并标记 `degraded_isolation`，Linux 行为不变。
- 安装器在自定义 `--prefix` 时同步改写 `plugins.dirs` 与 `bootstrap.install_root`，并拒绝会破坏表达式的 prefix 字符。

### Fixed

- systemd 单元 `Conflicts=` 与看门狗 unit 互相矛盾，导致可选的第二层看门狗无法启用。
- systemd 重启后 `226/NAMESPACE` 死循环：`ReadWritePaths=` 引用了安装器未创建 / 重启即清空的目录，改用 `StateDirectory` / `LogsDirectory` / `RuntimeDirectory` 由 systemd 自动创建。
- nft 后端在全新系统封禁失败：加规则前幂等创建表与基链。
- nft `verified` 语法错误与按表达式解封失败：改用 `nft list table` 与按 handle 删除。
- 看门狗单元此前未随安装器安装，现已安装（默认不启用）。

### 验证

在标准 x86_64 Ubuntu 24.04 + systemd + 真实 nftables 上完成：单元测试双解析路径全通过、`selftest` 50/50、真实安装、重启自启、`kill -9` 恢复、真实封禁/解封、看门狗共存。详见 `docs/SELFTEST_REPORT.md`。

[1.0.0]: https://github.com/mawenle-dev/autonomic-ops/releases/tag/v1.0.0
