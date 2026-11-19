# 工程交接说明（强约束）

本文件面向后续接手 AutonomicOps 的工程人员或自动化代理。开始修改前应先阅读
`docs/memory/PROJECT_MEMORY.md`、`docs/memory/DECISIONS.md` 与
`docs/memory/WORKLOG.md`。以下条款为强制约束；与交付速度冲突时，以本文件为准。

## 1. 当前状态

- 产品仅支持 Linux；`platform_guard` 在非 Linux 环境直接拒绝执行。
- 宪法运行时已接入 `executor.execute()`：预算预检、双钥检查、回执链、反账均为
  真实执行路径。
- 发布台门禁由 `publish()` 内部执行，不接受外部布尔值；HTTP 写操作仅 POST，
  并校验本地会话令牌。
- WSL Ubuntu 下 `tests/selftest.sh` 全部通过；单元测试 210 项通过，2 项按平台跳过。
- 版本台账当前为 `1.0.0` 基线，`release/receipts/` 为空，尚无 `1.0.1` 及以上版本
  的发布事实。`versions.yaml` 中 `implemented_local` 仅表示代码与测试存在于本地。
- 104 项模块验收中有部分为契约级检查，不构成发布许可；发布许可以预检与回执为准。

## 2. 禁止行为

1. 禁止伪造验收：检查函数内不得使用局部模拟逻辑顶替产品路径；不得把
   “未配置/未接线”显示为通过；不得将 skip 计为 PASS。
2. 禁止绕过发布门禁：不得恢复 `preflight_ok` 参数；不得用 GET 触发发布；
   不得把会话令牌写入页面以外的位置。
3. 禁止以覆盖当前版本目录的方式制造“多版本”：一次只发布一版，每版必须有独立
   提交、标签与 `release/receipts/<版本>.json`。
4. 禁止将构建产物纳入版本控制：`release/dist/`、`release/portable/`、`.tools/`、
   `.vm/`、`.secrets/`、`__pycache__` 均不提交、不打包进部署包。
5. 禁止直接删除运行数据：清理、轮转、删除必须经 `backup.quarantine()`；
   物理删除仅允许 `file.delete.hard`，且需满足 `confirm=DELETE`、双钥通过、
   目标为非目录非链接的普通文件。
6. 禁止夸大实现状态：功能未接入运行时，只能描述为“模型存在/未接线”，
   不得描述为“已实现并验证”。

## 3. 改动流程

1. 阅读 `docs/memory/PROJECT_MEMORY.md` 的工程约束。
2. 先编写会失败的测试，再实现功能。
3. 在 WSL 环境执行完整验证：
   ```bash
   cd /mnt/e/测试/自动维护/autonomic-ops
   PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"
   bash tests/selftest.sh
   PYTHONPATH=src python3 tools/audit_104.py
   ```
   Windows 仅用于单元测试与打包，不能替代 Linux 端到端验证。
4. 追加 `docs/memory/WORKLOG.md`，必要时更新 `DECISIONS.md`。
5. 更新 `CHANGELOG.md`，未发布条目标注 `(unreleased)`。
6. 提交粒度与版本一致，提交信息格式 `x.y.z: <一句话说明>`。

## 4. 目录与文档规范

- 面向维护者的文档放 `docs/human/`；工程记忆放 `docs/memory/`；交接材料放
  `docs/handover/`；版本文档快照放 `release/snapshots/`。
- 根目录只允许代码目录、`README*`、`CHANGELOG.md`、`KNOWN_GAPS.md`、`SECURITY.md`、
  `CONTRIBUTING.md`、`DISCLAIMER.md`、`LICENSE`、`Makefile`、`pyproject.toml`、
  `audit-report.json`。
- 临时脚本放 `.tmp/`（已忽略）；新增模块必须登记到 `docs/ARCHITECTURE.md` 或
  `PROJECT_MEMORY.md` 的目录地图。

## 5. 版本节奏

- 允许一次性完成多项功能，但发布必须逐版进行，每日最多一版。
- 版本号按 `release/versions.yaml` 顺序推进，禁止跳版、禁止一次创建多个标签。
- 每个版本的提交应包含代码、测试与该版本文档内容。
- 发布动作由维护者执行；工程代理不得代替维护者推送远端仓库。

## 6. 待办优先级

1. 将 `tools/audit_104.py` 中剩余的契约级检查替换为真实链路断言。
2. 增加 `constitution status` 的巡检输出（已提供 CLI，可继续扩展为告警）。
3. 将 104 项验收拆分为 `contract/` 与 `e2e/` 两套。
4. 发布台在缺少 git 时给出界面级提示。
5. 按能力域拆分 `advanced.py`，停止继续扩展“版本号函数集”。

## 7. 交接验收标准

- WSL 下 `tests/selftest.sh` 全部通过；
- `python3 -m unittest discover` 全部通过（平台相关跳过需注明原因）；
- 发布台预检全绿；空仓库、缺 MANIFEST、无源码时必须判红；
- 任一动作执行后可在 `data/receipts/receipts.jsonl` 找到回执，且
  `prev_hash` 链可复算；
- 预算扣减在重启后仍然有效；
- 文档中所有“已完成”结论均可指向具体文件、测试或回执。
