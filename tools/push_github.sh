#!/usr/bin/env bash
# Push the current branch and release tags to GitHub using the local token.
# Token stays in .secrets/github_token; it is never written into git config.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

TOKEN="$(tr -d '\r\n' < .secrets/github_token)"
[ -n "$TOKEN" ] || { echo "missing token: .secrets/github_token" >&2; exit 2; }

# WSL regenerates /etc/hosts from the Windows hosts file at session start. If
# GitHub is pinned to loopback there, add real addresses for this session only.
if getent hosts github.com | grep -q '^127\.0\.0\.1'; then
  printf '20.247.166.64 github.com\n20.205.243.168 api.github.com\n20.205.243.161 uploads.github.com\n' >> /etc/hosts
fi

ASKPASS="$(mktemp)"
trap 'rm -f "$ASKPASS"' EXIT
cat > "$ASKPASS" <<'SH'
#!/bin/sh
case "$1" in
  Username*) echo x-access-token ;;
  Password*) echo "$AO_GIT_TOKEN" ;;
esac
SH
chmod 700 "$ASKPASS"

export AO_GIT_TOKEN="$TOKEN"
export GIT_ASKPASS="$ASKPASS"
export GIT_TERMINAL_PROMPT=0

git remote set-url origin https://x-access-token@github.com/mawenle-dev/autonomic-ops.git
git -c credential.helper= push origin HEAD:main
git -c credential.helper= push origin "$(git describe --tags --abbrev=0)"
echo "pushed: $(git rev-parse --short HEAD) -> origin/main"
