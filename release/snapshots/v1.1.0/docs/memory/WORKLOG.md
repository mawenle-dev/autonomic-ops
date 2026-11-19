# 工作日志（按时间倒序，最新在最上面）

> 规则：每次动代码/目录/发布流程，都在这里追加一条。写“改了什么、为什么、怎么验证的”，
> 不要写“已完成”三个字就结束。

## 2026-11-18（第二轮：可信度收口与更新通道）

### 1. 回执链校验
- `ReceiptJournal.verify_chain()` 逐条复算条目哈希、校验 `prev_hash` 链接，
  返回 `broken_at` 与原因；`status()` 暴露链状态。
- 新增 CLI：`autonomic constitution status [--json]`，输出契约、五维预算用量、
  回执数量与链状态、反账覆盖、双钥开关。
- 新增测试 `test_chain_verifier_detects_tampering`：篡改首条回执后必须报
  `hash_mismatch` 且 `broken_at=1`。

### 2. 发布台更新通道
- `ReleaseStudio.available_updates()`：只展示高于当前版本的条目，逐包校验存在性
  与 sha256；`stage_update()` 仅复制到 `updates/staged/<版本>.zip`，不替换自身。
- HTTP：`GET /api/update` 只读；`POST /api/update/stage` 需要令牌与 Origin 校验；
  GET 访问暂存路径返回 405。
- 页面新增“更新通道”卡片：检查更新、显示哈希状态、暂存按钮。
- 新增 `tools/update_manifest.py`：从 `release/updates/*.zip` 生成清单，`--check` 复核。
- 测试：更新可用性、哈希不符拒绝、暂存后台账版本不变。

### 3. 发布状态去伪
- `versions_full()` 不再把 1.0.0 硬编码为已发布；发布状态仅来自
  `release/released.json`，基线标记 `baseline`，页面显示“基线版本（无发布回执）”。
- 更新对应测试：基线 `released=False`。

### 4. 版本快照
- 新增 `tools/snapshot_version.py`：复制 `docs/` 与发布台账到
  `release/snapshots/v<版本>/`，写入逐文件 sha256；重复执行生成 `-r2`，不覆盖。
- 已生成 `v1.1.0` 快照，作为本轮成果的离线存档。

### 5. 文档中性化
- `docs/human/`、`docs/handover/` 去除第二人称与拟人化表述，统一为操作规范语气；
- 新增 `docs/human/版本规划.md`：统计各系列条目分布，给出补丁上限与新 minor 规则。

### 待办（下一阶段）

- 将 `tools/audit_104.py` 中剩余的契约级检查替换为真实链路断言。
- 发布台在缺少 git 时给出界面级提示（当前仅有返回 JSON 与文档说明）。
- 按能力域拆分 `advanced.py`。
- 为 `constitution status` 增加阈值告警（预算接近上限、链断裂、反账激增）。

## 2026-11-18（第一轮：运行时接线）

### 1. 宪法运行时接线（核心）
- 新增 `src/autonomic/runtime.py`：
  - `ConstitutionRuntime`：启动时物化一个 `fvc.Covenant`；
  - `PersistentBudget`：五维预算（bytes/files/actions/blast/rate），
    `data_dir/state/budget.json` 持久化，`0`=禁止、`-1`=无限；
  - `ReceiptJournal`：`data_dir/receipts/receipts.jsonl`，`prev_hash → hash` 链；
  - 反账写入 `data_dir/ledger/negative.jsonl`；
  - 可选双钥：`constitution.two_key.enabled` 开启后，不可逆动作必须双签。
- `ActionExecutor.execute()` 现在：
  - 执行前做预算/契约/双钥预检，拒绝则写反账并返回；
  - 执行后（成功或失败）生成 `Receipt`，把 `receipt_id/hash/seq` 写进审计；
  - `ActionResult` 新增 `receipt_id`、`receipt_hash`。
- 新增 `file.delete.hard`：`confirm=DELETE` + 双钥已配置 + 双签通过 + 非目录/非链接；
  未配置双钥时**拒绝**（不再假装物理删除可用）。
- 验证：`tests/test_runtime_constitution.py` 5 项全过（回执、链、预算拒绝、跨重启、反账）。

### 2. 发布台门禁收口
- `studio/core.py`：`publish()` 不再接受 `preflight_ok`；在发布器内部执行
  `build_preflight()`；发布前对 `src/` 做内容封存（seal），失败即拒发。
- 预检改为闭式：MANIFEST 缺失=红、src 下 0 文件=红、路线图乱序/重复/当前版本不在表内=红。
- `retract()`：清理 `released.json`、回退 `release.yaml` 与 `channel/version.json`，
  撤回后同一版本可重新发布（补了测试）。
- `studio/app.py`：`/publish`、`/retract`、`/api/preflight` 改为 POST-only，
  必须带 `X-AO-Token`（每次启动随机生成，仅注入本机页面），并校验 Origin/Referer；
  GET 请求一律 405。UI 的按钮同步改成 POST。
- 测试：`tests/test_studio.py` 14 项（含“GET 不能发布/无 token 不能发布”）。

### 3. 执行器/沙箱/部署修复
- `log.rotate`：旧日志改为入 quarantine，不再 `unlink`；修 `keep=0` 时
  `[:-0]` 空切片的 bug；轮转失败会标红而不是永远 verified=True。
- `_verify_service`：没有验证目标时返回 `False`（“未验证”不再等于“健康”）。
- `bootstrap`：模拟部署/打包永久排除 `.tools/.vm/.secrets/dist/portable`；
  磁盘上清掉了 `release/portable` 的递归空目录树（它是
  `File name too long` 的根因）。
- 验证：WSL `bash tests/selftest.sh` → **ALL CHECKS PASSED**（48 项，含 IPC、面板、部署模拟）。

### 4. 验收去自证
- `tools/audit_104.py`：
  - c29 影子误差：先预测，再真实落盘读回，按文件数算误差；
  - c40 物理删除：驱动真实 `ActionExecutor`，验证未配置双钥时拒绝 + handler 已注册；
  - c96 六演练：未接线必须红、接线后才绿；
  - c99 发布回执：空仓库必须被真实预检拒绝；绿跑回执的 checks_hash 由 checks 重算比对；
  - c101 撤回：使用真实 publish/retract，并校验 released registry 已清理；
  - 修 `sys.path`，让 `studio` 在 WSL 下可导入。
- 结果：**103/104 → 修 c99 判定后待重跑**（c6 依赖真实 selftest，WSL 已全绿）。

### 5. 目录整理
- 新建 `docs/human`、`docs/memory`、`docs/handover`；
- `交付说明-完整版.md` 移入 `docs/handover/`；
- 根目录只保留代码/构建/许可证/README/CHANGELOG 等必要文件。

## 待办（下一阶段）

- 把 104 项里的 c7/c8/c10/c11/c12/c16/c22/c35/c42/c43/c59/c71/c78/c79/c93/c98/c100/c104
  继续替换为真实链路断言（当前已有 21 项被标记为“自证”，详见审查报告）。
- 给 `cli` 增加 `constitution status` 命令，直接显示 covenant/预算/最近回执。
- 将 `audit_104.py` 拆成 `contract/` 与 `e2e/` 两套，避免再混用。
- 发布台在 Windows 下没有 git：要么内置 git（不可行），要么在 UI 明确提示
  “请在有 git 的环境发布”，或在 `publish()` 前置检查 `shutil.which("git")`。
