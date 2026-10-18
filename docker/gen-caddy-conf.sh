#!/bin/bash
# =============================================================================
# wdtt-gen-caddy-conf — рендер /etc/wdtt-docker/Caddyfile из шаблона
# /etc/wdtt-docker/caddy.conf.tmpl. Плейсхолдеры @@VAR@@ (в Caddyfile тоже
# есть свои {}, поэтому без envsubst). Вызывается из entrypoint на каждом
# старте и из панели после смены домена. В конце — caddy validate.
# =============================================================================
set -Eeuo pipefail

TMPL="${WDTT_CADDY_TEMPLATE:-/etc/wdtt-docker/caddy.conf.tmpl}"
OUT="${WDTT_CADDY_OUTPUT:-/etc/wdtt-docker/Caddyfile}"

PANEL_HOST="${PANEL_HOST:?PANEL_HOST не задан}"
PANEL_PATH="${PANEL_PATH:?PANEL_PATH не задан}"
PANEL_HTTPS_PORT="${PANEL_HTTPS_PORT:?PANEL_HTTPS_PORT не задан}"
PANEL_LISTEN_PORT="${PANEL_LISTEN_PORT:-8787}"
# caddy: internal (IP / без выхода в интернет) | acme (домен, открытые :80/:443)
TLS_MODE="${TLS_MODE:-internal}"
PANEL_EMAIL="${PANEL_EMAIL:-}"

PANEL_PATH_TRIM="${PANEL_PATH%/}"

TLS_BLOCK=""
HSTS_BLOCK=""
EMAIL_BLOCK=""
case "$TLS_MODE" in
  internal)
    TLS_BLOCK="tls internal"
    ;;
  acme)
    # Порт 80 уже слушается (редирект на :80 в шаблоне), HSTS раз TLS публичный.
    HSTS_BLOCK='	header Strict-Transport-Security "max-age=31536000"'
    [ -n "$PANEL_EMAIL" ] && EMAIL_BLOCK="	email $PANEL_EMAIL"
    ;;
  *)
    echo "wdtt-gen-caddy-conf: TLS_MODE=$TLS_MODE не поддерживается caddy-режимом (internal|acme)" >&2
    exit 1
    ;;
esac

export TLS_BLOCK HSTS_BLOCK EMAIL_BLOCK PANEL_PATH_TRIM
sed -e "s|@@PANEL_HOST@@|$PANEL_HOST|g" \
	-e "s|@@PANEL_PATH@@|$PANEL_PATH|g" \
	-e "s|@@PANEL_PATH_TRIM@@|$PANEL_PATH_TRIM|g" \
	-e "s|@@PANEL_HTTPS_PORT@@|$PANEL_HTTPS_PORT|g" \
	-e "s|@@PANEL_LISTEN_PORT@@|$PANEL_LISTEN_PORT|g" \
	-e "s|@@TLS_BLOCK@@|$TLS_BLOCK|g" \
	-e "s|@@HSTS_BLOCK@@|\n$HSTS_BLOCK|g" \
	-e "s|@@EMAIL_BLOCK@@|\n$EMAIL_BLOCK|g" \
	"$TMPL" > "$OUT"

chmod 0600 "$OUT"

# Рендер-скрипт отвечает и за валидацию: reload из панели рассчитывает,
# что конфиг уже выверен (см. _reload_docker_nginx в wdtt_panel/admin.py).
caddy validate --config "$OUT" --adapter caddyfile
