# 快速开始（QUICKSTART）

从零到"看见它如何先彩排、再动手、还能退回去"，一条链路七步。所有命令均可复制执行。
安装与 systemd 细节见 [DEPLOYMENT.md](DEPLOYMENT.md)，字段说明见 [USAGE.md](USAGE.md)。

> 前置：一台 Linux 主机（systemd + Python 3.9+），有 sudo。演示在一次性测试机上进行，不要直接上生产。

## 1. 安装（不常驻，先看效果）

```bash
git clone <your-repo-url> autonomic-ops && cd autonomic-ops
sudo bash scripts/install.sh --prefix /opt/autonomic-ops --no-service --non-interactive
```

安装器不启动守护进程，便于先观察运行效果。若仅需假根校验，使用 `--simulate`。

## 2. 启动守护进程 + 看面板

```bash
sudo /opt/autonomic-ops/bin/autonomic-ops service start
sudo /opt/autonomic-ops/bin/autonomic-ops status
sudo /opt/autonomic-ops/bin/autonomic-ops serve --bind 127.0.0.1:8081
```

浏览器打开 `http://127.0.0.1:8081/`。面板只读，写操作一律走本地 socket。
（兼容写法 `--port 8081`、`--bind :8081` 也可用；默认只监听本机。）

## 3. 触发一次故障（磁盘写满剧本）

```bash
mkdir -p /tmp/ao-demo && printf 'OLD-DATA\n' > /tmp/ao-demo/old.log
touch -d '3 hours ago' /tmp/ao-demo/old.log
```

在 `config.yaml` 里把 `policy.cleanup_paths` 指向 `/tmp/ao-demo`，`policy.dry_run: true` 保持演练态，然后重载：

```bash
sudo /opt/autonomic-ops/bin/autonomic-ops config reload
```

## 4. 看彩排（影子面）

```bash
sudo /opt/autonomic-ops/bin/autonomic-ops submit --kind action --command "" \
  --title "disk-cleanup" --priority 50
sudo /opt/autonomic-ops/bin/autonomic-ops pending
```

`pending` 里能看到动作的事实引用、判据推理链、契约预算与"若执行会发生什么"。这就是**先彩排**。

## 5. 执行

```bash
sudo /opt/autonomic-ops/bin/autonomic-ops approve <pending-id>
sudo /opt/autonomic-ops/bin/autonomic-ops tasks
```

执行后 `/tmp/ao-demo/old.log` 从原位置消失，但**字节完整入仓**到 `vault/quarantine/<日期>/<快照id>/`。

## 6. 看回执

```bash
sudo /opt/autonomic-ops/bin/autonomic-ops audit tail -n 25
```

回执（正账）记录：事实引用 → 判据 → 契约 → 影子预测 vs 实际 → 撤销句柄。
`autonomic-ops explain <id>` 可把任意一条拆成"结论/依据/卡点/下一步"四段。

## 7. 撤销（10 分钟内免审批）

```bash
sudo /opt/autonomic-ops/bin/autonomic-ops rollback <snapshot-id>
cat /tmp/ao-demo/old.log     # 应精确恢复为 OLD-DATA
```

撤销是反向 rename，字节级精确。退出码：`0` 全成功 / `2` 部分 / `1` 全失败 / `3` 参数错。

## 自证与自查

```bash
autonomic-ops prove                       # 反向测试：故意打坏必须被拦住
autonomic-ops capabilities --kind probes  # 26 类探针
autonomic-ops capabilities --kind controls# 30 条合规控制
bash tests/selftest.sh                    # 50 项端到端断言（Linux）
```

## 发布台（AO Release Studio）

双击 `AO-Release-Studio.exe`（或 `python -m studio.app`），浏览器打开本地控制台：
一屏显示当前/上一版/本次发什么版本，预检全绿才能点发布，支持撤回与实时日志。
完整 120 版路线见 `release/versions.yaml`。

截图见 [docs/screenshots/dashboard.png](screenshots/dashboard.png)。
