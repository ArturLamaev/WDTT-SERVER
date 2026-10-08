#!/bin/bash
# =============================================================================
# WDTT-SERVER (docker) — entrypoint. Готовит всё для первого старта и каждого
# рестарта контейнера, затем передаёт управление супервизору.
#
# Порядок (повторяет panel/install.sh и deploy.sh ядра):
#   1. валидация переменных окружения;
#   2. секреты ядра (/etc/wdtt) и admin TLS ядра;
#   3. Telegram-настройки ядра (если заданы);
#   4. config.json панели (секреты из volume не перезаписываются);
#   5. сид VK-хешей;
#   6. iptables/NAT (best-effort) + sysctl;
#   7. TLS панели (self-signed или Let's Encrypt через venv-certbot);
#   8. рендер nginx-конфига;
#   9. рантаймы xray/wgcf (пропуск при WDTT_FETCH_RUNTIME=0);
#  10. печать итоговых доступов в stdout (видно в docker compose logs).
# =============================================================================
set -Eeuo pipefail

# CMD образа сохраняем сразу: ниже циклы используют собственные переменные,
# а финал делает exec сохранённой команды.
CMD_ARGS=("$@")

log() { printf '[wdtt-entrypoint] %s\n' "$*"; }
die() { log "ERROR: $*"; exit 1; }

random_token() { python3 -c "import secrets; print(secrets.token_urlsafe($1))"; }
random_password() { python3 -c 'import secrets,string; a=string.ascii_letters+string.digits+"._~-"; print("".join(secrets.choice(a) for _ in range(24)))'; }

# ── 1. переменные ────────────────────────────────────────────────────────────
: "${PANEL_USER:=admin}"
: "${PANEL_HTTPS_PORT:=9999}"
: "${PANEL_LISTEN_PORT:=8787}"
: "${WDTT_DTLS_PORT:=56000}"
: "${WDTT_WG_PORT:=56001}"
: "${WDTT_ADMIN_PORT:=56002}"
: "${WDTT_DNS:=1.1.1.1,1.0.0.1}"
: "${TLS_MODE:=self-signed}"
: "${WDTT_FETCH_RUNTIME:=1}"
export PANEL_USER PANEL_HTTPS_PORT PANEL_LISTEN_PORT TLS_MODE
export WDTT_DTLS_PORT WDTT_WG_PORT WDTT_ADMIN_PORT WDTT_DNS WDTT_FETCH_RUNTIME

[[ "$PANEL_USER" =~ ^[A-Za-z0-9_.-]{3,32}$ ]] || die "Некорректный PANEL_USER"
[[ "$PANEL_HTTPS_PORT" =~ ^[0-9]+$ ]] && [ "$PANEL_HTTPS_PORT" -ge 1 ] && [ "$PANEL_HTTPS_PORT" -le 65535 ] \
  || die "Некорректный PANEL_HTTPS_PORT"
[ "$PANEL_HTTPS_PORT" -ne 80 ] || die "PANEL_HTTPS_PORT=80 зарезервирован для ACME"
[[ "$PANEL_LISTEN_PORT" =~ ^[0-9]+$ ]] && [ "$PANEL_LISTEN_PORT" -ge 1024 ] && [ "$PANEL_LISTEN_PORT" -le 65535 ] \
  || die "Некорректный PANEL_LISTEN_PORT"
[ "$PANEL_HTTPS_PORT" != "$PANEL_LISTEN_PORT" ] || die "Внешний и внутренний порты панели должны отличаться"
for pair in "WDTT_DTLS_PORT $WDTT_DTLS_PORT" "WDTT_WG_PORT $WDTT_WG_PORT" "WDTT_ADMIN_PORT $WDTT_ADMIN_PORT"; do
  pname="${pair%% *}"; pport="${pair##* }"
  [[ "$pport" =~ ^[0-9]+$ ]] && [ "$pport" -ge 1 ] && [ "$pport" -le 65535 ] || die "Некорректный $pname"
done
case "$TLS_MODE" in self-signed|letsencrypt) ;; *) die "TLS_MODE должен быть self-signed или letsencrypt" ;; esac

# PANEL_HOST: как в discover_host(), но без интерактива.
if [ -z "${PANEL_HOST:-}" ]; then
  PANEL_HOST="$(curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
fi
[ -n "${PANEL_HOST:-}" ] || die "Не задан PANEL_HOST и не определился публичный IP — укажите домен или IPv4 в .env"
[[ "$PANEL_HOST" != *:* ]] || die "IPv6 для PANEL_HOST не поддерживается; используйте домен или IPv4"
export PANEL_HOST

# ── 2. секреты ядра и admin TLS ───────────────────────────────────────────────
install -d -m 0700 /etc/wdtt
if [ -s /etc/wdtt/main.password ]; then
  log "Главный пароль ядра уже есть в volume — сохраняю"
else
  if [ -z "${WDTT_MAIN_PASSWORD:-}" ]; then
    WDTT_MAIN_PASSWORD="$(random_password)"
    log "WDTT_MAIN_PASSWORD не задан — сгенерирован (см. итог ниже)"
  fi
  [[ "$WDTT_MAIN_PASSWORD" =~ ^[A-Za-z0-9._~-]{12,64}$ ]] \
    || die "WDTT_MAIN_PASSWORD: 12-64 безопасных символа без пробелов и двоеточия"
  umask 077
  printf '%s' "$WDTT_MAIN_PASSWORD" > /etc/wdtt/main.password
  chmod 0600 /etc/wdtt/main.password
fi
unset WDTT_MAIN_PASSWORD

if [ ! -s /etc/wdtt/admin.token ]; then
  umask 077
  random_token 32 > /etc/wdtt/admin.token
  chmod 0600 /etc/wdtt/admin.token
fi
if [ ! -s /etc/wdtt/admin.crt ] || [ ! -s /etc/wdtt/admin.key ]; then
  openssl req -x509 -newkey rsa:2048 -sha256 -nodes -days 3650 \
    -keyout /etc/wdtt/admin.key -out /etc/wdtt/admin.crt -subj "/CN=qwdtt-admin" \
    || die "Не удалось создать admin TLS ядра"
  chmod 0600 /etc/wdtt/admin.key /etc/wdtt/admin.crt
fi

# ── 3. Telegram-бот ядра ─────────────────────────────────────────────────────
if [ -n "${WDTT_TELEGRAM_BOT_TOKEN:-}" ] || [ -n "${WDTT_TELEGRAM_ADMIN_ID:-}" ]; then
  [[ "${WDTT_TELEGRAM_ADMIN_ID:-}" =~ ^-?[0-9]{1,20}$ ]] || die "WDTT_TELEGRAM_ADMIN_ID должен быть числовым chat_id"
  [[ "${WDTT_TELEGRAM_BOT_TOKEN:-}" =~ ^[0-9]{5,20}:[A-Za-z0-9_-]{20,200}$ ]] || die "WDTT_TELEGRAM_BOT_TOKEN должен быть в формате 123456:ABC..."
  export WDTT_TELEGRAM_BOT_TOKEN WDTT_TELEGRAM_ADMIN_ID
  python3 - <<'PY'
import json, os
from pathlib import Path
db = Path("/etc/wdtt/passwords.json")
data = json.loads(db.read_text(encoding="utf-8")) if db.exists() else {}
if not isinstance(data, dict):
    raise SystemExit("passwords.json повреждён")
data.setdefault("passwords", {})
data.setdefault("devices", {})
data["admin_id"] = os.environ["WDTT_TELEGRAM_ADMIN_ID"]
data["bot_token"] = os.environ["WDTT_TELEGRAM_BOT_TOKEN"]
tmp = db.with_suffix(".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.chmod(0o600)
tmp.replace(db)
Path("/etc/wdtt/bot.token").write_text(os.environ["WDTT_TELEGRAM_BOT_TOKEN"] + "\n", encoding="utf-8")
Path("/etc/wdtt/bot.token").chmod(0o600)
print("Telegram-бот ядра настроен")
PY
fi

# ── 4. config.json панели ────────────────────────────────────────────────────
PANEL_VERSION="$(sed -n 's/^__version__ = "\(.*\)"$/\1/p' /opt/wdtt-panel/wdtt_panel/__init__.py | head -n 1)"
[ -n "$PANEL_VERSION" ] || die "Не удалось прочитать версию панели"
export PANEL_VERSION

if [ -s /etc/wdtt-panel/config.json ]; then
  log "config.json уже есть в volume — переношу на версию $PANEL_VERSION с сохранением секретов"
  python3 - "/etc/wdtt-panel/config.json" "$PANEL_VERSION" <<'PY'
import json, os, sys
path, version = sys.argv[1], sys.argv[2]
data = json.load(open(path, encoding="utf-8"))
if not isinstance(data, dict) or not data.get("password_hash") or not data.get("session_secret"):
    raise SystemExit("config.json неполон")
for key, value in {"username": "admin", "base_path": "/", "public_host": "",
                   "https_port": 9999, "listen_host": "127.0.0.1", "listen_port": 8787,
                   "certificate_path": "", "tls_mode": "self-signed",
                   "certificate_email": ""}.items():
    data.setdefault(key, value)
data["version"] = version
tmp = path + ".tmp"
json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(tmp, "a", encoding="utf-8").write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
print("config.json перенесён")
PY
else
  [ -n "${PANEL_PASSWORD:-}" ] || { PANEL_PASSWORD="$(random_password)"; log "PANEL_PASSWORD не задан — сгенерирован (см. итог ниже)"; }
  [ "${#PANEL_PASSWORD}" -ge 12 ] || die "PANEL_PASSWORD должен содержать не менее 12 символов"
  [ -n "${PANEL_PATH:-}" ] || { PANEL_PATH="/$(random_token 18)/"; log "PANEL_PATH не задан — сгенерирован (см. итог ниже)"; }
  PANEL_PATH="/${PANEL_PATH#/}"
  PANEL_PATH="${PANEL_PATH%/}/"
  [[ "$PANEL_PATH" =~ ^/[A-Za-z0-9_-]{16,80}/$ ]] || die "PANEL_PATH должен быть случайным путём из 16-80 символов"
  SESSION_SECRET="$(random_token 48)"
  export PANEL_PASSWORD PANEL_PATH SESSION_SECRET
  CERTIFICATE_PATH="" TLS_MODE_PENDING="$TLS_MODE" PANEL_EMAIL="${PANEL_EMAIL:-}" \
  PYTHONPATH=/opt/wdtt-panel python3 - <<'PY'
import json, os, sys
sys.path.insert(0, "/opt/wdtt-panel")
from wdtt_panel.security import hash_password
path = "/etc/wdtt-panel/config.json"
data = {
    "version": os.environ["PANEL_VERSION"],
    "username": os.environ["PANEL_USER"],
    "password_hash": hash_password(os.environ["PANEL_PASSWORD"]),
    "session_secret": os.environ["SESSION_SECRET"],
    "base_path": os.environ["PANEL_PATH"],
    "public_host": os.environ["PANEL_HOST"],
    "https_port": int(os.environ["PANEL_HTTPS_PORT"]),
    "listen_host": "127.0.0.1",
    "listen_port": int(os.environ["PANEL_LISTEN_PORT"]),
    "certificate_path": "",
    "tls_mode": "self-signed",
    "certificate_email": os.environ.get("PANEL_EMAIL", ""),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
print("config.json создан")
PY
  unset SESSION_SECRET
fi
chmod 0640 /etc/wdtt-panel/config.json

# ── 5. сид VK-хешей ──────────────────────────────────────────────────────────
if [ -f /opt/userdata/vk-hash.txt ] && [ ! -s /etc/wdtt-panel/vk-hash.txt ]; then
  install -m 0600 /opt/userdata/vk-hash.txt /etc/wdtt-panel/vk-hash.txt
  log "VK-хеши из userdata установлены (импорт в библиотеку при первом старте панели)"
fi

# ── 6. сеть: sysctl + iptables/NAT (best-effort, как deploy.sh) ──────────────
sysctl -w net.ipv4.ip_forward=1 >/dev/null 2>&1 || log "WARN: не удалось включить ip_forward (нужен --privileged или sysctls в compose)"
WAN_IFACE="$(ip route show default 2>/dev/null | head -1 | awk '{for(i=1;i<=NF;i++) if($i=="dev") print $(i+1)}')"
if command -v iptables >/dev/null 2>&1 && [ -n "$WAN_IFACE" ]; then
  log "WAN-интерфейс: $WAN_IFACE"
  for spec in "udp $WDTT_DTLS_PORT" "tcp $WDTT_DTLS_PORT"; do
    proto="${spec%% *}"; port="${spec##* }"
    iptables -C INPUT -p "$proto" --dport "$port" -m comment --comment WDTT_MANAGED -j ACCEPT 2>/dev/null || \
      iptables -I INPUT -p "$proto" --dport "$port" -m comment --comment WDTT_MANAGED -j ACCEPT 2>/dev/null || \
      log "WARN: iptables INPUT $port/$proto не применился"
  done
  # WG-порт: только с loopback (панель ходит локально; наружу он не нужен)
  iptables -C INPUT -i lo -p udp --dport "$WDTT_WG_PORT" -m comment --comment WDTT_WG_INTERNAL -j ACCEPT 2>/dev/null || \
    iptables -I INPUT -i lo -p udp --dport "$WDTT_WG_PORT" -m comment --comment WDTT_WG_INTERNAL -j ACCEPT 2>/dev/null || true
  iptables -C INPUT ! -i lo -p udp --dport "$WDTT_WG_PORT" -m comment --comment WDTT_WG_INTERNAL -j DROP 2>/dev/null || \
    iptables -I INPUT ! -i lo -p udp --dport "$WDTT_WG_PORT" -m comment --comment WDTT_WG_INTERNAL -j DROP 2>/dev/null || true
  # Admin API ядра: только loopback (наружу не публикуется)
  iptables -C INPUT -i lo -p tcp --dport "$WDTT_ADMIN_PORT" -m comment --comment WDTT_MANAGED -j ACCEPT 2>/dev/null || \
    iptables -I INPUT -i lo -p tcp --dport "$WDTT_ADMIN_PORT" -m comment --comment WDTT_MANAGED -j ACCEPT 2>/dev/null || true
  iptables -C INPUT ! -i lo -p tcp --dport "$WDTT_ADMIN_PORT" -m comment --comment WDTT_MANAGED -j DROP 2>/dev/null || \
    iptables -I INPUT ! -i lo -p tcp --dport "$WDTT_ADMIN_PORT" -m comment --comment WDTT_MANAGED -j DROP 2>/dev/null || true
  # FORWARD для wdtt0 + MASQUERADE подсети ядра + MSS clamping
  for dir in "-i wdtt0" "-o wdtt0"; do
    # shellcheck disable=SC2086
    # shellcheck disable=SC2145
    iptables -C FORWARD $dir -m comment --comment WDTT_MANAGED -j ACCEPT 2>/dev/null || \
      iptables -I FORWARD $dir -m comment --comment WDTT_MANAGED -j ACCEPT 2>/dev/null || true
  done
  iptables -t nat -C POSTROUTING -s 10.66.0.0/16 -o "$WAN_IFACE" -m comment --comment WDTT_MANAGED -j MASQUERADE 2>/dev/null || \
    iptables -t nat -A POSTROUTING -s 10.66.0.0/16 -o "$WAN_IFACE" -m comment --comment WDTT_MANAGED -j MASQUERADE 2>/dev/null || \
    log "WARN: MASQUERADE не применился — проверьте NET_ADMIN"
  for sd in "-s 10.66.0.0/16" "-d 10.66.0.0/16"; do
    # shellcheck disable=SC2086
    iptables -t mangle -C FORWARD $sd -p tcp -m tcp --tcp-flags SYN,RST SYN -m comment --comment WDTT_MANAGED -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || \
      iptables -t mangle -I FORWARD $sd -p tcp -m tcp --tcp-flags SYN,RST SYN -m comment --comment WDTT_MANAGED -j TCPMSS --clamp-mss-to-pmtu 2>/dev/null || true
  done
else
  log "WARN: iptables или WAN-интерфейс недоступны — NAT настраивайте вручную"
fi

# ── 7. TLS панели ────────────────────────────────────────────────────────────
if [ "$TLS_MODE" = "letsencrypt" ]; then
  [[ "$PANEL_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    && die "TLS_MODE=letsencrypt требует доменное имя в PANEL_HOST (получен IP)"
  [ -n "${PANEL_EMAIL:-}" ] || log "WARN: PANEL_EMAIL пуст — сертификат запросится без email"
fi
export CERTIFICATE_PATH="" PRIVATE_KEY_PATH=""
issue_self_signed() {
  install -d -m 0700 /etc/wdtt-panel/tls
  if [[ "$PANEL_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then SAN="IP:$PANEL_HOST"; else SAN="DNS:$PANEL_HOST"; fi
  openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes \
    -keyout /etc/wdtt-panel/tls/privkey.pem -out /etc/wdtt-panel/tls/fullchain.pem \
    -subj "/CN=$PANEL_HOST" -addext "subjectAltName=$SAN" \
    || die "Не удалось выпустить самоподписанный сертификат"
  chmod 0600 /etc/wdtt-panel/tls/privkey.pem
  chmod 0644 /etc/wdtt-panel/tls/fullchain.pem
  CERTIFICATE_PATH="/etc/wdtt-panel/tls/fullchain.pem"
  PRIVATE_KEY_PATH="/etc/wdtt-panel/tls/privkey.pem"
  export CERTIFICATE_PATH PRIVATE_KEY_PATH
  python3 - <<'PY'
import json
path = "/etc/wdtt-panel/config.json"
data = json.load(open(path, encoding="utf-8"))
data["certificate_path"] = "/etc/wdtt-panel/tls/fullchain.pem"
data["tls_mode"] = "self-signed"
tmp = path + ".tmp"
json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(tmp, "a", encoding="utf-8").write("\n")
import os
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
  log "Самоподписанный сертификат выпущен"
}
if [ "$TLS_MODE" = "letsencrypt" ]; then
  if [ -f "/etc/letsencrypt/live/$PANEL_HOST/fullchain.pem" ] && [ -f "/etc/letsencrypt/live/$PANEL_HOST/privkey.pem" ]; then
    CERTIFICATE_PATH="/etc/letsencrypt/live/$PANEL_HOST/fullchain.pem"
    PRIVATE_KEY_PATH="/etc/letsencrypt/live/$PANEL_HOST/privkey.pem"
    export CERTIFICATE_PATH PRIVATE_KEY_PATH
    python3 - <<'PY'
import json, os
path = "/etc/wdtt-panel/config.json"
data = json.load(open(path, encoding="utf-8"))
data["certificate_path"] = os.environ["CERTIFICATE_PATH"]
data["tls_mode"] = "letsencrypt"
data["certificate_email"] = os.environ.get("PANEL_EMAIL", data.get("certificate_email", ""))
tmp = path + ".tmp"
json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(tmp, "a", encoding="utf-8").write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
    log "Использую существующий Let's Encrypt сертификат из volume (продление — по таймеру супервизора)"
  else
  # Временный nginx только для ACME webroot, затем финальный конфиг (шаг 8).
  mkdir -p /var/lib/wdtt-panel/acme
  cat > /etc/nginx/conf.d/wdtt-panel.conf <<'NGINX'
server { listen 80; listen [::]:80; server_name _;
  location ^~ /.well-known/acme-challenge/ { root /var/lib/wdtt-panel/acme; }
  location / { return 404; } }
NGINX
  nginx -t || die "Nginx не принял временную ACME-конфигурацию"
  nginx
  CERTBOT=(/opt/wdtt-panel/certbot/bin/certbot certonly --non-interactive --agree-tos)
  if [ -n "${PANEL_EMAIL:-}" ]; then CERTBOT+=(--email "$PANEL_EMAIL"); else CERTBOT+=(--register-unsafely-without-email); fi
  CERTBOT+=(-d "$PANEL_HOST" --webroot --webroot-path /var/lib/wdtt-panel/acme)
  if "${CERTBOT[@]}"; then
    CERTIFICATE_PATH="/etc/letsencrypt/live/$PANEL_HOST/fullchain.pem"
    PRIVATE_KEY_PATH="/etc/letsencrypt/live/$PANEL_HOST/privkey.pem"
    [ -f "$CERTIFICATE_PATH" ] && [ -f "$PRIVATE_KEY_PATH" ] || die "Certbot отчитался успехом, но файлов нет"
    export CERTIFICATE_PATH PRIVATE_KEY_PATH
    python3 - <<'PY'
import json, os
path = "/etc/wdtt-panel/config.json"
data = json.load(open(path, encoding="utf-8"))
data["certificate_path"] = os.environ["CERTIFICATE_PATH"]
data["tls_mode"] = "letsencrypt"
data["certificate_email"] = os.environ.get("PANEL_EMAIL", "")
tmp = path + ".tmp"
json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
open(tmp, "a", encoding="utf-8").write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
    log "Let's Encrypt сертификат получен"
  else
    log "WARN: Let's Encrypt не выдался (DNS/порт 80?) — откатываюсь на self-signed"
    issue_self_signed
  fi
  nginx -s stop 2>/dev/null || true
  sleep 1
  fi
else
  if [ ! -s /etc/wdtt-panel/tls/fullchain.pem ] || [ ! -s /etc/wdtt-panel/tls/privkey.pem ]; then
    issue_self_signed
  else
    CERTIFICATE_PATH="/etc/wdtt-panel/tls/fullchain.pem"
    PRIVATE_KEY_PATH="/etc/wdtt-panel/tls/privkey.pem"
    export CERTIFICATE_PATH PRIVATE_KEY_PATH
    log "Использую существующий TLS из volume"
  fi
fi

# ── 8. финальный nginx-конфиг ────────────────────────────────────────────────
/usr/local/sbin/wdtt-gen-nginx-conf

# ── 9. рантаймы xray/wgcf ────────────────────────────────────────────────────
if [ "$WDTT_FETCH_RUNTIME" = "1" ]; then
  /usr/local/sbin/wdtt-fetch-runtime || log "WARN: рантаймы не докачались — Xray/WARP включаются вручную через панель"
else
  log "WDTT_FETCH_RUNTIME=0: пропуск скачивания xray/wgcf"
fi

# ── 10. итог ─────────────────────────────────────────────────────────────────
PANEL_PATH_SAVED="$(python3 -c 'import json; print(json.load(open("/etc/wdtt-panel/config.json"))["base_path"])')"
echo ""
echo "======================================================"
echo "WDTT-SERVER готов (панель $PANEL_VERSION, docker)"
echo "  Панель:  https://$PANEL_HOST:$PANEL_HTTPS_PORT$PANEL_PATH_SAVED"
echo "  Логин:   $PANEL_USER"
if [ -n "${PANEL_PASSWORD:-}" ]; then
  echo "  Пароль:  $PANEL_PASSWORD"
else
  echo "  Пароль:  (сохранён в volume, см. смену: wdtt-change-password)"
fi
echo "  TLS:     $TLS_MODE"
echo "  Ядро:    DTLS/UDP $WDTT_DTLS_PORT, API/TCP $WDTT_DTLS_PORT, WG/UDP $WDTT_WG_PORT"
echo "  Логи:    docker compose logs -f"
echo "======================================================"
unset PANEL_PASSWORD

exec "${CMD_ARGS[@]}"
