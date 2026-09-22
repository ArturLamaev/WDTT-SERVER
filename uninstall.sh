#!/usr/bin/env bash
# WDTT-SERVER: удаление панели (ядро WDTT и пользователи не затрагиваются).
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/panel/install.sh" uninstall "$@"