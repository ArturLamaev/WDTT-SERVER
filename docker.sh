#!/usr/bin/env bash
# WDTT-SERVER — shortcut для docker compose из корня репозитория.
# Compose-файл живёт в docker/ (контекст сборки — корень), поэтому все
# команды надо выполнять из каталога docker/:
#   sudo ./docker.sh up -d --build
#   sudo ./docker.sh logs -f wdtt
# Этот шим просто переходит в docker/ и пробрасывает аргументы.
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/docker"
exec docker compose "$@"
