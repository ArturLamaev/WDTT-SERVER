#!/usr/bin/env bash
# =============================================================================
# WDTT-SERVER — монолитный установщик форка
#
# Ставит на чистый Ubuntu/Debian/Astra Linux:
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
#   * обнаружение установленной панели: предлагает обновить её с сохранением
#     конфига (снимок конфига создаётся автоматически); --force-clean — снести
#     и поставить начисто, --non-interactive — обновить панель без вопросов;
#   * пост-проверка установки (директории, sudoers, пользователь wdtt-panel);
#   * авто-рестарт панели и WDTT каждые 6 часов (systemd timer + cron fallback);
#   * полный тейкдаун через `uninstall` (панель + ядро + данные) с резервной
#     копией в /var/lib/wdtt-uninstall-backup-<ts>.tar.gz; -y/--yes без запроса.
#
# Использование:
#   sudo ./install.sh [--mode node|controller] [--non-interactive] [--domain panel.example.com] [--ip A.B.C.D]
#                     [--user admin] [--password '...'] [--email '...']
#                     [--https-port 9999] [--path /secret] [--wdtt-password '...']
#                     [--telegram-token '123:...' --telegram-admin-id 123456]
#   sudo ./install.sh status | update | renew-cert | change-password | clean-system | uninstall
#
# Режим выбирается вопросом при старте, флагом --mode (node|controller) или
# env WDTT_MODE. Нода = VPN + локальная панель; контроллер = панель управления
# флотом для дома (без ядра, только fleet web/bot + nginx, наружу один порт).
# =============================================================================
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_DIR="$ROOT_DIR/src/proxy-turn-vk-android-1.4.3"
PANEL_INSTALL="$ROOT_DIR/panel/install.sh"
PANEL_CONFIG_FILE="/etc/wdtt-panel/config.json"
WDTT_MODE_ENV_GIVEN=0
[ -n "${WDTT_MODE:-}" ] && WDTT_MODE_ENV_GIVEN=1
WDTT_MODE="${WDTT_MODE:-node}"

info() { printf '[wdtt-server] %s\n' "$*"; }
die()  { printf '[wdtt-server] ERROR: %s\n' "$*" >&2; exit 1; }
# systemctl на подвисшей системе иногда ждёт D-Bus вечно: ограничиваем ожидание.
sys_timeout() {
  local limit="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$limit" "$@" 2>/dev/null || true
  else
    "$@" 2>/dev/null || true
  fi
}

# Режим node|controller: --mode/--mode=, env WDTT_MODE, вопрос на tty, иначе node.
resolve_root_mode() {
  local a next_is_mode=0 given="$WDTT_MODE_ENV_GIVEN"
  for a in "$@"; do
    if [ "$next_is_mode" = "1" ]; then WDTT_MODE="$a"; given=1; next_is_mode=0; continue; fi
    case "$a" in
      --mode=*) WDTT_MODE="${a#--mode=}"; given=1 ;;
      --mode) next_is_mode=1 ;;
    esac
  done
  [ "$next_is_mode" = "1" ] && die "--mode требует значение: node или controller"
  WDTT_MODE="${WDTT_MODE:-node}"
  case "$WDTT_MODE" in
    node|controller) ;;
    *) die "--mode должен быть node или controller (получено: $WDTT_MODE)" ;;
  esac
  if [ "$given" = "0" ] && [ -r /dev/tty ] && [ -w /dev/tty ]; then
    local ans=""
    cat > /dev/tty <<'EOF'

Что устанавливаем?
  1) Нода — VPN-сервер WDTT + локальная панель (публичный сервер)
  2) Панель управления — контроллер флота для дома (без VPN-ядра: веб, бот, API)
EOF
    printf 'Выбор [1]: ' > /dev/tty
    IFS= read -r ans </dev/tty || true
    case "${ans:-1}" in
      1) WDTT_MODE="node" ;;
      2) WDTT_MODE="controller" ;;
      *) die "Неизвестный вариант: $ans" ;;
    esac
  fi
  export WDTT_MODE
  info "Режим установки: $WDTT_MODE"
}

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

# ---------------------------------------------------------------------------
# Проверка установленной панели и предложение обновления (WDTT-SERVER)
# ---------------------------------------------------------------------------
panel_is_installed() {
  [ -f "$PANEL_CONFIG_FILE" ] || [ -f /opt/wdtt-panel/install.sh ] || [ -d /opt/wdtt-panel ]
}

panel_installed_version() {
  [ -r "$PANEL_CONFIG_FILE" ] || return 0
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("version", ""))' "$PANEL_CONFIG_FILE" 2>/dev/null || true
}

# Режим установленной панели: node|controller. Пусто — конфига нет (битая установка).
installed_panel_mode() {
  [ -r "$PANEL_CONFIG_FILE" ] || return 0
  python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8")).get("mode", "node"))' "$PANEL_CONFIG_FILE" 2>/dev/null || true
}

panel_target_version() {
  sed -n 's/^PANEL_VERSION="\([^"]*\)".*$/\1/p' "$PANEL_INSTALL" | head -n 1 || true
}

requested_force_clean() {
  case " $* " in
    *" --force-clean "*) return 0 ;;
  esac
  return 1
}

confirm_update() {
  local ans="" flags="$*" cur target
  if requested_force_clean "$@"; then
    return 1
  fi
  if [[ " $flags " == *" --non-interactive "* ]]; then
    info "--non-interactive: обновляю установленную панель с сохранением конфигурации"
    return 0
  fi
  cur="$(panel_installed_version)"
  target="$(panel_target_version)"
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    printf "Обновить панель %s до версии %s? Конфиг и данные сохранятся (будет создан снимок конфига). [Y/n]: " "${cur:-неизвестной}" "${target:-новой}" > /dev/tty
    IFS= read -r ans </dev/tty || true
  else
    printf "Обновить панель %s до версии %s? [Y/n]: " "${cur:-неизвестной}" "${target:-новой}"
    IFS= read -r ans || true
  fi
  case "${ans:-Y}" in
    y|Y|yes|YES|да|Да) return 0 ;;
    *) return 1 ;;
  esac
}

upgrade_existing_install() {
  info "Обновляю панель до версии $(panel_target_version) с сохранением конфигурации..."
  bash "$PANEL_INSTALL" update
}

wipe_previous_install() {
  info "Делаю полную очистку старой панели и WDTT..."
  info "Останавливаю службы..."
  sys_timeout 180 systemctl stop wdtt wdtt-panel wdtt-app
  sys_timeout 60 systemctl disable wdtt wdtt-panel wdtt-app
  pkill -x wdtt-server 2>/dev/null || true
  uninstall_wdtt_kernel
  rm -f /etc/systemd/system/wdtt.service /etc/systemd/system/wdtt-app.service \
        /etc/systemd/system/wdtt-panel.service /etc/systemd/system/wdtt-panel-wdtt-extensions.* \
        /etc/systemd/system/wdtt-auto-restart.* /etc/systemd/system/wdtt-fleet-agent.service 2>/dev/null || true
  sys_timeout 60 systemctl daemon-reload
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
  for f in /opt/wdtt-panel /usr/local/sbin/wdtt-panel-admin; do
    if [ -e "$f" ]; then
      info "OK: найден $f"
    else
      info "ERROR: отсутствует $f — установка панели не завершена!"
      broken=1
    fi
  done
  if [ "${WDTT_MODE:-node}" = "controller" ]; then
    info "OK: контроллер работает от root, sudoers/admin-helper ему не нужны"
  elif [ -f /etc/sudoers.d/wdtt-panel ]; then
    if visudo -cf /etc/sudoers.d/wdtt-panel 2>/dev/null; then
      info "OK: sudoers панели валиден"
    else
      info "ERROR: sudoers панели невалиден!"
      broken=1
    fi
  elif grep -q '^Environment=WDTT_PANEL_ADMIN=' /etc/systemd/system/wdtt-panel.service 2>/dev/null; then
    info "OK: панель работает от root через WDTT_PANEL_ADMIN (sudo не нужен)"
  else
    info "ERROR: нет ни /etc/sudoers.d/wdtt-panel, ни Environment=WDTT_PANEL_ADMIN в юните панели!"
    broken=1
  fi
  if id -u wdtt-panel >/dev/null 2>&1; then
    info "OK: пользователь wdtt-panel существует"
  else
    info "ERROR: пользователь wdtt-panel не создан — установка не завершена!"
    broken=1
  fi
  if [ "${WDTT_MODE:-node}" = "controller" ]; then
    if systemctl is-active --quiet wdtt-fleet 2>/dev/null; then
      info "OK: контроллер флота запущен (wdtt-fleet.service)"
    else
      info "ERROR: wdtt-fleet.service не запущен — установка не завершена!"
      broken=1
    fi
    info "Ядро WDTT в режиме контроллера не ставится (так и должно быть)"
  elif [ -e /usr/local/bin/wdtt-server ]; then
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
ExecStart=/bin/bash -c 'systemctl restart wdtt.service 2>/dev/null || true; systemctl restart wdtt-panel.service 2>/dev/null || true; systemctl restart wdtt-app.service 2>/dev/null || true; systemctl restart wdtt-fleet.service 2>/dev/null || true; systemctl restart wdtt-fleet-bot.service 2>/dev/null || true'
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
  if [ "${WDTT_MODE:-node}" = "controller" ]; then
    echo ""
    echo "======================================================"
    echo "[OK] Контроллер флота установлен!"
    echo ""
    echo "Проверь статус:"
    echo "  systemctl status wdtt-fleet"
    echo "  systemctl status wdtt-fleet-bot"
    echo ""
    echo "Логи:"
    echo "  journalctl -u wdtt-fleet -f"
    echo "  /var/log/wdtt-panel-install.log"
    echo ""
    echo "Ноды добавляются на странице «Ноды» панели или командой:"
    echo "  fleet add --id ... --url https://IP:9999/путь"
    echo "======================================================"
    return 0
  fi
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
# Штатное удаление ядра официальным deploy.sh: сервис, интерфейс, NAT/firewall,
# sysctl. Идущие дальше ручные rm — fallback для остатков и данных.
uninstall_wdtt_kernel() {
  local deploy="$KERNEL_DIR/app/src/main/assets/deploy.sh"
  if [ -f "$deploy" ]; then
    info "Удаляю ядро WDTT штатным deploy.sh (сервис, интерфейс, NAT, firewall, sysctl)..."
    bash "$deploy" uninstall >>/var/log/wdtt-panel-install.log 2>&1 || true
  else
    info "WARN: deploy.sh ядра не найден ($deploy) — удаляю ядро вручную"
  fi
}

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

  uninstall_wdtt_kernel

  info "Удаляю авто-рестарт, остатки ядра WDTT и лефтоверы..."
  sys_timeout 120 systemctl disable --now wdtt-auto-restart.timer wdtt-auto-restart.service
  rm -f /etc/systemd/system/wdtt-auto-restart.* 2>/dev/null || true
  info "Останавливаю ядро WDTT..."
  sys_timeout 120 systemctl stop wdtt
  sys_timeout 60 systemctl disable wdtt
  rm -f /etc/systemd/system/wdtt.service /etc/systemd/system/wdtt-app.service 2>/dev/null || true
  rm -f /usr/local/bin/wdtt-server /usr/local/bin/wdtt-app 2>/dev/null || true
  rm -rf /etc/wdtt /var/lib/wdtt /var/lib/wdtt-panel /var/lib/wdtt-panel-private 2>/dev/null || true
  rm -f /var/log/wdtt-panel* /var/log/wdtt-server*.log 2>/dev/null || true
  sys_timeout 60 systemctl daemon-reload

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
      resolve_root_mode "$@"
      require_local_sources
      check_fork_limits
      local upgrade=0
      if panel_is_installed; then
        local cur target cur_mode
        cur="$(panel_installed_version)"
        target="$(panel_target_version)"
        cur_mode="$(installed_panel_mode)"
        if [ -z "$cur_mode" ]; then
          # Каталоги есть, а config.json нет — установка битая: обновлять нечего.
          info "Найдены остатки панели без config.json (битая установка)"
          if requested_force_clean "$@" || confirm_wipe "$@"; then
            wipe_previous_install
          else
            die "Без сноса продолжить нельзя: задайте --force-clean для чистой установки в режиме $WDTT_MODE"
          fi
        elif [ "$cur_mode" != "$WDTT_MODE" ]; then
          # Смена режима (нода <-> контроллер) только через чистую установку:
          # состав служб и конфиг несовместимы.
          info "Установлен режим $cur_mode, выбран $WDTT_MODE — смена режима только начисто"
          if requested_force_clean "$@" || confirm_wipe "$@"; then
            wipe_previous_install
          else
            die "Режим отличается: задайте --force-clean для переустановки в режиме $WDTT_MODE"
          fi
        else
          info "Найдена установленная панель WDTT-SERVER${cur:+ (версия $cur)}${target:+, в репозитории $target}"
          if requested_force_clean "$@"; then
            info "--force-clean: сношу установленную панель и ставлю начисто"
            wipe_previous_install
          elif confirm_update "$@"; then
            upgrade=1
          elif confirm_wipe "$@"; then
            wipe_previous_install
          else
            info "Оставляю установку как есть, ставлю поверх (может остаться мусор)"
          fi
        fi
      elif detect_previous_install; then
        info "Найдены следы прежней установки WDTT без панели"
        if confirm_wipe "$@"; then
          wipe_previous_install
        else
          info "Пропускаю снос, ставлю поверх (может остаться мусор)"
        fi
      else
        info "Установка не найдена — ставлю начисто"
      fi

      if [ "$upgrade" -eq 1 ]; then
        upgrade_existing_install "$@"
        validate_installation || true
      else
        bash "$PANEL_INSTALL" "$@" --mode "$WDTT_MODE"
        validate_installation
      fi
      setup_auto_restart
      print_final_notes
      ;;
    update|--update)
      require_local_sources
      bash "$PANEL_INSTALL" "$@"
      validate_installation || true
      ;;
    restart|--restart) require_local_sources; exec bash "$PANEL_INSTALL" "$@" ;;
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
      die "Использование: $0 [--mode node|controller] [install|update|restart|renew-cert|status|change-password|clean-system|uninstall|install-xray-runtime|install-warp-runtime|enable-wdtt-extensions]"
      ;;
  esac
}

main "$@"