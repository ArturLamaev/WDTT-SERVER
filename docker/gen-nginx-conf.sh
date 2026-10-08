#!/bin/bash
# =============================================================================
# wdtt-gen-nginx-conf — рендер /etc/nginx/conf.d/wdtt-panel.conf из шаблона
# /etc/wdtt-docker/nginx.conf.tmpl. Плейсхолдеры @@VAR@@ (не envsubst: в конфиге
# nginx полно $переменных, которые envsubst бы съел).
# Вызывается из entrypoint при каждом старте и после смены домена/сертификата.
# =============================================================================
set -Eeuo pipefail

TMPL="${WDTT_NGINX_TEMPLATE:-/etc/wdtt-docker/nginx.conf.tmpl}"
OUT="${WDTT_NGINX_OUTPUT:-/etc/nginx/conf.d/wdtt-panel.conf}"

PANEL_HOST="${PANEL_HOST:?PANEL_HOST не задан}"
PANEL_PATH="${PANEL_PATH:?PANEL_PATH не задан}"
PANEL_HTTPS_PORT="${PANEL_HTTPS_PORT:?PANEL_HTTPS_PORT не задан}"
PANEL_LISTEN_PORT="${PANEL_LISTEN_PORT:-8787}"
CERTIFICATE_PATH="${CERTIFICATE_PATH:?CERTIFICATE_PATH не задан}"
PRIVATE_KEY_PATH="${PRIVATE_KEY_PATH:?PRIVATE_KEY_PATH не задан}"
TLS_MODE="${TLS_MODE:-self-signed}"

PANEL_PATH_TRIM="${PANEL_PATH%/}"
if [ "$TLS_MODE" = "letsencrypt" ]; then
  HSTS_HEADER='    add_header Strict-Transport-Security "max-age=31536000" always;'
else
  HSTS_HEADER=""
fi
export PANEL_PATH PANEL_PATH_TRIM PANEL_HTTPS_PORT PANEL_LISTEN_PORT
export CERTIFICATE_PATH PRIVATE_KEY_PATH TLS_MODE HSTS_HEADER

python3 - "$TMPL" "$OUT" <<'PY'
import sys
from pathlib import Path
import os

template_path, output_path = sys.argv[1], sys.argv[2]
text = Path(template_path).read_text(encoding="utf-8")
values = {
    "PANEL_HOST": os.environ["PANEL_HOST"],
    "PANEL_PATH": os.environ["PANEL_PATH"],
    "PANEL_PATH_TRIM": os.environ["PANEL_PATH_TRIM"],
    "PANEL_HTTPS_PORT": os.environ["PANEL_HTTPS_PORT"],
    "PANEL_LISTEN_PORT": os.environ.get("PANEL_LISTEN_PORT", "8787"),
    "CERTIFICATE_PATH": os.environ["CERTIFICATE_PATH"],
    "PRIVATE_KEY_PATH": os.environ["PRIVATE_KEY_PATH"],
    "HSTS_HEADER": os.environ.get("HSTS_HEADER", ""),
}
for key, value in values.items():
    text = text.replace("@@" + key + "@@", value)
leftover = [line.strip() for line in text.splitlines() if "@@" in line]
if leftover:
    raise SystemExit(f"В шаблоне остались незаменённые плейсхолдеры: {leftover[:3]}")
Path(output_path).write_text(text, encoding="utf-8")
print(f"nginx conf записан: {output_path}")
PY

nginx -t
