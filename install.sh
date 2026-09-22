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

main() {
  require_local_sources
  check_fork_limits
  case "${1:-install}" in
    install|--install|-i)
      # панель грузит ядро из src/ (локального форка), не с GitHub
      exec bash "$PANEL_INSTALL" "$@"
      ;;
    update|--update)   exec bash "$PANEL_INSTALL" "$@" ;;
    renew-cert|--renew-cert) exec bash "$PANEL_INSTALL" "$@" ;;
    status|--status|-s) exec bash "$PANEL_INSTALL" "$@" ;;
    change-password|--change-password) exec bash "$PANEL_INSTALL" "$@" ;;
    clean-system|--clean-system|clean-logs) exec bash "$PANEL_INSTALL" "$@" ;;
    uninstall|--uninstall|-u) exec bash "$PANEL_INSTALL" "$@" ;;
    install-xray-runtime|install-warp-runtime|enable-wdtt-extensions)
      exec bash "$PANEL_INSTALL" "$@" ;;
    *)
      die "Использование: $0 [install|update|renew-cert|status|change-password|clean-system|uninstall|install-xray-runtime|install-warp-runtime|enable-wdtt-extensions]"
      ;;
  esac
}

main "$@"