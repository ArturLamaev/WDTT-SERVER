#!/bin/bash
# =============================================================================
# wdtt-maxdevices — гарантирует полю max_devices записей passwords.json
# целевое значение (WDTT-SERVER: по умолчанию новые ключи получают 10000,
# max_devices <= 0 трактуется ядром как «без ограничения»).
#
# Использование: sudo wdtt-maxdevices [run|verify]
#   run     — привести базу к целевому значению (атомарно, с fsync, 0600);
#   verify  — проверить, что у всех записей max_devices валиден (>=1);
#
# Env:
#   WDTT_DB_FILE=/etc/wdtt/passwords.json
#   WDTT_SERVICE=wdtt.service             (опционально: перезапуск не делаем сами)
#   WDTT_MAX_DEVICES=10000
#   WDTT_MAX_DEVICES_MODE=ensure|raise|set
# =============================================================================
set -euo pipefail

DB_FILE="${WDTT_DB_FILE:-/etc/wdtt/passwords.json}"
MAX_DEVICES="${WDTT_MAX_DEVICES:-10000}"
MODE="${WDTT_MAX_DEVICES_MODE:-ensure}"

[ -f "$DB_FILE" ] || { echo "[wdtt-maxdevices] База не найдена: $DB_FILE — пропуск"; exit 0; }
case "${1:-run}" in
  run|verify) ;;
  *) echo "usage: wdtt-maxdevices [run|verify]" >&2; exit 2 ;;
esac

python3 - "$DB_FILE" "$MAX_DEVICES" "$MODE" "${1:-run}" <<'PY'
import json, os, sys, tempfile

path, target, mode, action = sys.argv[1], int(sys.argv[2]), sys.argv[3], sys.argv[4]

if not os.path.exists(path):
    print(f"[wdtt-maxdevices] База не найдена: {path} — пропуск")
    sys.exit(0)

with open(path, encoding="utf-8") as handle:
    data = json.load(handle)

passwords = data.get("passwords") or {}
if not isinstance(passwords, dict):
    print(f"[wdtt-maxdevices] Раздел passwords не является словарём в {path}")
    sys.exit(0)

if action == "verify":
    print(f"{'пароль':<18} {'max_devices':<12} статус")
    print("-" * 48)
    bad = 0
    for password in sorted(passwords):
        entry = passwords[password]
        if not isinstance(entry, dict):
            continue
        value = entry.get("max_devices")
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        print(f"{password:<18} {str(value) if value is not None else '-':<12} {'OK' if ok else 'НЕТ'}")
        if not ok:
            bad += 1
    print("-" * 48)
    print(f"Всего записей: {len(passwords)}; без поля / < 1: {bad}")
    sys.exit(1 if bad else 0)


def normalize(value):
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value if value >= 0 else 0
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return 0
    return 0


changed = []
for password, entry in passwords.items():
    if not isinstance(entry, dict):
        continue
    current = normalize(entry.get("max_devices"))
    if mode == "set":
        new = target
    elif mode == "raise":
        new = target if current < target else current
    else:  # ensure
        new = target if current < 1 else current
    if new != current:
        entry["max_devices"] = new
        changed.append((password, current, new))

if not changed:
    print(f"[wdtt-maxdevices] Изменений нет: все {len(passwords)} записей уже валидны")
    sys.exit(0)

encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
fd, temp_name = tempfile.mkstemp(prefix="passwords.", suffix=".tmp", dir=os.path.dirname(path))
try:
    with os.fdopen(fd, "wb") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(temp_name, 0o600)
    os.replace(temp_name, path)
    os.chmod(path, 0o600)
finally:
    if os.path.exists(temp_name):
        os.unlink(temp_name)

for password, old, new in changed:
    print(f"[wdtt-maxdevices] {password}: max_devices {old} -> {new}")
print(f"[wdtt-maxdevices] Сохранено: {path}; изменено записей: {len(changed)}")
PY