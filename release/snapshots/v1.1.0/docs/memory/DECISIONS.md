# 工程决策记录（ADR）

> 只记录“为什么这么选”，不记录实现细节。新决策追加在末尾，不要改历史结论。

## ADR-001 回执在生产执行链生成，而不是在测试里生成
**背景**：原实现只有 `fvc.Receipt` 数据类，`executor` 从不构造回执，导致“无回执不执行”
只是文档承诺。
**决策**：在 `ActionExecutor.execute()` 内，执行前预检、执行后强制 `runtime.finalize()`；
策略/预算拒绝写反账，不写回执。
**代价**：每次动作多一次 sha256 + 一次 fsync；换来可离线验证的执行事实。
**不接受**：用测试夹具伪造回执、或在 handler 内部各自生成回执（会漏）。

## ADR-002 预算是持久化的五维账户，不是内存计数器
**背景**：`fvc.BudgetAccount` 只在单测中使用，重启即清空，等于没有预算。
**决策**：`PersistentBudget` 落盘 `state/budget.json`，滑动窗口 3600s；
`0` 表示该维度禁止（安全红线），`-1` 才表示无限。
**代价**：每笔扣减一次小文件写；换来重启后预算仍然有效。

## ADR-003 发布门禁必须由发布器自己执行
**背景**：`publish(preflight_ok=True)` 让任何调用方（包括审计脚本）绕过门禁。
**决策**：`publish()` 内部调用 `build_preflight()`；预检代码搬进 `studio/core.py`，
HTTP 层只负责触发。另加对 `src/` 的内容封存（seal）。
**代价**：发布时预检会重复跑一遍；换来“门禁不能被传参绕过”。

## ADR-004 HTTP 写操作一律 POST + 本地会话 token
**背景**：`/publish`、`/retract` 曾是 GET，任意网页的 `<img src>` 就能触发发布。
**决策**：GET 只读；写操作 POST-only，校验随机 `X-AO-Token` 与 Origin/Referer。
**代价**：页面必须注入 token（仅本机页面可见）；换来 CSRF/预取安全。

## ADR-005 撤回 = 状态回滚 + 允许重发
**背景**：旧 `retract()` 只删 tag、改版本号，`released.json` 仍保留该版本，
导致撤回后永远无法重发同一版本。
**决策**：撤回同时清理 `released.json`、回退 `release.yaml` 和 `channel/version.json`，
回执标 `retracted`；保留历史文档快照。
**代价**：撤回不是“销毁历史”，只是“取消发布状态”。

## ADR-006 预检失败必须闭（fail-closed）
**背景**：MANIFEST 缺失 / src 为空 / 路线图乱序时，旧预检会“跳过即通过”。
**决策**：这些情况全部判红；frozen 模式跳过编译/单测必须在输出里写明“build-time”，
且仍然跑内置自检（预算/回执/Ed25519/账本）。
**代价**：便携包必须携带 `Data/src` 与 `MANIFEST.json`；换来预检不能空转。

## ADR-007 日志保留也走 quarantine
**背景**：`log.rotate` 直接 `unlink` 旧日志，违反“删除必须可撤销”。
**决策**：轮转用 copy2 + 清空原文件；过期代际整体 `backup.quarantine()`；
quarantine 失败就保留文件并标红。
**代价**：日志目录会多占一份隔离区空间；换来可追溯。

## ADR-008 未接线的演练/模型必须判红，不许默认成功
**背景**：`ProveHarness.run_all({})` 的缺省 lambda 返回 `ok=True`，c96 因此“六演练全通”。
**决策**：缺省返回 `ok=False, detail=not implemented`；`counsel` 文档明确写成
“提案 schema 校验器”，不再宣称本地模型。
**代价**：审计不再全绿，但绿灯有意义。
