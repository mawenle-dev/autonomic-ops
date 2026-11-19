# AutonomicOps 项目记忆（AI 必读）

> 这份文件是给“下一个接手的人/AI”看的工程记忆，不是宣传材料。
> 每次改动代码、目录、发布流程，都必须在这里或 `WORKLOG.md` 追加一条，
> 不允许只改代码不写记忆。

## 1. 这是什么

AutonomicOps 是一个 Linux 单机自治运维代理：采集指标 → 检测器产生事实 →
规则引擎产生处置请求 → 策略引擎判定 → 调度器执行 → 审计/回执 → 面板。
发布台（Release Studio）是 Windows 可运行的独立小工具，只负责“点一下发布一个版本”。

## 2. 目录地图（改代码前先看这里）

```
autonomic-ops/
├─ src/autonomic/           # 产品源码（唯一可信实现）
│  ├─ runtime.py            # 【新】宪法运行时：Covenant + 持久预算 + 回执链 + 反账
│  ├─ executor.py           # 动作执行；所有成功/失败动作在这里生成回执
│  ├─ fvc.py                # Fact/Verdict/Covenant/Receipt 数据模型
│  ├─ ledger.py             # 反账、日锚、签名 bundle、回放
│  ├─ policy.py             # 默认拒绝、白名单、风险上限、核心防自改
│  ├─ sandbox.py            # Linux-only 沙箱（rlimit/timeout/netns）
│  ├─ backup.py             # 删除入仓、快照、事务
│  └─ ...                   # 其余模块见 docs/ARCHITECTURE.md
├─ studio/                  # 发布台（跨平台；Windows EXE 由它打包）
│  ├─ core.py               # 版本状态机、预检、发布、撤回、封存
│  ├─ app.py                # HTTP 控制台（POST + 本地 token）
│  └─ build_portable.py     # PyInstaller onedir 打包
├─ tools/audit_104.py       # 模块契约验收（不是端到端验收）
├─ tests/                   # 单测 + selftest.sh（Linux 端到端）
├─ config/                  # 配置样例/自测配置/默认规则
├─ release/                 # 版本台账、文档快照、回执、频道
│  ├─ versions.yaml         # 路线图（120 条，status 仅表示“代码+测试在本地”）
│  ├─ release.yaml          # 当前版本 + 闸门表
│  └─ receipts/             # 发布回执（真正的发布事实）
├─ docs/
│  ├─ human/                # 人看的：项目状态、日常操作、发布/回滚
│  ├─ memory/               # AI 记忆：工程约束、决策、工作日志
│  └─ handover/             # 交接文档（含历史交付说明）
└─ .tools/ .vm/ .secrets/   # 本地构建/虚拟机/密钥，永不入库、永不打包
```

## 3. 不可违反的工程约束

1. **回执是执行的一部分**：`executor.execute()` 只要真正进入 handler，就必须产出
   `Receipt`（成功/失败都要），写入 `data_dir/receipts/receipts.jsonl`，并带
   `prev_hash → hash` 链。策略拒绝/预算拒绝走反账，不产生回执。
2. **预算必须落盘**：`runtime.PersistentBudget` 写 `data_dir/state/budget.json`。
   预算窗口默认 3600s；`0` 表示该维度完全禁止，`-1` 才是无限。
3. **删除必须可撤销**：清理/轮转/删除统一走 `backup.quarantine()`；失败就拒绝删除。
   唯一例外是 `file.delete.hard`，它必须同时满足：`confirm=DELETE`、双钥已启用并通过、
   路径非目录/非符号链接、非禁止路径。
4. **发布门禁在发布器内部执行**：`ReleaseStudio.publish()` 必跑 `build_preflight()`，
   不接受外部传入的 `preflight_ok`。`publish()` 还会对 `src/` 做内容封存（seal）。
5. **HTTP 写操作只能 POST**：`/publish`、`/retract`、`/api/preflight` 必须带
   `X-AO-Token`（会话随机 token，只注入本机页面），并校验 Origin/Referer；
   GET 一律 405。
6. **预检失败必须闭**：找不到 MANIFEST、src 下 0 个 py、没有源文件可封存、
   路线图版本乱序/重复/当前版本不在表内，全部判红，不允许“跳过即通过”。
7. **不伪造验收**：`ProveHarness.run_all()` 对未接线的演练判红；
   `audit_104.py` 不允许用局部 lambda 代替产品路径。新增验收必须能指出
   “跑的是哪个真实模块的哪条链路”。

## 4. 运行与验证（本机现状）

- Windows：只有 Python 3.13（沙箱运行时），**没有 git**。可用于单元测试与打包。
- Linux：WSL2 Ubuntu（Python 3.10 + git + jq）是唯一权威端到端环境。
  - 单测：`wsl -d Ubuntu -- bash -lc 'cd /mnt/e/.../autonomic-ops && PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"'`
  - 端到端：`bash tests/selftest.sh`（需要 jq；会启动真实 daemon、IPC、面板）
  - 104 项：`PYTHONPATH=src python3 tools/audit_104.py`
- Windows 打包：`.tools` 内自带 PyInstaller 6.22.3；用
  `python studio/build_portable.py --exe release/dist/AO-Release-Studio.exe`
  （onedir：EXE + `_internal` + `Data`，三者必须一起分发）。

## 5. 已知边界（不要假装没有）

- **Linux-only 产品**：`platform_guard.require_linux()` 会拒绝非 Linux 执行；
  Windows 只运行发布台。
- **沙箱不是容器**：rlimit + 超时 + 可选 netns，无 root 时标 `degraded_isolation`。
- **counsel 不是模型**：它是“提案 schema 校验器 + trace”，没有本地推理；
  文档与验收不得写成“本地模型可用”。
- **`versions.yaml` 的 status=implemented_local 只代表“代码和测试在本地”**，
  不代表已发布、也不代表端到端可用；发布事实只看 `release/receipts/*.json` 与 tag。
- **git 不在 Windows PATH**：发布台的打 tag 依赖外部 git；在 Windows 本机点发布
  会得到 `tag_failed`。正式发布请在 Linux/WSL 或装了 git 的环境执行。
- **WSL 文件系统性能**：`/mnt/e` 上 copytree/测试较慢；大批量构建建议在 Linux
  家目录做副本，避免 9P 开销。

## 6. 版本与发布节奏（当前约定）

- 版本号：`1.0.x` 修信用与运行时；`1.1.x` 观测/探针；`1.2.x` 编排/调度；
  `1.3.x` 影子/封存；`1.5.x` 集群；`2.x` 合规/恢复；`3.x` 发布台/生态。
- **一次只发一版**：`publish()` 强制 next = current + 1，且一天最多一版。
- **撤回必须可重发**：`retract()` 会把版本从 `released.json` 移除、回退
  `release.yaml` 与 `channel/version.json`；回执标 `retracted`。
- 每版发布前必须有：预检全绿 + seal 成功 + 回执落盘（`release/receipts/<ver>.json`）。
- 发布状态只认 `release/released.json` 与回执；`1.0.0` 是手工基线，控制台标记
  `baseline`，不视为回执发布。
- 本地版本快照：`tools/snapshot_version.py --version <ver>` 写入
  `release/snapshots/v<ver>/`（含 `MANIFEST.json`），不覆盖历史。
- 更新通道：`release/updates/*.zip` + `manifest.json`（`tools/update_manifest.py`）；
  发布台只校验与暂存到 `updates/staged/`，运行中不替换自身。

## 7. 改动记录入口

- 工程决策：`docs/memory/DECISIONS.md`
- 每次工作：`docs/memory/WORKLOG.md`
- 人类操作：`docs/human/`
- 交接给下一个 AI：`docs/handover/AI_HANDOVER.md`
