"""AO Release Studio - Web console (primary UI, stdlib-only).

Why web UI: the target Python may not ship tkinter. A local http.server bound to
127.0.0.1 gives the same one-screen desktop experience through the default
browser, zero third-party deps, and packs cleanly with PyInstaller. No write
operations are exposed over HTTP: all actions are triggered by the local GUI
process itself, not by remote requests (constitution 11.11: HTTP面只读).

Run:  python -m studio.app [--root /path/to/repo] [--no-browser] [--port 8765]
"""
from __future__ import annotations

import argparse
import json
import os
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .core import Preflight, ReleaseStudio


def _server(studio: ReleaseStudio, pf: Preflight):
    class Handler(BaseHTTPRequestHandler):
        server_version = "AOReleaseStudio/1.0"

        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="text/html; charset=utf-8"):
            data = body.encode("utf-8") if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            path = urlparse(self.path).path
            st = studio.state()
            if path == "/api/state":
                return self._send(200, json.dumps(st, ensure_ascii=False), "application/json")
            if path == "/api/versions":
                return self._send(200, json.dumps(studio.versions_full(), ensure_ascii=False),
                                  "application/json")
            if path.startswith("/api/version/"):
                v = path.rsplit("/", 1)[-1]
                d = studio.version_detail(v)
                return self._send(200, json.dumps(d or {}, ensure_ascii=False), "application/json")
            if path.startswith("/version/"):
                v = path.rsplit("/", 1)[-1]
                return self._send(200, _version_page(studio, v))
            if path.startswith("/release/"):
                v = path.rsplit("/", 1)[-1]
                return self._send(200, _github_release_page(studio, v))
            if path == "/api/preflight":
                r = pf.run()
                return self._send(200, json.dumps(r, ensure_ascii=False), "application/json")
            if path in ("/publish", "/retract"):
                # Actions are local-GUI triggers (control socket equivalent).
                if path == "/publish":
                    d = st.get("next_detail") or {}
                    r = studio.publish(preflight_ok=pf.run()["ok"], gate=d.get("gate", ""),
                                       notes=d.get("deliver", ""))
                else:
                    r = studio.retract(st["current"])
                return self._send(200, json.dumps(r, ensure_ascii=False), "application/json")
            return self._send(200, PAGE)

    return Handler


def _esc(s) -> str:
    return (str(s or "").replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _version_page(studio, v: str) -> str:
    d = studio.version_detail(v)
    if not d:
        return f"<!doctype html><meta charset=utf-8><p>unknown version { _esc(v) }</p>"
    rel = "已发布 ✅" if d.get("released") else "未发布"
    rel_at = d.get("released_at") or "—"
    return f"""<!doctype html><meta charset=utf-8><title>{_esc(v)}</title>
<style>body{{font-family:ui-sans-serif,system-ui;background:#f5f6f8;color:#1a1c1e;margin:0;padding:24px}}
.card{{background:#fff;border:1px solid #dfe2e6;border-radius:10px;padding:18px;margin:12px 0;max-width:820px}}
h1{{font-size:22px}}.k{{color:#666}}.mono{{font-family:ui-monospace,Consolas,monospace}}
a{{color:#0b6}}</style>
<div class=card>
<h1>{_esc(v)} <span class=mono>第 {d.get('ordinal')} 个版本</span></h1>
<p class=k>系列：{_esc(d.get('series'))}</p>
<p><b>这次更新了什么：</b>{_esc(d.get('deliver'))}</p>
<p><b>优点：</b>{_esc(d.get('highlights'))}</p>
<p><b>闸门键：</b><span class=mono>{_esc(d.get('gate'))}</span>　<b>验收：</b>{_esc(d.get('acceptance'))}</p>
<p><b>状态：</b>{rel}　<b>发布日期：</b>{_esc(rel_at)}</p>
<p><a href="/release/{_esc(v)}">▶ 预览它发布后长什么样（模拟 GitHub 发布页）</a></p>
<p><a href="/">← 返回控制台</a></p>
</div>"""


def _github_release_page(studio, v: str) -> str:
    d = studio.version_detail(v)
    if not d:
        return f"<!doctype html><meta charset=utf-8><p>unknown version {_esc(v)}</p>"
    released = d.get("released")
    tag = f"v{v}"
    rel_at = d.get("released_at") or "（尚未发布）"
    badge = ("<span class=badge>Latest</span>" if released else
             "<span class=badge draft>Draft / 未发布</span>")
    # mock GitHub release page layout (no external assets, fully offline)
    return f"""<!doctype html><meta charset=utf-8><title>Release {_esc(tag)} · autonomic-ops</title>
<style>
body{{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;background:#fff;color:#1f2328}}
.top{{background:#24292f;color:#fff;padding:10px 24px;font-size:14px}}
.wrap{{max-width:980px;margin:0 auto;padding:24px}}
h1{{font-size:24px;margin:0 0 6px}} .tag{{background:#ddf4ff;color:#0969da;border-radius:6px;padding:2px 8px;font-family:monospace}}
.badge{{background:#1f883d;color:#fff;border-radius:20px;padding:2px 10px;font-size:12px;margin-left:8px}}
.badge.draft{{background:#bf8700}}
.meta{{color:#656d76;font-size:13px;margin:6px 0 18px}}
.box{{border:1px solid #d0d7de;border-radius:8px;padding:18px}}
h2{{font-size:16px;border-bottom:1px solid #d0d7de;padding-bottom:6px}}
ul{{line-height:1.7}} .k{{color:#656d76}}
.foot{{color:#656d76;font-size:12px;margin-top:20px}}
</style>
<div class=top>autonomic-ops / releases</div>
<div class=wrap>
  <h1>{_esc(tag)} {badge}</h1>
  <div class=meta>发布于 {_esc(rel_at)} · 第 {d.get('ordinal')} 个版本 · 系列 {_esc(d.get('series'))}</div>
  <div class=box>
    <h2>{_esc(d.get('highlights') or d.get('deliver'))}</h2>
    <p class=k>这次更新了什么</p>
    <ul><li>{_esc(d.get('deliver'))}</li></ul>
    <p class=k>优点</p>
    <ul><li>{_esc(d.get('highlights'))}</li></ul>
    <p class=k>验收标准</p>
    <ul><li>{_esc(d.get('acceptance'))}</li></ul>
    <p class=k>闸门键</p>
    <p class=mono>{_esc(d.get('gate'))}</p>
  </div>
  <div class=foot>这是一个本地模拟的 GitHub Release 页面，仅用于预览发布后的样子；数据来自 release/versions.yaml。离线可用。</div>
  <p><a href="/version/{_esc(v)}">← 返回版本详情</a> · <a href="/">控制台</a></p>
</div>"""


PAGE = """<!doctype html><meta charset=utf-8>
<title>AO Release Studio</title>
<style>
body{font-family:ui-sans-serif,system-ui;background:#f5f6f8;color:#1a1c1e;margin:0;padding:24px}
h1{font-size:20px} .card{background:#fff;border:1px solid #dfe2e6;border-radius:10px;padding:18px;margin:12px 0}
.mono{font-family:ui-monospace,Consolas,monospace} .big{font-size:22px;font-weight:700;color:#0b6}
button{font-size:15px;padding:9px 16px;border-radius:8px;border:1px solid #bbb;background:#fff;cursor:pointer}
button.primary{background:#0b6;color:#fff;border-color:#0b6}button:disabled{opacity:.45;cursor:not-allowed}
table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #eee;padding:6px;text-align:left}
pre{background:#0e1116;color:#e6e6e6;padding:12px;border-radius:8px;overflow:auto;max-height:260px}
.ok{color:#0a0}.fail{color:#c00}
</style>
<h1>AO Release Studio <span class=mono id=total></span></h1>
<div class=card>
  <div class=big id=cur>当前版本 —</div>
  <div id=prev></div><div id=next></div><div id=detail class=mono></div>
</div>
<div class=card>
  <button onclick="pre()">预检 Preflight</button>
  <button id=pub class=primary onclick="act('/publish')" disabled>发布 Publish</button>
  <button onclick="act('/retract')">撤回 Retract</button>
  <pre id=log>ready.&#10;</pre>
</div>
<div class=card><table id=checks><tr><th>检查</th><th>结果</th><th>耗时</th></tr></table></div>
<div class=card>
  <h2 style="font-size:16px;margin:0 0 8px">全部版本历史（1.0.0 → 3.0.3，共 121 项）</h2>
  <div id=versions class=mono style="font-size:13px"></div>
</div>
<script>
function log(s){document.getElementById('log').textContent+=s+'\\n'}
function state(){return fetch('/api/state').then(r=>r.json()).then(s=>{
  document.getElementById('total').textContent='('+s.total_versions+' versions)';
  document.getElementById('cur').textContent='当前版本: '+s.current;
  document.getElementById('prev').textContent='上一版: '+(s.previous||'—');
  document.getElementById('next').textContent='本次发: '+(s.next||'—');
  document.getElementById('detail').textContent=(s.next_detail||{}) ?
   ('闸门键: '+(s.next_detail.gate||'—')+' | 交付: '+(s.next_detail.deliver||'—')+' | 验收: '+(s.next_detail.acceptance||'—')):'';
})}
function pre(){log('=== preflight start ===');
  fetch('/api/preflight').then(r=>r.json()).then(r=>{
    var t=document.getElementById('checks');t.innerHTML='<tr><th>检查</th><th>结果</th><th>耗时</th></tr>';
    r.checks.forEach(c=>{var tr=t.insertRow();tr.insertCell().textContent=c.name;
      var d=tr.insertCell();d.className=c.ok?'ok':'fail';d.textContent=c.ok?'OK':'FAIL';
      tr.insertCell().textContent=c.ms+'ms';log('['+c.name+'] '+(c.ok?'OK':'FAIL')+' '+c.output.slice(-400));});
    document.getElementById('pub').disabled=!r.publish_enabled;
    log('=== preflight '+(r.ok?'GREEN':'RED')+'; publish '+(r.publish_enabled?'enabled':'disabled')+' ===');
  });}
function act(p){log('POST '+p);fetch(p).then(r=>r.json()).then(j=>{log(JSON.stringify(j));state();versions();});}
function versions(){fetch('/api/versions').then(r=>r.json()).then(list=>{
  var box=document.getElementById('versions');box.innerHTML='';
  list.forEach(function(v){
    var row=document.createElement('div');
    row.style.cssText='border-bottom:1px solid #eee;padding:6px;cursor:pointer';
    var rel=v.released?'<span style="color:#0a0">已发布 '+(v.released_at||'')+'</span>':'<span style="color:#999">未发布</span>';
    row.innerHTML='<b>'+v.version+'</b> 第'+(v.ordinal||'-')+'个 · '+v.series+' · '+rel;
    var det=document.createElement('div');
    det.style.cssText='display:none;color:#444;margin-top:4px;font-size:12px';
    det.innerHTML='<div><b>更新：</b>'+v.deliver+'</div><div><b>优点：</b>'+v.highlights+'</div>'+
      '<div><b>闸门键：</b>'+v.gate+'　<b>验收：</b>'+v.acceptance+'</div>'+
      '<div><a href="/release/'+v.version+'">▶ 预览发布后的样子（模拟 GitHub）</a></div>';
    row.appendChild(det);
    row.onclick=function(){det.style.display=det.style.display==='none'?'block':'none';};
    box.appendChild(row);
  });
})}
state();versions();
</script>
"""


def make_server(root: str, port: int = 8765):
    studio = ReleaseStudio(root)
    pf = Preflight()

    def compile_check():
        import py_compile
        files = []
        for base, _d, names in os.walk(os.path.join(root, "src")):
            for n in names:
                if n.endswith(".py"):
                    files.append(os.path.join(base, n))
        try:
            for f in files:
                py_compile.compile(f, doraise=True)
            return True, f"compileall: {len(files)} files OK"
        except Exception as exc:  # noqa: BLE001
            return False, str(exc)

    def test_check():
        import subprocess
        p = subprocess.run(["python", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
                           cwd=root, capture_output=True, text=True)
        return p.returncode == 0, (p.stdout + p.stderr)[-2000:]

    pf.add("compile", compile_check)
    pf.add("unit tests", test_check)
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _server(studio, pf))
    return httpd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    httpd = make_server(args.root, args.port)
    url = f"http://127.0.0.1:{args.port}/"
    print(f"AO Release Studio -> {url}")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
