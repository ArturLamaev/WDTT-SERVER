#!/usr/bin/env bash
# WDTT-SERVER: обновление панели и ядра из локального форка.
set -Eeuo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$ROOT_DIR/panel/install.sh" update "$@"