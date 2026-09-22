#!/usr/bin/env bash
# =============================================================================
# WDTT-SERVER — сборка ядра из локального форка
#
# Собирает /bin/wdtt-server (linux/<по умолчанию текущая arch>):
#   * исходники берутся ТОЛЬКО из src/proxy-turn-vk-android-1.4.3 (форк);
#   * поверх накладывается расширение панели panel/wdtt_panel/wdtt_server_patch.py
#     (метки Telegram, активность, квоты трафика);
#   * GO toolchain берётся из env/go (кэш) или скачивается с go.dev.
#
# Использование:
#   sudo ./build.sh            # amd64/arm64 по факту архитектуры
#   ./build.sh amd64           # явная архитектура
#   GO_VERSION=1.25.0 ./build.sh arm64
# =============================================================================
set -Eeuo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
KERNEL_DIR="$ROOT_DIR/src/proxy-turn-vk-android-1.4.3"
PANEL_PATCH="$ROOT_DIR/panel/wdtt_panel/wdtt_server_patch.py"

GO_VERSION="${GO_VERSION:-1.25.0}"
GOOS="${GOOS:-linux}"
ARCH="${1:-$(uname -m)}"

case "$ARCH" in
  x86_64|amd64) GOARCH="amd64" ;;
  aarch64|arm64) GOARCH="arm64" ;;
  *) echo "usage: $0 [amd64|arm64]" >&2; exit 2 ;;
esac

info() { printf '[wdtt-build] %s\n' "$*"; }
die()  { printf '[wdtt-build] ERROR: %s\n' "$*" >&2; exit 1; }

[ -f "$KERNEL_DIR/server/main.go" ] || die "Локальный форк ядра не найден: $KERNEL_DIR"
[ -f "$PANEL_PATCH" ] || die "Патч панели не найден: $PANEL_PATCH"
grep -q 'maxGeneratedPasswords = 10000' "$KERNEL_DIR/server/database_bot.go" \
  || die "В ядре не применён патч лимита ключей — работаем с неправильной веткой"

ENV_DIR="$ROOT_DIR/env"
GO_BIN="$ENV_DIR/go/bin/go"
INSTALL_DIR="$ROOT_DIR/bin"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

info "Архитектура: linux/$GOARCH; Go $GO_VERSION"

if [ ! -x "$GO_BIN" ]; then
  if command -v go >/dev/null 2>&1 && [ "$("go version" | awk '{print $3}' | sed 's/go//')" = "$GO_VERSION" ]; then
    info "Использую системный Go $GO_VERSION"
    GO_BIN="$(command -v go)"
  else
    info "Скачиваю Go $GO_VERSION в env/go (кэш: $ENV_DIR)..."
    mkdir -p "$ENV_DIR"
    TARBALL="go${GO_VERSION}.linux-${GOARCH}.tar.gz"
    curl -fsSL --retry 3 "https://go.dev/dl/$TARBALL" -o "$WORK/$TARBALL"
    curl -fsSL --retry 3 "https://dl.google.com/go/$TARBALL.sha256" -o "$WORK/$TARBALL.sha256"
    CHECKSUM="$(awk 'NR == 1 { print $1; exit }' "$WORK/$TARBALL.sha256")"
    [[ "$CHECKSUM" =~ ^[a-fA-F0-9]{64}$ ]] || die "Некорректная контрольная сумма Go"
    printf '%s  %s\n' "$CHECKSUM" "$WORK/$TARBALL" | sha256sum -c -
    tar -xzf "$WORK/$TARBALL" -C "$ENV_DIR"
  fi
fi

info "Копирую локальные исходники форка"
SOURCE="$WORK/source"
mkdir -p "$SOURCE"
cp -a "$KERNEL_DIR/." "$SOURCE/"

info "Накладываю расширение панели (wdtt_server_patch.py)"
python3 "$PANEL_PATCH" "$SOURCE" || die "Не удалось применить расширение панели"

mkdir -p "$INSTALL_DIR"
info "go build ./server -> $INSTALL_DIR/wdtt-server"
(
  cd "$SOURCE"
  export PATH="$ENV_DIR/go/bin:$PATH"
  export GOPATH="$ENV_DIR/gopath"
  export GOMODCACHE="$ENV_DIR/gopath/pkg/mod"
  export GOCACHE="$ENV_DIR/go-cache"
  export CGO_ENABLED=0
  export GOOS GOARCH
  "$GO_BIN" build -mod=mod -trimpath -ldflags='-s -w' -o "$INSTALL_DIR/wdtt-server" ./server
)
chmod 0755 "$INSTALL_DIR/wdtt-server"
[ -x "$INSTALL_DIR/wdtt-server" ] || die "Сборка не дала исполняемый файл"

info "Готово: $INSTALL_DIR/wdtt-server"
info "Метка расширения в бинарнике:"
LC_ALL=C grep -aoF 'wdtt-panel-extension-v9' "$INSTALL_DIR/wdtt-server" | head -1 || true
info "Установка: sudo install -m 0755 $INSTALL_DIR/wdtt-server /usr/local/bin/wdtt-server && sudo systemctl restart wdtt"