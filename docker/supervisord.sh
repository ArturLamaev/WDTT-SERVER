#!/bin/bash
# =============================================================================
# WDTT-SERVER (docker) — супервизор (PID 1). Заменяет systemd-юниты и таймеры
# оригинальной установки:
#   wdtt.service / wdtt-panel.service / nginx        → циклы перезапуска;
#   wdtt-auto-restart.timer (каждые 6ч)               → AUTO_RESTART_HOURS;
#   wdtt-panel-backup.timer                           → расписание из панели;
#   wdtt-panel-autoclean.timer                        → каждые 6ч при enabled;
#   wdtt-panel-geofiles-update.timer (каждые 6ч)      → раз в сутки;
#   wdtt-panel-cert-renew.timer                       → certbot renew раз в неделю.
#
# Панель работает с WDTT_SKIP_SYSTEMD=1 (штатный режим панели без systemd):
# проверки юнитов возвращают «test», тяжёлые действия — no-op, а данные
# (юзеры, маршруты Xray, каскад, шлюз) применяются напрямую.
# =============================================================================
set -u

log() { printf '[wdtt-supervisord] %s\n' "$*"; }

# Окружение панели (те же пути, что в install.sh; конфиг — через volume)
export PYTHONPATH=/opt/wdtt-panel
export WDTT_PANEL_CONFIG=/etc/wdtt-panel/config.json
export WDTT_PANEL_STATE=/var/lib/wdtt-panel/panel.db
export WDTT_PANEL_SEED_HASHES=/etc/wdtt-panel/vk-hash.txt
export WDTT_PANEL_ADMIN=/usr/local/bin/wdtt-panel-admin
export WDTT_SKIP_SYSTEMD=1
export XRAY_LOCATION_ASSET="${WDTT_XRAY_ASSETS:-/var/lib/wdtt-panel-private/xray-assets}"
: "${AUTO_RESTART_HOURS:=6}"

SHUTDOWN=0
trap 'SHUTDOWN=1; kill $(jobs -p) 2>/dev/null || true' TERM INT

# JSON-действие панели через админ-обёртку (как /usr/local/sbin/wdtt-panel-*)
wdtt_admin() { printf '%s' "$1" | /usr/local/bin/wdtt-panel-admin 2>&1 | head -c 500; echo; }

# ── wdtt-server: args пересобираются перед каждым стартом, поэтому смена
#    настроек во вкладке «WDTT» применяется при следующем рестарте ────────────
run_wdtt() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    if ! mapfile -t WDTT_ARGS < <(/usr/local/sbin/wdtt-exec-args --dump) || [ "${#WDTT_ARGS[@]}" -eq 0 ]; then
      log "wdtt: не собрались аргументы запуска, повтор через 10с"
      sleep 10
      continue
    fi
    log "wdtt: старт (${WDTT_ARGS[*]})"
    # Чистим stale TUN перед стартом (как ExecStartPre в wdtt.service)
    ip link show wdtt0 >/dev/null 2>&1 && ip link del wdtt0 2>/dev/null || true
    "${WDTT_ARGS[@]}" &
    WDTT_PID=$!
    echo "$WDTT_PID" > /run/wdtt/wdtt.pid
    wait "$WDTT_PID"
    code=$?
    [ "$SHUTDOWN" -eq 1 ] && break
    log "wdtt: завершился (код $code), перезапуск через 5с"
    sleep 5
  done
}

# ── панель: порт/хост берёт из config.json ────────────────────────────────────
run_panel() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    log "panel: старт"
    python3 -m wdtt_panel.app &
    PANEL_PID=$!
    echo "$PANEL_PID" > /run/wdtt/panel.pid
    wait "$PANEL_PID"
    code=$?
    [ "$SHUTDOWN" -eq 1 ] && break
    log "panel: завершилась (код $code), перезапуск через 3с"
    sleep 3
  done
}

# ── nginx на переднем плане ───────────────────────────────────────────────────
run_nginx() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    log "nginx: старт"
    nginx -g 'daemon off;' &
    NGINX_PID=$!
    echo "$NGINX_PID" > /run/wdtt/nginx.pid
    wait "$NGINX_PID"
    code=$?
    [ "$SHUTDOWN" -eq 1 ] && break
    log "nginx: завершился (код $code), перезапуск через 3с"
    sleep 3
  done
}

# ── авто-рестарт ядра и панели (аналог wdtt-auto-restart.timer) ──────────────
run_auto_restart() {
  [ "${AUTO_RESTART_HOURS:-6}" -gt 0 ] 2>/dev/null || { log "auto-restart выключен"; return 0; }
  while [ "$SHUTDOWN" -eq 0 ]; do
    sleep $(( AUTO_RESTART_HOURS * 3600 ))
    [ "$SHUTDOWN" -eq 1 ] && break
    log "auto-restart: перезапускаю wdtt и панель"
    # Мягко гасим — циклы run_* поднимут процессы заново
    [ -f /run/wdtt/wdtt.pid ] && kill "$(cat /run/wdtt/wdtt.pid)" 2>/dev/null || true
    [ -f /run/wdtt/panel.pid ] && kill "$(cat /run/wdtt/panel.pid)" 2>/dev/null || true
  done
}

# ── автобэкапы по расписанию панели (daily/weekly, HH:MM) ────────────────────
run_backup_scheduler() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    sleep 120
    [ "$SHUTDOWN" -eq 1 ] && break
    due="$(python3 - <<'PY'
import json
from datetime import date, datetime
from pathlib import Path
try:
    s = json.loads(Path("/var/lib/wdtt-panel-private/backup-schedule.json").read_text(encoding="utf-8"))
except (OSError, ValueError):
    raise SystemExit("")
if not isinstance(s, dict) or s.get("frequency") not in {"daily", "weekly"}:
    raise SystemExit("")
try:
    hour, minute = (s.get("time") or "03:30").split(":")
    now = datetime.now().replace(hour=int(hour), minute=int(minute), second=0, microsecond=0)
except ValueError:
    raise SystemExit("")
if datetime.now() < now:
    raise SystemExit("")
state = Path("/run/wdtt/backup-state.json")
try:
    last = json.loads(state.read_text(encoding="utf-8")).get("last", "")
except (OSError, ValueError):
    last = ""
if s["frequency"] == "daily":
    tag = date.today().isoformat()
else:
    if date.today().weekday() != 6:  # weekly = воскресенье, как OnCalendar в install.sh
        raise SystemExit("")
    tag = f"{date.today().isocalendar().year}-W{date.today().isocalendar().week:02d}"
if last == tag:
    raise SystemExit("")
state.write_text(json.dumps({"last": tag}) + "\n", encoding="utf-8")
print(s.get("type") or "full")
PY
)" || true
    if [ -n "${due:-}" ]; then
      log "backup: плановый ($due)"
      wdtt_admin "{\"action\":\"backups.create\",\"payload\":{\"type\":\"$due\",\"scheduled\":true}}" || log "backup: ошибка"
    fi
  done
}

# ── автоочистка при enabled (каждые 6ч, порог диска — в самой панели) ────────
run_autoclean() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    sleep 21600
    [ "$SHUTDOWN" -eq 1 ] && break
    enabled="$(python3 -c 'import json; print(json.load(open("/var/lib/wdtt-panel-private/auto-clean.json")).get("enabled", True))' 2>/dev/null || echo True)"
    if [ "$enabled" = "True" ]; then
      log "autoclean: плановый"
      wdtt_admin '{"action":"autoclean.run","payload":{}}' || log "autoclean: ошибка"
    fi
  done
}

# ── обновление GeoFiles Xray (раз в сутки) ────────────────────────────────────
run_geofiles() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    sleep 86400
    [ "$SHUTDOWN" -eq 1 ] && break
    log "geofiles: плановое обновление"
    wdtt_admin '{"action":"xray.geofiles.refresh_auto","payload":{}}' || log "geofiles: ошибка"
  done
}

# ── продление Let's Encrypt (раз в неделю; self-signed пропускается) ────────
run_cert_renew() {
  while [ "$SHUTDOWN" -eq 0 ]; do
    sleep 86400
    [ "$SHUTDOWN" -eq 1 ] && break
    mode="$(python3 -c 'import json; print(json.load(open("/etc/wdtt-panel/config.json")).get("tls_mode", ""))' 2>/dev/null || true)"
    [ "$mode" = "letsencrypt" ] || continue
    stamp="/run/wdtt/certbot-renew.stamp"
    if [ ! -f "$stamp" ] || [ "$(( $(date +%s) - $(cat "$stamp") ))" -gt 518400 ]; then
      log "certbot: renew"
      if /opt/wdtt-panel/certbot/bin/certbot renew --quiet; then
        date +%s > "$stamp"
        nginx -s reload 2>/dev/null || true
      else
        log "certbot: renew не удался"
      fi
    fi
  done
}

mkdir -p /run/wdtt
run_wdtt &
run_panel &
run_nginx &
run_auto_restart &
run_backup_scheduler &
run_autoclean &
run_geofiles &
run_cert_renew &

log "супервизор запущен (PID $$)"
wait
