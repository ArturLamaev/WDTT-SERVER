#!/usr/bin/env bash
# =============================================================================
# WDTT-SERVER — монолитный установщик форка
#
# Ставит на чистый Ubuntu/Debian:
#   * ядро qWDTT v1.4.3 (замороженный форк, src/)   — лимит ключей 10000,
#     лимит устройств на ключ 10000 по умолчанию, max_devices<=0 = безлимит;
#   * веб-панель 0.12.3 (замороженный форк, panel/) — лимит пользователей 10000.
#
# Всё собирается ТОЛЬКО из локальных исходников этого репозитория.
# Единственные внешние загрузки при сборке — системные пакеты apt и Go toolchain
# (+ модули Go), т.е. сам оригинальный проект с GitHub не ставится и не качается.
#
# Возможности (по мотивам wdtt-old.sh):
#   * проверка root с авто-эскалацией через sudo (диагностика no-new-privileges);
#   * обнаружение предыдущей установки WDTT/панели и запрос на чистый снос
#     (--force-clean — снести без запроса, --non-interactive — не трогать);
#   * пост-проверка установки (директории, sudoers, пользователь wdtt-panel);
#   * авто-рестарт панели и WDTT каждые 6 часов (systemd timer + cron fallback);
#   * полный тейкдаун через `uninstall` (панель + ядро + данные) с резервной
#     копией в /var/lib/wdtt-uninstall-backup-<ts>.tar.gz; -y/--yes без запроса.
#
# Использование:
#   sudo ./install.sh [--non-interactive] [--domain panel.example.com] [--ip A.B.C.D]
#                     [--user admin] [--password '...'] [--email '...']
#                     [--https-port 8443] [--path /secret] [--wdtt-password '...']
#                     [--telegram-token '123:...' --telegram-admin-id 123456]
#   sudo ./install.sh status | update | renew-cert | change-password | clean-system | uninstall
# =============================================================================
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_DIR="$ROOT_DIR/src/proxy-turn-vk-android-1.4.3"
PANEL_INSTALL="$ROOT_DIR/panel/install.sh"

info() { printf '[wdtt-server] %s\n' "$*"; }
die()  { printf '[wdtt-server] ERROR: %s\n' "$*" >&2; exit 1; }

require_local_sources() {
  [ -f "$KERNEL_DIR/server/main.go" ] || die "Локальный форк ядра не найден: $KERNEL_DIR (проверьте целостность репозитория)"
  [ -f "$PANEL_INSTALL" ] || die "Панель не найдена: $PANEL_INSTALL"
}

check_fork_limits() {
  grep -q 'maxGeneratedPasswords = 10000' "$KERNEL_DIR/server/database_bot.go" \
    || die "В ядре src/ не применён патч лимита ключей — обновите репозиторий"
  grep -q 'MAX_USERS = 10000' "$ROOT_DIR/panel/wdtt_panel/core.py" \
    || die "В панели panel/ не применён патч лимита пользователей — обновите репозиторий"
}

# ---------------------------------------------------------------------------
# Проверка root прав с авто-эскалацией через sudo (как в wdtt-old.sh)
# ---------------------------------------------------------------------------
require_root() {
  [ "$(id -u)" -eq 0 ] && return 0
  info "Требуются права root. Пытаюсь поднять через sudo..."
  if sudo -n true 2>/dev/null; then
    info "sudo без пароля — перезапуск под root"
    exec sudo bash "$0" "$@"
  fi
  info "sudo запросит пароль"
  if sudo bash "$0" "$@"; then
    exit 0
  fi
  printf '[wdtt-server] DEBUG: whoami=%s uid=%s NoNewPrivs=%s\n' \
    "$(whoami)" "$(id -u)" "$(awk '/NoNewPrivs/ {print $2}' /proc/self/status 2>/dev/null)"
  if sudo -n true 2>&1 | grep -qi "no new privileges"; then
    die "Контейнер запущен с флагом no-new-privileges — sudo не может поднять права.
    Пересоздай контейнер с --security-opt no-new-privileges=false
    или зайди от root: docker exec -u 0 -it <container> bash"
  fi
  die "sudo не сработал (нет прав в sudoers или нет tty). Выполни: su - && bash $0"
}

# ---------------------------------------------------------------------------
# Обнаружение и снос предыдущей установки (как в wdtt-old.sh)
# ---------------------------------------------------------------------------
detect_previous_install() {
  local p
  for p in /opt/wdtt-panel /etc/wdtt-panel /var/lib/wdtt-panel /var/lib/wdtt-panel-private \
           /etc/wdtt /usr/local/sbin/wdtt-panel; do
    [ -e "$p" ] && return 0
  done
  [ -e /etc/nginx/conf.d/wdtt-panel.conf ] && return 0
  systemctl list-units --all 2>/dev/null | grep -qE 'wdtt-panel|wdtt\.service' && return 0
  return 1
}

wipe_previous_install() {
  info "Делаю полную очистку старой панели и WDTT..."
  systemctl stop wdtt wdtt-panel wdtt-app 2>/dev/null || true
  systemctl disable wdtt wdtt-panel wdtt-app 2>/dev/null || true
  pkill -x wdtt-server 2>/dev/null || true
  rm -f /etc/systemd/system/wdtt.service /etc/systemd/system/wdtt-app.service \
        /etc/systemd/system/wdtt-panel.service /etc/systemd/system/wdtt-panel-wdtt-extensions.* \
        /etc/systemd/system/wdtt-auto-restart.* /etc/systemd/system/wdtt-fleet-agent.service 2>/dev/null || true
  systemctl daemon-reload 2>/dev/null || true
  rm -f /usr/local/bin/wdtt-server /usr/local/bin/wdtt-app /usr/local/bin/xray
  rm -rf /opt/wdtt-panel /etc/wdtt-panel /var/lib/wdtt-panel /var/lib/wdtt-panel-private \
         /etc/wdtt /var/lib/wdtt
  rm -f /etc/nginx/conf.d/wdtt-panel.conf
  rm -f /usr/local/sbin/wdtt-panel* /etc/sudoers.d/wdtt-panel
  rm -f /var/log/wdtt-panel* /var/log/wdtt-server*.log 2>/dev/null || true
  info "Старая установка снесена"
}

confirm_wipe() {
  local ans="" flags="$*"
  case " $flags " in
    *" --force-clean"*) return 0 ;;
    *" --non-interactive"*) return 1 ;;
  esac
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    printf "Снести найденную старую панель и WDTT для чистой установки? [y/N]: " > /dev/tty
    IFS= read -r ans </dev/tty || true
  else
    printf "Снести найденную старую панель и WDTT для чистой установки? [y/N]: "
    IFS= read -r ans || true
  fi
  case "${ans:-N}" in
    y|Y|yes|YES|да|Да) return 0 ;;
    *) return 1 ;;
  esac
}

# ---------------------------------------------------------------------------
# Пост-проверка установки (как в wdtt-old.sh)
# ---------------------------------------------------------------------------
validate_installation() {
  local broken=0 f
  info "Проверяем установку панели..."
  for f in /opt/wdtt-panel /usr/local/sbin/wdtt-panel-admin /etc/sudoers.d/wdtt-panel; do
    if [ -e "$f" ]; then
      info "OK: найден $f"
    else
      info "ERROR: отсутствует $f — установка панели не завершена!"
      broken=1
    fi
  done
  if [ -f /etc/sudoers.d/wdtt-panel ]; then
    if visudo -cf /etc/sudoers.d/wdtt-panel 2>/dev/null; then
      info "OK: sudoers панели валиден"
    else
      info "ERROR: sudoers панели невалиден!"
      broken=1
    fi
  fi
  if id -u wdtt-panel >/dev/null 2>&1; then
    info "OK: пользователь wdtt-panel существует"
  else
    info "ERROR: пользователь wdtt-panel не создан — установка не завершена!"
    broken=1
  fi
  if [ -e /usr/local/bin/wdtt-server ]; then
    info "OK: ядро WDTT развёрнуто (/usr/local/bin/wdtt-server)"
  else
    info "WARN: /usr/local/bin/wdtt-server отсутствует (возможно INSTALL_WDTT=no)"
  fi
  if [ "$broken" -eq 1 ]; then
    info "Панель встала криво. Смотри лог: tail -n 50 /var/log/wdtt-panel-install.log"
    return 1
  fi
  return 0
}

# ---------------------------------------------------------------------------
# Авто-рестарт панели и WDTT каждые 6 часов (как в wdtt-old.sh)
# ---------------------------------------------------------------------------
setup_auto_restart() {
  info "Настраиваем авто-рестарт панели и WDTT каждые 6 часов..."
  cat > /etc/systemd/system/wdtt-auto-restart.service <<'EOF'
[Unit]
Description=Restart WDTT and panel every 6h (prevent silent crash)
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'systemctl restart wdtt.service 2>/dev/null || true; systemctl restart wdtt-panel.service 2>/dev/null || true; systemctl restart wdtt-app.service 2>/dev/null || true'
EOF

  cat > /etc/systemd/system/wdtt-auto-restart.timer <<'EOF'
[Unit]
Description=Restart WDTT and panel every 6 hours

[Timer]
OnBootSec=6h
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
EOF

  systemctl daemon-reload 2>/dev/null || true
  systemctl enable --now wdtt-auto-restart.timer 2>/dev/null || true

  if systemctl is-enabled wdtt-auto-restart.timer 2>/dev/null | grep -q enabled; then
    info "Таймер wdtt-auto-restart.timer включён (каждые 6ч)"
  else
    info "WARN: таймер не включился, ставлю cron fallback..."
    (crontab -l 2>/dev/null | grep -v "wdtt.*restart"; echo "0 */6 * * * /bin/systemctl restart wdtt.service wdtt-panel.service 2>/dev/null || true") | crontab - 2>/dev/null || true
    info "OK: cron fallback — 0 */6 * * * restart wdtt/panel"
  fi
}

print_final_notes() {
  echo ""
  echo "======================================================"
  echo "[OK] Установка WDTT-SERVER завершена!"
  echo ""
  echo "Проверь статус:"
  echo "  systemctl status wdtt"
  echo "  systemctl status wdtt-panel"
  echo ""
  echo "Авто-рестарт (каждые 6ч):"
  echo "  systemctl status wdtt-auto-restart.timer"
  echo "  systemctl list-timers wdtt-auto-restart.timer"
  echo ""
  echo "Логи:"
  echo "  journalctl -u wdtt -f"
  echo "  journalctl -u wdtt-panel -f"
  echo "  /var/log/wdtt-panel-install.log"
  echo ""
  echo "Панель: адрес (https), логин и пароль напечатаны выше при установке."
  echo "Перезапуск вручную: systemctl restart wdtt wdtt-panel"
  echo "======================================================"
}

# ---------------------------------------------------------------------------
# Полный тейкдаун: панель + ядро WDTT + данные, с резервной копией
# ---------------------------------------------------------------------------
cmd_uninstall() {
  local auto=0 a ts backup_file
  for a in "$@"; do
    case "$a" in -y|--yes|--assume-yes) auto=1 ;; esac
  done

  info "Полный тейкдаун WDTT-SERVER: панель + ядро WDTT + данные."
  if [ "$auto" -ne 1 ]; then
    local ans=""
    if [ -r /dev/tty ] && [ -w /dev/tty ]; then
      printf "Удалить всё с резервной копией. Продолжить? [y/N]: " > /dev/tty
      IFS= read -r ans </dev/tty || true
    else
      printf "Удалить всё с резервной копией. Продолжить? [y/N]: "
      IFS= read -r ans || true
    fi
    case "${ans:-N}" in
      y|Y|yes|YES|да|Да) ;;
      *) info "Отменено"; exit 0 ;;
    esac
  fi

  ts="$(date +%Y%m%d-%H%M%S)"
  backup_file="/var/lib/wdtt-uninstall-backup-${ts}.tar.gz"
  info "Создаю резервную копию: $backup_file"
  tar -czf "$backup_file" /etc/wdtt /etc/wdtt-panel /opt/wdtt-panel \
      /var/lib/wdtt-panel /var/lib/wdtt-panel-private /var/lib/wdtt \
      /etc/nginx/conf.d/wdtt-panel.conf 2>/dev/null || true

  info "Удаляю web-панель (штатным снос-скриптом)..."
  bash "$PANEL_INSTALL" uninstall "$@" || true

  info "Удаляю авто-рестарт, ядро WDTT и лефтоверы..."
  systemctl disable --now wdtt-auto-restart.timer wdtt-auto-restart.service 2>/dev/null || true
  rm -f /etc/systemd/system/wdtt-auto-restart.* 2>/dev/null || true
  systemctl stop wdtt 2>/dev/null || true
  systemctl disable wdtt 2>/dev/null || true
  rm -f /etc/systemd/system/wdtt.service /etc/systemd/system/wdtt-app.service 2>/dev/null || true
  rm -f /usr/local/bin/wdtt-server /usr/local/bin/wdtt-app 2>/dev/null || true
  rm -rf /etc/wdtt /var/lib/wdtt /var/lib/wdtt-panel /var/lib/wdtt-panel-private 2>/dev/null || true
  rm -f /var/log/wdtt-panel* /var/log/wdtt-server*.log 2>/dev/null || true
  systemctl daemon-reload

  echo ""
  echo "======================================================"
  info "Полный тейкдаун завершён."
  info "Резервная копия: $backup_file"
  echo "При необходимости восстановить (панель+ядро+данные):"
  echo "  sudo mkdir -p /restore && sudo tar -xzf $backup_file -C /restore"
  echo "======================================================"
}

main() {
  require_root "$@"
  case "${1:-install}" in
    install|--install|-i)
      require_local_sources
      check_fork_limits
      if detect_previous_install; then
        info "Обнаружена предыдущая установка WDTT / панели"
        if confirm_wipe "$@"; then
          wipe_previous_install
        else
          info "Пропускаю снос, ставлю поверх (может остаться мусор)"
        fi
      else
        info "Старая установка не найдена — ставлю начисто"
      fi
      bash "$PANEL_INSTALL" "$@"
      validate_installation
      setup_auto_restart
      print_final_notes
      ;;
    update|--update)
      require_local_sources
      bash "$PANEL_INSTALL" "$@"
      validate_installation || true
      ;;
    uninstall|--uninstall|-u) cmd_uninstall "$@" ;;
    renew-cert|--renew-cert) exec bash "$PANEL_INSTALL" "$@" ;;
    status|--status|-s) exec bash "$PANEL_INSTALL" "$@" ;;
    change-password|--change-password) exec bash "$PANEL_INSTALL" "$@" ;;
    clean-system|--clean-system|clean-logs) exec bash "$PANEL_INSTALL" "$@" ;;
    install-xray-runtime|install-warp-runtime|enable-wdtt-extensions)
      exec bash "$PANEL_INSTALL" "$@" ;;
    *)
      if [[ "${1:-}" == -* ]]; then
        set -- install "$@"
        main "$@"
        return 0
      fi
      die "Использование: $0 [install|update|renew-cert|status|change-password|clean-system|uninstall|install-xray-runtime|install-warp-runtime|enable-wdtt-extensions]"
      ;;
  esac
}

main "$@"