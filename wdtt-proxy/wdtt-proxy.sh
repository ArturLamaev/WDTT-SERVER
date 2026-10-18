#!/usr/bin/env bash
#
# wdtt-proxy — заворачивает весь трафик, приходящий с интерфейса $CAPTURE_IF,
# в SOCKS5-прокси через sing-box (tun + policy routing).
#
# Использование:
#   ./wdtt-proxy.sh start      запустить прокси и включить маршрутизацию
#   ./wdtt-proxy.sh stop       остановить и убрать маршрутизацию
#   ./wdtt-proxy.sh restart    перезапустить (после правки config.env)
#   ./wdtt-proxy.sh status     показать состояние
#   ./wdtt-proxy.sh rules-up   только поднять правила маршрутизации
#   ./wdtt-proxy.sh rules-down только убрать правила маршрутизации
#   ./wdtt-proxy.sh gen-config напечатать сгенерированный конфиг sing-box
#   ./wdtt-proxy.sh run        запустить sing-box на переднем плане (для systemd)
#   ./wdtt-proxy.sh logs       показать лог
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${WDTT_PROXY_CONFIG:-$SCRIPT_DIR/config.env}"

[[ -f "$CONFIG_FILE" ]] || { echo "Нет файла конфигурации: $CONFIG_FILE" >&2; exit 1; }
# shellcheck disable=SC1090
set -a; source "$CONFIG_FILE"; set +a

# ---- значения по умолчанию на случай отсутствия в конфиге ----
: "${PROXY_SCHEME:=socks5}"
: "${CAPTURE_IF:=wdtt0}"
: "${SOURCE_CIDR:=10.66.0.0/16}"
: "${MARK:=0x100}"
MARK_VALUE="${MARK%%/*}"
: "${TUN_NAME:=sbtun0}"
: "${TUN_ADDRESS:=10.200.0.1/30}"
: "${TUN_MTU:=1280}"
: "${STACK:=gvisor}"
: "${ROUTE_TABLE:=100}"
: "${RULE_PRIORITY_EXCLUDE:=90}"
: "${RULE_PRIORITY_PROXY:=100}"
: "${LOG_LEVEL:=warn}"
: "${SINGBOX_BIN:=/usr/local/bin/sing-box}"
: "${RUNTIME_DIR:=/run/wdtt-proxy}"
: "${EXCLUDE_CIDRS:=}"

GEN_CONFIG="$RUNTIME_DIR/sing-box.json"
PIDFILE="$RUNTIME_DIR/sing-box.pid"
LOGFILE="$RUNTIME_DIR/sing-box.log"

log()  { printf '%s %s\n' "$(date '+%F %T')" "$*"; }
die()  { printf 'Ошибка: %s\n' "$*" >&2; exit 1; }

need_root() { [[ $EUID -eq 0 ]] || die "нужны права root (запустите через sudo)."; }

# --------------------------------------------------------------------------
# Генерация конфига sing-box из config.env
# --------------------------------------------------------------------------
gen_config() {
  mkdir -p "$RUNTIME_DIR"
  chmod 700 "$RUNTIME_DIR" 2>/dev/null || true

  local auth=""
  if [[ -n "${PROXY_USER:-}" ]]; then
    auth=", \"username\": \"${PROXY_USER}\", \"password\": \"${PROXY_PASS:-}\""
  fi

  cat > "$GEN_CONFIG" <<EOF
{
  "log": { "level": "${LOG_LEVEL}", "timestamp": true },
  "dns": { "servers": [ { "tag": "dns-local", "type": "local" } ] },
  "inbounds": [
    {
      "type": "tun",
      "tag": "tun-in",
      "interface_name": "${TUN_NAME}",
      "address": ["${TUN_ADDRESS}"],
      "mtu": ${TUN_MTU},
      "auto_route": false,
      "strict_route": false,
      "stack": "${STACK}"
    }
  ],
  "outbounds": [
    {
      "type": "socks",
      "tag": "proxy",
      "server": "${PROXY_HOST}",
      "server_port": ${PROXY_PORT},
      "version": "5"${auth}
    },
    { "type": "direct", "tag": "direct" }
  ],
  "route": { "final": "proxy" }
}
EOF

  "$SINGBOX_BIN" check -c "$GEN_CONFIG" >/dev/null || die "сгенерированный конфиг sing-box некорректен"
  chmod 600 "$GEN_CONFIG" 2>/dev/null || true
}

cmd_run() { gen_config; exec "$SINGBOX_BIN" run -c "$GEN_CONFIG"; }

# --------------------------------------------------------------------------
# Маршрутизация: трафик с интерфейса CAPTURE_IF метится fwmark = MARK и
# через таблицу ROUTE_TABLE уходит в tun sing-box. Исключения → напрямую.
# --------------------------------------------------------------------------
rules_up() {
  log "включаю маршрутизацию: ${CAPTURE_IF} -> ${TUN_NAME} (mark ${MARK_VALUE}, таблица ${ROUTE_TABLE})"

  # ждём, пока sing-box создаст tun-интерфейс
  local i
  for i in $(seq 1 100); do
    ip link show "$TUN_NAME" >/dev/null 2>&1 && break
    sleep 0.1
  done
  ip link show "$TUN_NAME" >/dev/null 2>&1 || die "интерфейс $TUN_NAME не найден (sing-box не запущен?)"

  # поднимаем интерфейс (sing-box обычно делает это сам)
  ip link set dev "$TUN_NAME" up 2>/dev/null || true

  # forwarding
  sysctl -w net.ipv4.ip_forward=1 >/dev/null

  # таблица с дефолтом в tun (ждём готовности устройства к маршрутизации)
  local err=""
  for i in $(seq 1 100); do
    if err=$(ip route replace default dev "$TUN_NAME" table "$ROUTE_TABLE" 2>&1); then
      err=""; break
    fi
    sleep 0.2
  done
  [[ -z "$err" ]] || die "не удалось задать маршрут по умолчанию в $TUN_NAME: $err"

  # метим весь трафик, приходящий с CAPTURE_IF (точно по интерфейсу,
  # не задевая другие туннели с пересекающимися подсетями)
  iptables -t mangle -N WDTT_PROXY 2>/dev/null || true
  iptables -t mangle -F WDTT_PROXY
  iptables -t mangle -A WDTT_PROXY -j MARK --set-mark "$MARK_VALUE"
  if ! iptables -t mangle -C PREROUTING -i "$CAPTURE_IF" -m comment --comment WDTT_PROXY -j WDTT_PROXY 2>/dev/null; then
    iptables -t mangle -A PREROUTING -i "$CAPTURE_IF" -m comment --comment WDTT_PROXY -j WDTT_PROXY
  fi

  # исключения для помеченного трафика идут по обычной таблице (напрямую)
  local cidr
  for cidr in $EXCLUDE_CIDRS; do
    ip rule del fwmark "$MARK_VALUE" to "$cidr" lookup main 2>/dev/null || true
    ip rule add priority "$RULE_PRIORITY_EXCLUDE" fwmark "$MARK_VALUE" to "$cidr" lookup main
  done

  # основное правило: помеченный трафик -> в прокси
  ip rule del fwmark "$MARK_VALUE" lookup "$ROUTE_TABLE" 2>/dev/null || true
  ip rule add priority "$RULE_PRIORITY_PROXY" fwmark "$MARK_VALUE" lookup "$ROUTE_TABLE"

  log "маршрутизация включена"
}

rules_down() {
  log "выключаю маршрутизацию"
  local cidr
  iptables -t mangle -D PREROUTING -i "$CAPTURE_IF" -m comment --comment WDTT_PROXY -j WDTT_PROXY 2>/dev/null || true
  iptables -t mangle -F WDTT_PROXY 2>/dev/null || true
  iptables -t mangle -X WDTT_PROXY 2>/dev/null || true
  for cidr in $EXCLUDE_CIDRS; do
    ip rule del fwmark "$MARK_VALUE" to "$cidr" lookup main 2>/dev/null || true
  done
  ip rule del fwmark "$MARK_VALUE" lookup "$ROUTE_TABLE" 2>/dev/null || true
  ip route flush table "$ROUTE_TABLE" 2>/dev/null || true
  log "маршрутизация выключена"
}

# --------------------------------------------------------------------------
# Управление процессом sing-box
# --------------------------------------------------------------------------
is_running() {
  [[ -f "$PIDFILE" ]] && kill -0 "$(cat "$PIDFILE" 2>/dev/null)" 2>/dev/null
}

# процесс sing-box, запущенный systemd (или вручную без pidfile)
sb_pid() {
  if is_running; then cat "$PIDFILE"; return; fi
  pgrep -f "$SINGBOX_BIN run -c $GEN_CONFIG" 2>/dev/null | head -1 || true
}

systemd_active() {
  command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet wdtt-proxy 2>/dev/null
}

require_no_systemd() {
  if systemd_active; then
    die "сервис уже управляется через systemd. Используйте: systemctl {start|stop|restart} wdtt-proxy"
  fi
}

wait_tun() {
  local i
  for i in $(seq 1 50); do
    ip link show "$TUN_NAME" >/dev/null 2>&1 && return 0
    sleep 0.1
  done
  return 1
}

cmd_start() {
  need_root
  require_no_systemd
  if is_running; then
    log "sing-box уже запущен (pid $(cat "$PIDFILE"))"; rules_up; return 0
  fi
  gen_config
  mkdir -p "$RUNTIME_DIR"
  log "запускаю sing-box"
  nohup "$SINGBOX_BIN" run -c "$GEN_CONFIG" >>"$LOGFILE" 2>&1 &
  echo $! > "$PIDFILE"
  sleep 0.3
  if ! is_running; then
    echo "----- последние строки лога -----"; tail -n 20 "$LOGFILE" || true
    die "sing-box не запустился"
  fi
  wait_tun || { tail -n 20 "$LOGFILE" || true; die "интерфейс $TUN_NAME не появился"; }
  rules_up
  log "готово: трафик с $CAPTURE_IF идёт через ${PROXY_SCHEME}://${PROXY_HOST}:${PROXY_PORT}"
}

cmd_stop() {
  need_root
  require_no_systemd
  rules_down
  if is_running; then
    local pid; pid=$(cat "$PIDFILE")
    log "останавливаю sing-box (pid $pid)"
    kill "$pid" 2>/dev/null || true
    local i
    for i in $(seq 1 30); do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
    kill -9 "$pid" 2>/dev/null || true
  else
    log "sing-box не запущен"
  fi
  rm -f "$PIDFILE"
  log "остановлено"
}

cmd_restart() { cmd_stop; sleep 0.3; cmd_start; }

cmd_status() {
  echo "Конфиг:            $CONFIG_FILE"
  echo "SOCKS5:            ${PROXY_SCHEME}://${PROXY_HOST}:${PROXY_PORT} (user: ${PROXY_USER:-<нет>})"
  echo "Захват интерфейса: $CAPTURE_IF  (${SOURCE_CIDR})"
  echo "tun sing-box:      $TUN_NAME ($TUN_ADDRESS)"
  local pid; pid="$(sb_pid)"
  if systemd_active; then
    echo "sing-box:          ЗАПУЩЕН (pid ${pid:-?}, через systemd)"
  elif [[ -n "$pid" ]]; then
    echo "sing-box:          ЗАПУЩЕН (pid $pid)"
  else
    echo "sing-box:          остановлен"
  fi
  echo
  echo "--- ip rule ---"
  ip rule show
  echo "--- таблица $ROUTE_TABLE ---"
  ip route show table "$ROUTE_TABLE" 2>/dev/null || true
  echo "--- интерфейс $TUN_NAME ---"
  ip -br addr show "$TUN_NAME" 2>/dev/null || echo "нет интерфейса"
}

cmd_logs() { [[ -f "$LOGFILE" ]] && tail -n "${1:-50}" "$LOGFILE" || echo "лог пуст"; }

case "${1:-}" in
  start)       cmd_start ;;
  stop)        cmd_stop ;;
  restart)     cmd_restart ;;
  status)      cmd_status ;;
  rules-up)    need_root; rules_up ;;
  rules-down)  need_root; rules_down ;;
  gen-config)  gen_config; cat "$GEN_CONFIG" ;;
  run)         cmd_run ;;
  logs)        shift || true; cmd_logs "${1:-50}" ;;
  *)
    sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
    exit 1 ;;
esac
