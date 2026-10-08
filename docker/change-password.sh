#!/bin/bash
# =============================================================================
# wdtt-change-password — смена пароля панели внутри запущенного контейнера:
#   docker compose exec wdtt wdtt-change-password 'Новый-пароль-от-12-символов'
# Хеш PBKDF2 — тем же кодом, что при установке (wdtt_panel.security).
# Перезапуск панели не требуется: хеш читается из config.json при каждом входе.
# =============================================================================
set -Eeuo pipefail

[ $# -eq 1 ] || { echo "Использование: $0 'новый-пароль-(мин-12-символов)'" >&2; exit 2; }
[ "${#1}" -ge 12 ] || { echo "Пароль должен содержать не менее 12 символов" >&2; exit 1; }

NEW_PASSWORD="$1" python3 - <<'PY'
import json, os, sys
sys.path.insert(0, "/opt/wdtt-panel")
from wdtt_panel.security import hash_password
path = "/etc/wdtt-panel/config.json"
data = json.load(open(path, encoding="utf-8"))
data["password_hash"] = hash_password(os.environ["NEW_PASSWORD"])
tmp = path + ".tmp"
with open(tmp, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
    handle.write("\n")
os.chmod(tmp, 0o640)
os.replace(tmp, path)
print("Пароль панели обновлён")
PY
