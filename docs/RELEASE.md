# 发布与更新机制（Release & Update）

本文说明三件事：**发布会不会覆盖旧版**、**每个版本的文档在哪**、**本地如何用快照更新与回滚**。

## 1. 发布是追加式的，不会覆盖旧版

- 每个版本对应一个 **git tag**（`v1.0.1`、`v1.0.2` …）和一个 **GitHub Release**。
- 发布新版本 = **新建**一个 tag 与 Release，旧版本原样保留，不会被改写。
- GitHub 的 Release 资产**不可同名覆盖**：向同一个 Release 上传同名文件会返回 `422`，必须先删除旧资产。本项目**不删除、不替换**——遇到已存在直接拒绝并报告（`release_exists_no_overwrite` / `asset_exists_no_overwrite`）。
- 依据（已核对官方文档）：
  - Create a release: `POST /repos/{owner}/{repo}/releases`
  - Upload a release asset: `POST .../releases/{id}/assets?name=...`，同名返回 422
  - GitHub 亦提供 "immutable releases" 选项，进一步锁定已发布资产。

## 2. 每个版本都有自己的文档

- 路径：`release/docs/<版本号>.md`（如 `release/docs/1.0.1.md`），共 **120 份**。
- 索引：`release/docs/INDEX.md`。
- 每份文档包含：本版新增/修复、优点、验收标准、如何验证、升级与回滚。
- 生成器：`python tools/gen_version_docs.py`（从 `release/versions.yaml` 可复现）。
- 发布时 `ReleaseStudio.publish()` 会写一份**不可覆盖**的版本快照文档（已存在则不覆盖）。
- 项目总文档同步更新：`CHANGELOG.md`、`交付说明-完整版.md`。

## 3. 一键发布（默认 dry-run）

```bash
# 只看计划，不发任何写请求
python tools/publish_release.py --version 1.0.1 \
  --asset release/dist/AO-Studio-portable-v1.0.0/AO-Release-Studio.exe

# 确认后真正发布（需要 token）
python tools/publish_release.py --version 1.0.1 --asset ... --confirm PUBLISH

# 同时把文档改动推上去
python tools/publish_release.py --version 1.0.1 --asset ... --confirm PUBLISH --push-docs
```

安全约束：
- 无 `--confirm PUBLISH` → 只计划，不发请求。
- 已存在同 tag 的 Release → 拒绝，不覆盖。
- 同名资产 → 拒绝，不删除旧资产。
- 发布台 Web 界面同样调用 `ReleaseStudio.publish_github()`，默认 dry-run。

## 4. Token 放置（不要提交）

- 环境变量 `GITHUB_TOKEN` 或 `GH_TOKEN`，或
- 项目内 `.secrets/github_token`（已在 `.gitignore` 中，永不入库）。
- 仓库 remote 为 `git@github.com:mawenle-dev/autonomic-ops.git`。

## 5. 本地用快照更新（可回滚）

`src/autonomic/snapshot_update.py` 实现**签名快照**更新，这是桌面更新的主流做法（Sparkle 用全量 zip + 可选 delta + EdDSA 签名；全量快照是事实来源，增量只是优化）：

```python
from autonomic import snapshot_update as su
seed, pub = ...                     # 发布方私钥 / 使用方公钥
su.build_snapshot("dist/", "ao-1.0.1.tar.gz", seed)
su.verify_snapshot("ao-1.0.1.tar.gz", pub)          # 验签 + 逐文件 sha256
su.apply_snapshot("ao-1.0.1.tar.gz", "/opt/ao", pub)  # 原子替换，保留 backup
su.rollback("/opt/ao", backup)                      # 回滚
```

- 验签或任一文件哈希不符 → **不落盘**，旧版保持可用。
- 解包拒绝绝对路径与 `..`（路径穿越）。
- 失败自动从 backup 恢复。

## 6. 不做的事

- 不自动发布、不自动 push（必须显式 `--confirm PUBLISH` / `allow_push=True`）。
- 不删除或替换任何已发布资产。
- 不生成 delta 补丁（先保证正确性；快照格式已预留扩展空间）。
