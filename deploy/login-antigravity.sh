#!/bin/sh
# Login-only: no source checkout, production files, host sockets, or API key.
set -eu
if [ "$#" -ne 1 ]; then
  printf 'Usage: %s /absolute/state/auth/antigravity\n' "$0" >&2
  exit 2
fi
case "$1" in /*) ;; *) printf 'Auth directory must be absolute\n' >&2; exit 2 ;; esac
mkdir -p "$1"
chmod 700 "$1"
# Scoped read/test permissions; the review checkout is mounted read-only.
mkdir -p "$1/.gemini/antigravity-cli"
if [ ! -e "$1/.gemini/antigravity-cli/settings.json" ]; then
  umask 077
  printf '%s\n' '{"permissions":{"allow":["command(git)","command(regex:^python3 -m unittest discover -s tests -v$)"]}}' > "$1/.gemini/antigravity-cli/settings.json"
fi
exec docker run --rm -it --name kodi-antigravity-login \
  --user "$(id -u):$(id -g)" --cap-drop ALL --security-opt no-new-privileges \
  --cpus 1.5 --memory 3g --memory-swap 3g --pids-limit 192 \
  --read-only --tmpfs /tmp:rw,nosuid,nodev,size=512m \
  --env SSH_CONNECTION=kodi-remote-login \
  --mount "type=bind,src=$1,dst=/home/worker" \
  omiro-worker:agy-1.3.3 agy
