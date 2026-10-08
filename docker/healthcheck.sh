#!/bin/bash
# =============================================================================
# wdtt-healthcheck — проверка стека для HEALTHCHECK образа и compose.
# OK, только если живы ВСЕ ТРИ процесса (ядро, панель, nginx) И панель отвечает
# по HTTPS на секретном пути.
# =============================================================================
set -u

pidof wdtt-server >/dev/null 2>&1 || exit 1
pidof nginx >/dev/null 2>&1 || exit 1

PANEL_INFO="$(python3 - /etc/wdtt-panel/config.json <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
print(f"PORT={int(d.get('https_port', 9999))}")
print(f"PPATH={d.get('base_path', '/')}")
PY
)" || exit 1
eval "$PANEL_INFO"

curl --noproxy '*' -kfsS --connect-timeout 3 --max-time 8 \
  "https://127.0.0.1:${PORT}${PPATH}" -o /dev/null
