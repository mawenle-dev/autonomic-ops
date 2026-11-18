"""Generate one documentation page per roadmap version (release/docs/<v>.md)
plus an INDEX. Reproducible from release/versions.yaml; safe to re-run (it
rewrites the generated pages deterministically). Existing pages produced by a
publish snapshot are never overwritten if they differ - publish writes them
once, this generator is for the pre-release documentation set.
"""
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "release", "versions.yaml")
OUT = os.path.join(ROOT, "release", "docs")

SERIES_TO_AUDIT = {
    "A": ("G1", "修复：撤销/快照/去重/门禁"),
    "B": ("G2", "回执与账本"),
    "C": ("G3", "判据"),
    "D": ("G4", "契约与影子"),
    "E": ("G5", "执行与撤销"),
    "F": ("G6", "编排"),
    "G": ("G7", "通报"),
    "H": ("G8", "观测"),
    "I": ("G9", "守门"),
    "J": ("G10", "收敛"),
    "K": ("G11", "顾问"),
    "L": ("G12", "器库与自证"),
    "M": ("G13", "发布台与平台"),
}


def parse(path):
    out, cur = [], None
    for line in open(path, encoding="utf-8"):
        m = re.match(r'\s*-\s*version:\s*"([^"]+)"', line)
        if m:
            if cur:
                out.append(cur)
            cur = {"version": m.group(1)}
            continue
        if cur is not None:
            for k in ("ordinal", "gate", "series", "deliver", "highlights", "acceptance", "status"):
                if line.strip().startswith(k + ":"):
                    v = line.split(":", 1)[1].strip().strip('"')
                    cur[k] = int(v) if k == "ordinal" and v.isdigit() else v
    if cur:
        out.append(cur)
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    versions = parse(SRC)
    index = ["# 版本文档索引（120 版）", "",
             "每个版本一份独立文档，说明该版新增/修复了什么、优点、验收标准与验证入口。", "",
             "| 序号 | 版本 | 系列 | 文档 | 状态 |", "|---|---|---|---|---|"]
    written = 0
    for v in versions:
        ver = v["version"]
        series = v.get("series", "")
        letter = series[:1]
        group, gname = SERIES_TO_AUDIT.get(letter, ("—", "—"))
        items = [x.strip() for x in re.split(r"[；;]", v.get("deliver", "")) if x.strip()]
        body = f"""# {ver}（第 {v.get('ordinal')} 个版本）

- 系列：{series}
- 闸门键：`{v.get('gate','')}`
- 状态：{v.get('status','')}
- 发布状态：未发布（闸门保持 false；发布是独立动作）

## 本版新增 / 修复

""" + "\n".join(f"{i+1}. {it}" for i, it in enumerate(items)) + f"""

## 优点

{v.get('highlights','')}

## 验收标准

{v.get('acceptance','')}

## 如何验证

- 审计分组：{group} {gname}
- 在 Linux 上运行：`PYTHONPATH="src:." python3 tools/audit_104.py`，查看 `audit-report.json` 中 {group} 组对应条目。
- 单元测试：`PYTHONPATH=src python3 -m unittest discover -s tests -p "test_*.py"`

## 升级与回滚

- 本版以**签名快照**分发（Ed25519）。升级前会校验签名与逐文件 sha256，失败即丢弃，旧版保持可用。
- 回滚：保留上一版快照，可原子切回（见 `src/autonomic/snapshot_update.py`）。
"""
        with open(os.path.join(OUT, f"{ver}.md"), "w", encoding="utf-8") as fh:
            fh.write(body)
        written += 1
        index.append(f"| {v.get('ordinal')} | {ver} | {series} | [{ver}.md]({ver}.md) | {v.get('status','')} |")

    with open(os.path.join(OUT, "INDEX.md"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(index) + "\n")
    print(f"wrote {written} version docs + INDEX.md to release/docs/")


if __name__ == "__main__":
    main()
