#!/usr/bin/env bash
set -Eeuo pipefail

PANEL_VERSION="1.9.5"
PANEL_REPOSITORY="${WDTT_PANEL_REPOSITORY:-lebrit/wdtt-control-panel}"
PANEL_BRANCH="${WDTT_PANEL_BRANCH:-main}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
INSTALL_DIR="/opt/wdtt-panel"
CONFIG_DIR="/etc/wdtt-panel"
STATE_DIR="/var/lib/wdtt-panel"
PRIVATE_STATE_DIR="/var/lib/wdtt-panel-private"
CONFIG_FILE="$CONFIG_DIR/config.json"
NGINX_FILE="/etc/nginx/conf.d/wdtt-panel.conf"
PANEL_SERVICE="wdtt-panel.service"
ADMIN_WRAPPER="/usr/local/sbin/wdtt-panel-admin"
SUDOERS_FILE="/etc/sudoers.d/wdtt-panel"
UPDATE_WRAPPER="/usr/local/sbin/wdtt-panel-update"
SELF_UPDATE_WRAPPER="/usr/local/sbin/wdtt-panel-self-update"
SOURCE_CONF_FILE="$CONFIG_DIR/source.conf"
UNINSTALL_WRAPPER="/usr/local/sbin/wdtt-panel-uninstall"
STATUS_WRAPPER="/usr/local/sbin/wdtt-panel-status"
GEOFILES_UPDATE_WRAPPER="/usr/local/sbin/wdtt-panel-geofiles-update"
BACKUP_RUNNER="/usr/local/sbin/wdtt-panel-backup"
AUTOCLEAN_RUNNER="/usr/local/sbin/wdtt-panel-autoclean"
AUTOCLEAN_SERVICE="wdtt-panel-autoclean.service"
AUTOCLEAN_TIMER="wdtt-panel-autoclean.timer"
CASCADE_RULES_WRAPPER="/usr/local/sbin/wdtt-panel-cascade-rules"
GATEWAY_RULES_WRAPPER="/usr/local/sbin/wdtt-panel-xray-gateway"
MANAGER_WRAPPER="/usr/local/sbin/wdtt-panel"
XRAY_SERVICE="wdtt-xray.service"
LEGACY_CASCADE_SERVICE="wdtt-cascade.service"
XRAY_CONFIG="$PRIVATE_STATE_DIR/xray-config.json"
XRAY_SETTINGS="$PRIVATE_STATE_DIR/xray-settings.json"
XRAY_ASSETS="$PRIVATE_STATE_DIR/xray-assets"
XRAY_CASCADE_SETTINGS="$PRIVATE_STATE_DIR/xray-cascade.json"
XRAY_CASCADE_SERVICE="wdtt-xray-cascade.service"
XRAY_GATEWAY_SERVICE="wdtt-xray-gateway.service"
WARP_DIR="$PRIVATE_STATE_DIR/warp"
LOG_FILE="/var/log/wdtt-panel-install.log"
USERDATA_DIR="${WDTT_USERDATA_DIR:-$SCRIPT_DIR/../userdata}"
SEED_HASHES_FILE="$CONFIG_DIR/vk-hash.txt"

PANEL_USER="${PANEL_USER:-admin}"
PANEL_PASSWORD="${PANEL_PASSWORD:-}"
PANEL_PATH="${PANEL_PATH:-}"
PANEL_HOST="${PANEL_HOST:-}"
PANEL_HTTPS_PORT="${PANEL_HTTPS_PORT:-9999}"
PANEL_LISTEN_PORT="${PANEL_LISTEN_PORT:-8787}"
PANEL_EMAIL="${PANEL_EMAIL:-}"
INSTALL_WDTT="${INSTALL_WDTT:-auto}"
WDTT_MAIN_PASSWORD="${WDTT_MAIN_PASSWORD:-}"
WDTT_TELEGRAM_BOT_TOKEN="${WDTT_TELEGRAM_BOT_TOKEN:-}"
WDTT_TELEGRAM_ADMIN_ID="${WDTT_TELEGRAM_ADMIN_ID:-}"
WDTT_REPOSITORY="${WDTT_REPOSITORY:-SpaceNeuroX/proxy-turn-vk-android}"
WDTT_REF="${WDTT_REF:-v1.4.3}"
# WDTT-SERVER: если указан каталог локального форка ядра — сборка идёт только из него,
# без загрузки исходников с GitHub. По умолчанию: src/ рядом с панелью ($SCRIPT_DIR/../src).
WDTT_SOURCE_DIR="${WDTT_SOURCE_DIR:-}"
GO_VERSION="${GO_VERSION:-1.25.0}"
WDTT_SERVICE="wdtt.service"
WDTT_EXTENSIONS_SERVICE="wdtt-panel-wdtt-extensions.service"
WDTT_EXTENSIONS_TIMER="wdtt-panel-wdtt-extensions.timer"
WDTT_EXTENSION_MARKER="wdtt-panel-extension-v9"
# Панель всегда запускается от root и вызывает root-хелпер напрямую (без sudo).
# Причина: юнит задаёт RestrictAddressFamilies=/LockPersonality=, а systemd для них
# принудительно включает NoNewPrivileges=yes (его нельзя отключить), поэтому sudo
# из-под панели НИКОГДА не может повысить права. Переопределяется WDTT_PANEL_RUN_AS_ROOT=0.
PANEL_RUN_AS_ROOT="${WDTT_PANEL_RUN_AS_ROOT:-1}"

log() { printf '[wdtt-panel] %s\n' "$*" | tee -a "$LOG_FILE"; }
die() { log "ERROR: $*"; exit 1; }
command_exists() { command -v "$1" >/dev/null 2>&1; }
random_token() { python3 -c "import secrets; print(secrets.token_urlsafe(${1:-24}))"; }
random_password() { python3 -c 'import secrets,string; a=string.ascii_letters+string.digits+"._~-"; print("".join(secrets.choice(a) for _ in range(24)))'; }

TMPFS_ORIG_MB=""
BUILD_TEMPDIRS=()

# Расширяет /tmp (tmpfs) до 4GiB перед сборками, если его размер меньше 1GiB.
grow_tmp_for_build() {
  local fstype size_bytes size_mb
  fstype="$(findmnt -n -o FSTYPE /tmp 2>/dev/null || true)"
  [ "$fstype" = "tmpfs" ] || { log "/tmp не tmpfs ($fstype) — расширение не требуется"; return 0; }
  size_bytes="$(findmnt -n -o SIZE -b /tmp 2>/dev/null || echo 0)"
  size_mb=$(( size_bytes / 1024 / 1024 ))
  log "/tmp (tmpfs): ${size_mb} MiB"
  [ "$size_mb" -ge 1024 ] && { log "/tmp >= 1GiB — расширение не требуется"; return 0; }
  TMPFS_ORIG_MB="$size_mb"
  log "/tmp меньше 1GiB — расширяю до 4GiB"
  mount -o remount,size=4G /tmp || die "Не удалось расширить /tmp до 4GiB"
  log "OK: /tmp (tmpfs) расширен до 4GiB"
}

# Убирает наши временные артефакты и возвращает /tmp к прежнему размеру.
restore_tmp_state() {
  local d
  for d in "${BUILD_TEMPDIRS[@]}"; do
    [ -n "$d" ] && rm -rf -- "$d" 2>/dev/null || true
  done
  rm -f /tmp/wdtt-server /tmp/wdtt-main.password /tmp/wdtt-admin.token /tmp/wdtt-bot.token
  [ -n "$TMPFS_ORIG_MB" ] || return 0
  log "Возвращаю /tmp к прежнему размеру (${TMPFS_ORIG_MB} MiB)"
  if mount -o remount,size="${TMPFS_ORIG_MB}M" /tmp 2>/dev/null; then
    log "OK: /tmp возвращён к ${TMPFS_ORIG_MB} MiB"
  else
    log "WARN: не удалось вернуть /tmp к ${TMPFS_ORIG_MB} MiB"
  fi
  TMPFS_ORIG_MB=""
}
trap restore_tmp_state EXIT

normalize_wdtt_main_password() {
  if [[ "${WDTT_MAIN_PASSWORD:-}" =~ ^[[:space:]]*$ ]]; then
    WDTT_MAIN_PASSWORD=""
  fi
}

validate_wdtt_main_password() {
  [[ "$WDTT_MAIN_PASSWORD" =~ ^[A-Za-z0-9._~-]{12,64}$ ]] || die "WDTT_MAIN_PASSWORD: 12-64 безопасных символа без пробелов и двоеточия"
}

normalize_telegram_settings() {
  if [[ "${WDTT_TELEGRAM_BOT_TOKEN:-}" =~ ^[[:space:]]*$ ]]; then
    WDTT_TELEGRAM_BOT_TOKEN=""
  fi
  if [[ "${WDTT_TELEGRAM_ADMIN_ID:-}" =~ ^[[:space:]]*$ ]]; then
    WDTT_TELEGRAM_ADMIN_ID=""
  fi
}

validate_telegram_settings() {
  normalize_telegram_settings
  [ -z "$WDTT_TELEGRAM_BOT_TOKEN$WDTT_TELEGRAM_ADMIN_ID" ] && return 0
  [[ "$WDTT_TELEGRAM_ADMIN_ID" =~ ^-?[0-9]{1,20}$ ]] || die "WDTT_TELEGRAM_ADMIN_ID должен быть числовым chat_id"
  [[ "$WDTT_TELEGRAM_BOT_TOKEN" =~ ^[0-9]{5,20}:[A-Za-z0-9_-]{20,200}$ ]] || die "WDTT_TELEGRAM_BOT_TOKEN должен быть в формате 123456:ABC..."
}

require_root() {
  [ "$(id -u)" -eq 0 ] || die "Запустите установщик от root: sudo bash install.sh"
  mkdir -p "$(dirname "$LOG_FILE")"
  touch "$LOG_FILE"
}

detect_os() {
  [ -r /etc/os-release ] || die "Не найден /etc/os-release"
  # shellcheck disable=SC1091
  . /etc/os-release
  OS_ID="${ID:-unknown}"
  OS_LIKE="${ID_LIKE:-}"
  # Astra Linux опознаём отдельно: только для неё разрешена автосборка Python
  # (про остальные старые дистрибутивы неизвестно, заведётся ли сборка).
  IS_ASTRA=0
  if [ -f /etc/astra_version ] || [[ "${OS_ID,,}" == *astra* ]] || [[ "${OS_LIKE,,}" == *astra* ]]; then
    IS_ASTRA=1
  fi
  case "$OS_ID" in
    ubuntu|debian|linuxmint|pop|astra) PKG="apt" ;;
    fedora|rhel|centos|rocky|almalinux|oracle) command_exists dnf && PKG="dnf" || PKG="yum" ;;
    arch|manjaro|endeavouros) PKG="pacman" ;;
    *)
      # Деривативы (Astra Linux, Альт, РОСА и др.) опознаём по ID_LIKE.
      case " $OS_LIKE " in
        *" debian "*|*" ubuntu "*) PKG="apt" ;;
        *" rhel "*|*" fedora "*|*" centos "*) command_exists dnf && PKG="dnf" || PKG="yum" ;;
        *" arch "*) PKG="pacman" ;;
        *) die "Неподдерживаемый дистрибутив: $OS_ID (ID_LIKE=${OS_LIKE:-пусто})" ;;
      esac
      ;;
  esac
  log "ОС: ${PRETTY_NAME:-$OS_ID}"
  if command_exists nginx; then NGINX_WAS_INSTALLED=1; else NGINX_WAS_INSTALLED=0; fi
}

# Панель использует dataclasses/f-строки — нужен Python 3.8+.
# Путь к нему запоминаем в PYTHON3_BIN и подставляем в юнит и admin-обёртку:
# на Astra Linux собранный вручную Python 3.9 живёт в /usr/local/bin,
# а не в /usr/bin, и хардкод сломал бы запуск панели.
PYTHON3_BIN="/usr/bin/python3"
PYTHON_BUILD_VERSION="3.9.22"

resolve_python_bin() {
  PYTHON3_BIN="$(command -v python3 2>/dev/null || echo /usr/bin/python3)"
}

python_version_ok() {
  python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null
}

# На Astra Linux (штатный Python 3.5 в 2.12) автоматически собираем
# Python 3.9.22 из исходников: это новейшая ветка, которая ещё собирается
# с OpenSSL 1.1.0 таких систем (3.10+ потеряет ssl-модуль).
# На остальных дистрибутивах автосборка отключена — только понятная ошибка.
ensure_modern_python() {
  command_exists python3 || die "python3 не найден после установки пакетов"
  if python_version_ok; then
    resolve_python_bin
    log "Python: $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') ($PYTHON3_BIN)"
    return 0
  fi
  [ "$IS_ASTRA" = "1" ] || die "Нужен Python 3.8+, а в системе $(python3 --version 2>&1). Автосборка Python включена только для Astra Linux — поставьте python3.8+ вручную и повторите установку"
  command_exists curl || die "curl нужен для скачивания исходников Python"
  log "python3 старше 3.8 — собираю Python $PYTHON_BUILD_VERSION из исходников (займёт несколько минут)"
  local build_tmp="${TMPDIR:-/tmp}" free_mb jobs src_dir
  free_mb="$(df -m "$build_tmp" 2>/dev/null | awk 'NR==2 {print $4}')"
  [ "${free_mb:-0}" -ge 1200 ] || die "Для сборки Python нужно ≥1200 МБ свободно в $build_tmp (сейчас ${free_mb:-?} МБ)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get install -y build-essential libssl-dev zlib1g-dev libbz2-dev libreadline-dev libsqlite3-dev libffi-dev libncurses5-dev libncursesw5-dev xz-utils tk-dev liblzma-dev >>"$LOG_FILE" 2>&1 \
    || log "WARN: часть build-зависимостей Python не встала — пробую собрать с доступными"
  jobs="$(nproc 2>/dev/null || echo 1)"
  src_dir="$build_tmp/Python-$PYTHON_BUILD_VERSION"
  rm -rf "$src_dir" "$build_tmp/python-src.tgz"
  curl -fsSL --max-time 300 "https://www.python.org/ftp/python/$PYTHON_BUILD_VERSION/Python-$PYTHON_BUILD_VERSION.tgz" -o "$build_tmp/python-src.tgz" >>"$LOG_FILE" 2>&1 \
    || die "Не скачался Python $PYTHON_BUILD_VERSION с python.org (см. $LOG_FILE)"
  tar -xzf "$build_tmp/python-src.tgz" -C "$build_tmp" >>"$LOG_FILE" 2>&1 \
    || die "Не распаковался архив Python $PYTHON_BUILD_VERSION"
  if ! (
    cd "$src_dir" || exit 1
    ./configure --with-ensurepip=install --enable-loadable-sqlite-extensions >>/tmp/python-configure.log 2>&1 \
      && make -j"$jobs" >>/tmp/python-make.log 2>&1 \
      && make altinstall >>/tmp/python-altinstall.log 2>&1
  ); then
    log "Параллельная сборка не удалась — повторяю в один поток (для слабых VM)"
    (
      cd "$src_dir" || exit 1
      ./configure --with-ensurepip=install --enable-loadable-sqlite-extensions >>/tmp/python-configure.log 2>&1 \
        && make -j1 >>/tmp/python-make.log 2>&1 \
        && make altinstall >>/tmp/python-altinstall.log 2>&1
    ) || die "Сборка Python $PYTHON_BUILD_VERSION не удалась (см. /tmp/python-*.log)"
  fi
  # altinstall не трогает системный /usr/bin/python3; /usr/local/bin раньше
  # в PATH, поэтому python3 теперь указывает на 3.9.
  ln -sf /usr/local/bin/python3.9 /usr/local/bin/python3
  ln -sf /usr/local/bin/python3.9 /usr/local/bin/python
  ln -sf /usr/local/bin/pip3.9 /usr/local/bin/pip3
  ln -sf /usr/local/bin/pip3.9 /usr/local/bin/pip
  rm -rf "$src_dir" "$build_tmp/python-src.tgz"
  resolve_python_bin
  python_version_ok || die "Сборка прошла, но python3 всё ещё старше 3.8 ($(python3 --version 2>&1), $PYTHON3_BIN)"
  log "Python: $(python3 -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])') ($PYTHON3_BIN)"
}

install_packages() {
  log "Установка системных зависимостей"
  case "$PKG" in
    apt)
      export DEBIAN_FRONTEND=noninteractive
      apt-get update -y >>"$LOG_FILE" 2>&1
      apt-get install -y -qq python3 python3-venv python3-pip nginx sudo curl ca-certificates openssl iproute2 iptables conntrack unzip >>"$LOG_FILE" 2>&1
      ;;
    dnf|yum)
      "$PKG" install -y python3 python3-pip nginx sudo curl ca-certificates openssl iproute iptables conntrack-tools unzip tar gzip >>"$LOG_FILE" 2>&1
      ;;
    pacman)
      pacman -Sy --noconfirm --needed python python-pip nginx sudo curl ca-certificates openssl iproute2 iptables conntrack-tools unzip tar gzip >>"$LOG_FILE" 2>&1
      ;;
  esac
}

validate_inputs() {
  [[ "$PANEL_HTTPS_PORT" =~ ^[0-9]+$ ]] && [ "$PANEL_HTTPS_PORT" -ge 1 ] && [ "$PANEL_HTTPS_PORT" -le 65535 ] || die "Некорректный PANEL_HTTPS_PORT"
  [ "$PANEL_HTTPS_PORT" -ne 80 ] || die "PANEL_HTTPS_PORT=80 зарезервирован для ACME; выберите другой порт"
  [[ "$PANEL_LISTEN_PORT" =~ ^[0-9]+$ ]] && [ "$PANEL_LISTEN_PORT" -ge 1024 ] && [ "$PANEL_LISTEN_PORT" -le 65535 ] || die "Некорректный PANEL_LISTEN_PORT"
  [ "$PANEL_HTTPS_PORT" != "$PANEL_LISTEN_PORT" ] || die "Внешний и внутренний порты панели должны отличаться"
  [[ "$PANEL_USER" =~ ^[A-Za-z0-9_.-]{3,32}$ ]] || die "Некорректный PANEL_USER"
  [[ "$WDTT_REPOSITORY" =~ ^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]] || die "Некорректный WDTT_REPOSITORY"
  [[ "$WDTT_REF" =~ ^[A-Za-z0-9._-]+$ ]] || die "Некорректный WDTT_REF"
  [[ "$GO_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || die "Некорректный GO_VERSION"
}

validate_port_availability() {
  local listeners
  listeners="$(ss -ltnp "( sport = :$PANEL_LISTEN_PORT )" 2>/dev/null || true)"
  if grep -q LISTEN <<<"$listeners" && ! systemctl is-active --quiet "$PANEL_SERVICE"; then
    die "Внутренний порт $PANEL_LISTEN_PORT уже занят"
  fi
  listeners="$(ss -ltnp "( sport = :$PANEL_HTTPS_PORT )" 2>/dev/null || true)"
  if grep -q LISTEN <<<"$listeners" && ! grep -qi nginx <<<"$listeners"; then
    die "Внешний порт $PANEL_HTTPS_PORT занят не Nginx; задайте PANEL_HTTPS_PORT"
  fi
}

discover_host() {
  if [ -z "$PANEL_HOST" ]; then
    PANEL_HOST="$(curl -4fsS --max-time 8 https://api.ipify.org 2>/dev/null || true)"
  fi
  if [ -z "$PANEL_HOST" ] && [ -t 0 ]; then
    read -r -p "Домен или публичный IPv4 панели: " PANEL_HOST
  fi
  [ -n "$PANEL_HOST" ] || die "Укажите домен или публичный IPv4 через интерактивный установщик"
  [[ "$PANEL_HOST" != *:* ]] || die "Автоматическая настройка IPv6 пока не поддерживается; используйте домен или IPv4"
  PANEL_HOST="$(python3 - "$PANEL_HOST" <<'PY'
import ipaddress, re, sys
value = sys.argv[1].strip().rstrip(".").lower()
try:
    address = ipaddress.ip_address(value)
    if address.version != 4:
        raise ValueError
    print(value)
    raise SystemExit
except ValueError:
    pass
labels = value.split(".")
pattern = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
if len(labels) < 2 or len(value) > 253 or not all(pattern.fullmatch(label) for label in labels):
    raise SystemExit(2)
print(value)
PY
)" || die "Некорректный PANEL_HOST"
}

prepare_secrets() {
  [ -n "$PANEL_PASSWORD" ] || PANEL_PASSWORD="$(random_password)"
  [ "${#PANEL_PASSWORD}" -ge 12 ] || die "PANEL_PASSWORD должен содержать не менее 12 символов"
  [ -n "$PANEL_PATH" ] || PANEL_PATH="$(random_token 18)"
  PANEL_PATH="/${PANEL_PATH#/}"
  PANEL_PATH="${PANEL_PATH%/}/"
  [[ "$PANEL_PATH" =~ ^/[A-Za-z0-9_-]{16,80}/$ ]] || die "PANEL_PATH должен быть случайным путем из 16-80 символов"
  SESSION_SECRET="$(random_token 48)"
  normalize_wdtt_main_password
  validate_telegram_settings
}

load_panel_config() {
  [ -r "$CONFIG_FILE" ] || die "Панель не установлена: $CONFIG_FILE не найден"
  mapfile -t PANEL_CONFIG_VALUES < <(python3 - "$CONFIG_FILE" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
for key, default in (
    ("username", "admin"),
    ("base_path", "/"),
    ("public_host", ""),
    ("https_port", 9999),
    ("listen_port", 8787),
    ("certificate_path", ""),
    ("tls_mode", "self-signed"),
    ("certificate_email", ""),
):
    print(d.get(key, default))
PY
  )
  [ "${#PANEL_CONFIG_VALUES[@]}" -eq 8 ] || die "Не удалось прочитать конфигурацию панели"
  PANEL_USER="${PANEL_CONFIG_VALUES[0]}"
  PANEL_PATH="${PANEL_CONFIG_VALUES[1]}"
  PANEL_HOST="${PANEL_CONFIG_VALUES[2]}"
  PANEL_HTTPS_PORT="${PANEL_CONFIG_VALUES[3]}"
  PANEL_LISTEN_PORT="${PANEL_CONFIG_VALUES[4]}"
  CERTIFICATE_PATH="${PANEL_CONFIG_VALUES[5]}"
  TLS_MODE="${PANEL_CONFIG_VALUES[6]}"
  PANEL_EMAIL="${PANEL_CONFIG_VALUES[7]}"
  if [ "$TLS_MODE" = "letsencrypt" ]; then
    PRIVATE_KEY_PATH="/etc/letsencrypt/live/$PANEL_HOST/privkey.pem"
  else
    PRIVATE_KEY_PATH="$CONFIG_DIR/tls/privkey.pem"
  fi
}

wdtt_installed() {
  systemctl cat wdtt.service >/dev/null 2>&1 || [ -x /usr/local/bin/wdtt-server ]
}

download_wdtt_archive() {
  local dest="$1"
  local first="heads" second="tags"
  if [[ "$WDTT_REF" == v[0-9]* ]]; then
    first="tags"
    second="heads"
  fi
  for kind in "$first" "$second"; do
    if curl -fsSL --retry 3 "https://github.com/${WDTT_REPOSITORY}/archive/refs/${kind}/${WDTT_REF}.zip" -o "$dest"; then
      log "WDTT source: ${WDTT_REPOSITORY} ${kind}/${WDTT_REF}"
      return 0
    fi
  done
  die "Не удалось скачать WDTT source для WDTT_REF=$WDTT_REF"
}

# WDTT-SERVER: локальный форк ядра. Возвращает каталог, если он существует и похож
# на исходник qWDTT (есть модульный сервер). Иначе — пусто.
local_wdtt_source_dir() {
  local candidates c
  if [ -n "$WDTT_SOURCE_DIR" ]; then
    candidates="$WDTT_SOURCE_DIR"
  else
    candidates="$SCRIPT_DIR/../src/proxy-turn-vk-android-1.4.3 $SCRIPT_DIR/src/proxy-turn-vk-android-1.4.3 $SCRIPT_DIR/../src $SCRIPT_DIR/src"
  fi
  for c in $candidates; do
    if [ -d "$c" ] && [ -f "$c/server/main.go" ]; then
      printf '%s' "$c"
      return 0
    fi
  done
  return 1
}

# WDTT-SERVER: разворачивает исходник ядра в рабочий каталог сборки.
# При наличии локального форка копирует его, иначе скачивает архив с GitHub.
resolve_wdtt_source() {
  local work_dir="$1" local_dir
  if local_dir="$(local_wdtt_source_dir)" && [ -n "$local_dir" ]; then
    rm -rf "$work_dir/source"
    mkdir -p "$work_dir/source/local"
    cp -a "$local_dir/." "$work_dir/source/local/"
    log "WDTT source: локальный форк ($local_dir)"
    WDTT_SOURCE_RESOLVED="$work_dir/source/local"
    return 0
  fi
  download_wdtt_archive "$work_dir/wdtt.zip"
  unzip -q "$work_dir/wdtt.zip" -d "$work_dir/source"
  WDTT_SOURCE_RESOLVED="$(find "$work_dir/source" -mindepth 1 -maxdepth 1 -type d | head -1)"
}

wdtt_extensions_binary_is_current() {
  [ -x /usr/local/bin/wdtt-server ] || return 1
  LC_ALL=C grep -aFq "$WDTT_EXTENSION_MARKER" /usr/local/bin/wdtt-server
}

wdtt_extensions_are_enabled() {
  wdtt_extensions_binary_is_current || return 1
  python3 - "$PRIVATE_STATE_DIR/wdtt-extensions.json" "$WDTT_EXTENSION_MARKER" "$WDTT_REPOSITORY" "$WDTT_REF" <<'PY'
import json
import sys
try:
    state = json.load(open(sys.argv[1], encoding="utf-8"))
    features = state.get("features", [])
    raise SystemExit(0 if (
        {"labels", "main_traffic", "activity", "traffic_quota", "retained_expired"}.issubset(features)
        and state.get("marker") == sys.argv[2]
        and state.get("wdtt_repository") == sys.argv[3]
        and state.get("wdtt_ref") == sys.argv[4]
    ) else 1)
except (OSError, ValueError, AttributeError):
    raise SystemExit(1)
PY
}

wdtt_database_preserved() {
  python3 - "$1" "$2" <<'PY'
import json
import sys
from pathlib import Path

before = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
after = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
if not isinstance(before, dict) or not isinstance(after, dict):
    raise SystemExit(1)
before_main = str(before.get("main_password") or "")
after_main = str(after.get("main_password") or "")
if before_main and before_main != after_main:
    raise SystemExit(1)
for field in ("passwords", "devices"):
    before_items = before.get(field) or {}
    after_items = after.get(field) or {}
    if not isinstance(before_items, dict) or not isinstance(after_items, dict):
        raise SystemExit(1)
    if not set(before_items).issubset(after_items):
        raise SystemExit(1)

protected_user_fields = (
    "label", "expires_at", "last_upload_at", "last_download_at",
    "traffic_managed", "traffic_unlimited", "traffic_baseline_bytes",
    "traffic_primary_bytes", "traffic_extra_bytes", "traffic_operations",
)
before_passwords = before.get("passwords") or {}
after_passwords = after.get("passwords") or {}
for password, before_entry in before_passwords.items():
    after_entry = after_passwords.get(password)
    if not isinstance(before_entry, dict) or not isinstance(after_entry, dict):
        raise SystemExit(1)
    for field in protected_user_fields:
        if field in before_entry and before_entry[field] != after_entry.get(field):
            raise SystemExit(1)

for field in ("main_down_bytes", "main_up_bytes", "main_last_upload_at", "main_last_download_at"):
    if field in before and before[field] != after.get(field):
        raise SystemExit(1)
PY
}

restore_wdtt_extension_backup() {
  local binary_backup="$1" database_backup="${2:-}" restart_service="${3:-0}"
  systemctl stop "$WDTT_SERVICE" >>"$LOG_FILE" 2>&1 || true
  install -m 0755 "$binary_backup" /usr/local/bin/wdtt-server
  if [ -n "$database_backup" ]; then
    install -m 0600 "$database_backup" /etc/wdtt/passwords.json
  fi
  if [ "$restart_service" = "1" ]; then
    systemctl start "$WDTT_SERVICE" >>"$LOG_FILE" 2>&1 || true
  fi
}

apply_telegram_settings() {
  [ -n "$WDTT_TELEGRAM_BOT_TOKEN$WDTT_TELEGRAM_ADMIN_ID" ] || return 0
  validate_telegram_settings
  [ -f /etc/wdtt/passwords.json ] || die "Не найдена база WDTT /etc/wdtt/passwords.json для настройки Telegram"
  [ -f "/etc/systemd/system/$WDTT_SERVICE" ] || die "Не найден /etc/systemd/system/$WDTT_SERVICE для сохранения Telegram-настроек"
  python3 - /etc/wdtt/passwords.json "/etc/systemd/system/$WDTT_SERVICE" /etc/wdtt/bot.token "$WDTT_TELEGRAM_ADMIN_ID" "$WDTT_TELEGRAM_BOT_TOKEN" <<'PY'
import json
import os
import re
import shlex
import sys
import tempfile
from pathlib import Path

db_path = Path(sys.argv[1])
unit_path = Path(sys.argv[2])
bot_token_path = Path(sys.argv[3])
admin_id = sys.argv[4]
bot_token = sys.argv[5]

data = json.loads(db_path.read_text(encoding="utf-8")) if db_path.exists() else {}
if not isinstance(data, dict):
    raise SystemExit("passwords.json is not an object")
data.setdefault("passwords", {})
data.setdefault("devices", {})
data["admin_id"] = admin_id
data["bot_token"] = bot_token
fd, tmp = tempfile.mkstemp(prefix="passwords.", suffix=".tmp", dir=db_path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o600)
    os.replace(tmp, db_path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)

if bot_token:
    fd, tmp = tempfile.mkstemp(prefix="bot-token.", suffix=".tmp", dir=bot_token_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(bot_token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, bot_token_path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
else:
    bot_token_path.unlink(missing_ok=True)

def quote(token: str) -> str:
    if re.fullmatch(r"[^\s\"'\\]+", token):
        return token
    return '"' + token.replace("\\", "\\\\").replace('"', '\\"') + '"'

lines = unit_path.read_text(encoding="utf-8").splitlines()
next_lines = []
found = False
for line in lines:
    if not line.startswith("ExecStart="):
        next_lines.append(line)
        continue
    found = True
    tokens = shlex.split(line.split("=", 1)[1])
    clean = []
    skip = False
    for token in tokens:
        if skip:
            skip = False
            continue
        if token in {"-admin", "--admin", "-bot-token", "--bot-token", "-bot-token-file", "--bot-token-file"}:
            skip = True
            continue
        if token.startswith(("-admin=", "--admin=", "-bot-token=", "--bot-token=", "-bot-token-file=", "--bot-token-file=")):
            continue
        clean.append(token)
    clean.extend(["-admin", admin_id, "-bot-token-file", str(bot_token_path)])
    next_lines.append("ExecStart=" + " ".join(quote(token) for token in clean))
if not found:
    raise SystemExit("ExecStart not found")
fd, tmp = tempfile.mkstemp(prefix=f"{unit_path.name}.", suffix=".tmp", dir=unit_path.parent)
try:
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write("\n".join(next_lines) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(tmp, 0o644)
    os.replace(tmp, unit_path)
finally:
    if os.path.exists(tmp):
        os.unlink(tmp)
PY
  systemctl daemon-reload
  if systemctl is-active --quiet "$WDTT_SERVICE"; then
    systemctl restart "$WDTT_SERVICE" >>"$LOG_FILE" 2>&1 || die "Не удалось перезапустить WDTT после настройки Telegram"
  fi
  log "Telegram-бот WDTT настроен"
}

install_clean_wdtt() {
  case "$INSTALL_WDTT" in
    0|false|no) log "Установка WDTT отключена"; return ;;
    auto) wdtt_installed && { log "Обнаружен существующий WDTT, его файлы не изменяются"; return; } ;;
    1|true|yes) wdtt_installed && { log "Обнаружен существующий WDTT, повторный деплой пропущен"; return; } ;;
    *) die "INSTALL_WDTT должен быть auto, yes или no" ;;
  esac

  log "Чистый сервер: сборка ядра из локальных исходников (форк qWDTT $WDTT_REF)"
  [ -n "$WDTT_MAIN_PASSWORD" ] || WDTT_MAIN_PASSWORD="$(random_password)"
  validate_wdtt_main_password
  grow_tmp_for_build
  BUILD_DIR="$(mktemp -d)"
  BUILD_TEMPDIRS+=("$BUILD_DIR")
  trap 'rm -rf "${BUILD_DIR:-}"' RETURN

  case "$(uname -m)" in
    x86_64|amd64) GO_ARCH="amd64" ;;
    aarch64|arm64) GO_ARCH="arm64" ;;
    *) die "Сборка WDTT поддержана для amd64 и arm64" ;;
  esac

  GO_TARBALL="go${GO_VERSION}.linux-${GO_ARCH}.tar.gz"
  curl -fsSL "https://go.dev/dl/${GO_TARBALL}" -o "$BUILD_DIR/$GO_TARBALL"
  curl -fsSL "https://dl.google.com/go/${GO_TARBALL}.sha256" -o "$BUILD_DIR/$GO_TARBALL.sha256"
  GO_CHECKSUM="$(awk 'NR == 1 { print $1; exit }' "$BUILD_DIR/$GO_TARBALL.sha256")"
  [[ "$GO_CHECKSUM" =~ ^[a-fA-F0-9]{64}$ ]] || die "Некорректная контрольная сумма Go"
  printf '%s  %s\n' "$GO_CHECKSUM" "$BUILD_DIR/$GO_TARBALL" | sha256sum -c - >>"$LOG_FILE"
  tar -xzf "$BUILD_DIR/$GO_TARBALL" -C "$BUILD_DIR"
  install -d "$BUILD_DIR/gopath/pkg/mod" "$BUILD_DIR/go-cache"

  resolve_wdtt_source "$BUILD_DIR"
  WDTT_SOURCE="$WDTT_SOURCE_RESOLVED"
  [ -f "$WDTT_SOURCE/server/main.go" ] || die "В qWDTT $WDTT_REF не найден модульный сервер"
  (
    cd "$WDTT_SOURCE"
    PATH="$BUILD_DIR/go/bin:$PATH" GOPATH="$BUILD_DIR/gopath" GOMODCACHE="$BUILD_DIR/gopath/pkg/mod" GOCACHE="$BUILD_DIR/go-cache" CGO_ENABLED=0 "$BUILD_DIR/go/bin/go" build -mod=mod -trimpath -ldflags='-s -w' -o /tmp/wdtt-server ./server
  ) >>"$LOG_FILE" 2>&1
  chmod 0755 /tmp/wdtt-server
  (
    umask 077
    printf '%s' "$WDTT_MAIN_PASSWORD" > /tmp/wdtt-main.password
    random_token > /tmp/wdtt-admin.token
    if [ -n "$WDTT_TELEGRAM_BOT_TOKEN" ]; then
      printf '%s' "$WDTT_TELEGRAM_BOT_TOKEN" > /tmp/wdtt-bot.token
    else
      rm -f /tmp/wdtt-bot.token
    fi
  )
  WDTT_ADMIN_ID="$WDTT_TELEGRAM_ADMIN_ID" bash "$WDTT_SOURCE/app/src/main/assets/deploy.sh" install >>"$LOG_FILE" 2>&1
  log "WDTT установлен официальным deploy.sh"
}

install_wdtt_extensions() {
  require_root
  if wdtt_extensions_are_enabled; then
    log "Расширение WDTT уже установлено"
    return 0
  fi
  wdtt_installed || die "WDTT не найден: сначала установите или разверните WDTT"
  [ -x /usr/local/bin/wdtt-server ] || die "Не найден /usr/local/bin/wdtt-server"

  grow_tmp_for_build
  local work source go_arch go_tarball go_checksum backup database_backup target was_active=0
  work="$(mktemp -d)"
  BUILD_TEMPDIRS+=("$work")
  trap 'rm -rf "${work:-}"' RETURN
  target="/usr/local/bin/wdtt-server"

  case "$(uname -m)" in
    x86_64|amd64) go_arch="amd64" ;;
    aarch64|arm64) go_arch="arm64" ;;
    *) die "Сборка WDTT поддержана для amd64 и arm64" ;;
  esac

  log "Сборка расширения WDTT: общие метки Telegram и счётчики главного пароля"
  go_tarball="go${GO_VERSION}.linux-${go_arch}.tar.gz"
  curl -fsSL --retry 3 "https://go.dev/dl/${go_tarball}" -o "$work/$go_tarball"
  curl -fsSL --retry 3 "https://dl.google.com/go/${go_tarball}.sha256" -o "$work/$go_tarball.sha256"
  go_checksum="$(awk 'NR == 1 { print $1; exit }' "$work/$go_tarball.sha256")"
  [[ "$go_checksum" =~ ^[a-fA-F0-9]{64}$ ]] || die "Некорректная контрольная сумма Go"
  printf '%s  %s\n' "$go_checksum" "$work/$go_tarball" | sha256sum -c - >>"$LOG_FILE"
  tar -xzf "$work/$go_tarball" -C "$work"
  install -d "$work/gopath/pkg/mod" "$work/go-cache"
  resolve_wdtt_source "$work"
  source="$WDTT_SOURCE_RESOLVED"
  [ -f "$source/server/main.go" ] || die "В qWDTT $WDTT_REF не найден модульный сервер"

  if [ "$WDTT_REPOSITORY" = "SpaceNeuroX/proxy-turn-vk-android" ]; then
    python3 "$SCRIPT_DIR/wdtt_panel/wdtt_server_patch.py" "$source" || die "Не удалось адаптировать qWDTT $WDTT_REF для панели"
  else
  die "Расширение панели 0.12.3 поддерживает только SpaceNeuroX/proxy-turn-vk-android v1.4.3"
  python3 - "$source/server.go" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")

def replace_once(old, new, title):
    global source
    if old not in source:
        raise SystemExit(f"WDTT source changed: cannot apply {title}")
    source = source.replace(old, new, 1)

replace_once(
    'func main() {\n',
    'const wdttPanelExtensionMarker = "wdtt-panel-extension-v9"\n\nfunc main() {\n\tlog.Printf("[WDTT Panel] extension %s enabled", wdttPanelExtensionMarker)\n',
    "extension marker",
)

replace_once(
    '\tIsDeactivated bool   `json:"is_deactivated,omitempty"`\n}',
    '\tIsDeactivated bool   `json:"is_deactivated,omitempty"`\n\tLabel         string `json:"label,omitempty"`\n\tLastUploadAt  int64  `json:"last_upload_at,omitempty"`\n\tLastDownloadAt int64  `json:"last_download_at,omitempty"`\n}',
    "user label field",
)
replace_once(
    '\tMainPassword string                    `json:"main_password"`\n',
    '\tMainPassword string                    `json:"main_password"`\n\tMainDownBytes int64                     `json:"main_down_bytes,omitempty"`\n\tMainUpBytes   int64                     `json:"main_up_bytes,omitempty"`\n\tMainLastUploadAt int64                  `json:"main_last_upload_at,omitempty"`\n\tMainLastDownloadAt int64                `json:"main_last_download_at,omitempty"`\n',
    "main traffic fields",
)
replace_once(
    '\tvar waitingForHash bool\n',
    '\tvar waitingForHash bool\n\tvar waitingForLabel bool\n',
    "label input state",
)
replace_once(
    '\t\t\t\t\ttxt := fmt.Sprintf("🔑 *Пароль:* `%s`\\n", pass)\n',
    '\t\t\t\t\ttxt := fmt.Sprintf("🔑 *Пароль:* `%s`\\n", pass)\n\t\t\t\t\tif entry.Label != "" {\n\t\t\t\t\t\ttxt += fmt.Sprintf("🏷 *Метка:* %s\\n", telegramLabel(entry.Label))\n\t\t\t\t\t}\n',
    "label in Telegram details",
)
replace_once(
    '\t\t\t\t\tif entry.DeviceID == "" {\n',
    '\t\t\t\t\tkb = append(kb, map[string]interface{}{\n\t\t\t\t\t\t"text":          "🏷 Изменить метку",\n\t\t\t\t\t\t"callback_data": "label_" + pass,\n\t\t\t\t\t})\n\t\t\t\t\tif entry.DeviceID == "" {\n',
    "label button",
)
replace_once(
    '\t\t\t\t} else if strings.HasPrefix(data, "deact_") {\n',
    '\t\t\t\t} else if strings.HasPrefix(data, "label_") {\n\t\t\t\t\tpass := strings.TrimPrefix(data, "label_")\n\t\t\t\t\tdbMutex.Lock()\n\t\t\t\t\t_, exists := db.Passwords[pass]\n\t\t\t\t\tdbMutex.Unlock()\n\t\t\t\t\tif !exists {\n\t\t\t\t\t\tsendTelegram(token, adminID, "❌ Пароль не найден", nil)\n\t\t\t\t\t\tcontinue\n\t\t\t\t\t}\n\t\t\t\t\ttargetPassword = pass\n\t\t\t\t\twaitingForLabel = true\n\t\t\t\t\tsendTelegram(token, adminID, "🏷 Отправьте метку до 64 символов. Отправьте - чтобы очистить.", nil)\n\n\t\t\t\t} else if strings.HasPrefix(data, "deact_") {\n',
    "label callback",
)
replace_once(
    '\t\t\t// Обработка ввода количества дней\n\t\t\tif waitingForDays {\n',
    '\t\t\tif waitingForLabel {\n\t\t\t\twaitingForLabel = false\n\t\t\t\tlabel, labelErr := normalizeUserLabel(cmd)\n\t\t\t\tif labelErr != nil {\n\t\t\t\t\tsendTelegram(token, adminID, "❌ Метка должна быть не длиннее 64 символов и без служебных символов.", nil)\n\t\t\t\t\tcontinue\n\t\t\t\t}\n\t\t\t\tdbMutex.Lock()\n\t\t\t\tentry, exists := db.Passwords[targetPassword]\n\t\t\t\tif exists && entry != nil {\n\t\t\t\t\tentry.Label = label\n\t\t\t\t\tsaveDB()\n\t\t\t\t}\n\t\t\t\tdbMutex.Unlock()\n\t\t\t\tif !exists || entry == nil {\n\t\t\t\t\tsendTelegram(token, adminID, "❌ Пароль не найден", nil)\n\t\t\t\t} else if label == "" {\n\t\t\t\t\tsendTelegram(token, adminID, "✅ Метка очищена", nil)\n\t\t\t\t} else {\n\t\t\t\t\tsendTelegram(token, adminID, fmt.Sprintf("✅ Метка сохранена: %s", telegramLabel(label)), nil)\n\t\t\t\t}\n\t\t\t\ttargetPassword = ""\n\t\t\t\tcontinue\n\t\t\t}\n\n\t\t\t// Обработка ввода количества дней\n\t\t\tif waitingForDays {\n',
    "label input",
)
replace_once(
    '\t\t\ttxt += fmt.Sprintf("%s `%s` (%s)\\n", status, p, expiry)\n\t\t\tinlineKb = append(inlineKb, map[string]interface{}{\n\t\t\t\t"text":          "🔍 " + p,\n',
    '\t\t\tlabelSuffix := ""\n\t\t\tif entry.Label != "" {\n\t\t\t\tlabelSuffix = " — " + telegramLabel(entry.Label)\n\t\t\t}\n\t\t\ttxt += fmt.Sprintf("%s `%s`%s (%s)\\n", status, p, labelSuffix, expiry)\n\t\t\tbuttonText := "🔍 " + p\n\t\t\tif entry.Label != "" {\n\t\t\t\tbuttonText = "🔍 " + entry.Label\n\t\t\t}\n\t\t\tinlineKb = append(inlineKb, map[string]interface{}{\n\t\t\t\t"text":          buttonText,\n',
    "label in Telegram list",
)
replace_once(
    '\t\t\t// Per-password upload tracking\n\t\t\tif connPassword != "" && !connIsMainPass {\n\t\t\t\tdbMutex.Lock()\n\t\t\t\te, ok := db.Passwords[connPassword]\n\t\t\t\tif !ok || e == nil || isPasswordExpired(e) || e.IsDeactivated {\n\t\t\t\t\tdbMutex.Unlock()\n\t\t\t\t\treturn\n\t\t\t\t}\n\t\t\t\te.UpBytes += int64(nn)\n\t\t\t\tdbMutex.Unlock()\n\t\t\t}\n',
    '\t\t\t// Per-password and main-password upload tracking\n\t\t\tif connPassword != "" {\n\t\t\t\tdbMutex.Lock()\n\t\t\t\tnow := time.Now().Unix()\n\t\t\t\tif connIsMainPass {\n\t\t\t\t\tdb.MainUpBytes += int64(nn)\n\t\t\t\t\tdb.MainLastUploadAt = now\n\t\t\t\t} else {\n\t\t\t\t\te, ok := db.Passwords[connPassword]\n\t\t\t\t\tif !ok || e == nil || isPasswordExpired(e) || e.IsDeactivated {\n\t\t\t\t\t\tdbMutex.Unlock()\n\t\t\t\t\t\treturn\n\t\t\t\t\t}\n\t\t\t\t\te.UpBytes += int64(nn)\n\t\t\t\t\te.LastUploadAt = now\n\t\t\t\t}\n\t\t\t\tdbMutex.Unlock()\n\t\t\t}\n',
    "main upload counter",
)
replace_once(
    '\t\t\t// Per-password download tracking\n\t\t\tif connPassword != "" && !connIsMainPass {\n\t\t\t\tdbMutex.Lock()\n\t\t\t\te, ok := db.Passwords[connPassword]\n\t\t\t\tif !ok || e == nil || isPasswordExpired(e) || e.IsDeactivated {\n\t\t\t\t\tdbMutex.Unlock()\n\t\t\t\t\treturn\n\t\t\t\t}\n\t\t\t\te.DownBytes += int64(nn)\n\t\t\t\tdbMutex.Unlock()\n\t\t\t}\n',
    '\t\t\t// Per-password and main-password download tracking\n\t\t\tif connPassword != "" {\n\t\t\t\tdbMutex.Lock()\n\t\t\t\tnow := time.Now().Unix()\n\t\t\t\tif connIsMainPass {\n\t\t\t\t\tdb.MainDownBytes += int64(nn)\n\t\t\t\t\tdb.MainLastDownloadAt = now\n\t\t\t\t} else {\n\t\t\t\t\te, ok := db.Passwords[connPassword]\n\t\t\t\t\tif !ok || e == nil || isPasswordExpired(e) || e.IsDeactivated {\n\t\t\t\t\t\tdbMutex.Unlock()\n\t\t\t\t\t\treturn\n\t\t\t\t\t}\n\t\t\t\t\te.DownBytes += int64(nn)\n\t\t\t\t\te.LastDownloadAt = now\n\t\t\t\t}\n\t\t\t\tdbMutex.Unlock()\n\t\t\t}\n',
    "main download counter",
)
replace_once(
    '\t\t\tnumDevices := len(db.Devices)\n\t\t\tdbMutex.Unlock()\n',
    '\t\t\tnumDevices := len(db.Devices)\n\t\t\tsaveDB()\n\t\t\tdbMutex.Unlock()\n',
    "periodic counter persistence",
)
replace_once(
    '\tvar waitingForHash bool\n\tvar waitingForLabel bool\n',
    '\tvar waitingForHash bool\n\tvar waitingForLabel bool\n\tvar tempLabel string\n',
    "Telegram creation label state",
)
replace_once(
    '\t\tcmds := `{"commands":[{"command":"start","description":"Главное меню"},{"command":"new","description":"Создать временный пароль"},{"command":"list","description":"Управление доступами"}]}`\n',
    '\t\tcmds := `{"commands":[{"command":"start","description":"Главное меню"},{"command":"new","description":"Создать пользователя"},{"command":"list","description":"Управление доступами"},{"command":"settings","description":"Настройки сервера"}]}`\n',
    "Telegram settings command",
)
replace_once(
    '\t\t\tif waitingForLabel {\n\t\t\t\twaitingForLabel = false\n\t\t\t\tlabel, labelErr := normalizeUserLabel(cmd)\n\t\t\t\tif labelErr != nil {\n\t\t\t\t\tsendTelegram(token, adminID, "❌ Метка должна быть не длиннее 64 символов и без служебных символов.", nil)\n\t\t\t\t\tcontinue\n\t\t\t\t}\n\t\t\t\tdbMutex.Lock()\n',
    '\t\t\tif waitingForLabel {\n\t\t\t\twaitingForLabel = false\n\t\t\t\tlabel, labelErr := normalizeUserLabel(cmd)\n\t\t\t\tif labelErr != nil {\n\t\t\t\t\tsendTelegram(token, adminID, "❌ Метка должна быть не длиннее 64 символов и без служебных символов.", nil)\n\t\t\t\t\tcontinue\n\t\t\t\t}\n\t\t\t\tif targetPassword == "__new_label__" {\n\t\t\t\t\ttempLabel = label\n\t\t\t\t\ttargetPassword = ""\n\t\t\t\t\twaitingForDays = true\n\t\t\t\t\tsendTelegram(token, adminID, "📅 Введите срок действия в днях (1–365):", nil)\n\t\t\t\t\tcontinue\n\t\t\t\t}\n\t\t\t\tdbMutex.Lock()\n',
    "Telegram creation label input",
)
replace_once(
    '\t\t\ttxt += fmt.Sprintf("%s `%s`%s (%s)\\n", status, p, labelSuffix, expiry)\n\t\t\tbuttonText := "🔍 " + p\n',
    '\t\t\tlabelPrefix := ""\n\t\t\tif entry.Label != "" {\n\t\t\t\tlabelPrefix = telegramLabel(entry.Label) + " · "\n\t\t\t}\n\t\t\ttxt += fmt.Sprintf("%s %s`%s` (%s)\\n", status, labelPrefix, p, expiry)\n\t\t\tbuttonText := "🔍 " + p\n',
    "label before password in Telegram list",
)
replace_once(
    '\t\t\t\tdb.Passwords[newPass] = &PasswordEntry{\n\t\t\t\t\tExpiresAt: expiresAt,\n\t\t\t\t\tVkHash:    hash,\n\t\t\t\t\tPorts:     tempPorts,\n\t\t\t\t}\n',
    '\t\t\t\tdb.Passwords[newPass] = &PasswordEntry{\n\t\t\t\t\tExpiresAt: expiresAt,\n\t\t\t\t\tVkHash:    hash,\n\t\t\t\t\tPorts:     tempPorts,\n\t\t\t\t\tLabel:     tempLabel,\n\t\t\t\t}\n',
    "label on Telegram creation",
)
replace_once(
    '\t\t\t\tdbMutex.Unlock()\n\t\t\t\twaitingForDays = true\n\t\t\t\tsendTelegram(token, adminID, "📅 Введите срок действия пароля в днях (1–365):\\n\\n_Примеры: 30 = месяц, 365 = год_", nil)\n',
    '\t\t\t\tdbMutex.Unlock()\n\t\t\t\ttargetPassword = "__new_label__"\n\t\t\t\twaitingForLabel = true\n\t\t\t\tsendTelegram(token, adminID, "🏷 Отправьте метку нового пользователя до 64 символов. Отправьте - без метки.", nil)\n',
    "label prompt on Telegram creation",
)
replace_once(
    '\t\t\tif cmd == "/start" || cmd == "/help" {\n\t\t\t\tsendTelegram(token, adminID, "🤖 *WDTT VPN Manager*\\n\\n/new — Создать пароль\\n/list — Список паролей", nil)\n\n\t\t\t} else if cmd == "/new" {\n',
    '\t\t\tif cmd == "/start" || cmd == "/help" {\n\t\t\t\tsendTelegram(token, adminID, "🤖 *WDTT VPN Manager*\\n\\n/new — Создать пользователя\\n/list — Список пользователей\\n/settings — Настройки сервера", nil)\n\n\t\t\t} else if cmd == "/settings" {\n\t\t\t\tsendTelegram(token, adminID, fmt.Sprintf("⚙️ *Настройки сервера*\\n\\n• DNS: `%s`\\n• MTU: `%d`\\n• Keepalive WireGuard: `%d сек.`\\n\\nНастройки маршрутизации и доступа меняются в WDTT Control Panel.", dns, wgMTU, keepalive), nil)\n\n\t\t\t} else if cmd == "/new" {\n',
    "Telegram settings response",
)
replace_once(
    '\t\t\tlabelSuffix := ""\n\t\t\tif entry.Label != "" {\n\t\t\t\tlabelSuffix = " — " + telegramLabel(entry.Label)\n\t\t\t}\n\t\t\tlabelPrefix := ""\n',
    '\t\t\tlabelPrefix := ""\n',
    "remove password-first Telegram label formatting",
)
marker = 'func getNextIP() string {'
if marker not in source:
    raise SystemExit("WDTT source changed: cannot add label validation")
helpers = '''func normalizeUserLabel(value string) (string, error) {
\tlabel := strings.TrimSpace(value)
\tif label == "-" {
\t\treturn "", nil
\t}
\tif len([]rune(label)) > 64 {
\t\treturn "", errors.New("label is too long")
\t}
\tfor _, char := range label {
\t\tif char < 32 || char == 127 {
\t\t\treturn "", errors.New("label contains a control character")
\t\t}
\t}
\treturn label, nil
}

func telegramLabel(value string) string {
\treplacer := strings.NewReplacer("\\\\", "\\\\\\\\", "_", "\\\\_", "*", "\\\\*", "`", "\\\\`", "[", "\\\\[")
\treturn replacer.Replace(value)
}

'''
source = source.replace(marker, helpers + marker, 1)
path.write_text(source, encoding="utf-8")
PY
  fi

  (
    cd "$source"
    PATH="$work/go/bin:$PATH" GOPATH="$work/gopath" GOMODCACHE="$work/gopath/pkg/mod" GOCACHE="$work/go-cache" CGO_ENABLED=0 "$work/go/bin/go" build -mod=mod -trimpath -ldflags='-s -w' -o "$work/wdtt-server" ./server
  ) >>"$LOG_FILE" 2>&1 || die "Не удалось собрать расширенный WDTT; действующий сервер не изменён"

  install -d -m 0700 "$PRIVATE_STATE_DIR"
  backup="$PRIVATE_STATE_DIR/wdtt-server-before-extension-$(date +%Y%m%d-%H%M%S)"
  install -m 0700 "$target" "$backup"
  if systemctl is-active --quiet "$WDTT_SERVICE"; then
    was_active=1
    systemctl stop "$WDTT_SERVICE" >>"$LOG_FILE" 2>&1 || die "Не удалось остановить WDTT перед обновлением"
  fi
  if [ -f /etc/wdtt/passwords.json ]; then
    database_backup="$PRIVATE_STATE_DIR/passwords-before-extension-$(date +%Y%m%d-%H%M%S).json"
    install -m 0600 /etc/wdtt/passwords.json "$database_backup"
    python3 - /etc/wdtt/passwords.json "$PRIVATE_STATE_DIR/user-labels.json" <<'PY'
import json
import os
import sys
import tempfile
from pathlib import Path

path = Path(sys.argv[1])
panel_labels_path = Path(sys.argv[2])
data = json.loads(path.read_text(encoding="utf-8"))
try:
    panel_labels = json.loads(panel_labels_path.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError):
    panel_labels = {}
if not isinstance(panel_labels, dict):
    panel_labels = {}
entry_label_fields = ("label", "remark", "name", "comment", "tag", "mark", "user_label", "userLabel", "user_name", "userName", "note", "description")
mapping_label_fields = ("labels", "remarks", "user_labels", "userLabels", "names", "comments", "tags", "marks")


def saved_label(source, password, entry):
    if not isinstance(source, dict) or not isinstance(entry, dict):
        return ""
    for key in entry_label_fields:
        value = entry.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in mapping_label_fields:
        values = source.get(key)
        value = values.get(password) if isinstance(values, dict) else None
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


sources = [data]
backup_roots = (panel_labels_path.parent, panel_labels_path.parent / "backups")
backup_paths = sorted(
    {candidate for root in backup_roots if root.is_dir() for candidate in root.glob("passwords-*.json")},
    reverse=True,
)
for candidate in backup_paths:
    try:
        backup = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        continue
    if isinstance(backup, dict):
        sources.append(backup)

recovered_labels = {}
for source in sources:
    for password, entry in (source.get("passwords") or {}).items():
        label = saved_label(source, password, entry)
        if label and password not in recovered_labels:
            recovered_labels[password] = label

changed = False
for password, entry in (data.get("passwords") or {}).items():
    if not isinstance(entry, dict) or str(entry.get("label") or "").strip():
        continue
    value = panel_labels.get(password)
    if not isinstance(value, str) or not value.strip():
        value = recovered_labels.get(password)
    if isinstance(value, str) and value.strip():
        entry["label"] = value.strip()
        changed = True
if changed:
    fd, temporary = tempfile.mkstemp(prefix="passwords.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
PY
  fi
  install -m 0755 "$work/wdtt-server" "$target.new"
  mv -f "$target.new" "$target"
  if [ "$was_active" = "1" ] && ! systemctl start "$WDTT_SERVICE" >>"$LOG_FILE" 2>&1; then
    restore_wdtt_extension_backup "$backup" "$database_backup" "$was_active"
    die "Обновлённый WDTT не запустился; прежний бинарный файл восстановлен"
  fi
  if [ "$was_active" = "1" ]; then
    sleep 2
    if ! systemctl is-active --quiet "$WDTT_SERVICE"; then
      restore_wdtt_extension_backup "$backup" "$database_backup" "$was_active"
      die "Обновлённый WDTT завершился после запуска; прежний бинарный файл и база пользователей восстановлены"
    fi
  fi
  if ! wdtt_extensions_binary_is_current; then
    restore_wdtt_extension_backup "$backup" "$database_backup" "$was_active"
    die "Собранный WDTT не прошёл проверку расширений; прежний бинарный файл восстановлен"
  fi
  if [ -n "$database_backup" ] && ! wdtt_database_preserved "$database_backup" /etc/wdtt/passwords.json; then
    restore_wdtt_extension_backup "$backup" "$database_backup" "$was_active"
    die "После обновления WDTT обнаружена потеря пользователей, устройств или данных квот; прежний бинарный файл и база восстановлены"
  fi
  rm -f "$PRIVATE_STATE_DIR/user-labels.json"
  printf '{"enabled_at": %s, "marker": "%s", "wdtt_repository": "%s", "wdtt_ref": "%s", "features": ["labels", "main_traffic", "activity", "traffic_quota", "retained_expired", "spaceneurox_v1_4_3"]}\n' "$(date +%s)" "$WDTT_EXTENSION_MARKER" "$WDTT_REPOSITORY" "$WDTT_REF" > "$PRIVATE_STATE_DIR/wdtt-extensions.json"
  chmod 0600 "$PRIVATE_STATE_DIR/wdtt-extensions.json"
  log "Расширение WDTT включено: метки общие с Telegram-ботом, трафик и последняя активность пользователей учитываются"
}

schedule_wdtt_extensions() {
  if wdtt_extensions_are_enabled; then
    log "Расширение WDTT уже установлено"
    return 0
  fi
  systemctl restart --no-block "$WDTT_EXTENSIONS_SERVICE" >>"$LOG_FILE" 2>&1 || die "Не удалось запустить автоматическое обновление WDTT"
  log "Автоматическое обновление WDTT запущено; при временной ошибке оно повторится автоматически"
}

install_vk_hash_seed() {
  local source="$USERDATA_DIR/vk-hash.txt"
  if [ -f "$source" ]; then
    install -m 0600 "$source" "$SEED_HASHES_FILE"
    log "VK-хеши из userdata/vk-hash.txt установлены в $SEED_HASHES_FILE (импорт в библиотеку при первом старте панели)"
  else
    log "userdata/vk-hash.txt не найден — библиотека VK-хешей останется пустой до ручного добавления"
  fi
}

install_panel_files() {
  [ -d "$SCRIPT_DIR/wdtt_panel" ] || die "Каталог wdtt_panel не найден рядом с install.sh"
  id -u wdtt-panel >/dev/null 2>&1 || useradd --system --home-dir "$STATE_DIR" --create-home --shell /usr/sbin/nologin wdtt-panel
  install -d -m 0755 "$INSTALL_DIR" "$CONFIG_DIR"
  install -d -o wdtt-panel -g wdtt-panel -m 0755 "$STATE_DIR"
  install -d -o root -g root -m 0700 "$PRIVATE_STATE_DIR" "$PRIVATE_STATE_DIR/backups"
  install -d -m 0755 "$STATE_DIR/acme"
  if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
    rm -rf "$INSTALL_DIR/wdtt_panel"
    cp -a "$SCRIPT_DIR/wdtt_panel" "$INSTALL_DIR/wdtt_panel"
    install -m 0755 "$SCRIPT_DIR/install.sh" "$INSTALL_DIR/install.sh"
    install -m 0755 "$SCRIPT_DIR/bootstrap.sh" "$INSTALL_DIR/bootstrap.sh"
    install -m 0755 "$SCRIPT_DIR/uninstall.sh" "$INSTALL_DIR/uninstall.sh"
    # WDTT-SERVER: замораживаем локальный форк ядра в /opt, чтобы обновления
    # панели из /opt/wdtt-panel не обращались на GitHub.
    if [ -d "$SCRIPT_DIR/../src" ] && { [ -f "$SCRIPT_DIR/../src/server/main.go" ] || [ -f "$SCRIPT_DIR/../src/proxy-turn-vk-android-1.4.3/server/main.go" ]; }; then
      rm -rf "$INSTALL_DIR/src"
      cp -a "$SCRIPT_DIR/../src" "$INSTALL_DIR/src"
    fi
  fi
  chown -R root:root "$INSTALL_DIR/wdtt_panel"
  find "$INSTALL_DIR/wdtt_panel" -type d -exec chmod 0755 {} +
  find "$INSTALL_DIR/wdtt_panel" -type f -exec chmod 0644 {} +

  cat > "$ADMIN_WRAPPER" <<EOF
#!/bin/sh
cd /opt/wdtt-panel || exit 1
exec $PYTHON3_BIN -m wdtt_panel.admin
EOF
  chown root:root "$ADMIN_WRAPPER"
  chmod 0755 "$ADMIN_WRAPPER"

  if [ "$PANEL_RUN_AS_ROOT" = "1" ]; then
    rm -f "$SUDOERS_FILE"
    log "sudo-правило не создаётся: панель работает от root (юнит принудительно включает NoNewPrivileges)"
  else
    printf 'wdtt-panel ALL=(root) NOPASSWD: %s\n' "$ADMIN_WRAPPER" > "$SUDOERS_FILE"
    chown root:root "$SUDOERS_FILE"
    chmod 0440 "$SUDOERS_FILE"
    visudo -cf "$SUDOERS_FILE" >>"$LOG_FILE"
  fi
  install_vk_hash_seed
}

# WDTT-SERVER: запоминает каталог git-репозитория для веб-обновления, чтобы
# панель могла выполнить git reset --hard. Срабатывает только когда install.sh запущен
# из самого репозитория (рядом есть .git и корневой install.sh).
record_source_repo() {
  local repo
  repo="$(cd -- "$SCRIPT_DIR/.." 2>/dev/null && pwd || true)"
  if [ -z "$repo" ] || [ ! -d "$repo/.git" ] || [ ! -f "$repo/install.sh" ]; then
    return 0
  fi
  install -d -m 0755 "$CONFIG_DIR"
  printf 'WDTT_REPO_DIR=%s\n' "$(printf '%q' "$repo")" > "$SOURCE_CONF_FILE"
  chmod 0644 "$SOURCE_CONF_FILE"
  log "Источник веб-обновления: $repo"
}

# WDTT-SERVER: обёртка самообновления. Запускается вне systemd-песочницы панели
# (через systemd-run) от root и умеет два режима:
#   check  — git fetch и запись доступной версии в self-update-status.json;
#   update — git reset --hard до origin, install.sh update (конфиг сохраняется)
#            и полный перезапуск. Историю не мержит, а перезаписывает.
# Панель лишь читает статус и запускает обёртку, поэтому её sandbox не мешает.
write_self_update_wrapper() {
  cat > "$SELF_UPDATE_WRAPPER" <<'EOF'
#!/bin/sh
# WDTT-SERVER panel self-update helper (check|update). Runs as root outside the
# panel sandbox. Поведение: git reset --hard -> install.sh update -> restart служб.
set -eu

SOURCE_CONF="/etc/wdtt-panel/source.conf"
CONFIG_FILE="/etc/wdtt-panel/config.json"
STATUS_FILE="/var/lib/wdtt-panel-private/self-update-status.json"
LOG_FILE="/var/log/wdtt-panel-self-update.log"
MODE="${1:-update}"

# install.sh update перезаписывает этот самый файл. Чтобы продолжение работы не
# сломалось на перечитывании изменившегося файла, для режима update копируем себя
# в /tmp и перезапускаемся уже оттуда — оригинал спокойно перезапишет установщик.
if [ "$MODE" = "update" ] && [ "${WDTT_SELF_UPDATE_RELOCATED:-}" != "1" ]; then
  RELOCATED="$(mktemp "${TMPDIR:-/tmp}/wdtt-panel-self-update.XXXXXX")"
  cp "$0" "$RELOCATED"
  chmod 0700 "$RELOCATED"
  WDTT_SELF_UPDATE_RELOCATED=1 WDTT_SELF_UPDATE_FILE="$RELOCATED" exec sh "$RELOCATED" "$MODE"
fi
WORK_DIR=""
cleanup() {
  [ -z "${WORK_DIR:-}" ] || rm -rf "$WORK_DIR"
  [ -z "${WDTT_SELF_UPDATE_FILE:-}" ] || rm -f "$WDTT_SELF_UPDATE_FILE"
}
trap cleanup EXIT

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >>"$LOG_FILE" 2>/dev/null || true
}

set_status() {
  # state current latest available message
  python3 - "$STATUS_FILE" "$1" "$2" "$3" "$4" "$5" <<'PY'
import json, os, sys, time
path, state, current, latest, available, message = sys.argv[1:7]
data = {
    "state": state,
    "current": current,
    "latest": latest,
    "update_available": available == "1",
    "message": message,
    "checked_at": int(time.time()),
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False)
    handle.write("\n")
os.chmod(tmp, 0o600)
os.replace(tmp, path)
PY
}

current_version() {
  python3 - "$CONFIG_FILE" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        print(json.load(handle).get("version", ""))
except Exception:
    print("")
PY
}

version_gt() {
  # Истина, если версия $1 новее $2.
  [ "$1" != "$2" ] && [ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | tail -n 1)" = "$1" ]
}

resolve_repo() {
  [ -r "$SOURCE_CONF" ] || return 1
  # shellcheck disable=SC1090
  . "$SOURCE_CONF"
  [ -n "${WDTT_REPO_DIR:-}" ] || return 1
  [ -d "$WDTT_REPO_DIR/.git" ] || return 1
  printf '%s' "$WDTT_REPO_DIR"
}

robust_fetch() {
  # Принудительно перезаписывает remote-tracking ветки: переживает force-push
  # (история переписана) и гонку двух процессов check/update, когда второй
  # падает с "cannot lock ref ... but expected ...".
  attempt=1
  while [ "$attempt" -le 5 ]; do
    if git -C "$REPO" fetch --force --prune origin "+refs/heads/*:refs/remotes/origin/*" >>"$LOG_FILE" 2>&1; then
      return 0
    fi
    sleep 2
    attempt=$((attempt + 1))
  done
  return 1
}

mkdir -p "$(dirname "$STATUS_FILE")"

CUR="$(current_version)"
REPO="$(resolve_repo || true)"

case "$MODE" in
  check)
    if [ -z "$REPO" ]; then
      set_status error "$CUR" "" 0 "Репозиторий не найден (WDTT_REPO_DIR в $SOURCE_CONF)"
      exit 1
    fi
    if ! command -v git >/dev/null 2>&1; then
      set_status error "$CUR" "" 0 "git не установлен"
      exit 1
    fi
    log "check: git fetch в $REPO"
    set_status checking "$CUR" "" 0 "Проверка обновлений"
    if ! robust_fetch; then
      set_status error "$CUR" "" 0 "git fetch не удался (см. $LOG_FILE)"
      exit 1
    fi
    UPSTREAM="$(git -C "$REPO" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
    [ -n "$UPSTREAM" ] || UPSTREAM="origin/$(git -C "$REPO" symbolic-ref --short HEAD 2>/dev/null || echo main)"
    LATEST="$(git -C "$REPO" show "$UPSTREAM:panel/install.sh" 2>/dev/null | sed -n 's/^PANEL_VERSION="\([^"]*\)".*$/\1/p' | head -n 1 || true)"
    if [ -z "$LATEST" ]; then
      set_status error "$CUR" "" 0 "Не удалось определить версию в $UPSTREAM"
      exit 1
    fi
    if version_gt "$LATEST" "$CUR"; then
      set_status ok "$CUR" "$LATEST" 1 ""
    else
      set_status ok "$CUR" "$LATEST" 0 ""
    fi
    log "check: установлено $CUR, в репозитории $LATEST"
    ;;
  update)
    if [ -z "$REPO" ]; then
      set_status error "$CUR" "" 0 "Репозиторий не найден (WDTT_REPO_DIR в $SOURCE_CONF)"
      exit 1
    fi
    set_status running "$CUR" "" 0 "Обновление запущено"
    BRANCH="$(git -C "$REPO" symbolic-ref --short HEAD 2>/dev/null || echo main)"
    UPSTREAM="origin/$BRANCH"
    log "update: git fetch в $REPO"
    if ! robust_fetch; then
      set_status error "$CUR" "" 0 "git fetch не удался (см. $LOG_FILE)"
      exit 1
    fi
    # Историю не мержим, а перезаписываем: reset --hard переживает force-push,
    # разошедшиеся ветки и любые правки в отслеживаемых файлах. Неотслеживаемые
    # файлы (userdata, бэкапы) не трогаем — чистку через clean не делаем.
    log "update: сброс рабочего дерева до $UPSTREAM"
    if ! git -C "$REPO" reset --hard "$UPSTREAM" >>"$LOG_FILE" 2>&1; then
      set_status error "$CUR" "" 0 "git reset не удался (см. $LOG_FILE)"
      exit 1
    fi
    log "update: install.sh update"
    if ! bash "$REPO/install.sh" update >>"$LOG_FILE" 2>&1; then
      set_status error "$CUR" "" 0 "install.sh update завершился ошибкой (см. $LOG_FILE)"
      exit 1
    fi
    NEW="$(current_version)"
    set_status done "$NEW" "$NEW" 0 "Панель обновлена до v$NEW"
    log "update: перезапуск панели, ядра и демонов"
    bash "$REPO/install.sh" restart >>"$LOG_FILE" 2>&1 || true
    log "update: готово (v$NEW)"
    ;;
  *)
    echo "Usage: $0 [check|update]" >&2
    exit 2
    ;;
esac
EOF
  chown root:root "$SELF_UPDATE_WRAPPER"
  chmod 0755 "$SELF_UPDATE_WRAPPER"
}

write_maintenance_scripts() {
  record_source_repo
  rm -f "$MANAGER_WRAPPER" /usr/local/sbin/wddt-panel /usr/local/sbin/wdtt-pane
  install -m 0755 "$INSTALL_DIR/bootstrap.sh" "$MANAGER_WRAPPER"
  rm -f "$UPDATE_WRAPPER"
  write_self_update_wrapper
  ln -sfn "$INSTALL_DIR/uninstall.sh" "$UNINSTALL_WRAPPER"
  cat > "$STATUS_WRAPPER" <<EOF
#!/bin/sh
exec /bin/bash $INSTALL_DIR/install.sh status
EOF
  chmod 0755 "$STATUS_WRAPPER"
  cat > "$GEOFILES_UPDATE_WRAPPER" <<EOF
#!/bin/sh
printf '%s\n' '{"action":"xray.geofiles.refresh_auto","payload":{}}' | $ADMIN_WRAPPER
EOF
  chmod 0755 "$GEOFILES_UPDATE_WRAPPER"
  cat > "$BACKUP_RUNNER" <<EOF
#!/bin/sh
case "\${1:-full}" in
  full) printf '%s\n' '{"action":"backups.create","payload":{"type":"full","scheduled":true}}' | $ADMIN_WRAPPER ;;
  users) printf '%s\n' '{"action":"backups.create","payload":{"type":"users","scheduled":true}}' | $ADMIN_WRAPPER ;;
  *) exit 2 ;;
esac
EOF
  chmod 0755 "$BACKUP_RUNNER"
  cat > "$AUTOCLEAN_RUNNER" <<EOF
#!/bin/sh
printf '%s\n' '{"action":"autoclean.run","payload":{}}' | $ADMIN_WRAPPER
EOF
  chmod 0755 "$AUTOCLEAN_RUNNER"
  cat > "$CASCADE_RULES_WRAPPER" <<EOF
#!/bin/sh
case "\${1:-apply}" in
  apply) printf '%s\n' '{"action":"cascade.apply","payload":{}}' | $ADMIN_WRAPPER ;;
  remove) printf '%s\n' '{"action":"cascade.remove","payload":{}}' | $ADMIN_WRAPPER ;;
  *) exit 2 ;;
esac
EOF
  chmod 0755 "$CASCADE_RULES_WRAPPER"
  cat > "$GATEWAY_RULES_WRAPPER" <<EOF
#!/bin/sh
case "\${1:-apply}" in
  apply) printf '%s\n' '{"action":"xray.gateway.apply","payload":{}}' | $ADMIN_WRAPPER ;;
  remove) printf '%s\n' '{"action":"xray.gateway.remove","payload":{}}' | $ADMIN_WRAPPER ;;
  *) exit 2 ;;
esac
EOF
  chmod 0755 "$GATEWAY_RULES_WRAPPER"
}

# WDTT-SERVER: полный перезапуск панели, ядра WDTT и демонов. Используется
# веб-обновлением (обёртка self-update) и командой `install.sh restart`.
restart_services() {
  require_root
  log "Перезапуск панели, ядра WDTT и демонов"
  local unit
  for unit in "$WDTT_SERVICE" "$PANEL_SERVICE" wdtt-app.service; do
    systemctl restart "$unit" >>"$LOG_FILE" 2>&1 || true
  done
  for unit in nginx.service "$XRAY_SERVICE" "$XRAY_GATEWAY_SERVICE" "$XRAY_CASCADE_SERVICE" "$LEGACY_CASCADE_SERVICE"; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      systemctl restart "$unit" >>"$LOG_FILE" 2>&1 || true
    fi
  done
  for unit in wdtt-auto-restart.timer wdtt-panel-cert-renew.timer "$WDTT_EXTENSIONS_TIMER" "$AUTOCLEAN_TIMER" wdtt-panel-backup.timer; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      systemctl restart "$unit" >>"$LOG_FILE" 2>&1 || true
    fi
  done
  log "Перезапуск завершён"
}

backup_wdtt_database_before_update() {
  [ -f /etc/wdtt/passwords.json ] || return 0
  install -d -m 0700 "$PRIVATE_STATE_DIR"
  local snapshot="$PRIVATE_STATE_DIR/passwords-before-panel-update-$(date +%Y%m%d-%H%M%S).json"
  install -m 0600 /etc/wdtt/passwords.json "$snapshot"
  log "Создан снимок базы WDTT перед обновлением: $(basename "$snapshot")"
}

# WDTT-SERVER: снимок конфигурации панели перед обновлением, чтобы можно было
# откатиться, если миграция конфига на новую версию пойдёт не так.
backup_panel_config_before_update() {
  [ -f "$CONFIG_FILE" ] || return 0
  local dir="$PRIVATE_STATE_DIR/config-backups" stamp target
  install -d -m 0700 "$dir"
  stamp="$(date +%Y%m%d-%H%M%S)"
  target="$dir/config-before-$PANEL_VERSION-$stamp.json"
  install -m 0600 "$CONFIG_FILE" "$target"
  log "Создан снимок конфигурации панели: $target"
  # Держим не больше 20 последних копий конфига.
  ls -1t "$dir"/config-before-*.json 2>/dev/null | tail -n +21 | xargs -r rm -f
}

# WDTT-SERVER: переносит существующий config.json на текущую версию панели:
# досыпает недостающие поля значениями по умолчанию, чинит типы портов и
# обновляет поле version. Пароль и session_secret сохраняются.
migrate_panel_config() {
  if ! python3 - "$CONFIG_FILE" "$PANEL_VERSION" <<'PY'
import json, os, sys

path, version = sys.argv[1:]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
if not isinstance(data, dict):
    raise SystemExit("config.json повреждён")

missing_secrets = [key for key in ("password_hash", "session_secret") if not data.get(key)]
if missing_secrets:
    raise SystemExit("config.json неполон: нет " + ", ".join(missing_secrets))

defaults = {
    "username": "admin",
    "base_path": "/",
    "public_host": "",
    "https_port": 9999,
    "listen_host": "127.0.0.1",
    "listen_port": 8787,
    "certificate_path": "",
    "tls_mode": "self-signed",
    "certificate_email": "",
}
added = []
for key, value in defaults.items():
    if key not in data:
        data[key] = value
        added.append(key)
for key in ("https_port", "listen_port"):
    try:
        data[key] = int(data[key])
    except (TypeError, ValueError):
        data[key] = defaults[key]
data["version"] = version

tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
if added:
    print("Перенесены поля конфига: " + ", ".join(added))
PY
  then
    die "Не удалось перенести конфигурацию панели на версию $PANEL_VERSION — проверьте $CONFIG_FILE (снимок в $PRIVATE_STATE_DIR/config-backups)"
  fi
  chown root:wdtt-panel "$CONFIG_FILE"
  chmod 0640 "$CONFIG_FILE"
}

write_xray_services() {
  systemctl disable --now "$LEGACY_CASCADE_SERVICE" wdtt-panel-geofiles-update.timer wdtt-panel-geofiles-update.service 2>/dev/null || true
  rm -f "/etc/systemd/system/$LEGACY_CASCADE_SERVICE"
  install -d -m 0700 "$XRAY_ASSETS"

  cat > "/etc/systemd/system/$XRAY_SERVICE" <<EOF
[Unit]
Description=WDTT Xray Routing Runtime
After=network-online.target wdtt.service
Wants=network-online.target
ConditionPathExists=$XRAY_CONFIG

[Service]
Type=simple
User=root
Environment=XRAY_LOCATION_ASSET=$XRAY_ASSETS
ExecStartPre=/usr/local/bin/xray run -test -c $XRAY_CONFIG
ExecStart=/usr/local/bin/xray run -c $XRAY_CONFIG
Restart=on-failure
RestartSec=3
LimitNOFILE=1048576
AmbientCapabilities=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_ADMIN CAP_NET_BIND_SERVICE CAP_NET_RAW
NoNewPrivileges=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$PRIVATE_STATE_DIR /run

[Install]
WantedBy=multi-user.target
EOF

  cat > "/etc/systemd/system/$XRAY_CASCADE_SERVICE" <<EOF
[Unit]
Description=WDTT Xray RU to EU Cascade Rules
After=network-online.target wdtt.service $XRAY_SERVICE
Wants=network-online.target
ConditionPathExists=$XRAY_CASCADE_SETTINGS

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$CASCADE_RULES_WRAPPER apply
ExecStop=$CASCADE_RULES_WRAPPER remove

[Install]
WantedBy=multi-user.target
EOF

  cat > "/etc/systemd/system/$XRAY_GATEWAY_SERVICE" <<EOF
[Unit]
Description=WDTT traffic gateway to Xray
After=network-online.target wdtt.service $XRAY_SERVICE
Wants=network-online.target
ConditionPathExists=$XRAY_SETTINGS

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=$GATEWAY_RULES_WRAPPER apply
ExecStop=$GATEWAY_RULES_WRAPPER remove

[Install]
WantedBy=multi-user.target
EOF

  cat > /etc/systemd/system/wdtt-panel-geofiles-update.service <<EOF
[Unit]
Description=Update WDTT Panel Xray GeoFiles
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$GEOFILES_UPDATE_WRAPPER
EOF
  cat > /etc/systemd/system/wdtt-panel-geofiles-update.timer <<'EOF'
[Unit]
Description=Automatic WDTT Panel Xray GeoFiles updates

[Timer]
OnBootSec=20min
OnUnitActiveSec=6h
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now wdtt-panel-geofiles-update.timer >>"$LOG_FILE" 2>&1
}

github_asset_url() {
  local repository="$1" pattern="$2"
  python3 - "$repository" "$pattern" <<'PY'
import json, re, sys, urllib.request
repo, pattern = sys.argv[1:]
request = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases/latest",
    headers={"User-Agent": "wdtt-control-panel"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
for asset in release.get("assets", []):
    if re.search(pattern, asset.get("name", "")):
        print(asset["browser_download_url"])
        raise SystemExit
raise SystemExit(2)
PY
}

install_xray_runtime() {
  require_root
  local machine asset_pattern work xray_url bundled_asset
  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64) asset_pattern='Xray-linux-64\.zip$' ;;
    aarch64|arm64) asset_pattern='Xray-linux-arm64-v8a\.zip$' ;;
    *) die "Xray поддержан для amd64 и arm64" ;;
  esac
  work="$(mktemp -d)"
  trap 'rm -rf "${work:-}"' RETURN

  log "Установка Xray Core"
  xray_url="$(github_asset_url XTLS/Xray-core "$asset_pattern")" || die "Не найден релиз Xray Core"
  curl -fsSL --retry 3 "$xray_url" -o "$work/xray.zip"
  unzip -q "$work/xray.zip" -d "$work/xray"
  install -m 0755 "$(find "$work/xray" -type f \( -name Xray -o -name xray \) | head -1)" /usr/local/bin/xray
  install -d -m 0700 "$XRAY_ASSETS"
  for bundled_asset in geoip.dat geosite.dat; do
    if [ ! -f "$XRAY_ASSETS/$bundled_asset" ] && [ -f "$work/xray/$bundled_asset" ]; then
      install -m 0600 "$work/xray/$bundled_asset" "$XRAY_ASSETS/$bundled_asset"
    fi
  done
  write_xray_services
  if [ -r "$XRAY_SETTINGS" ] && python3 -c 'import json,sys; raise SystemExit(0 if json.load(open(sys.argv[1])).get("enabled") else 1)' "$XRAY_SETTINGS"; then
    systemctl enable --now "$XRAY_SERVICE" >>"$LOG_FILE" 2>&1
  fi
  log "Xray Core установлен"
  /usr/local/bin/xray version | head -1
}

install_warp_runtime() {
  require_root
  local machine asset_pattern warp_url work
  machine="$(uname -m)"
  case "$machine" in
    x86_64|amd64) asset_pattern='wgcf_[^/]*_linux_amd64$' ;;
    aarch64|arm64) asset_pattern='wgcf_[^/]*_linux_arm64$' ;;
    *) die "Cloudflare WARP поддержан для amd64 и arm64" ;;
  esac
  work="$(mktemp -d)"
  trap 'rm -rf "${work:-}"' RETURN
  log "Установка wgcf для Cloudflare WARP"
  warp_url="$(github_asset_url ViRb3/wgcf "$asset_pattern")" || die "Не найден релиз wgcf"
  curl -fsSL --retry 3 "$warp_url" -o "$work/wgcf"
  install -m 0755 "$work/wgcf" /usr/local/bin/wgcf
  install -d -m 0700 "$WARP_DIR"
  log "Компонент Cloudflare WARP установлен"
  /usr/local/bin/wgcf --version || true
}

write_panel_config() {
  PASSWORD_HASH="$(PYTHONPATH="$INSTALL_DIR" python3 -c 'import sys; from wdtt_panel.security import hash_password; print(hash_password(sys.argv[1]))' "$PANEL_PASSWORD")"
  python3 - "$CONFIG_FILE" "$PANEL_VERSION" "$PANEL_USER" "$PASSWORD_HASH" "$SESSION_SECRET" "$PANEL_PATH" "$PANEL_HOST" "$PANEL_HTTPS_PORT" "$PANEL_LISTEN_PORT" "$CERTIFICATE_PATH" "$TLS_MODE" "$PANEL_EMAIL" <<'PY'
import json, os, sys
path, version, username, password_hash, session_secret, base_path, public_host, https_port, listen_port, certificate_path, tls_mode, certificate_email = sys.argv[1:]
data = {
    "version": version,
    "username": username,
    "password_hash": password_hash,
    "session_secret": session_secret,
    "base_path": base_path,
    "public_host": public_host,
    "https_port": int(https_port),
    "listen_host": "127.0.0.1",
    "listen_port": int(listen_port),
    "certificate_path": certificate_path,
    "tls_mode": tls_mode,
    "certificate_email": certificate_email,
}
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
  chown root:wdtt-panel "$CONFIG_FILE"
  chmod 0640 "$CONFIG_FILE"
}

update_panel_config_metadata() {
  python3 - "$CONFIG_FILE" "$PANEL_VERSION" "${CERTIFICATE_PATH:-}" "${TLS_MODE:-}" "${PANEL_EMAIL:-}" <<'PY'
import json, os, sys
path, version, certificate_path, tls_mode, certificate_email = sys.argv[1:]
data = json.load(open(path, encoding="utf-8"))
data["version"] = version
if certificate_path:
    data["certificate_path"] = certificate_path
if tls_mode:
    data["tls_mode"] = tls_mode
if certificate_email:
    data["certificate_email"] = certificate_email
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
  chown root:wdtt-panel "$CONFIG_FILE"
  chmod 0640 "$CONFIG_FILE"
}

write_panel_service() {
  local panel_user_lines="User=wdtt-panel
Group=wdtt-panel"
  local panel_admin_env=""
  if [ "$PANEL_RUN_AS_ROOT" = "1" ]; then
    panel_user_lines="User=root
Group=root"
    panel_admin_env="Environment=WDTT_PANEL_ADMIN=$ADMIN_WRAPPER"
  fi
  cat > "/etc/systemd/system/$PANEL_SERVICE" <<EOF
[Unit]
Description=WDTT Web Control Panel
After=network.target wdtt.service
Wants=network-online.target

[Service]
Type=simple
$panel_user_lines
$panel_admin_env
WorkingDirectory=$INSTALL_DIR
ExecStart=$PYTHON3_BIN -m wdtt_panel.app
Restart=on-failure
RestartSec=3
UMask=0027
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=$STATE_DIR $PRIVATE_STATE_DIR -/etc/wdtt
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK
LockPersonality=true

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$PANEL_SERVICE" >>"$LOG_FILE" 2>&1
}

remove_obsolete_fleet_agent() {
  systemctl disable --now wdtt-fleet-agent.service >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/wdtt-fleet-agent.service "$STATE_DIR/fleet-agent.json"
  systemctl daemon-reload
}

remove_obsolete_openwrt_podkop() {
  local changed
  changed="$(python3 - "$XRAY_CONFIG" "$XRAY_SETTINGS" <<'PY'
import json
import sys
from pathlib import Path

config_path = Path(sys.argv[1])
settings_path = Path(sys.argv[2])
changed = False

def load_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None

def save_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

settings = load_json(settings_path) if settings_path.is_file() else None
if isinstance(settings, dict):
    for key in ("podkop_native_enabled", "podkop_inbound_port", "podkop_ws_path"):
        if key in settings:
            settings.pop(key, None)
            changed = True
    if changed:
        save_json(settings_path, settings)

config_changed = False
config = load_json(config_path) if config_path.is_file() else None
if isinstance(config, dict) and isinstance(config.get("inbounds"), list):
    inbounds = config["inbounds"]
    filtered = [item for item in inbounds if not (isinstance(item, dict) and item.get("tag") == "podkop-plus-in")]
    if len(filtered) != len(inbounds):
        config["inbounds"] = filtered
        config_changed = True
        changed = True
if config_changed:
    save_json(config_path, config)

print("changed" if changed else "unchanged")
PY
)"
  if [ "$changed" = "changed" ]; then
    log "Удалена устаревшая OpenWrt/Podkop Plus настройка Xray"
    if systemctl is-active --quiet "$XRAY_SERVICE"; then
      systemctl restart "$XRAY_SERVICE" >/dev/null 2>&1 || true
    fi
  fi
}

port_80_available_for_nginx() {
  local listeners
  listeners="$(ss -ltnp '( sport = :80 )' 2>/dev/null || true)"
  ! grep -q LISTEN <<<"$listeners" && return 0
  grep -qi nginx <<<"$listeners"
}

write_acme_nginx() {
  cat > "$NGINX_FILE" <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name $PANEL_HOST;
    location ^~ /.well-known/acme-challenge/ { root $STATE_DIR/acme; }
    location / { return 404; }
}
EOF
  nginx -t >>"$LOG_FILE" 2>&1 || { log "Nginx не принял временную ACME-конфигурацию"; return 1; }
  systemctl enable --now nginx >>"$LOG_FILE" 2>&1 || { log "Не удалось запустить Nginx для ACME"; return 1; }
  systemctl reload nginx >>"$LOG_FILE" 2>&1 || { log "Не удалось применить временную ACME-конфигурацию Nginx"; return 1; }
}

# Старый ufw (Debian 9 / Astra 2.12, ufw 0.35) не знает ключевое слово comment:
# пробуем с комментарием, при неудаче — без него, иначе порт молча остаётся закрыт.
ufw_allow() {
  local spec="$1" comment="$2"
  if ufw allow "$spec" comment "$comment" >/dev/null 2>&1; then
    return 0
  fi
  ufw allow "$spec" >/dev/null 2>&1 || log "WARN: ufw не открыл $spec"
}

open_acme_firewall() {
  if command_exists ufw && ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw_allow "80/tcp" "WDTT Panel ACME"
  elif command_exists firewall-cmd && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port=80/tcp >/dev/null || true
    firewall-cmd --reload >/dev/null || true
  elif command_exists iptables; then
    iptables -C INPUT -p tcp --dport 80 -m comment --comment WDTT_PANEL -j ACCEPT 2>/dev/null || \
      iptables -I INPUT -p tcp --dport 80 -m comment --comment WDTT_PANEL -j ACCEPT || true
  fi
}

install_certbot() {
  if [ ! -x "$INSTALL_DIR/certbot/bin/certbot" ]; then
    python3 -m venv "$INSTALL_DIR/certbot" >>"$LOG_FILE" 2>&1 || return 1
    "$INSTALL_DIR/certbot/bin/pip" install --upgrade pip >>"$LOG_FILE" 2>&1 || return 1
    "$INSTALL_DIR/certbot/bin/pip" install 'certbot>=5.4,<6' >>"$LOG_FILE" 2>&1 || return 1
  fi
}

request_certificate() {
  CERTIFICATE_PATH=""
  TLS_MODE="self-signed"
  port_80_available_for_nginx || { log "Порт 80 занят не Nginx: публичный сертификат пропущен"; return 1; }
  write_acme_nginx || return 1
  open_acme_firewall
  if ! run_certbot_request; then
    log "Не удалось получить Let's Encrypt: убедитесь, что $PANEL_HOST доступен из интернета по TCP 80; подробности в $LOG_FILE"
    return 1
  fi
}

run_certbot_request() {
  local nginx_was_active=0 certificate_ok=0
  local -a CERTBOT_OPTIONS
  install_certbot || return 1
  CERTBOT_OPTIONS=(--non-interactive --agree-tos)
  if [ -n "$PANEL_EMAIL" ]; then CERTBOT_OPTIONS+=(--email "$PANEL_EMAIL"); else CERTBOT_OPTIONS+=(--register-unsafely-without-email); fi
  if [[ "$PANEL_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    CERTBOT_OPTIONS+=(--preferred-profile shortlived --ip-address "$PANEL_HOST" --cert-name "$PANEL_HOST")
  else
    CERTBOT_OPTIONS+=(-d "$PANEL_HOST")
  fi

  CERTBOT=("$INSTALL_DIR/certbot/bin/certbot" certonly "${CERTBOT_OPTIONS[@]}" --webroot --webroot-path "$STATE_DIR/acme")
  if "${CERTBOT[@]}" >>"$LOG_FILE" 2>&1; then
    CERTIFICATE_PATH="/etc/letsencrypt/live/$PANEL_HOST/fullchain.pem"
    PRIVATE_KEY_PATH="/etc/letsencrypt/live/$PANEL_HOST/privkey.pem"
    [ -f "$CERTIFICATE_PATH" ] && [ -f "$PRIVATE_KEY_PATH" ] || return 1
    TLS_MODE="letsencrypt"
    return 0
  fi

  log "Webroot-проверка Let's Encrypt не прошла; используется временный standalone режим на TCP 80"
  if systemctl is-active --quiet nginx; then
    nginx_was_active=1
    systemctl stop nginx >>"$LOG_FILE" 2>&1 || { log "Не удалось временно остановить Nginx для Certbot"; return 1; }
  fi
  CERTBOT=("$INSTALL_DIR/certbot/bin/certbot" certonly "${CERTBOT_OPTIONS[@]}" --standalone --preferred-challenges http)
  if "${CERTBOT[@]}" >>"$LOG_FILE" 2>&1; then
    CERTIFICATE_PATH="/etc/letsencrypt/live/$PANEL_HOST/fullchain.pem"
    PRIVATE_KEY_PATH="/etc/letsencrypt/live/$PANEL_HOST/privkey.pem"
    if [ -f "$CERTIFICATE_PATH" ] && [ -f "$PRIVATE_KEY_PATH" ]; then
      TLS_MODE="letsencrypt"
      certificate_ok=1
    fi
  fi
  if [ "$nginx_was_active" = "1" ]; then
    systemctl start nginx >>"$LOG_FILE" 2>&1 || { log "Не удалось вернуть Nginx после standalone проверки Certbot"; return 1; }
  fi
  [ "$certificate_ok" = "1" ]
}

try_upgrade_certificate() {
  port_80_available_for_nginx || { log "Публичный сертификат не запрошен: TCP 80 занят не Nginx"; return 1; }
  [ -r "$NGINX_FILE" ] || { log "Публичный сертификат не запрошен: не найден $NGINX_FILE"; return 1; }
  grep -q '/.well-known/acme-challenge/' "$NGINX_FILE" || { log "Публичный сертификат не запрошен: в Nginx отсутствует ACME location"; return 1; }
  open_acme_firewall
  if ! run_certbot_request; then
    log "Не удалось получить Let's Encrypt: проверьте DNS и публичную доступность $PANEL_HOST:80; подробности в $LOG_FILE"
    return 1
  fi
}

create_self_signed_certificate() {
  install -d -m 0700 "$CONFIG_DIR/tls"
  CERTIFICATE_PATH="$CONFIG_DIR/tls/fullchain.pem"
  PRIVATE_KEY_PATH="$CONFIG_DIR/tls/privkey.pem"
  if [[ "$PANEL_HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then SAN="IP:$PANEL_HOST"; else SAN="DNS:$PANEL_HOST"; fi
  openssl req -x509 -newkey rsa:3072 -sha256 -days 365 -nodes \
    -keyout "$PRIVATE_KEY_PATH" -out "$CERTIFICATE_PATH" \
    -subj "/CN=$PANEL_HOST" -addext "subjectAltName=$SAN" >>"$LOG_FILE" 2>&1
  chmod 0600 "$PRIVATE_KEY_PATH"
  chmod 0644 "$CERTIFICATE_PATH"
  TLS_MODE="self-signed"
}

write_final_nginx() {
  HTTP_BLOCK=""
  HSTS_HEADER=""
  HTTP_ENABLED=0
  if [ "$TLS_MODE" = "letsencrypt" ]; then
    HSTS_HEADER='    add_header Strict-Transport-Security "max-age=31536000" always;'
  fi
  if port_80_available_for_nginx; then
    HTTP_ENABLED=1
    HTTP_BLOCK="server {
    listen 80;
    listen [::]:80;
    server_name $PANEL_HOST;
    location ^~ /.well-known/acme-challenge/ { root $STATE_DIR/acme; }
    location ^~ /client/ { return 302 https://$PANEL_HOST:$PANEL_HTTPS_PORT\$request_uri; }
    location / { return 302 https://$PANEL_HOST:$PANEL_HTTPS_PORT$PANEL_PATH; }
}"
  fi
  cat > "$NGINX_FILE" <<EOF
$HTTP_BLOCK
server {
    listen $PANEL_HTTPS_PORT ssl;
    listen [::]:$PANEL_HTTPS_PORT ssl;
    server_name $PANEL_HOST;

    ssl_certificate $CERTIFICATE_PATH;
    ssl_certificate_key $PRIVATE_KEY_PATH;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_session_timeout 1d;
    ssl_session_cache shared:WDTTTLS:10m;
$HSTS_HEADER

    location = ${PANEL_PATH%/} { return 302 $PANEL_PATH; }
    location ^~ $PANEL_PATH {
        proxy_pass http://127.0.0.1:$PANEL_LISTEN_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 75s;
        client_max_body_size 90m;
    }
    location ^~ /client/ {
        proxy_pass http://127.0.0.1:$PANEL_LISTEN_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 30s;
    }
    location / { return 404; }
}
EOF
  nginx -t >>"$LOG_FILE" 2>&1 || die "Ошибка конфигурации Nginx, см. $LOG_FILE"
  systemctl enable --now nginx >>"$LOG_FILE" 2>&1
  systemctl reload nginx >>"$LOG_FILE" 2>&1
}

write_renew_timer() {
  cat > /etc/systemd/system/wdtt-panel-cert-renew.service <<EOF
[Unit]
Description=Renew WDTT Panel TLS certificate
After=network-online.target nginx.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=/bin/bash $INSTALL_DIR/install.sh renew-cert
EOF
  cat > /etc/systemd/system/wdtt-panel-cert-renew.timer <<'EOF'
[Unit]
Description=Frequent renewal check for WDTT Panel certificates

[Timer]
OnBootSec=15min
OnUnitActiveSec=12h
RandomizedDelaySec=30min
Persistent=true

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now wdtt-panel-cert-renew.timer >>"$LOG_FILE" 2>&1
}

write_autoclean_timer() {
  cat > "/etc/systemd/system/$AUTOCLEAN_SERVICE" <<EOF
[Unit]
Description=Clean up WDTT Panel logs and stale backups when disk is low
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$AUTOCLEAN_RUNNER
EOF
  cat > "/etc/systemd/system/$AUTOCLEAN_TIMER" <<EOF
[Unit]
Description=Periodic WDTT Panel disk cleanup check

[Timer]
OnBootSec=10min
OnUnitActiveSec=30min
RandomizedDelaySec=2min
Persistent=true
Unit=$AUTOCLEAN_SERVICE

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$AUTOCLEAN_TIMER" >>"$LOG_FILE" 2>&1
}

renew_certificates() {
  local renewal_file nginx_was_active=0 renewal_ok=0
  require_root
  load_panel_config

  if [ "$TLS_MODE" = "letsencrypt" ]; then
    install_certbot || die "Не удалось подготовить Certbot"
    open_acme_firewall
    renewal_file="/etc/letsencrypt/renewal/$PANEL_HOST.conf"
    if [ -r "$renewal_file" ] && grep -q '^authenticator = standalone$' "$renewal_file"; then
      if systemctl is-active --quiet nginx; then
        nginx_was_active=1
        systemctl stop nginx >>"$LOG_FILE" 2>&1 || die "Не удалось временно остановить Nginx для продления сертификата"
      fi
    fi
    if "$INSTALL_DIR/certbot/bin/certbot" renew --quiet --deploy-hook "systemctl reload nginx" >>"$LOG_FILE" 2>&1; then
      renewal_ok=1
    fi
    if [ "$nginx_was_active" = "1" ]; then
      systemctl start nginx >>"$LOG_FILE" 2>&1 || die "Не удалось вернуть Nginx после продления сертификата"
    fi
    [ "$renewal_ok" = "1" ] || die "Не удалось проверить или обновить сертификат Let's Encrypt"
    log "Проверка сертификата Let's Encrypt завершена"
    return 0
  fi

  if try_upgrade_certificate; then
    update_panel_config_metadata
    write_final_nginx
    log "Self-signed сертификат заменен публичным сертификатом Let's Encrypt"
    return 0
  fi

  if [ -r "$CERTIFICATE_PATH" ] && openssl x509 -checkend 2592000 -noout -in "$CERTIFICATE_PATH" >/dev/null 2>&1; then
    log "Self-signed сертификат действителен более 30 дней; замена не требуется"
    return 0
  fi

  create_self_signed_certificate
  update_panel_config_metadata
  write_final_nginx
  log "Self-signed сертификат автоматически обновлен"
}

open_firewall() {
  [ "${HTTP_ENABLED:-0}" = "1" ] && open_acme_firewall
  if command_exists ufw && ufw status 2>/dev/null | grep -q '^Status: active'; then
    ufw_allow "$PANEL_HTTPS_PORT/tcp" "WDTT Panel HTTPS"
  elif command_exists firewall-cmd && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --add-port="$PANEL_HTTPS_PORT/tcp" >/dev/null || true
    firewall-cmd --reload >/dev/null || true
  elif command_exists iptables; then
    iptables -C INPUT -p tcp --dport "$PANEL_HTTPS_PORT" -m comment --comment WDTT_PANEL -j ACCEPT 2>/dev/null || \
      iptables -I INPUT -p tcp --dport "$PANEL_HTTPS_PORT" -m comment --comment WDTT_PANEL -j ACCEPT || true
  fi
}

write_wdtt_extensions_timer() {
  cat > "/etc/systemd/system/$WDTT_EXTENSIONS_SERVICE" <<EOF
[Unit]
Description=Install WDTT Panel traffic and label extensions
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
TimeoutStartSec=20min
Restart=on-failure
RestartSec=10min
ExecStart=/bin/bash $INSTALL_DIR/install.sh enable-wdtt-extensions
EOF
  cat > "/etc/systemd/system/$WDTT_EXTENSIONS_TIMER" <<EOF
[Unit]
Description=Retry WDTT Panel traffic and label extensions

[Timer]
OnBootSec=20s
OnUnitActiveSec=10min
OnUnitInactiveSec=10min
RandomizedDelaySec=30s
Persistent=true
Unit=$WDTT_EXTENSIONS_SERVICE

[Install]
WantedBy=timers.target
EOF
  systemctl daemon-reload
  systemctl enable --now "$WDTT_EXTENSIONS_TIMER" >>"$LOG_FILE" 2>&1
}

change_panel_password() {
  require_root
  load_panel_config
  [ -n "$PANEL_PASSWORD" ] || die "Укажите новый пароль через меню или PANEL_PASSWORD"
  [ "${#PANEL_PASSWORD}" -ge 12 ] || die "PANEL_PASSWORD должен содержать не менее 12 символов"

  PASSWORD_HASH="$(PYTHONPATH="$INSTALL_DIR" python3 -c 'import sys; from wdtt_panel.security import hash_password; print(hash_password(sys.argv[1]))' "$PANEL_PASSWORD")"
  SESSION_SECRET="$(random_token 48)"
  python3 - "$CONFIG_FILE" "$PASSWORD_HASH" "$SESSION_SECRET" <<'PY'
import json, os, sys
path, password_hash, session_secret = sys.argv[1:]
data = json.load(open(path, encoding="utf-8"))
data["password_hash"] = password_hash
data["session_secret"] = session_secret
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
PY
  chown root:wdtt-panel "$CONFIG_FILE"
  chmod 0640 "$CONFIG_FILE"
  systemctl restart "$PANEL_SERVICE" >>"$LOG_FILE" 2>&1 || die "Не удалось перезапустить панель после смены пароля"
  log "Пароль входа в панель изменен; все активные сессии завершены"
}

clean_system_safe() {
  require_root
  local keep_days="${CLEAN_KEEP_DAYS:-14}"
  [[ "$keep_days" =~ ^[0-9]+$ ]] || keep_days=14
  log "Безопасная очистка журналов и системного кэша"
  for file in \
    /var/log/wdtt-panel-install.log \
    /var/lib/wdtt-panel-private/xray-access.log \
    /var/lib/wdtt-panel-private/xray-error.log \
    /var/log/nginx/access.log \
    /var/log/nginx/error.log
  do
    if [ -f "$file" ]; then
      : > "$file" || true
      log "Очищен журнал: $file"
    fi
  done
  if command_exists journalctl; then
    journalctl "--vacuum-time=${keep_days}d" >>"$LOG_FILE" 2>&1 || log "Systemd journal не очищен, подробности в $LOG_FILE"
  fi
  if command_exists apt-get; then
    apt-get clean >>"$LOG_FILE" 2>&1 || log "Кэш apt не очищен"
  elif command_exists dnf; then
    dnf clean all >>"$LOG_FILE" 2>&1 || log "Кэш dnf не очищен"
  elif command_exists yum; then
    yum clean all >>"$LOG_FILE" 2>&1 || log "Кэш yum не очищен"
  elif command_exists paccache; then
    paccache -rk1 >>"$LOG_FILE" 2>&1 || log "Кэш pacman не очищен"
  fi
  if command_exists systemctl; then
    systemctl reset-failed >>"$LOG_FILE" 2>&1 || true
  fi
  log "Очистка завершена. Пользователи, backup, сертификаты и настройки не затронуты"
}

status_panel() {
  local attempt
  systemctl --no-pager --full status "$PANEL_SERVICE" || true
  [ -r "$CONFIG_FILE" ] && python3 - "$CONFIG_FILE" <<'PY'
import json, sys
d=json.load(open(sys.argv[1], encoding="utf-8"))
print(f"Version: {d.get('version', 'unknown')}")
print(f"URL: https://{d['public_host']}:{d['https_port']}{d['base_path']}")
print(f"TLS: {d.get('tls_mode', 'unknown')}")
PY
  if [ -n "${PANEL_HTTPS_PORT:-}" ]; then
    for ((attempt = 1; attempt <= 10; attempt++)); do
      if curl --noproxy '*' -kfsS --connect-timeout 2 --max-time 5 "https://127.0.0.1:$PANEL_HTTPS_PORT$PANEL_PATH" >/dev/null 2>&1; then
        echo "HTTPS local check: OK"
        break
      fi
      sleep 0.5
    done
    if [ "$attempt" -gt 10 ]; then
      echo "HTTPS local check: FAILED (проверьте nginx и journalctl -u nginx)"
    fi
  else
    echo "HTTPS local check: FAILED (не найден HTTPS-порт панели)"
  fi
  [ "${TLS_MODE:-}" != "self-signed" ] || echo "Browser trust: self-signed требует ручного доверия; шифрование при этом работает"
}

remove_firewall_rule() {
  local port="$1" proto="${2:-tcp}"
  [ -n "$port" ] || return 0
  case "$proto" in tcp|udp) ;; *) return 0 ;; esac
  if command_exists ufw; then
    while ufw --force delete allow "$port/$proto" >/dev/null 2>&1; do :; done
  fi
  if command_exists firewall-cmd && systemctl is-active --quiet firewalld; then
    firewall-cmd --permanent --remove-port="$port/$proto" >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
  fi
  if command_exists iptables; then
    local comment
    for comment in WDTT_PANEL WDTT_MANAGED WDTT_MIRRORED; do
      while iptables -C INPUT -p "$proto" --dport "$port" -m comment --comment "$comment" -j ACCEPT 2>/dev/null; do
        iptables -D INPUT -p "$proto" --dport "$port" -m comment --comment "$comment" -j ACCEPT || break
      done
    done
  fi
}

uninstall_panel() {
  local panel_port=""
  if [ -r "$CONFIG_FILE" ]; then
    panel_port="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("https_port", ""))' "$CONFIG_FILE" 2>/dev/null || true)"
  fi
  log "Удаление только web-панели; WDTT не затрагивается"
  systemctl disable --now "$PANEL_SERVICE" wdtt-fleet-agent.service wdtt-panel-cert-renew.timer wdtt-panel-cert-renew.service "$WDTT_EXTENSIONS_TIMER" "$WDTT_EXTENSIONS_SERVICE" wdtt-panel-backup.timer wdtt-panel-backup.service wdtt-panel-autoclean.timer wdtt-panel-autoclean.service 2>/dev/null || true
  rm -f "/etc/systemd/system/$PANEL_SERVICE" /etc/systemd/system/wdtt-fleet-agent.service /etc/systemd/system/wdtt-panel-cert-renew.service /etc/systemd/system/wdtt-panel-cert-renew.timer "/etc/systemd/system/$WDTT_EXTENSIONS_SERVICE" "/etc/systemd/system/$WDTT_EXTENSIONS_TIMER" /etc/systemd/system/wdtt-panel-backup.service /etc/systemd/system/wdtt-panel-backup.timer /etc/systemd/system/wdtt-panel-autoclean.service /etc/systemd/system/wdtt-panel-autoclean.timer "$STATE_DIR/fleet-agent.json"
  systemctl disable --now "$LEGACY_CASCADE_SERVICE" "$XRAY_SERVICE" "$XRAY_CASCADE_SERVICE" "$XRAY_GATEWAY_SERVICE" wdtt-panel-geofiles-update.timer wdtt-panel-geofiles-update.service 2>/dev/null || true
  rm -f "/etc/systemd/system/$LEGACY_CASCADE_SERVICE" "/etc/systemd/system/$XRAY_SERVICE" "/etc/systemd/system/$XRAY_CASCADE_SERVICE" "/etc/systemd/system/$XRAY_GATEWAY_SERVICE" /etc/systemd/system/wdtt-panel-geofiles-update.service /etc/systemd/system/wdtt-panel-geofiles-update.timer
  rm -f "$NGINX_FILE" "$ADMIN_WRAPPER" "$SUDOERS_FILE" "$MANAGER_WRAPPER" /usr/local/sbin/wddt-panel /usr/local/sbin/wdtt-pane "$UPDATE_WRAPPER" "$UNINSTALL_WRAPPER" "$STATUS_WRAPPER" "$GEOFILES_UPDATE_WRAPPER" "$BACKUP_RUNNER" "$AUTOCLEAN_RUNNER" "$CASCADE_RULES_WRAPPER" "$GATEWAY_RULES_WRAPPER"
  rm -rf "$INSTALL_DIR" "$CONFIG_DIR"
  remove_firewall_rule "$panel_port" tcp
  # Порты ядра WDTT (56000/56001 — DTLS/WG, 56002 — admin; открывает deploy.sh).
  # 80/443 осознанно не трогаем: их могли открыть раньше для других служб.
  # SSH-порт тоже не трогаем, чтобы не потерять доступ.
  for kernel_port in 56000 56001 56002; do
    remove_firewall_rule "$kernel_port" tcp
    remove_firewall_rule "$kernel_port" udp
  done
  systemctl daemon-reload
  nginx -t >/dev/null 2>&1 && systemctl reload nginx || true
  log "Панель удалена. Аудит оставлен в $STATE_DIR, резервные копии в $PRIVATE_STATE_DIR"
}

update_panel() {
  require_root
  resolve_python_bin
  load_panel_config
  log "Обновление панели до версии $PANEL_VERSION"
  remove_obsolete_fleet_agent
  backup_wdtt_database_before_update
  backup_panel_config_before_update
  install_panel_files
  write_maintenance_scripts
  migrate_panel_config
  update_panel_config_metadata
  write_panel_service
  write_final_nginx
  write_renew_timer
  write_wdtt_extensions_timer
  write_autoclean_timer
  write_xray_services
  remove_obsolete_openwrt_podkop
  schedule_wdtt_extensions
  systemctl restart "$PANEL_SERVICE"
  log "Панель обновлена; адрес, пароль, сертификаты и данные сохранены"
  status_panel
}

install_panel() {
  require_root
  detect_os
  install_packages
  ensure_modern_python
  if [ "$NGINX_WAS_INSTALLED" = "0" ] && ! port_80_available_for_nginx; then
    rm -f /etc/nginx/sites-enabled/default
  fi
  validate_inputs
  validate_port_availability
  discover_host
  prepare_secrets
  install_clean_wdtt
  install_panel_files
  remove_obsolete_fleet_agent
  install_wdtt_extensions
  apply_telegram_settings
  write_maintenance_scripts

  if request_certificate; then
    log "Получен публично доверенный сертификат Let's Encrypt"
  else
    log "Let's Encrypt недоступен, создается автоматический self-signed сертификат"
    create_self_signed_certificate
  fi

  write_panel_config
  write_panel_service
  write_final_nginx
  write_renew_timer
  write_wdtt_extensions_timer
  write_autoclean_timer
  write_xray_services
  open_firewall
  systemctl restart "$PANEL_SERVICE"

  printf '\n'
  log "Установка завершена"
  printf 'URL: https://%s:%s%s\n' "$PANEL_HOST" "$PANEL_HTTPS_PORT" "$PANEL_PATH"
  printf 'Login: %s\n' "$PANEL_USER"
  printf 'Password: %s\n' "$PANEL_PASSWORD"
  printf 'TLS: %s\n' "$TLS_MODE"
  if [ -n "$WDTT_MAIN_PASSWORD" ]; then printf 'WDTT main password: %s\n' "$WDTT_MAIN_PASSWORD"; fi
  printf 'Install log: %s\n' "$LOG_FILE"
}

case "${1:-install}" in
  install|--install|-i) install_panel ;;
  update|--update) update_panel ;;
  restart|--restart) restart_services ;;
  renew-cert|--renew-cert) renew_certificates ;;
  status|--status|-s) require_root; load_panel_config; status_panel ;;
  change-password|--change-password) change_panel_password ;;
  clean-system|--clean-system|clean-logs) clean_system_safe ;;
  uninstall|--uninstall|-u) require_root; uninstall_panel ;;
  install-xray-runtime) install_xray_runtime ;;
  install-warp-runtime) install_warp_runtime ;;
  enable-wdtt-extensions) install_wdtt_extensions ;;
  *) die "Использование: $0 [install|update|restart|renew-cert|status|change-password|clean-system|uninstall|install-xray-runtime|install-warp-runtime|enable-wdtt-extensions]" ;;
esac
