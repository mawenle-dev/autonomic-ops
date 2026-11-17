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
function act(p){log('POST '+p);fetch(p).then(r=>r.json()).then(j=>{log(JSON.stringify(j));state();});}
state();
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
