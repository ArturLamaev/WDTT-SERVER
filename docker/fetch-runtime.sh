#!/bin/bash
# =============================================================================
# wdtt-fetch-runtime — докачать опциональные рантаймы, которых нет в образе:
#   * Xray Core  (XTLS/Xray-core, latest; linux-64 или linux-arm64-v8a)
#   * wgcf       (ViRb3/wgcf, latest; linux_amd64 или linux_arm64) — Cloudflare WARP
# Плюс: geoip.dat/geosite.dat из zip Xray в XRAY_ASSETS (панель их раздаёт,
# если пользователь не задал свои URL).
#
# Идемпотентен: при наличии бинарников только досыпает недостающие geo-файлы.
# Пропуск: WDTT_FETCH_RUNTIME=0. Без интернета — предупреждение, не ошибка.
# =============================================================================
set -Eeuo pipefail

log() { printf '[wdtt-fetch-runtime] %s\n' "$*"; }

XRAY_ASSETS="${WDTT_XRAY_ASSETS:-/var/lib/wdtt-panel-private/xray-assets}"
install -d -m 0700 "$XRAY_ASSETS"

case "$(uname -m)" in
  x86_64|amd64)   XRAY_PATTERN='Xray-linux-64\.zip$';       WGCF_PATTERN='wgcf_[^/]*_linux_amd64$' ;;
  aarch64|arm64)  XRAY_PATTERN='Xray-linux-arm64-v8a\.zip$'; WGCF_PATTERN='wgcf_[^/]*_linux_arm64$' ;;
  *) log "Архитектура $(uname -m) не поддерживается Xray/wgcf — пропуск"; exit 0 ;;
esac

# Резолв URL ассета latest-релиза через GitHub API (как github_asset_url в install.sh)
github_asset_url() {
  python3 - "$1" "$2" <<'PY'
import json, re, sys, urllib.request
repo, pattern = sys.argv[1], sys.argv[2]
request = urllib.request.Request(
    f"https://api.github.com/repos/{repo}/releases/latest",
    headers={"User-Agent": "wdtt-control-panel"},
)
with urllib.request.urlopen(request, timeout=30) as response:
    release = json.load(response)
for asset in release.get("assets", []):
    if re.search(pattern, asset.get("name", "")):
        print(asset["browser_download_url"])
        raise SystemExit(0)
raise SystemExit(2)
PY
}

WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

need_xray=0; need_wgcf=0
[ -x /usr/local/bin/xray ] || need_xray=1
[ -x /usr/local/bin/wgcf ] || need_wgcf=1
[ -f "$XRAY_ASSETS/geoip.dat" ] && [ -f "$XRAY_ASSETS/geosite.dat" ] || need_xray=1

if [ "$need_xray" = "0" ] && [ "$need_wgcf" = "0" ]; then
  log "xray + wgcf на месте, geo-файлы есть — нечего делать"
  exit 0
fi

if [ "$need_xray" = "1" ]; then
  log "Ставлю Xray Core..."
  if xray_url="$(github_asset_url XTLS/Xray-core "$XRAY_PATTERN")"; then
    curl -fsSL --retry 3 "$xray_url" -o "$WORK/xray.zip" \
      && unzip -q "$WORK/xray.zip" -d "$WORK/xray" \
      && install -m 0755 "$(find "$WORK/xray" -type f \( -name Xray -o -name xray \) | head -1)" /usr/local/bin/xray \
      && log "Xray: $(/usr/local/bin/xray version 2>/dev/null | head -1)" \
      || log "WARN: не удалось скачать/распаковать Xray Core"
    for asset in geoip.dat geosite.dat; do
      if [ ! -f "$XRAY_ASSETS/$asset" ] && [ -f "$WORK/xray/$asset" ]; then
        install -m 0600 "$WORK/xray/$asset" "$XRAY_ASSETS/$asset" && log "Geo: $asset"
      fi
    done
  else
    log "WARN: не найден релиз Xray Core (нет интернета?)"
  fi
fi

if [ "$need_wgcf" = "1" ]; then
  log "Ставлю wgcf (Cloudflare WARP)..."
  if wgcf_url="$(github_asset_url ViRb3/wgcf "$WGCF_PATTERN")"; then
    curl -fsSL --retry 3 "$wgcf_url" -o "$WORK/wgcf" \
      && install -m 0755 "$WORK/wgcf" /usr/local/bin/wgcf \
      && log "wgcf: $(/usr/local/bin/wgcf --version 2>/dev/null || true)" \
      || log "WARN: не удалось скачать wgcf"
  else
    log "WARN: не найден релиз wgcf (нет интернета?)"
  fi
fi
