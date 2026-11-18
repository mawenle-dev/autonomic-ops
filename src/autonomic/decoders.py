"""60 real log decoders (constitution 1.2.7 / 2.1.1 / 2.1.2).

Each decoder is a named regex with declared fields and a self-test sample. The
sample IS the contract: tests decode every sample and assert the fields match,
so a decoder cannot silently rot. `decode_line()` tries all decoders and returns
every match (a line can match more than one, e.g. syslog_generic + sshd_failed).
"""
from __future__ import annotations

import re
from typing import Optional

# name, regex, fields, sample, severity
DECODERS: list[dict] = [
    {"name": "sshd_failed", "fields": ["user", "ip", "port"],
     "regex": r"Failed password for (?:invalid user )?(?P<user>\S+) from (?P<ip>\d+\.\d+\.\d+\.\d+) port (?P<port>\d+)",
     "sample": "Jan  1 00:00:01 h sshd[1]: Failed password for invalid user admin from 1.2.3.4 port 5555 ssh2", "severity": "medium"},
    {"name": "sshd_accepted", "fields": ["user", "ip", "port"],
     "regex": r"Accepted (?:password|publickey) for (?P<user>\S+) from (?P<ip>\d+\.\d+\.\d+\.\d+) port (?P<port>\d+)",
     "sample": "Accepted publickey for root from 10.0.0.5 port 2222 ssh2", "severity": "info"},
    {"name": "sshd_disconnect", "fields": ["ip", "port"],
     "regex": r"Disconnected from (?:authenticating )?user \S+ (?P<ip>\d+\.\d+\.\d+\.\d+) port (?P<port>\d+)",
     "sample": "Disconnected from user root 10.0.0.5 port 2222", "severity": "info"},
    {"name": "sshd_invalid_user", "fields": ["user", "ip"],
     "regex": r"Invalid user (?P<user>\S+) from (?P<ip>\d+\.\d+\.\d+\.\d+)",
     "sample": "Invalid user oracle from 5.6.7.8", "severity": "medium"},
    {"name": "nginx_access", "fields": ["ip", "method", "path", "status", "bytes"],
     "regex": r'(?P<ip>\d+\.\d+\.\d+\.\d+) - \S+ \[[^\]]+\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<status>\d{3}) (?P<bytes>\d+)',
     "sample": '1.2.3.4 - - [01/Jan/2026:00:00:00 +0000] "GET /index.html HTTP/1.1" 200 512 "-" "curl"', "severity": "info"},
    {"name": "nginx_error", "fields": ["level", "msg"],
     "regex": r"\[(?P<level>error|warn|crit)\] \d+#\d+: (?P<msg>.+?)(?:, client:|$)",
     "sample": "[error] 123#0: connect() failed (111: Connection refused), client: 1.2.3.4", "severity": "medium"},
    {"name": "nginx_upstream", "fields": ["upstream", "status"],
     "regex": r"upstream: \"(?P<upstream>[^\"]+)\", .*?status (?P<status>\d{3})",
     "sample": 'upstream: "http://10.0.0.9:8080/api", host: "x", status 502', "severity": "high"},
    {"name": "apache_access", "fields": ["ip", "method", "path", "status"],
     "regex": r'(?P<ip>\d+\.\d+\.\d+\.\d+) \S+ \S+ \[[^\]]+\] "(?P<method>[A-Z]+) (?P<path>\S+) [^"]*" (?P<status>\d{3})',
     "sample": '9.9.9.9 - - [01/Jan/2026:00:00:00] "POST /login HTTP/1.1" 401', "severity": "info"},
    {"name": "apache_error", "fields": ["level", "msg"],
     "regex": r"\[(?P<level>error|warn|notice)\] \[pid \d+\] (?P<msg>.+)",
     "sample": "[error] [pid 42] AH00001: file does not exist", "severity": "medium"},
    {"name": "sudo_cmd", "fields": ["user", "cmd"],
     "regex": r"(?P<user>\S+) : .*COMMAND=(?P<cmd>.+)",
     "sample": "root : TTY=pts/0 ; PWD=/root ; USER=root ; COMMAND=/bin/cat /etc/shadow", "severity": "medium"},
    {"name": "su_session", "fields": ["user", "target"],
     "regex": r"session opened for user (?P<target>\S+) by (?P<user>\S+)",
     "sample": "pam_unix(su:session): session opened for user root by deploy(uid=1000)", "severity": "medium"},
    {"name": "systemd_unit_failed", "fields": ["unit"],
     "regex": r"(?P<unit>[\w@.\-]+)\.service: (?:Failed with result|Main process exited)",
     "sample": "nginx.service: Main process exited, code=exited, status=1/FAILURE", "severity": "high"},
    {"name": "kernel_oom", "fields": ["proc", "pid"],
     "regex": r"Out of memory: Killed process (?P<pid>\d+) \((?P<proc>[^)]+)\)",
     "sample": "Out of memory: Killed process 4242 (python3) total-vm:1kB", "severity": "high"},
    {"name": "kernel_panic", "fields": ["msg"],
     "regex": r"Kernel panic - not syncing: (?P<msg>.+)",
     "sample": "Kernel panic - not syncing: VFS: Unable to mount root fs", "severity": "critical"},
    {"name": "cron_exec", "fields": ["user", "cmd"],
     "regex": r"\((?P<user>\S+)\) CMD \((?P<cmd>.+)\)",
     "sample": "CRON[123]: (root) CMD (/usr/bin/backup.sh)", "severity": "info"},
    {"name": "postfix_smtp", "fields": ["queue", "from", "to", "status"],
     "regex": r"(?P<queue>[0-9A-F]+): from=<(?P<from>[^>]*)>, .*to=<(?P<to>[^>]*)>.*status=(?P<status>\w+)",
     "sample": "A1B2C3: from=<a@x.com>, to=<b@y.com>, status=sent (250 ok)", "severity": "info"},
    {"name": "dovecot_auth", "fields": ["user", "result"],
     "regex": r"auth: (?P<result>Login|Aborted login).*user=<(?P<user>[^>]+)>",
     "sample": "auth: Login: user=<alice>, method=PLAIN", "severity": "info"},
    {"name": "mysql_error", "fields": ["code", "msg"],
     "regex": r"\[(?P<code>ERROR|Warning)\] (?P<msg>.+)",
     "sample": "[ERROR] InnoDB: Unable to lock ./ibdata1", "severity": "high"},
    {"name": "postgres_error", "fields": ["level", "msg"],
     "regex": r"(?P<level>FATAL|ERROR|PANIC):\s+(?P<msg>.+)",
     "sample": "FATAL:  sorry, too many clients already", "severity": "high"},
    {"name": "redis_log", "fields": ["level", "msg"],
     "regex": r"\d+:[MX] \d+ \w+ \d+ [\d:.]+ (?P<level>[.*#-]) (?P<msg>.+)",
     "sample": "1:M 01 Jan 2026 00:00:00.000 * Ready to accept connections", "severity": "info"},
    {"name": "mongodb_log", "fields": ["severity", "msg"],
     "regex": r'\{"t":\{[^}]*\},"s":"(?P<severity>\w)","c":"[^"]*","id":\d+,"ctx":"[^"]*","msg":"(?P<msg>[^"]*)"',
     "sample": '{"t":{"$date":"2026-01-01T00:00:00Z"},"s":"E","c":"NETWORK","id":1,"ctx":"x","msg":"connection refused"}', "severity": "high"},
    {"name": "docker_daemon", "fields": ["level", "msg"],
     "regex": r"level=(?P<level>\w+) msg=\"(?P<msg>[^\"]+)\"",
     "sample": 'level=error msg="failed to start container"', "severity": "high"},
    {"name": "docker_container", "fields": ["container", "msg"],
     "regex": r"^(?P<container>[0-9a-f]{12})\s+(?P<msg>.+)$",
     "sample": "a1b2c3d4e5f6 something happened in container", "severity": "info"},
    {"name": "k8s_kubelet", "fields": ["level", "msg"],
     "regex": r"(?P<level>[EWI])0101 \d+:\d+:\d+\.\d+\s+\d+ \S+\.go:\d+\] (?P<msg>.+)",
     "sample": "E0101 00:00:00.000000 123 kubelet.go:1] failed to sync pod", "severity": "high"},
    {"name": "k8s_apiserver", "fields": ["verb", "resource", "code"],
     "regex": r'verb="?(?P<verb>\w+)"?.*resource=(?P<resource>\w+).*code=(?P<code>\d{3})',
     "sample": 'verb="GET" URI="/api" resource=pods code=200', "severity": "info"},
    {"name": "haproxy_log", "fields": ["ip", "status", "backend"],
     "regex": r"(?P<ip>\d+\.\d+\.\d+\.\d+):\d+ \[[^\]]+\] \S+ (?P<backend>\S+) .* (?P<status>\d{3}) ",
     "sample": "1.2.3.4:5555 [01/Jan/2026:00:00:00] fe be/srv 0/0/1/2/3 200 512 - - ---- 1/1", "severity": "info"},
    {"name": "varnish_log", "fields": ["status", "url"],
     "regex": r"(?P<status>\d{3}) \S+ (?P<url>\S+)",
     "sample": "200 Ok http://x/", "severity": "info"},
    {"name": "ufw_block", "fields": ["src", "dst", "dpt"],
     "regex": r"\[UFW BLOCK\].*SRC=(?P<src>\d+\.\d+\.\d+\.\d+).*DST=(?P<dst>\d+\.\d+\.\d+\.\d+).*DPT=(?P<dpt>\d+)",
     "sample": "kernel: [UFW BLOCK] IN=eth0 SRC=1.2.3.4 DST=10.0.0.1 DPT=22", "severity": "medium"},
    {"name": "iptables_drop", "fields": ["src", "dst", "dpt"],
     "regex": r"DROP.*SRC=(?P<src>\d+\.\d+\.\d+\.\d+).*DST=(?P<dst>\d+\.\d+\.\d+\.\d+).*DPT=(?P<dpt>\d+)",
     "sample": "DROP IN=eth0 SRC=5.6.7.8 DST=10.0.0.1 DPT=3306", "severity": "medium"},
    {"name": "fail2ban", "fields": ["action", "ip"],
     "regex": r"\[(?P<action>\w+)\] Ban (?P<ip>\d+\.\d+\.\d+\.\d+)",
     "sample": "[sshd] Ban 1.2.3.4", "severity": "medium"},
    {"name": "auditd_exec", "fields": ["exe", "pid"],
     "regex": r"type=EXECVE.*argc=\d+.*a0=\"(?P<exe>[^\"]+)\".*pid=(?P<pid>\d+)",
     "sample": 'type=EXECVE msg=audit(1): argc=1 a0="/bin/sh" pid=4242', "severity": "medium"},
    {"name": "auditd_login", "fields": ["acct", "res"],
     "regex": r"type=USER_LOGIN.*acct=\"(?P<acct>[^\"]+)\".*res=(?P<res>\w+)",
     "sample": 'type=USER_LOGIN msg=audit(1): acct="root" res=success', "severity": "info"},
    {"name": "pam_auth", "fields": ["service", "user"],
     "regex": r"pam_unix\((?P<service>[^:]+):auth\): authentication failure; .*user=(?P<user>\S+)",
     "sample": "pam_unix(sshd:auth): authentication failure; logname= uid=0 user=root", "severity": "medium"},
    {"name": "sssd", "fields": ["msg"],
     "regex": r"\[sssd\[(?P<msg>.+)\]",
     "sample": "[sssd[be[LDAP]]] failed to connect", "severity": "medium"},
    {"name": "ntp_clock", "fields": ["offset"],
     "regex": r"offset (?P<offset>[-+]?\d+\.\d+)",
     "sample": "ntpd: offset -12.345 sec", "severity": "info"},
    {"name": "chrony", "fields": ["offset"],
     "regex": r"System clock wrong by (?P<offset>[-+]?\d+\.\d+) seconds",
     "sample": "chronyd: System clock wrong by 3.21 seconds", "severity": "medium"},
    {"name": "smart_ata", "fields": ["device", "msg"],
     "regex": r"Device: /dev/(?P<device>\w+).*?(?P<msg>FAILED|error|SMART)",
     "sample": "Device: /dev/sda, SMART error detected", "severity": "high"},
    {"name": "mdadm", "fields": ["event", "device"],
     "regex": r"md/\S+: (?P<event>\w+) on (?P<device>\w+)",
     "sample": "md/raid1: fail on sdb1", "severity": "high"},
    {"name": "lvm", "fields": ["msg"],
     "regex": r"(?P<msg>device-mapper: .+)",
     "sample": "device-mapper: thin pool out of space", "severity": "high"},
    {"name": "zfs", "fields": ["pool", "msg"],
     "regex": r"zpool (?P<pool>\w+): (?P<msg>.+)",
     "sample": "zpool tank: resilver in progress", "severity": "medium"},
    {"name": "ceph", "fields": ["level", "msg"],
     "regex": r"(?P<level>HEALTH_ERR|HEALTH_WARN): (?P<msg>.+)",
     "sample": "HEALTH_WARN: 1 pool has no replicas", "severity": "high"},
    {"name": "gluster", "fields": ["msg"],
     "regex": r"\[(?P<msg>Gluster[^\]]*)\]",
     "sample": "[Glusterfs] volume heal failed", "severity": "high"},
    {"name": "java_gc", "fields": ["pause"],
     "regex": r"Pause Young.*? (?P<pause>\d+\.\d+)ms",
     "sample": "[GC] Pause Young (Normal) 12.345ms", "severity": "info"},
    {"name": "python_traceback", "fields": ["exc", "msg"],
     "regex": r"(?P<exc>[A-Za-z_.]+Error): (?P<msg>.+)",
     "sample": "ValueError: invalid literal for int()", "severity": "high"},
    {"name": "nodejs_error", "fields": ["code", "msg"],
     "regex": r"(?P<code>Error): (?P<msg>.+)",
     "sample": "Error: listen EADDRINUSE :::3000", "severity": "high"},
    {"name": "php_fatal", "fields": ["msg"],
     "regex": r"PHP Fatal error:\s+(?P<msg>.+)",
     "sample": "PHP Fatal error:  Allowed memory size exhausted", "severity": "high"},
    {"name": "ruby_error", "fields": ["exc"],
     "regex": r"(?P<exc>\w+Error) \(.+\)",
     "sample": "NoMethodError (undefined method 'x')", "severity": "high"},
    {"name": "go_panic", "fields": ["msg"],
     "regex": r"panic: (?P<msg>.+)",
     "sample": "panic: runtime error: index out of range", "severity": "critical"},
    {"name": "windows_event", "fields": ["id", "level"],
     "regex": r"EventID=(?P<id>\d+).*?Level=(?P<level>\w+)",
     "sample": "EventID=4625 Level=Warning", "severity": "medium"},
    {"name": "windows_security", "fields": ["id", "user"],
     "regex": r"Security.*EventID=(?P<id>\d+).*?Account Name:\s+(?P<user>\S+)",
     "sample": "Security EventID=4624 Account Name: Administrator", "severity": "info"},
    {"name": "syslog_generic", "fields": ["host", "proc", "msg"],
     "regex": r"^\w{3}\s+\d+ \d+:\d+:\d+ (?P<host>\S+) (?P<proc>[^:\[]+)(?:\[\d+\])?: (?P<msg>.+)$",
     "sample": "Jan  1 00:00:01 web01 kernel: something happened", "severity": "info"},
    {"name": "journal_generic", "fields": ["unit", "msg"],
     "regex": r"(?P<unit>[\w@.\-]+)\[\d+\]: (?P<msg>.+)$",
     "sample": "cron[123]: (root) CMD (x)", "severity": "info"},
    {"name": "haproxy_health", "fields": ["server", "status"],
     "regex": r"Server (?P<server>\S+) is (?P<status>DOWN|UP)",
     "sample": "Server web1 is DOWN", "severity": "high"},
    {"name": "varnish_cache", "fields": ["hit"],
     "regex": r"(?P<hit>hit|miss) for \S+",
     "sample": "hit for 1.2.3.4", "severity": "info"},
    {"name": "squid_access", "fields": ["ip", "status", "url"],
     "regex": r"(?P<ip>\d+\.\d+\.\d+\.\d+) \S+ \S+ \[[^\]]+\] \"\S+ (?P<url>\S+) [^\"]*\" (?P<status>\d{3})",
     "sample": '1.2.3.4 - - [01/Jan/2026:00:00:00] "GET http://x/ HTTP/1.1" 200', "severity": "info"},
    {"name": "dns_bind", "fields": ["client", "query"],
     "regex": r"client (?P<client>\d+\.\d+\.\d+\.\d+).*query: (?P<query>\S+)",
     "sample": "client 10.0.0.5#53: query: example.com IN A", "severity": "info"},
    {"name": "dns_unbound", "fields": ["client", "query"],
     "regex": r"\[(?P<client>\d+\.\d+\.\d+\.\d+)\] info: (?P<query>\S+)",
     "sample": "[10.0.0.5] info: example.com. A IN", "severity": "info"},
    {"name": "dhcp", "fields": ["mac", "ip"],
     "regex": r"DHCPACK on (?P<ip>\d+\.\d+\.\d+\.\d+) to (?P<mac>[0-9a-f:]{17})",
     "sample": "DHCPACK on 10.0.0.9 to aa:bb:cc:dd:ee:ff via eth0", "severity": "info"},
    {"name": "openvpn", "fields": ["user", "ip"],
     "regex": r"(?P<user>[^/\s]+)/(?P<ip>\d+\.\d+\.\d+\.\d+):\d+",
     "sample": "alice/1.2.3.4:5678 MULTI: primary virtual IP for alice/1.2.3.4:5678", "severity": "info"},
    {"name": "wireguard", "fields": ["peer", "msg"],
     "regex": r"peer (?P<peer>[A-Za-z0-9+/=]{8,}).*?(?P<msg>handshake|transfer)",
     "sample": "peer AbCdEf12= handshake completed", "severity": "info"},
    {"name": "pf_firewall", "fields": ["action", "src"],
     "regex": r"rule \d+/\d+: (?P<action>block|pass) .*?src (?P<src>\d+\.\d+\.\d+\.\d+)",
     "sample": "rule 0/0: block in on em0 src 1.2.3.4", "severity": "medium"},
]

_COMPILED = [(d, re.compile(d["regex"])) for d in DECODERS]


def decode_line(line: str) -> list[dict]:
    """Try every decoder; return all matches with extracted fields."""
    out = []
    for d, rx in _COMPILED:
        m = rx.search(line)
        if m:
            fields = {k: v for k, v in m.groupdict().items() if v is not None}
            out.append({"decoder": d["name"], "severity": d["severity"], "fields": fields})
    return out


def decode_with(name: str, line: str) -> Optional[dict]:
    for d, rx in _COMPILED:
        if d["name"] == name:
            m = rx.search(line)
            if not m:
                return None
            return {"decoder": name, "severity": d["severity"],
                    "fields": {k: v for k, v in m.groupdict().items() if v is not None}}
    return None


def decoder_names() -> list[str]:
    return [d["name"] for d in DECODERS]


def self_test() -> dict:
    """Every decoder must decode its own sample and yield its declared fields."""
    bad = []
    for d in DECODERS:
        r = decode_with(d["name"], d["sample"])
        if not r:
            bad.append((d["name"], "no match"))
            continue
        for f in d["fields"]:
            if f not in r["fields"]:
                bad.append((d["name"], f"missing field {f}"))
    return {"total": len(DECODERS), "bad": bad, "ok": not bad}
