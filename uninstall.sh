#!/usr/bin/env bash
# =============================================================================
# WDTT-SERVER — полный тейкдаун (как «очистка» в wdtt-old.sh, но с бэкапом)
#
# Удаляет: web-панель, ядро WDTT, данные, таймеры/лефтоверы, nginx-конфиг,
# sudoers, логи. Перед удалением создаёт резервную копию:
#   /var/lib/wdtt-uninstall-backup-<ts>.tar.gz
#
# Использование:
#   sudo ./uninstall.sh           (спросит подтверждение)
#   sudo ./uninstall.sh -y        (без запроса)
# =============================================================================
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/install.sh" uninstall "$@"