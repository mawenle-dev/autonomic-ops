# GitHub 账号绑定与发布

## 绑定目标

默认账号写在 `config/account.json`，随产品一起分发：

```json
{
  "owner": "mawenle-dev",
  "repo": "autonomic-ops",
  "remote": "https://github.com/mawenle-dev/autonomic-ops.git"
}
```

本地可用 `.secrets/account.json` 覆盖，不需要修改随包配置。发布台读取顺序为：
git 远端 `origin` → `config/account.json` → `.secrets/account.json`。

## 令牌

令牌只存放在本机：

`<根目录>/.secrets/github_token`

文件内容为一行令牌，不含引号与空格。该路径已列入 `.gitignore`，不会进入提交或便携包。

细粒度令牌的最小权限：

- Repository access：仅选择 `mawenle-dev/autonomic-ops`
- Repository permissions：`Contents: Read and write`
- `Metadata: Read` 由 GitHub 自动授予

不需要单独的 `Releases` 权限；创建 Release 与上传附件由 `Contents` 覆盖。

## 发布流程

1. 发布台执行预检，三项全绿后发布按钮才可用。
2. 发布动作写入本地标签、回执、台账与频道。
3. 推送提交与标签：

```bash
bash tools/push_github.sh
```

该脚本从 `.secrets/github_token` 读取令牌，通过临时 `GIT_ASKPASS` 传入，
不把令牌写入 git 配置或远程 URL。

4. 创建 GitHub Release 并上传便携包：

```bash
python3 tools/publish_release.py --version 1.0.1 \
  --asset release/dist/AO-Studio-portable-v1.0.1.zip \
  --body-file .tmp/release-body-1.0.1.md --confirm PUBLISH
```

发布器为 append-only：同一标签的 Release 已存在时拒绝创建，同名附件已存在时拒绝覆盖。

## 常见错误

| 现象 | 原因 | 处理 |
|---|---|---|
| `403 Permission ... denied` | 令牌只有读权限 | 将 `Contents` 改为 Read and write 后重试 |
| `missing_token` | 令牌文件不存在或为空 | 检查 `.secrets/github_token` |
| `no_github_remote` | 远端与账号配置均为空 | 检查 `config/account.json` 或 `git remote -v` |
| 连接 `127.0.0.1:443` 被拒绝 | WSL 继承了把 GitHub 指向本机回环的 hosts | `tools/push_github.sh` 会在会话内补齐真实地址 |
