from __future__ import annotations

import base64
import errno
import ipaddress
import json
import os
import re
import shlex
import shutil
import signal
import socket
import threading
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable

from .core import (
    DEFAULT_MAX_DEVICES,
    DEFAULT_TRAFFIC_GIB_PER_MONTH,
    GIB,
    MAX_USERS,
    ValidationError,
    add_calendar_months,
    entry_device_ids,
    generate_password,
    is_expired,
    normalize_hash,
    normalize_hashes,
    normalize_max_devices,
    normalize_speed_mbps,
    normalize_user_label,
    parse_expiration,
    traffic_quota,
    user_label_from_entry,
    user_view,
    validate_password,
    validate_ports,
    validate_public_host,
)


DB_FILE = Path(os.environ.get("WDTT_DB_FILE", "/etc/wdtt/passwords.json"))
PANEL_STATE_DB = Path(os.environ.get("WDTT_PANEL_STATE", "/var/lib/wdtt-panel/panel.db"))
VK_HASH_LIMIT = 4
PANEL_LABELS_FILE = Path(os.environ.get("WDTT_PANEL_LABELS_FILE", "/var/lib/wdtt-panel-private/user-labels.json"))
WDTT_EXTENSION_STATE = Path(os.environ.get("WDTT_EXTENSION_STATE", "/var/lib/wdtt-panel-private/wdtt-extensions.json"))
WDTT_EXTENSION_MARKER = "wdtt-panel-extension-v10"
STATS_FILE = Path(os.environ.get("WDTT_STATS_FILE", "/etc/wdtt/server.log"))
BACKUP_DIR = Path(os.environ.get("WDTT_BACKUP_DIR", "/var/lib/wdtt-panel-private/backups"))
BACKUP_FORMAT = "wdtt-panel-backup-v1"
BACKUP_CONTENT_LIMIT = 3 * 1024 * 1024
BACKUP_SCHEDULE_FILE = Path(os.environ.get("WDTT_BACKUP_SCHEDULE_FILE", "/var/lib/wdtt-panel-private/backup-schedule.json"))
BACKUP_TIMER_NAME = "wdtt-panel-backup.timer"
BACKUP_SERVICE_NAME = "wdtt-panel-backup.service"
BACKUP_TIMER_FILE = Path(os.environ.get("WDTT_BACKUP_TIMER_FILE", f"/etc/systemd/system/{BACKUP_TIMER_NAME}"))
BACKUP_SERVICE_FILE = Path(os.environ.get("WDTT_BACKUP_SERVICE_FILE", f"/etc/systemd/system/{BACKUP_SERVICE_NAME}"))
BACKUP_RUNNER = Path(os.environ.get("WDTT_BACKUP_RUNNER", "/usr/local/sbin/wdtt-panel-backup"))
AUTOCLEAN_SETTINGS_FILE = Path(os.environ.get("WDTT_AUTOCLEAN_SETTINGS_FILE", "/var/lib/wdtt-panel-private/auto-clean.json"))
AUTOCLEAN_TIMER_NAME = "wdtt-panel-autoclean.timer"
AUTOCLEAN_SERVICE_NAME = "wdtt-panel-autoclean.service"
AUTOCLEAN_TIMER_FILE = Path(os.environ.get("WDTT_AUTOCLEAN_TIMER_FILE", f"/etc/systemd/system/{AUTOCLEAN_TIMER_NAME}"))
AUTOCLEAN_SERVICE_FILE = Path(os.environ.get("WDTT_AUTOCLEAN_SERVICE_FILE", f"/etc/systemd/system/{AUTOCLEAN_SERVICE_NAME}"))
AUTOCLEAN_RUNNER = Path(os.environ.get("WDTT_AUTOCLEAN_RUNNER", "/usr/local/sbin/wdtt-panel-autoclean"))
WDTT_UNIT_FILE = Path(os.environ.get("WDTT_UNIT_FILE", "/etc/systemd/system/wdtt.service"))
WDTT_BOT_TOKEN_FILE = Path(os.environ.get("WDTT_BOT_TOKEN_FILE", "/etc/wdtt/bot.token"))
WDTT_SETTINGS_FILE = Path(os.environ.get("WDTT_SETTINGS_FILE", "/var/lib/wdtt-panel-private/wdtt-settings.json"))
LOCK_FILE = Path(os.environ.get("WDTT_LOCK_FILE", "/var/lib/wdtt-panel-private/admin.lock"))
SERVICE = os.environ.get("WDTT_SERVICE", "wdtt.service")
SKIP_SYSTEMD = os.environ.get("WDTT_SKIP_SYSTEMD") == "1"
MAX_INPUT = 90 * 1024 * 1024
PANEL_RENEW_COMMAND = Path(os.environ.get("WDTT_PANEL_RENEW_COMMAND", "/opt/wdtt-panel/install.sh"))
PANEL_SELF_UPDATE_COMMAND = Path(
    os.environ.get("WDTT_PANEL_SELF_UPDATE_COMMAND", "/usr/local/sbin/wdtt-panel-self-update")
)
PANEL_UPDATE_STATUS_FILE = Path(
    os.environ.get("WDTT_PANEL_UPDATE_STATUS_FILE", "/var/lib/wdtt-panel-private/self-update-status.json")
)
TAILSCALE_DIR = Path(os.environ.get("WDTT_TAILSCALE_DIR", "/var/lib/wdtt-panel-private/tailscale"))
TAILSCALE_SETTINGS = Path(
    os.environ.get("WDTT_TAILSCALE_SETTINGS", "/var/lib/wdtt-panel-private/tailscale-settings.json")
)
TAILSCALE_AUTHKEY_FILE = Path(
    os.environ.get("WDTT_TAILSCALE_AUTHKEY_FILE", "/var/lib/wdtt-panel-private/tailscale/auth-key")
)
TAILSCALE_INSTALL_COMMAND = Path(
    os.environ.get("WDTT_TAILSCALE_INSTALL_COMMAND", "/opt/wdtt-panel/install.sh")
)
TAILSCALED_SERVICE = os.environ.get("WDTT_TAILSCALED_SERVICE", "tailscaled.service")
# wdtt-proxy: SOCKS5-шлюз (sing-box tun + policy routing), монолитно входит в
# этот репозиторий (каталог wdtt-proxy/) и ставится установщиком панели в
# /opt/wdtt-proxy — рядом с самой панелью. Панель управляет службой
# (systemd-юнит на хосте, скрипт — в Docker) и редактирует config.env.
PROXY_SERVICE = os.environ.get("WDTT_PROXY_SERVICE", "wdtt-proxy.service")
PROXY_DIR = Path(os.environ.get("WDTT_PROXY_DIR", "/opt/wdtt-proxy"))
PROXY_INSTALL_COMMAND = Path(os.environ.get("WDTT_PROXY_INSTALL_COMMAND", "/opt/wdtt-panel/install.sh"))
# В Docker установщик приходит не только из образа, но и монтированием из
# репозитория (docker/docker-compose.yml), поэтому путь по умолчанию — лишь
# первый кандидат: панель берёт первый реально существующий install.sh.
PROXY_INSTALL_FALLBACK_COMMAND = Path("/opt/wdtt-panel/install.sh")
INSTALL_LOG_FILE = Path(os.environ.get("WDTT_INSTALL_LOG_FILE", "/var/log/wdtt-panel-install.log"))
NGINX_ACCESS_LOG = Path(os.environ.get("WDTT_NGINX_ACCESS_LOG", "/var/log/nginx/access.log"))
NGINX_ERROR_LOG = Path(os.environ.get("WDTT_NGINX_ERROR_LOG", "/var/log/nginx/error.log"))
VK_HASH_LIBRARY_FORMAT = "wdtt-panel-vk-hash-library-v1"


class AdminError(RuntimeError):
    pass


def run(
    command: list[str], timeout: int = 20, check: bool = False, cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=check,
        cwd=cwd,
        env={**os.environ, **env} if env else None,
    )


def run_transient_unit(command: list[str], timeout: int = 20) -> subprocess.CompletedProcess[str]:
    """systemd-run для одноразовых юнитов.

    --collect появился только в systemd 235, на старых системах
    (Astra Linux 2.12, systemd 232) опция неизвестна — повторяем без неё.
    """
    result = run(command, timeout=timeout)
    if result.returncode == 0 or "--collect" not in command:
        return result
    stderr = (result.stderr or "").lower()
    if "unrecognized option" in stderr or "unknown option" in stderr:
        fallback = [arg for arg in command if arg != "--collect"]
        return run(fallback, timeout=timeout)
    return result


def service_active() -> bool:
    if SKIP_SYSTEMD:
        return False
    result = run(["systemctl", "is-active", "--quiet", SERVICE])
    return result.returncode == 0


def service_exists() -> bool:
    if SKIP_SYSTEMD:
        return False
    result = run(["systemctl", "show", SERVICE, "--property=LoadState", "--value"])
    return result.returncode == 0 and result.stdout.strip() not in {"", "not-found"}


def service_action(action: str) -> dict[str, Any]:
    if action not in {"start", "stop", "restart"}:
        raise ValidationError("Недопустимое действие сервиса")
    if SKIP_SYSTEMD:
        return {"action": action, "state": "test"}
    result = run(["systemctl", action, SERVICE], timeout=45)
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or f"Не удалось выполнить systemctl {action}")
    return {"action": action, "active": service_active()}


def is_docker() -> bool:
    return os.environ.get("WDTT_DOCKER", "") not in ("", "0", "false", "no")


def docker_wdtt_process_running() -> bool:
    """Жив ли wdtt-server в контейнере: тот же критерий, что в healthcheck.sh."""
    try:
        result = run(["pidof", "wdtt-server"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and bool((result.stdout or "").strip())


def docker_panel_https_ok(port: int, base_path: str) -> bool:
    """Отвечает ли панель по HTTPS через локальный nginx (как healthcheck.sh)."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    path = str(base_path or "/")
    if not path.startswith("/"):
        path = "/" + path
    if not 1 <= port <= 65535:
        return False
    try:
        context = ssl._create_unverified_context()
        with socket.create_connection(("127.0.0.1", port), timeout=4) as raw:
            raw.settimeout(4)
            with context.wrap_socket(raw) as tls:
                tls.sendall(
                    ("GET {} HTTP/1.0\r\nHost: 127.0.0.1\r\n"
                     "Connection: close\r\n\r\n").format(path).encode("ascii")
                )
                data = b""
                while len(data) < 8192:
                    try:
                        chunk = tls.recv(1024)
                    except socket.timeout:
                        break
                    if not chunk:
                        break
                    data += chunk
    except OSError:
        return False
    status_line = data.decode("latin-1", "replace").split("\r\n", 1)[0]
    parts = status_line.split(" ")
    return len(parts) >= 2 and parts[1] in {"200", "302", "303"}


def docker_service_active(port: int, base_path: str) -> bool:
    """Критерий 'WDTT активен' в Docker: процесс ядра + отвечающая панель."""
    return docker_wdtt_process_running() and docker_panel_https_ok(port, base_path)


def read_ip_forward() -> str:
    """Состояние IPv4 forwarding: sysctl, fallback — /proc (работает и в Docker)."""
    try:
        forward = run(["sysctl", "-n", "net.ipv4.ip_forward"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        forward = None
    if forward is not None and forward.returncode == 0:
        return forward.stdout.strip() or "unknown"
    try:
        return Path("/proc/sys/net/ipv4/ip_forward").read_text(encoding="utf-8").strip() or "unknown"
    except OSError:
        return "unknown"


def wdtt_listener_open(port: int) -> bool:
    """Слушается ли UDP-порт ядра (direct/raw): проверка через ss, без
    systemd-гейта — работает и в Docker. Порт 0/вне диапазона = выключено."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        return False
    if not 1 <= port <= 65535:
        return False
    try:
        listener = run(["ss", "-lun", f"sport = :{port}"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return listener.returncode == 0 and "UNCONN" in listener.stdout


def docker_wdtt_pid() -> int | None:
    """PID ядра в контейнере: pidof, fallback — pid-файл супервизора."""
    try:
        result = run(["pidof", "wdtt-server"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        result = None
    if result is not None and result.returncode == 0:
        for token in (result.stdout or "").split():
            try:
                return int(token)
            except ValueError:
                continue
    try:
        return int(Path("/run/wdtt/wdtt.pid").read_text(encoding="utf-8").strip().split()[0])
    except (OSError, ValueError, IndexError):
        return None


def reload_docker_kernel() -> bool:
    """Перезагрузить БД ядра без разрыва туннелей (SIGHUP → reloadDB).

    Best-effort: неудача не роняет операцию панели — файл уже записан
    и подхватится при следующем рестарте ядра.
    """
    sighup = getattr(signal, "SIGHUP", None)
    pid = docker_wdtt_pid()
    if sighup is None or pid is None:
        # WARN — только в stderr: stdout зарезервирован под JSON-ответ
        # админ-хелпера, лишний вывод роняет json.loads в app.py.
        print("[wdtt-panel] WARN: ядро wdtt-server не найдено, SIGHUP пропущен",
              file=sys.stderr, flush=True)
        return False
    try:
        os.kill(pid, sighup)
    except (OSError, ValueError) as exc:
        print(f"[wdtt-panel] WARN: SIGHUP ядру (pid {pid}) не доставлен: {exc}",
              file=sys.stderr, flush=True)
        return False
    return True


def normalize_telegram_admin_id(value: str) -> str:
    value = (value or "").strip()
    if value and not re.fullmatch(r"-?[0-9]{1,20}", value):
        raise ValidationError("Telegram Admin ID должен быть числовым chat_id")
    return value


def normalize_telegram_bot_token(value: str) -> str:
    value = (value or "").strip()
    if value and not re.fullmatch(r"[0-9]{5,20}:[A-Za-z0-9_-]{20,200}", value):
        raise ValidationError("Telegram Bot Token должен быть в формате 123456:ABC...")
    return value


def mask_telegram_bot_token(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 12:
        return "***"
    return f"{value[:6]}...{value[-4:]}"


def systemd_exec_token(value: str) -> str:
    if re.fullmatch(r"[^\s\"'\\]+", value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def set_wdtt_service_telegram(admin_id: str, bot_token: str) -> None:
    if SKIP_SYSTEMD:
        return
    if not WDTT_UNIT_FILE.is_file():
        raise AdminError(f"Не найден {WDTT_UNIT_FILE}; настройте Telegram в wdtt.service вручную")
    lines = WDTT_UNIT_FILE.read_text(encoding="utf-8").splitlines()
    changed = False
    found = False
    next_lines: list[str] = []
    for line in lines:
        if not line.startswith("ExecStart="):
            next_lines.append(line)
            continue
        found = True
        command = line.split("=", 1)[1]
        try:
            tokens = shlex.split(command)
        except ValueError as exc:
            raise AdminError(f"Не удалось разобрать ExecStart в {WDTT_UNIT_FILE}: {exc}") from exc
        clean: list[str] = []
        skip_next = False
        for token in tokens:
            if skip_next:
                skip_next = False
                continue
            if token in {"-admin", "--admin", "-bot-token", "--bot-token", "-bot-token-file", "--bot-token-file"}:
                skip_next = True
                continue
            if token.startswith(("-admin=", "--admin=", "-bot-token=", "--bot-token=", "-bot-token-file=", "--bot-token-file=")):
                continue
            clean.append(token)
        if admin_id and bot_token:
            clean.extend(["-admin", admin_id, "-bot-token-file", str(WDTT_BOT_TOKEN_FILE)])
        next_line = "ExecStart=" + " ".join(systemd_exec_token(token) for token in clean)
        changed = changed or next_line != line
        next_lines.append(next_line)
    if not found:
        raise AdminError(f"В {WDTT_UNIT_FILE} не найден ExecStart")
    if admin_id and bot_token:
        write_private_text(WDTT_BOT_TOKEN_FILE, bot_token + "\n")
    else:
        WDTT_BOT_TOKEN_FILE.unlink(missing_ok=True)
    if changed:
        write_systemd_unit(WDTT_UNIT_FILE, "\n".join(next_lines) + "\n")
        reloaded = run(["systemctl", "daemon-reload"], timeout=45)
        if reloaded.returncode != 0:
            raise AdminError(reloaded.stderr.strip() or "Не удалось обновить systemd после настройки Telegram")


def telegram_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = load_database()
    admin_id = str(data.get("admin_id") or "")
    bot_token = str(data.get("bot_token") or "")
    return {
        "enabled": bool(admin_id and bot_token),
        "admin_id": admin_id,
        "bot_token_set": bool(bot_token),
        "bot_token_hint": mask_telegram_bot_token(bot_token),
        "service_active": service_active(),
    }


def configure_telegram(payload: dict[str, Any]) -> dict[str, Any]:
    current = load_database()
    enabled = bool(payload.get("enabled"))
    admin_id = ""
    bot_token = ""
    if enabled:
        admin_id = normalize_telegram_admin_id(str(payload.get("admin_id") or ""))
        bot_token = normalize_telegram_bot_token(str(payload.get("bot_token") or current.get("bot_token") or ""))
        if not admin_id or not bot_token:
            raise ValidationError("Для включения Telegram укажите Admin ID и Bot Token")
    was_active = service_active()
    if was_active:
        service_action("stop")
    try:
        data = load_database()
        create_backup("telegram-settings")
        data["admin_id"] = admin_id
        data["bot_token"] = bot_token
        save_database(data)
        set_wdtt_service_telegram(admin_id, bot_token)
    except Exception:
        if was_active:
            service_action("start")
        raise
    if was_active:
        service_action("start")
    return telegram_status({})


def telegram_test(payload: dict[str, Any]) -> dict[str, Any]:
    data = load_database()
    admin_id = normalize_telegram_admin_id(str(data.get("admin_id") or ""))
    bot_token = normalize_telegram_bot_token(str(data.get("bot_token") or ""))
    if not admin_id or not bot_token:
        raise ValidationError("Сначала сохраните Telegram Bot Token и Admin ID")
    message = str(payload.get("message") or "WDTT Control Panel: Telegram bot connected").strip()[:500]
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=json.dumps({"chat_id": admin_id, "text": message}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise AdminError(f"Не удалось отправить тестовое сообщение Telegram: {exc}") from exc
    if not isinstance(result, dict) or not result.get("ok"):
        description = result.get("description") if isinstance(result, dict) else ""
        raise AdminError(f"Telegram вернул ошибку: {description or 'unknown'}")
    return {"sent": True, "admin_id": admin_id}


def empty_database() -> dict[str, Any]:
    return {
        "main_password": "",
        "admin_id": "",
        "bot_token": "",
        "passwords": {},
        "devices": {},
    }


def load_database() -> dict[str, Any]:
    if not DB_FILE.exists():
        return empty_database()
    try:
        data = json.loads(DB_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdminError(f"Не удалось прочитать {DB_FILE}: {exc}") from exc
    if not isinstance(data, dict):
        raise AdminError("База WDTT имеет неверный формат")
    data.setdefault("passwords", {})
    data.setdefault("devices", {})
    if not isinstance(data["passwords"], dict) or not isinstance(data["devices"], dict):
        raise AdminError("Разделы passwords/devices имеют неверный формат")
    return data


def save_database(data: dict[str, Any]) -> None:
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix="passwords.", suffix=".tmp", dir=DB_FILE.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, DB_FILE)
        os.chmod(DB_FILE, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_panel_labels() -> dict[str, str]:
    """Keep labels visible until an older WDTT binary is replaced."""
    try:
        raw = json.loads(PANEL_LABELS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(password): value.strip()
        for password, value in raw.items()
        if isinstance(value, str) and value.strip()
    }


def wdtt_extensions_are_verified() -> bool:
    try:
        state = json.loads(WDTT_EXTENSION_STATE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(state, dict) and state.get("marker") == WDTT_EXTENSION_MARKER


def save_panel_labels(labels: dict[str, str]) -> None:
    PANEL_LABELS_FILE.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(labels, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix="user-labels.", suffix=".tmp", dir=PANEL_LABELS_FILE.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, PANEL_LABELS_FILE)
        os.chmod(PANEL_LABELS_FILE, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def update_panel_label(password: str, label: str, previous_password: str = "") -> None:
    if wdtt_extensions_are_verified():
        return
    labels = load_panel_labels()
    if previous_password and previous_password != password:
        labels.pop(previous_password, None)
    if label:
        labels[password] = label
    else:
        labels.pop(password, None)
    save_panel_labels(labels)


def remove_panel_label(password: str) -> None:
    if wdtt_extensions_are_verified():
        return
    labels = load_panel_labels()
    if password in labels:
        labels.pop(password, None)
        save_panel_labels(labels)


def create_backup(label: str = "auto") -> str:
    if not DB_FILE.exists():
        return ""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_-]", "-", label)[:32]
    stamp = f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"
    name = f"passwords-{stamp}-{safe_label}.json"
    destination = BACKUP_DIR / name
    shutil.copy2(DB_FILE, destination)
    os.chmod(destination, 0o600)
    prune_backups()
    return name


def prune_backups(keep: int = 50) -> None:
    if not BACKUP_DIR.exists():
        return
    files = sorted(BACKUP_DIR.glob("passwords-*.json"), key=lambda item: item.stat().st_mtime)
    for path in files[:-keep]:
        path.unlink(missing_ok=True)


def mutate_database(label: str, mutator: Callable[[dict[str, Any]], Any], backup: bool = True) -> Any:
    was_active = service_active()
    if was_active:
        service_action("stop")
    try:
        data = load_database()
        if backup:
            create_backup(label)
        result = mutator(data)
        save_database(data)
        if SKIP_SYSTEMD:
            # Ядро держит БД в памяти и раз в 60с пишет её на диск (saveDB):
            # без перезагрузки оно затреёт только что записанный файл.
            reload_docker_kernel()
    except Exception:
        if was_active:
            service_action("start")
        raise
    if was_active:
        service_action("start")
    return result


def purge_expired(data: dict[str, Any]) -> int:
    # Expired users stay available for renewal, history and their personal page.
    return 0


def parse_traffic_gib(value: Any, *, default: int | None = None, maximum: int = 10240) -> int:
    if value in (None, ""):
        if default is None:
            raise ValidationError("Укажите объём трафика")
        return default
    try:
        gib = int(value)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Объём трафика должен быть целым числом гигабайт") from exc
    if not 0 <= gib <= maximum:
        raise ValidationError(f"Объём трафика должен быть от 0 до {maximum} ГиБ")
    return gib


def initial_traffic_fields(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("traffic_unlimited"):
        return {
            "traffic_managed": True,
            "traffic_unlimited": True,
            "traffic_baseline_bytes": 0,
            "traffic_primary_bytes": 0,
            "traffic_extra_bytes": 0,
            "traffic_operations": [],
        }
    try:
        months = int(payload.get("months") or 1)
    except (TypeError, ValueError):
        months = 1
    default_gib = DEFAULT_TRAFFIC_GIB_PER_MONTH * months if 1 <= months <= 36 else DEFAULT_TRAFFIC_GIB_PER_MONTH
    primary_gib = parse_traffic_gib(payload.get("traffic_primary_gib"), default=default_gib)
    return {
        "traffic_managed": True,
        "traffic_unlimited": False,
        "traffic_baseline_bytes": 0,
        "traffic_primary_bytes": primary_gib * GIB,
        "traffic_extra_bytes": 0,
        "traffic_operations": [
            {
                "id": f"create:{uuid.uuid4()}",
                "kind": "initial_grant",
                "created_at": int(time.time()),
                "primary_delta_bytes": primary_gib * GIB,
                "extra_delta_bytes": 0,
            }
        ],
    }


def quota_operation_id(payload: dict[str, Any]) -> str:
    value = str(payload.get("operation_id") or uuid.uuid4()).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,128}", value):
        raise ValidationError("Некорректный идентификатор операции")
    return value


def quota_operation_exists(entry: dict[str, Any], operation_id: str) -> bool:
    return any(isinstance(item, dict) and item.get("id") == operation_id for item in entry.get("traffic_operations", []))


def record_quota_operation(entry: dict[str, Any], operation: dict[str, Any]) -> None:
    operations = [item for item in entry.get("traffic_operations", []) if isinstance(item, dict)]
    operations.append(operation)
    entry["traffic_operations"] = operations[-50:]


def set_quota_balance(entry: dict[str, Any], primary_bytes: int, extra_bytes: int) -> None:
    current = max(0, int(entry.get("down_bytes") or 0) + int(entry.get("up_bytes") or 0))
    entry.update(
        {
            "traffic_managed": True,
            "traffic_unlimited": False,
            "traffic_baseline_bytes": current,
            "traffic_primary_bytes": max(0, int(primary_bytes)),
            "traffic_extra_bytes": max(0, int(extra_bytes)),
        }
    )


def entry_with_legacy_label(data: dict[str, Any], password: str, entry: dict[str, Any], panel_labels: dict[str, str] | None = None) -> dict[str, Any]:
    if user_label_from_entry(entry):
        return entry
    for field in ("labels", "remarks", "user_labels", "userLabels", "names", "comments", "tags", "marks"):
        labels = data.get(field)
        value = labels.get(password) if isinstance(labels, dict) else None
        if isinstance(value, str) and value.strip():
            return {**entry, "label": value.strip()}
    fallback = (panel_labels or {}).get(password)
    if fallback:
        return {**entry, "label": fallback}
    return entry


def _admin_device_records(
    data: dict[str, Any], handshakes: dict[str, int], window: int = 75
) -> list[dict[str, Any]]:
    user_device_ids = {
        device_id
        for entry in data.get("passwords", {}).values()
        if isinstance(entry, dict)
        for device_id in entry_device_ids(entry)
    }
    records: list[dict[str, Any]] = []
    for device_id, device in data.get("devices", {}).items():
        if device_id in user_device_ids or not isinstance(device, dict):
            continue
        public_key = str(device.get("pub_key") or device.get("PubKey") or "")
        last_handshake = int(handshakes.get(public_key) or 0)
        records.append(
            {
                "device": device,
                "device_id": device_id,
                "ip": str(device.get("ip") or ""),
                "last_handshake": last_handshake,
                "connected": handshake_is_active(last_handshake, window),
            }
        )
    records.sort(key=lambda record: record["device_id"])
    return records


def list_users() -> dict[str, Any]:
    data = load_database()
    panel_labels = {} if wdtt_extensions_are_verified() else load_panel_labels()
    handshakes = wireguard_handshakes()
    window = int(load_wdtt_settings().get("online_window_s") or 75)
    users = [
        connected_user_view(
            user_view(
                password,
                entry_with_legacy_label(data, password, entry, panel_labels) if isinstance(entry, dict) else {},
                data["devices"],
            ).as_dict(),
            handshakes,
            window,
        )
        for password, entry in data["passwords"].items()
    ]
    users.sort(key=lambda item: (item["expired"], item["is_deactivated"], item["password"]))
    records = _admin_device_records(data, handshakes, window)
    main_traffic_supported = "main_down_bytes" in data or "main_up_bytes" in data
    admins: list[dict[str, Any]] = []
    if data.get("main_password") or records:
        devices = [
            {
                "device_id": record["device_id"],
                "ip": record["ip"],
                "connected": record["connected"],
                "last_handshake": record["last_handshake"],
            }
            for record in records
        ]
        admins.append(
            {
                "password": "Главный пароль",
                "role": "admin",
                "device_id": "multi" if len(devices) > 1 else (devices[0]["device_id"] if devices else ""),
                "device": records[0]["device"] if len(records) == 1 else None,
                "devices": devices,
                "connected": any(record["connected"] for record in records),
                "last_handshake": max((record["last_handshake"] for record in records), default=0),
                "down_bytes": int(data.get("main_down_bytes") or 0),
                "up_bytes": int(data.get("main_up_bytes") or 0),
                "last_upload_at": int(data.get("main_last_upload_at") or 0),
                "last_download_at": int(data.get("main_last_download_at") or 0),
                "traffic_supported": main_traffic_supported,
                "expires_at": 0,
                "label": "Администратор WDTT",
                "vk_hash": "Администратор WDTT",
                "ports": "",
                "is_deactivated": False,
                "expired": False,
            }
        )
    return {
        "users": users,
        "admins": admins,
        "main_password_present": bool(data.get("main_password")),
        "limit": MAX_USERS,
    }


def wireguard_handshakes() -> dict[str, int]:
    handshakes: dict[str, int] = {}
    if SKIP_SYSTEMD:
        return handshakes
    if shutil.which("wg"):
        result = run(["wg", "show", "wdtt0", "dump"], timeout=10)
        if result.returncode == 0:
            for index, line in enumerate(result.stdout.splitlines()):
                fields = line.split("\t")
                if index == 0 or len(fields) < 5:
                    continue
                try:
                    handshakes[fields[0]] = int(fields[4])
                except ValueError:
                    continue
    handshakes.update(userspace_wireguard_handshakes())
    return handshakes


def userspace_wireguard_handshakes() -> dict[str, int]:
    """Read handshakes directly from WDTT's embedded WireGuard UAPI socket.

    WDTT starts WireGuard in userspace.  On servers without wireguard-tools,
    `wg show` cannot query it even though the tunnel is healthy, which made
    connected administrator devices appear offline in the panel.
    """
    raw = ""
    for path in ("/var/run/wireguard/wdtt0.sock", "/run/wireguard/wdtt0.sock"):
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(2)
                client.connect(path)
                client.sendall(b"get=1\n\n")
                chunks: list[bytes] = []
                while True:
                    chunk = client.recv(16384)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if b"\n\n" in b"".join(chunks):
                        break
                raw = b"".join(chunks).decode("utf-8", "replace")
            break
        except OSError:
            continue
    if not raw:
        return {}

    handshakes: dict[str, int] = {}
    public_key = ""
    stamp = 0

    def save_peer() -> None:
        nonlocal public_key, stamp
        if not public_key:
            return
        try:
            key_bytes = bytes.fromhex(public_key)
            handshakes[base64.b64encode(key_bytes).decode("ascii")] = stamp
        except ValueError:
            pass

    for line in raw.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "public_key":
            save_peer()
            public_key, stamp = value, 0
        elif key == "last_handshake_time_sec":
            try:
                stamp = int(value)
            except ValueError:
                stamp = 0
    save_peer()
    return handshakes


def handshake_is_active(stamp: int, window: int = 75) -> bool:
    return stamp > 0 and time.time() - stamp <= window


def connected_user_view(user: dict[str, Any], handshakes: dict[str, int], window: int = 75) -> dict[str, Any]:
    devices = user.get("devices") or []
    if not devices:
        device = user.get("device") or {}
        devices = [
            {
                "device_id": str(user.get("device_id") or ""),
                "ip": str(device.get("ip") or ""),
                "pub_key": str(device.get("pub_key") or device.get("PubKey") or ""),
            }
        ]
    max_handshake = 0
    for item in devices:
        public_key = str(item.get("pub_key") or "")
        last_handshake = int(handshakes.get(public_key) or 0)
        item["connected"] = handshake_is_active(last_handshake, window)
        item["last_handshake"] = last_handshake
        max_handshake = max(max_handshake, last_handshake)
    user["devices"] = devices
    user["connected"] = any(item.get("connected") for item in devices)
    user["last_handshake"] = max_handshake
    user["role"] = "user"
    return user


def split_vk_hashes(value: str) -> list[str]:
    return [item for item in re.split(r"[,\s]+", (value or "").strip()) if item]


def read_vk_hash_library() -> list[str]:
    try:
        if not PANEL_STATE_DB.is_file():
            return []
    except OSError:
        return []
    try:
        connection = sqlite3.connect(PANEL_STATE_DB)
    except sqlite3.Error:
        return []
    try:
        rows = connection.execute(
            "SELECT value FROM vk_hash_library ORDER BY created_at, rowid"
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        connection.close()
    return [str(row[0]) for row in rows if row and row[0]]


def _missing_library_hashes(current: list[str], library: list[str]) -> list[str]:
    missing: list[str] = []
    for candidate in library:
        if len(current) + len(missing) >= VK_HASH_LIMIT:
            break
        try:
            candidate = normalize_hash(str(candidate))
        except ValidationError:
            continue
        if candidate and candidate not in current and candidate not in missing:
            missing.append(candidate)
    return missing


def ensure_four_hashes(entry: dict[str, Any], library: list[str]) -> bool:
    current = split_vk_hashes(str(entry.get("vk_hash") or ""))
    if len(current) >= VK_HASH_LIMIT:
        return False
    missing = _missing_library_hashes(current, library)
    if not missing:
        return False
    entry["vk_hash"] = ",".join((current + missing)[:VK_HASH_LIMIT])
    return True


def entry_needs_hash_fill(entry: dict[str, Any], library: list[str]) -> bool:
    current = split_vk_hashes(str(entry.get("vk_hash") or ""))
    if len(current) >= VK_HASH_LIMIT:
        return False
    return bool(_missing_library_hashes(current, library))


def fill_missing_hashes(payload: dict[str, Any]) -> dict[str, Any]:
    library = read_vk_hash_library()
    summary = {"updated": 0, "scanned": 0, "library": len(library)}
    if not library:
        return summary
    data = load_database()
    entries = [entry for entry in data["passwords"].values() if isinstance(entry, dict)]
    summary["scanned"] = len(entries)
    if not any(entry_needs_hash_fill(entry, library) for entry in entries):
        return summary

    def apply(state: dict[str, Any]) -> dict[str, Any]:
        updated = 0
        for entry in state["passwords"].values():
            if isinstance(entry, dict) and ensure_four_hashes(entry, library):
                updated += 1
        return {"updated": updated, "scanned": summary["scanned"], "library": len(library)}

    return mutate_database("fill-vk-hashes", apply)


def create_user(payload: dict[str, Any]) -> dict[str, Any]:
    requested = str(payload.get("password") or "").strip()
    password = validate_password(requested or generate_password())
    expires_at = parse_expiration(payload)
    raw_vk_hash = str(payload.get("vk_hash") or "").strip()
    vk_hash = normalize_hashes(raw_vk_hash) if raw_vk_hash else ""
    ports = validate_ports(str(payload.get("ports") or "56000,56001,9000"))
    max_devices = normalize_max_devices(payload.get("max_devices"))
    max_down_mbps = normalize_speed_mbps(payload.get("max_down_mbps"))
    max_up_mbps = normalize_speed_mbps(payload.get("max_up_mbps"))
    label = normalize_user_label(str(payload.get("label") or ""))
    library = read_vk_hash_library()
    if not vk_hash and not library:
        raise ValidationError("Укажите хотя бы один VK-хеш")

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        purge_expired(data)
        if password in data["passwords"] or password == data.get("main_password"):
            raise ValidationError("Такой пароль уже существует")
        user_limit = wdtt_effective_user_limit()
        if len(data["passwords"]) >= user_limit:
            raise ValidationError(f"Лимит WDTT: не более {user_limit} пользователей")
        entry = {
            "device_id": "",
            "max_devices": max_devices,
            "max_down_mbps": max_down_mbps,
            "max_up_mbps": max_up_mbps,
            "expires_at": expires_at,
            "down_bytes": 0,
            "up_bytes": 0,
            "label": label,
            "vk_hash": vk_hash,
            "ports": ports,
            "is_deactivated": bool(payload.get("is_deactivated", False)),
            **initial_traffic_fields(payload),
        }
        ensure_four_hashes(entry, library)
        data["passwords"][password] = entry
        return user_view(password, entry, data["devices"]).as_dict()

    result = mutate_database("create", apply)
    update_panel_label(password, label)
    return result


def create_users_bulk(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        count = int(payload.get("count", 1))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Количество пользователей должно быть числом") from exc
    if not 1 <= count <= MAX_USERS:
        raise ValidationError(f"Можно создать от 1 до {MAX_USERS} пользователей")

    hashes = normalize_hashes(str(payload.get("vk_hash") or "")).split(",")
    hash_mode = str(payload.get("hash_mode") or "shared")
    if hash_mode not in {"shared", "rotate"}:
        raise ValidationError("Некорректный режим назначения VK-хешей")
    library = read_vk_hash_library()
    expires_at = parse_expiration(payload)
    ports = validate_ports(str(payload.get("ports") or "56000,56001,9000"))
    max_devices = normalize_max_devices(payload.get("max_devices"))
    max_down_mbps = normalize_speed_mbps(payload.get("max_down_mbps"))
    max_up_mbps = normalize_speed_mbps(payload.get("max_up_mbps"))
    is_deactivated = bool(payload.get("is_deactivated", False))
    label_prefix = normalize_user_label(str(payload.get("label_prefix") or ""))

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        purge_expired(data)
        user_limit = wdtt_effective_user_limit()
        available = user_limit - len(data["passwords"])
        if count > available:
            raise ValidationError(f"Доступно мест: {available}; запрошено пользователей: {count}")

        created: list[dict[str, Any]] = []
        reserved = set(data["passwords"])
        reserved.add(str(data.get("main_password") or ""))
        for index in range(count):
            password = generate_password()
            while password in reserved:
                password = generate_password()
            reserved.add(password)
            assigned_hashes = hashes if hash_mode == "shared" else [hashes[index % len(hashes)]]
            label = ""
            if label_prefix:
                suffix = f" {index + 1}" if count > 1 else ""
                label = normalize_user_label(f"{label_prefix}{suffix}")
            entry = {
                "device_id": "",
                "max_devices": max_devices,
                "max_down_mbps": max_down_mbps,
                "max_up_mbps": max_up_mbps,
                "expires_at": expires_at,
                "down_bytes": 0,
                "up_bytes": 0,
                "label": label,
                "vk_hash": ",".join(assigned_hashes),
                "ports": ports,
                "is_deactivated": is_deactivated,
                **initial_traffic_fields(payload),
            }
            ensure_four_hashes(entry, library)
            data["passwords"][password] = entry
            created.append(user_view(password, entry, data["devices"]).as_dict())
        return {"users": created, "count": len(created)}

    result = mutate_database("bulk-create", apply)
    for user in result["users"]:
        update_panel_label(user["password"], str(user.get("label") or ""))
    return result


def update_user(payload: dict[str, Any]) -> dict[str, Any]:
    current = validate_password(str(payload.get("current_password") or ""))
    replacement_raw = str(payload.get("password") or current).strip()
    replacement = validate_password(replacement_raw)
    library = read_vk_hash_library()

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].get(current)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        if replacement != current:
            if replacement in data["passwords"] or replacement == data.get("main_password"):
                raise ValidationError("Такой пароль уже существует")
            del data["passwords"][current]
            data["passwords"][replacement] = entry
        if "vk_hash" in payload:
            entry["vk_hash"] = normalize_hashes(str(payload["vk_hash"]))
            ensure_four_hashes(entry, library)
        if "ports" in payload:
            entry["ports"] = validate_ports(str(payload["ports"]))
        if "max_devices" in payload:
            entry["max_devices"] = normalize_max_devices(payload["max_devices"])
        if "max_down_mbps" in payload:
            entry["max_down_mbps"] = normalize_speed_mbps(payload["max_down_mbps"])
        if "max_up_mbps" in payload:
            entry["max_up_mbps"] = normalize_speed_mbps(payload["max_up_mbps"])
        if "label" in payload:
            entry["label"] = normalize_user_label(str(payload["label"] or ""))
        if any(key in payload for key in ("days", "expires_at", "unlimited")):
            entry["expires_at"] = parse_expiration(payload)
        if "is_deactivated" in payload:
            entry["is_deactivated"] = bool(payload["is_deactivated"])
        if "traffic_unlimited" in payload:
            if payload.get("traffic_unlimited"):
                entry.update(
                    {
                        "traffic_managed": True,
                        "traffic_unlimited": True,
                        "traffic_baseline_bytes": 0,
                        "traffic_primary_bytes": 0,
                        "traffic_extra_bytes": 0,
                    }
                )
            else:
                quota = traffic_quota(entry)
                primary_gib = parse_traffic_gib(payload.get("traffic_primary_gib"), default=DEFAULT_TRAFFIC_GIB_PER_MONTH)
                set_quota_balance(entry, primary_gib * GIB, int(quota["traffic_extra_remaining_bytes"]))
        return user_view(replacement, entry, data["devices"]).as_dict()

    result = mutate_database("update", apply)
    if "label" in payload:
        update_panel_label(replacement, normalize_user_label(str(payload["label"] or "")), current)
    elif replacement != current:
        labels = load_panel_labels()
        if current in labels:
            labels[replacement] = labels.pop(current)
            save_panel_labels(labels)
    return result


def delete_user(payload: dict[str, Any]) -> dict[str, Any]:
    password = validate_password(str(payload.get("password") or ""))

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].pop(password, None)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        for device_id in entry_device_ids(entry):
            data["devices"].pop(device_id, None)
        return {"deleted": password}

    result = mutate_database("delete", apply)
    remove_panel_label(password)
    return result


def unbind_user(payload: dict[str, Any]) -> dict[str, Any]:
    password = validate_password(str(payload.get("password") or ""))

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].get(password)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        for device_id in entry_device_ids(entry):
            data["devices"].pop(device_id, None)
        entry["device_id"] = ""
        entry["device_ids"] = []
        return user_view(password, entry, data["devices"]).as_dict()

    return mutate_database("unbind", apply)


def reset_traffic(payload: dict[str, Any]) -> dict[str, Any]:
    password = validate_password(str(payload.get("password") or ""))

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].get(password)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        quota = traffic_quota(entry)
        entry["down_bytes"] = 0
        entry["up_bytes"] = 0
        if quota["traffic_managed"] and not quota["traffic_unlimited"]:
            entry["traffic_baseline_bytes"] = 0
            entry["traffic_primary_bytes"] = int(quota["traffic_primary_remaining_bytes"])
            entry["traffic_extra_bytes"] = int(quota["traffic_extra_remaining_bytes"])
        return user_view(password, entry, data["devices"]).as_dict()

    return mutate_database("traffic-reset", apply)


def renew_user(payload: dict[str, Any]) -> dict[str, Any]:
    password = validate_password(str(payload.get("password") or ""))
    operation_id = quota_operation_id(payload)
    try:
        months = int(payload.get("months") or 0)
    except (TypeError, ValueError) as exc:
        raise ValidationError("Срок в месяцах должен быть числом") from exc
    if months and not 1 <= months <= 36:
        raise ValidationError("Срок должен быть от 1 до 36 месяцев")
    if not months and payload.get("expires_at") in (None, ""):
        raise ValidationError("Укажите срок продления или дату окончания")
    if not months and payload.get("traffic_primary_gib") in (None, ""):
        raise ValidationError("Для произвольной даты укажите новый основной трафик")
    primary_gib = parse_traffic_gib(
        payload.get("traffic_primary_gib"),
        default=DEFAULT_TRAFFIC_GIB_PER_MONTH * months if months else 0,
    )
    comment = str(payload.get("comment") or "").strip()[:200]

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].get(password)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        if quota_operation_exists(entry, operation_id):
            return {**user_view(password, entry, data["devices"]).as_dict(), "duplicate": True}
        now = int(time.time())
        old_expiry = int(entry.get("expires_at") or 0)
        active = old_expiry > now
        if months:
            entry["expires_at"] = add_calendar_months(old_expiry if active else now, months)
        else:
            entry["expires_at"] = parse_expiration({"expires_at": payload.get("expires_at")}, now)
        quota = traffic_quota(entry)
        carry_primary = int(quota["traffic_primary_remaining_bytes"]) if active and not quota["traffic_unlimited"] else 0
        carry_extra = int(quota["traffic_extra_remaining_bytes"]) if not quota["traffic_unlimited"] else 0
        grant_bytes = primary_gib * GIB
        set_quota_balance(entry, carry_primary + grant_bytes, carry_extra)
        entry["is_deactivated"] = False
        record_quota_operation(
            entry,
            {
                "id": operation_id,
                "kind": "renewal" if active else "reactivation",
                "created_at": now,
                "months": months,
                "primary_delta_bytes": grant_bytes,
                "extra_delta_bytes": 0,
                "comment": comment,
            },
        )
        return user_view(password, entry, data["devices"]).as_dict()

    return mutate_database("traffic-renew", apply)


def add_user_traffic(payload: dict[str, Any]) -> dict[str, Any]:
    password = validate_password(str(payload.get("password") or ""))
    operation_id = quota_operation_id(payload)
    gib = parse_traffic_gib(payload.get("gib"))
    if gib <= 0:
        raise ValidationError("Дополнительный трафик должен быть больше нуля")
    comment = str(payload.get("comment") or "").strip()[:200]

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].get(password)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        if quota_operation_exists(entry, operation_id):
            return {**user_view(password, entry, data["devices"]).as_dict(), "duplicate": True}
        quota = traffic_quota(entry)
        if quota["traffic_unlimited"]:
            raise ValidationError("Для безлимитного пользователя дополнительный пакет не требуется")
        grant_bytes = gib * GIB
        set_quota_balance(
            entry,
            int(quota["traffic_primary_remaining_bytes"]),
            int(quota["traffic_extra_remaining_bytes"]) + grant_bytes,
        )
        record_quota_operation(
            entry,
            {
                "id": operation_id,
                "kind": "extra_add",
                "created_at": int(time.time()),
                "primary_delta_bytes": 0,
                "extra_delta_bytes": grant_bytes,
                "comment": comment,
            },
        )
        return user_view(password, entry, data["devices"]).as_dict()

    return mutate_database("traffic-extra", apply)


def adjust_user_plan(payload: dict[str, Any]) -> dict[str, Any]:
    password = validate_password(str(payload.get("password") or ""))
    operation_id = quota_operation_id(payload)
    expiration_mode = str(payload.get("expiration_mode") or "keep")
    traffic_mode = str(payload.get("traffic_mode") or "keep")
    if expiration_mode not in {"keep", "adjust_days", "set_date", "unlimited"}:
        raise ValidationError("Неизвестный способ изменения срока")
    if traffic_mode not in {"keep", "add", "subtract", "set", "unlimited"}:
        raise ValidationError("Неизвестный способ изменения трафика")
    if expiration_mode == "keep" and traffic_mode == "keep":
        raise ValidationError("Выберите изменение срока или трафика")

    days_delta = 0
    if expiration_mode == "adjust_days":
        try:
            days_delta = int(payload.get("days_delta") or 0)
        except (TypeError, ValueError) as exc:
            raise ValidationError("Изменение срока должно быть числом дней") from exc
        if days_delta == 0 or not -3650 <= days_delta <= 3650:
            raise ValidationError("Изменение срока должно быть от -3650 до 3650 дней и не равно нулю")

    traffic_gib = 0
    if traffic_mode in {"add", "subtract", "set"}:
        traffic_gib = parse_traffic_gib(payload.get("traffic_gib"))
        if traffic_mode in {"add", "subtract"} and traffic_gib <= 0:
            raise ValidationError("Для добавления или уменьшения укажите объём больше нуля")
    comment = str(payload.get("comment") or "").strip()[:200]

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        entry = data["passwords"].get(password)
        if not isinstance(entry, dict):
            raise ValidationError("Пользователь не найден")
        if quota_operation_exists(entry, operation_id):
            return {**user_view(password, entry, data["devices"]).as_dict(), "duplicate": True}

        now = int(time.time())
        old_expiry = int(entry.get("expires_at") or 0)
        new_expiry = old_expiry
        if expiration_mode == "unlimited":
            new_expiry = 0
        elif expiration_mode == "set_date":
            new_expiry = parse_expiration({"expires_at": payload.get("expires_at")}, now)
        elif expiration_mode == "adjust_days":
            if old_expiry <= 0:
                raise ValidationError("Для бессрочного пользователя сначала задайте дату окончания")
            new_expiry = old_expiry + days_delta * 86400
            if new_expiry <= now:
                raise ValidationError("После уменьшения дата окончания должна оставаться в будущем")
        entry["expires_at"] = new_expiry

        quota = traffic_quota(entry)
        old_primary = int(quota["traffic_primary_remaining_bytes"])
        old_extra = int(quota["traffic_extra_remaining_bytes"])
        new_primary, new_extra = old_primary, old_extra
        if traffic_mode == "unlimited":
            entry.update(
                {
                    "traffic_managed": True,
                    "traffic_unlimited": True,
                    "traffic_baseline_bytes": 0,
                    "traffic_primary_bytes": 0,
                    "traffic_extra_bytes": 0,
                }
            )
            new_primary, new_extra = 0, 0
        elif traffic_mode != "keep":
            amount = traffic_gib * GIB
            if quota["traffic_unlimited"]:
                old_primary, old_extra = 0, 0
            if traffic_mode == "add":
                new_extra = old_extra + amount
            elif traffic_mode == "subtract":
                if amount > old_primary + old_extra:
                    raise ValidationError("Нельзя убрать больше оставшегося трафика")
                from_extra = min(old_extra, amount)
                new_extra = old_extra - from_extra
                new_primary = old_primary - (amount - from_extra)
            else:
                new_primary, new_extra = amount, 0
            set_quota_balance(entry, new_primary, new_extra)

        record_quota_operation(
            entry,
            {
                "id": operation_id,
                "kind": "manual_adjustment",
                "created_at": now,
                "expiration_mode": expiration_mode,
                "old_expires_at": old_expiry,
                "new_expires_at": new_expiry,
                "traffic_mode": traffic_mode,
                "primary_delta_bytes": new_primary - old_primary,
                "extra_delta_bytes": new_extra - old_extra,
                "comment": comment,
            },
        )
        return user_view(password, entry, data["devices"]).as_dict()

    return mutate_database("plan-adjust", apply)


def bulk_user_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    actions = {
        "activate": "Активация",
        "deactivate": "Деактивация",
        "set_expiration": "Изменение срока",
        "reset_traffic": "Сброс трафика",
        "unbind": "Отвязка устройств",
        "delete": "Удаление",
    }
    if action not in actions:
        raise ValidationError("Неизвестное массовое действие")
    raw_passwords = payload.get("passwords")
    if not isinstance(raw_passwords, list):
        raise ValidationError("Выберите хотя бы одного пользователя")
    passwords = list(dict.fromkeys(validate_password(str(item)) for item in raw_passwords))
    if not passwords or len(passwords) > MAX_USERS:
        raise ValidationError(f"Можно выбрать от 1 до {MAX_USERS} пользователей")
    expires_at = parse_expiration({"days": payload.get("days")}) if action == "set_expiration" else None

    def apply(data: dict[str, Any]) -> dict[str, Any]:
        missing = [password for password in passwords if not isinstance(data["passwords"].get(password), dict)]
        if missing:
            raise ValidationError("Часть выбранных пользователей уже не существует")
        for password in passwords:
            entry = data["passwords"][password]
            if action == "activate":
                entry["is_deactivated"] = False
            elif action == "deactivate":
                entry["is_deactivated"] = True
            elif action == "set_expiration":
                entry["expires_at"] = expires_at
            elif action == "reset_traffic":
                quota = traffic_quota(entry)
                entry["down_bytes"] = 0
                entry["up_bytes"] = 0
                if quota["traffic_managed"] and not quota["traffic_unlimited"]:
                    entry["traffic_baseline_bytes"] = 0
                    entry["traffic_primary_bytes"] = int(quota["traffic_primary_remaining_bytes"])
                    entry["traffic_extra_bytes"] = int(quota["traffic_extra_remaining_bytes"])
            elif action == "unbind":
                for device_id in entry_device_ids(entry):
                    data["devices"].pop(device_id, None)
                entry["device_id"] = ""
                entry["device_ids"] = []
            elif action == "delete":
                for device_id in entry_device_ids(entry):
                    data["devices"].pop(device_id, None)
                del data["passwords"][password]
        return {"action": action, "count": len(passwords)}

    return mutate_database(f"bulk-{action}", apply)


def backup_stamp() -> str:
    return f"{time.strftime('%Y%m%d-%H%M%S')}-{time.time_ns() % 1_000_000:06d}"


def backup_name(kind: str, label: str) -> str:
    safe_label = re.sub(r"[^A-Za-z0-9_-]", "-", label)[:32] or "manual"
    return f"{kind}-{backup_stamp()}-{safe_label}.json"


def read_private_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def read_private_text(path: Path) -> str | None:
    try:
        value = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    return value if len(value.encode("utf-8")) <= 512 * 1024 else None


def write_private_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def write_backup(name: str, data: dict[str, Any]) -> dict[str, Any]:
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if len(encoded.encode("utf-8")) > BACKUP_CONTENT_LIMIT:
        raise ValidationError("Резервная копия превышает 3 МБ")
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    destination = BACKUP_DIR / name
    write_private_text(destination, encoded)
    stat = destination.stat()
    return {"name": name, "size": stat.st_size, "created_at": int(stat.st_mtime)}


def backup_user_payload() -> dict[str, Any]:
    return {
        "format": BACKUP_FORMAT,
        "type": "users",
        "created_at": int(time.time()),
        "database": load_database(),
        "panel_labels": load_panel_labels(),
    }


def backup_full_payload() -> dict[str, Any]:
    return {
        **backup_user_payload(),
        "type": "full",
        "settings": {
            "extension_state": read_private_json(WDTT_EXTENSION_STATE),
            "backup_schedule": load_backup_schedule_settings(),
        },
        "tailscale": {
            "authkey": read_private_text(TAILSCALE_AUTHKEY_FILE),
            "settings": read_private_json(TAILSCALE_SETTINGS),
        },
    }


def backup_type(data: Any) -> str:
    if isinstance(data, dict) and data.get("format") == BACKUP_FORMAT and data.get("type") in {"users", "full"}:
        return str(data["type"])
    return "users"


def backup_metadata(path: Path) -> dict[str, Any]:
    kind = "users"
    try:
        kind = backup_type(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        pass
    stat = path.stat()
    return {"name": path.name, "size": stat.st_size, "created_at": int(stat.st_mtime), "type": kind}


def list_backups() -> dict[str, Any]:
    items: list[dict[str, Any]] = []
    if BACKUP_DIR.exists():
        paths = (path for path in BACKUP_DIR.glob("*.json") if re.fullmatch(r"(?:passwords|users|panel)-[A-Za-z0-9_-]+\.json", path.name))
        items = [backup_metadata(path) for path in sorted(paths, key=lambda item: item.stat().st_mtime, reverse=True)]
    return {"backups": items}


def create_manual_backup(payload: dict[str, Any]) -> dict[str, Any]:
    kind = str(payload.get("type") or "full")
    if kind not in {"full", "users"}:
        raise ValidationError("Выберите тип резервной копии")
    data = backup_full_payload() if kind == "full" else backup_user_payload()
    scheduled = bool(payload.get("scheduled", False))
    result = write_backup(backup_name("panel" if kind == "full" else "users", "scheduled" if scheduled else "manual"), data)
    if scheduled:
        prune_scheduled_backups(kind, load_backup_schedule_settings()["keep"])
    return {**result, "type": kind}


def default_backup_schedule() -> dict[str, Any]:
    return {"frequency": "disabled", "time": "03:30", "type": "full", "keep": 14}


def normalize_backup_schedule(payload: Any) -> dict[str, Any]:
    schedule = default_backup_schedule()
    if not isinstance(payload, dict):
        raise ValidationError("Некорректное расписание backup")
    frequency = str(payload.get("frequency") or schedule["frequency"])
    if frequency not in {"disabled", "daily", "weekly"}:
        raise ValidationError("Выберите период backup")
    schedule["frequency"] = frequency
    value = str(payload.get("time") or schedule["time"])
    if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
        raise ValidationError("Укажите время backup в формате ЧЧ:ММ")
    schedule["time"] = value
    kind = str(payload.get("type") or schedule["type"])
    if kind not in {"full", "users"}:
        raise ValidationError("Выберите тип автоматической копии")
    schedule["type"] = kind
    try:
        keep = int(payload.get("keep") or schedule["keep"])
    except (TypeError, ValueError) as exc:
        raise ValidationError("Укажите количество хранимых backup") from exc
    if not 1 <= keep <= 100:
        raise ValidationError("Можно хранить от 1 до 100 автоматических backup")
    schedule["keep"] = keep
    return schedule


def load_backup_schedule_settings() -> dict[str, Any]:
    saved = read_private_json(BACKUP_SCHEDULE_FILE)
    if not saved:
        return default_backup_schedule()
    try:
        return normalize_backup_schedule(saved)
    except ValidationError:
        return default_backup_schedule()


def write_systemd_unit(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o644)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def backup_schedule_units(schedule: dict[str, Any]) -> tuple[str, str]:
    hour, minute = schedule["time"].split(":")
    on_calendar = f"*-*-* {hour}:{minute}:00" if schedule["frequency"] == "daily" else f"Sun *-*-* {hour}:{minute}:00"
    service = f"""[Unit]
Description=WDTT panel automatic backup

[Service]
Type=oneshot
ExecStart={BACKUP_RUNNER} {schedule['type']}
"""
    timer = f"""[Unit]
Description=Schedule WDTT panel automatic backup

[Timer]
OnCalendar={on_calendar}
Persistent=true
Unit={BACKUP_SERVICE_NAME}

[Install]
WantedBy=timers.target
"""
    return service, timer


def prune_scheduled_backups(kind: str, keep: int) -> None:
    prefix = "panel" if kind == "full" else "users"
    paths = sorted(BACKUP_DIR.glob(f"{prefix}-*-scheduled.json"), key=lambda item: item.stat().st_mtime, reverse=True)
    for path in paths[keep:]:
        path.unlink(missing_ok=True)


def backup_schedule_status(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    schedule = load_backup_schedule_settings()
    active = False
    if not SKIP_SYSTEMD:
        active = run(["systemctl", "is-enabled", "--quiet", BACKUP_TIMER_NAME], timeout=20).returncode == 0
    return {"settings": schedule, "active": active}


def save_backup_schedule(payload: dict[str, Any]) -> dict[str, Any]:
    schedule = normalize_backup_schedule(payload)
    save_private_json(BACKUP_SCHEDULE_FILE, schedule)
    if SKIP_SYSTEMD:
        return {"settings": schedule, "active": False, "state": "test"}
    if schedule["frequency"] == "disabled":
        run(["systemctl", "disable", "--now", BACKUP_TIMER_NAME], timeout=45)
        BACKUP_TIMER_FILE.unlink(missing_ok=True)
        BACKUP_SERVICE_FILE.unlink(missing_ok=True)
        reloaded = run(["systemctl", "daemon-reload"], timeout=45)
        if reloaded.returncode != 0:
            raise AdminError(reloaded.stderr.strip() or "Не удалось обновить systemd")
        return {"settings": schedule, "active": False}
    if not BACKUP_RUNNER.is_file():
        raise AdminError("Не найден модуль автоматических backup; обновите панель")
    service, timer = backup_schedule_units(schedule)
    write_systemd_unit(BACKUP_SERVICE_FILE, service)
    write_systemd_unit(BACKUP_TIMER_FILE, timer)
    reloaded = run(["systemctl", "daemon-reload"], timeout=45)
    if reloaded.returncode != 0:
        raise AdminError(reloaded.stderr.strip() or "Не удалось обновить systemd")
    enabled = run(["systemctl", "enable", "--now", BACKUP_TIMER_NAME], timeout=45)
    if enabled.returncode != 0:
        raise AdminError(enabled.stderr.strip() or "Не удалось включить таймер backup")
    return {"settings": schedule, "active": True}


def delete_backup(payload: dict[str, Any]) -> dict[str, Any]:
    name = validate_backup_name(str(payload.get("name") or ""))
    path = BACKUP_DIR / name
    if not path.is_file():
        raise ValidationError("Резервная копия не найдена")
    path.unlink()
    return {"deleted": name}


def validate_backup_text(value: Any, label: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or "\x00" in value or len(value.encode("utf-8")) > 512 * 1024:
        raise ValidationError(f"В backup неверный {label}")
    return value


def validate_labels_payload(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValidationError("В backup отсутствуют корректные метки пользователей")
    return {
        str(password): label.strip()
        for password, label in value.items()
        if isinstance(label, str) and label.strip() and len(str(password)) <= 64 and len(label.strip()) <= 64
    }


def validate_full_backup(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict) or backup_type(data) != "full":
        raise ValidationError("В backup нет полного набора настроек")
    validate_database_payload(data.get("database"))
    settings = data.get("settings")
    if not isinstance(settings, dict):
        raise ValidationError("В backup отсутствуют настройки панели")
    # Секции Xray/WARP/cascade из старых копий просто игнорируются:
    # функционал удалён из панели, а Tailscale появился позже — его может не быть.
    tailscale = data.get("tailscale") or {}
    if not isinstance(tailscale, dict):
        raise ValidationError("В backup неверные настройки Tailscale")
    tailscale_settings = tailscale.get("settings")
    if tailscale_settings is not None and not isinstance(tailscale_settings, dict):
        raise ValidationError("В backup неверные настройки Tailscale")
    extension_state = settings.get("extension_state")
    backup_schedule = settings.get("backup_schedule", default_backup_schedule())
    if extension_state is not None and not isinstance(extension_state, dict):
        raise ValidationError("В backup неверное состояние WDTT")
    return {
        "database": data["database"],
        "panel_labels": validate_labels_payload(data.get("panel_labels", {})),
        "extension_state": extension_state,
        "backup_schedule": normalize_backup_schedule(backup_schedule),
        "tailscale_authkey": validate_backup_text(tailscale.get("authkey"), "ключ Tailscale"),
        "tailscale_settings": tailscale_settings,
    }


def restore_optional_json(path: Path, value: dict[str, Any] | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        save_private_json(path, value)


def restore_optional_text(path: Path, value: str | None) -> None:
    if value is None:
        path.unlink(missing_ok=True)
    else:
        write_private_text(path, value)


def restore_backup(payload: dict[str, Any]) -> dict[str, Any]:
    name = validate_backup_name(str(payload.get("name") or ""))
    source = BACKUP_DIR / name
    if not source.is_file():
        raise ValidationError("Резервная копия не найдена")
    try:
        restored = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValidationError(f"Резервная копия повреждена: {exc}") from exc

    kind = backup_type(restored)
    if kind == "full":
        full = validate_full_backup(restored)
        database, labels = full["database"], full["panel_labels"]
    elif isinstance(restored, dict) and restored.get("format") == BACKUP_FORMAT:
        validate_database_payload(restored.get("database"))
        database, labels = restored["database"], validate_labels_payload(restored.get("panel_labels", {}))
    else:
        validate_database_payload(restored)
        database, labels = restored, load_panel_labels()

    safety_backup = create_manual_backup({"type": "full"})["name"]

    def apply(data: dict[str, Any]) -> None:
        data.clear()
        data.update(database)
        data.setdefault("devices", {})

    mutate_database("before-restore", apply, backup=False)
    save_panel_labels(labels)
    warnings: list[str] = []
    if kind == "full":
        restore_optional_json(WDTT_EXTENSION_STATE, full["extension_state"])
        TAILSCALE_DIR.mkdir(parents=True, exist_ok=True)
        os.chmod(TAILSCALE_DIR, 0o700)
        restore_optional_text(TAILSCALE_AUTHKEY_FILE, full["tailscale_authkey"])
        restore_optional_json(TAILSCALE_SETTINGS, full["tailscale_settings"])
        save_backup_schedule(full["backup_schedule"])
    return {"restored": name, "type": kind, "safety_backup": safety_backup, "warnings": warnings}


def export_backup(payload: dict[str, Any]) -> dict[str, Any]:
    name = validate_backup_name(str(payload.get("name") or ""))
    source = BACKUP_DIR / name
    if not source.is_file():
        raise ValidationError("Резервная копия не найдена")
    return {"name": name, "content": source.read_text(encoding="utf-8")}


def import_backup(payload: dict[str, Any]) -> dict[str, Any]:
    content = str(payload.get("content") or "")
    if not content or len(content.encode("utf-8")) > BACKUP_CONTENT_LIMIT:
        raise ValidationError("Файл backup пустой или превышает 3 МБ")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValidationError(f"Файл backup содержит неверный JSON: {exc}") from exc
    kind = backup_type(data)
    if kind == "full":
        validate_full_backup(data)
    elif isinstance(data, dict) and data.get("format") == BACKUP_FORMAT:
        validate_database_payload(data.get("database"))
        validate_labels_payload(data.get("panel_labels", {}))
    else:
        validate_database_payload(data)
    return {**write_backup(backup_name("panel" if kind == "full" else "users", "uploaded"), data), "type": kind}


def validate_backup_name(name: str) -> str:
    if not re.fullmatch(r"(?:passwords|users|panel)-[A-Za-z0-9_-]+\.json", name):
        raise ValidationError("Некорректное имя резервной копии")
    return name


def validate_database_payload(data: Any) -> None:
    if not isinstance(data, dict):
        raise ValidationError("Резервная копия должна содержать JSON-объект")
    if not isinstance(data.get("passwords"), dict) or not isinstance(data.get("devices", {}), dict):
        raise ValidationError("В backup отсутствуют корректные passwords/devices")


def version_parts(value: str) -> tuple[int, ...]:
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}", value):
        raise ValidationError(f"Некорректная версия панели: {value}")
    parts = tuple(int(part) for part in value.split("."))
    return parts + (0,) * (4 - len(parts))


def read_panel_update_status() -> dict[str, Any]:
    try:
        data = json.loads(PANEL_UPDATE_STATUS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def panel_version(payload: dict[str, Any]) -> dict[str, Any]:
    current = str(payload.get("current_version") or "0.0.0")
    version_parts(current)
    status = read_panel_update_status()
    latest = str(status.get("latest") or "")
    if latest:
        try:
            version_parts(latest)
        except ValidationError:
            latest = ""
    try:
        checked_at = int(status.get("checked_at") or 0)
    except (TypeError, ValueError):
        checked_at = 0
    return {
        "current": current,
        "latest": latest,
        "update_available": bool(status.get("update_available")) and bool(latest),
        "state": str(status.get("state") or "unknown"),
        "message": str(status.get("message") or ""),
        "checked_at": checked_at,
        # Docker: код примонтирован/запечён в образ, кнопочного самообновления
        # нет — фронт показывает плашку «обновитесь через compose».
        "docker": os.environ.get("WDTT_DOCKER", "") not in ("", "0", "false", "no"),
    }


def schedule_panel_self_update(action: str) -> dict[str, Any]:
    if not PANEL_SELF_UPDATE_COMMAND.exists():
        raise AdminError(f"Команда обновления не найдена: {PANEL_SELF_UPDATE_COMMAND}")
    if SKIP_SYSTEMD:
        return {"scheduled": True, "state": "test"}
    unit = f"wdtt-panel-{action}-{int(time.time())}"
    result = run_transient_unit(
        [
            "systemd-run",
            "--quiet",
            "--collect",
            f"--unit={unit}",
            "--on-active=2s",
            str(PANEL_SELF_UPDATE_COMMAND),
            action,
        ],
        timeout=20,
    )
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or "Не удалось запланировать операцию панели")
    return {"scheduled": True, "unit": unit}


def schedule_panel_check(payload: dict[str, Any]) -> dict[str, Any]:
    return schedule_panel_self_update("check")


def start_panel_update(payload: dict[str, Any]) -> dict[str, Any]:
    return schedule_panel_self_update("update")


def schedule_certificate_renew(payload: dict[str, Any]) -> dict[str, Any]:
    if SKIP_SYSTEMD:
        return {"scheduled": True, "state": "test"}
    unit = f"wdtt-panel-cert-refresh-{int(time.time())}"
    result = run_transient_unit(
        ["systemd-run", "--quiet", "--collect", f"--unit={unit}", "--on-active=2s", str(PANEL_RENEW_COMMAND), "renew-cert"],
        timeout=20,
    )
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or "Не удалось запланировать обновление сертификата")
    return {"scheduled": True, "unit": unit}


def export_certificate(payload: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(payload.get("certificate_path") or ""))
    if not path.is_file():
        raise ValidationError("Файл сертификата не найден")
    return {"name": "wdtt-panel-certificate.pem", "content": path.read_text(encoding="utf-8")}


def read_stats() -> dict[str, Any]:
    try:
        data = json.loads(STATS_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def proxy_log_file() -> Path:
    """Лог sing-box из RUNTIME_DIR (для режима без systemd, Docker)."""
    try:
        _, values = read_proxy_config()
    except AdminError:
        values = {}
    runtime = str(values.get("RUNTIME_DIR") or "/run/wdtt-proxy")
    return Path(runtime) / "sing-box.log"


def journal_logs(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        limit = max(20, min(int(payload.get("limit", 1000)), 5000))
    except (TypeError, ValueError):
        limit = 1000
    source = str(payload.get("source") or "wdtt")
    sources = {
        "wdtt": ([SERVICE], "WDTT"),
        "panel": (["wdtt-panel.service"], "Панель"),
        "proxy": ([PROXY_SERVICE], "wdtt-proxy"),
        "nginx": (["nginx.service"], "Nginx"),
        "all": ([SERVICE, "wdtt-panel.service", "nginx.service"], "Все службы панели"),
    }
    if source == "installer":
        path = INSTALL_LOG_FILE
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError as exc:
            raise AdminError(f"Не удалось прочитать журнал установщика: {exc}") from exc
        return {"lines": lines, "source": source, "title": "Установщик", "units": [], "limit": limit}
    if source not in sources:
        raise ValidationError("Неизвестный источник журнала")
    units, title = sources[source]
    if source == "proxy" and not proxy_uses_systemd():
        # Docker: journald нет — читаем файл лога, который пишет wdtt-proxy.sh.
        path = proxy_log_file()
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit:]
        except OSError:
            lines = []
        return {"lines": lines, "source": source, "title": f"{title} (лог {path})", "units": [], "limit": limit}
    if SKIP_SYSTEMD:
        return {"lines": [], "source": source, "title": title, "units": [], "limit": limit}
    command = ["journalctl"]
    for unit in units:
        command.extend(["-u", unit])
    command.extend(["-n", str(limit), "--no-pager", "--all", "-o", "short-iso"])
    result = run(command, timeout=45)
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or "Не удалось прочитать journalctl")
    states = [
        {"unit": unit, "active": run(["systemctl", "is-active", "--quiet", unit], timeout=15).returncode == 0}
        for unit in units
    ]
    return {"lines": result.stdout.splitlines(), "source": source, "title": title, "units": states, "limit": limit}


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        iterator = path.rglob("*")
    except OSError:
        return 0
    for item in iterator:
        try:
            if item.is_file() or item.is_symlink():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def parse_size_text(value: str) -> int | None:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*([KMGTPE]?)(?:i?B)?", value, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1))
    unit = match.group(2).upper()
    multipliers = {
        "": 1,
        "K": 1024,
        "M": 1024**2,
        "G": 1024**3,
        "T": 1024**4,
        "P": 1024**5,
        "E": 1024**6,
    }
    return int(number * multipliers.get(unit, 1))


def cleanup_os_error(exc: OSError) -> str:
    code = getattr(exc, "errno", None)
    if code == errno.EROFS:
        return "Файловая система только для чтения"
    if code in {errno.EACCES, errno.EPERM}:
        return "Нет прав на запись"
    return str(exc) or exc.__class__.__name__


def cleanup_target_error(target: str, exc: Exception) -> dict[str, Any]:
    if isinstance(exc, OSError):
        message = cleanup_os_error(exc)
    elif isinstance(exc, subprocess.TimeoutExpired):
        message = "Команда очистки не ответила вовремя"
    else:
        message = str(exc) or exc.__class__.__name__
    return {"target": target, "before_bytes": 0, "freed_bytes": 0, "remaining_bytes": 0, "available": False, "error": message}


def cleanup_log_files(apply: bool) -> dict[str, Any]:
    targets = {
        "installer": INSTALL_LOG_FILE,
        "nginx_access": NGINX_ACCESS_LOG,
        "nginx_error": NGINX_ERROR_LOG,
    }
    files = []
    freed = 0
    before_total = 0
    remaining = 0
    for name, path in targets.items():
        size = directory_size(path)
        before_total += size
        try:
            exists = path.is_file()
        except OSError as exc:
            exists = False
            file_info = {
                "name": name,
                "path": str(path),
                "bytes": size,
                "before_bytes": size,
                "after_bytes": size,
                "exists": False,
                "skipped": True,
                "error": cleanup_os_error(exc),
            }
            remaining += size
            files.append(file_info)
            continue
        file_info = {"name": name, "path": str(path), "bytes": size, "before_bytes": size, "exists": exists}
        if apply and exists:
            try:
                with path.open("w", encoding="utf-8"):
                    pass
                after_size = directory_size(path)
                try:
                    os.chmod(path, 0o640 if name.startswith("nginx") else 0o600)
                except OSError as exc:
                    file_info["warning"] = cleanup_os_error(exc)
                file_info["cleared"] = True
                file_info["after_bytes"] = after_size
                freed += max(0, size - after_size)
                remaining += after_size
            except OSError as exc:
                file_info["skipped"] = True
                file_info["error"] = cleanup_os_error(exc)
                file_info["after_bytes"] = size
                remaining += size
        else:
            freed += size
            file_info["after_bytes"] = size
            remaining += size
        files.append(file_info)
    return {"target": "service_logs", "before_bytes": before_total, "freed_bytes": freed, "remaining_bytes": remaining, "files": files}


def cleanup_package_cache(apply: bool) -> dict[str, Any]:
    cache_paths = [Path("/var/cache/apt/archives"), Path("/var/cache/dnf"), Path("/var/cache/yum"), Path("/var/cache/pacman/pkg")]
    before = sum(directory_size(path) for path in cache_paths)
    command: list[str] = []
    if shutil.which("apt-get"):
        command = ["apt-get", "clean"]
    elif shutil.which("dnf"):
        command = ["dnf", "clean", "all"]
    elif shutil.which("yum"):
        command = ["yum", "clean", "all"]
    elif shutil.which("paccache"):
        command = ["paccache", "-rk1"]
    if apply and command:
        result = run(command, timeout=120)
        if result.returncode != 0:
            raise AdminError(result.stderr.strip() or "Не удалось очистить кэш пакетного менеджера")
    remaining = sum(directory_size(path) for path in cache_paths) if apply else before
    freed = max(0, before - remaining) if apply else before
    return {
        "target": "package_cache",
        "before_bytes": before,
        "freed_bytes": freed,
        "remaining_bytes": remaining,
        "command": " ".join(command) if command else "",
    }


def cleanup_journal(apply: bool, keep_days: int) -> dict[str, Any]:
    if SKIP_SYSTEMD or not shutil.which("journalctl"):
        return {"target": "journal", "before_bytes": 0, "freed_bytes": 0, "remaining_bytes": 0, "available": False, "detail": "systemd journal недоступен"}
    usage = run(["journalctl", "--disk-usage"], timeout=20)
    before_detail = usage.stdout.strip() or usage.stderr.strip()
    before = parse_size_text(before_detail) or 0
    detail = before_detail
    remaining = before
    freed = before
    if apply:
        result = run(["journalctl", f"--vacuum-time={keep_days}d"], timeout=120)
        if result.returncode != 0:
            raise AdminError(result.stderr.strip() or "Не удалось очистить systemd journal")
        after_usage = run(["journalctl", "--disk-usage"], timeout=20)
        after_detail = after_usage.stdout.strip() or after_usage.stderr.strip()
        remaining = parse_size_text(after_detail) or 0
        freed = max(0, before - remaining)
        detail = after_detail or result.stdout.strip() or detail
    return {
        "target": "journal",
        "before_bytes": before,
        "freed_bytes": freed,
        "remaining_bytes": remaining,
        "available": True,
        "detail": detail,
    }


def cleanup_failed_units(apply: bool) -> dict[str, Any]:
    if SKIP_SYSTEMD or not shutil.which("systemctl"):
        return {"target": "failed_units", "before_bytes": 0, "freed_bytes": 0, "remaining_bytes": 0, "available": False}
    if apply:
        result = run(["systemctl", "reset-failed"], timeout=30)
        if result.returncode != 0:
            raise AdminError(result.stderr.strip() or "Не удалось сбросить failed-units")
    return {"target": "failed_units", "before_bytes": 0, "freed_bytes": 0, "remaining_bytes": 0, "available": True}


def cleanup_system(payload: dict[str, Any], apply: bool) -> dict[str, Any]:
    try:
        keep_days = max(1, min(int(payload.get("keep_days") or 14), 365))
    except (TypeError, ValueError):
        keep_days = 14
    raw_targets = payload.get("targets")
    allowed = {"service_logs", "journal", "package_cache", "failed_units"}
    targets = [str(item) for item in raw_targets] if isinstance(raw_targets, list) else ["service_logs", "journal", "package_cache"]
    targets = [target for target in dict.fromkeys(targets) if target in allowed]
    if not targets:
        raise ValidationError("Выберите хотя бы один безопасный раздел очистки")
    items = []
    for target in targets:
        try:
            if target == "service_logs":
                items.append(cleanup_log_files(apply))
            elif target == "journal":
                items.append(cleanup_journal(apply, keep_days))
            elif target == "package_cache":
                items.append(cleanup_package_cache(apply))
            elif target == "failed_units":
                items.append(cleanup_failed_units(apply))
        except (AdminError, OSError, subprocess.TimeoutExpired) as exc:
            items.append(cleanup_target_error(target, exc))
    return {
        "applied": apply,
        "keep_days": keep_days,
        "targets": targets,
        "items": items,
        "estimated_freed_bytes": sum(int(item.get("freed_bytes") or 0) for item in items),
    }


def default_autoclean_settings() -> dict[str, Any]:
    return {"enabled": True, "disk_percent": 90, "keep_panel": 20, "keep_users": 20, "keep_days": 14}


def normalize_autoclean_settings(payload: Any) -> dict[str, Any]:
    settings = default_autoclean_settings()
    if not isinstance(payload, dict):
        raise ValidationError("Некорректные настройки авто-очистки")
    settings["enabled"] = bool(payload.get("enabled", settings["enabled"]))
    try:
        threshold = int(payload.get("disk_percent", settings["disk_percent"]))
    except (TypeError, ValueError) as exc:
        raise ValidationError("Укажите порог диска в процентах") from exc
    if not 50 <= threshold <= 99:
        raise ValidationError("Порог диска должен быть от 50 до 99%")
    settings["disk_percent"] = threshold
    for key in ("keep_panel", "keep_users", "keep_days"):
        try:
            value = int(payload.get(key, settings[key]))
        except (TypeError, ValueError) as exc:
            raise ValidationError("Некорректный лимит хранения") from exc
        if not 1 <= value <= 100:
            raise ValidationError("Хранить можно от 1 до 100 копий/дней")
        settings[key] = value
    return settings


def load_autoclean_settings() -> dict[str, Any]:
    saved = read_private_json(AUTOCLEAN_SETTINGS_FILE)
    if not saved:
        return default_autoclean_settings()
    try:
        return normalize_autoclean_settings(saved)
    except ValidationError:
        return default_autoclean_settings()


def disk_usage_percent() -> float:
    usage = shutil.disk_usage("/")
    return round(usage.used * 100 / usage.total, 1) if usage.total else 0.0


def prune_backups_to(keep_panel: int, keep_users: int) -> int:
    if not BACKUP_DIR.exists():
        return 0
    limits = {"full": max(0, keep_panel), "users": max(0, keep_users)}
    counters = {"full": 0, "users": 0}
    removed = 0
    for item in list_backups()["backups"]:
        kind = item.get("type") if item.get("type") in limits else "users"
        counters[kind] += 1
        if counters[kind] > limits[kind]:
            (BACKUP_DIR / str(item["name"])).unlink(missing_ok=True)
            removed += 1
    return removed


def autoclean_status() -> dict[str, Any]:
    settings = load_autoclean_settings()
    active = False
    if not SKIP_SYSTEMD:
        active = run(["systemctl", "is-enabled", "--quiet", AUTOCLEAN_TIMER_NAME], timeout=20).returncode == 0
    return {"settings": settings, "active": active, "disk_percent": disk_usage_percent()}


def run_autoclean(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_autoclean_settings()
    percent = disk_usage_percent()
    if not settings["enabled"]:
        return {"ran": False, "reason": "disabled", "disk_percent": percent, "settings": settings}
    if percent < settings["disk_percent"]:
        return {
            "ran": False,
            "reason": "below_threshold",
            "disk_percent": percent,
            "threshold": settings["disk_percent"],
            "settings": settings,
        }
    cleanup = cleanup_system(
        {"targets": ["service_logs", "journal", "package_cache", "failed_units"], "keep_days": settings["keep_days"]},
        True,
    )
    removed = prune_backups_to(settings["keep_panel"], settings["keep_users"])
    return {
        "ran": True,
        "reason": "cleaned",
        "disk_percent": percent,
        "threshold": settings["disk_percent"],
        "cleanup": cleanup,
        "backups_removed": removed,
        "freed_bytes": int(cleanup.get("estimated_freed_bytes") or 0),
        "settings": settings,
    }


def save_autoclean_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = normalize_autoclean_settings(payload)
    save_private_json(AUTOCLEAN_SETTINGS_FILE, settings)
    return {"settings": settings, "active": autoclean_status().get("active", False)}


def certificate_info(path: str) -> dict[str, Any]:
    if not path:
        return {}
    cert_path = Path(path)
    if not cert_path.is_file():
        return {"path": path, "exists": False}
    try:
        decoded = ssl._ssl._test_decode_cert(str(cert_path))
        expires = decoded.get("notAfter", "")
        expires_at = int(ssl.cert_time_to_seconds(expires)) if expires else 0
        return {
            "path": path,
            "exists": True,
            "expires_at": expires_at,
            "days_left": round((expires_at - time.time()) / 86400, 1) if expires_at else None,
            "subject_alt_name": decoded.get("subjectAltName", []),
        }
    except (OSError, ValueError, ssl.SSLError) as exc:
        return {"path": path, "exists": True, "error": str(exc)}


def cpu_usage() -> float:
    def snapshot() -> tuple[int, int]:
        fields = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0].split()[1:]
        values = [int(value) for value in fields]
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    try:
        total_a, idle_a = snapshot()
        time.sleep(0.08)
        total_b, idle_b = snapshot()
        total = total_b - total_a
        return round(100 * (1 - (idle_b - idle_a) / total), 1) if total > 0 else 0.0
    except (OSError, ValueError, IndexError):
        return 0.0


def memory_usage() -> dict[str, Any]:
    values: dict[str, int] = {}
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, raw = line.split(":", 1)
            values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return {"total": 0, "used": 0, "available": 0, "percent": 0.0}
    total = values.get("MemTotal", 0)
    available = values.get("MemAvailable", values.get("MemFree", 0))
    used = max(0, total - available)
    return {
        "total": total,
        "used": used,
        "available": available,
        "percent": round(used * 100 / total, 1) if total else 0.0,
    }


def local_tls_status(host: str, port: int) -> dict[str, Any]:
    result: dict[str, Any] = {"local_tls_ok": False, "listening": False, "error": ""}
    if not host or not port:
        return result
    try:
        listener = run(["ss", "-ltn", f"sport = :{port}"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return result
    result["listening"] = listener.returncode == 0 and "LISTEN" in listener.stdout
    try:
        context = ssl._create_unverified_context()
        with socket.create_connection(("127.0.0.1", port), timeout=4) as raw:
            with context.wrap_socket(raw, server_hostname=host):
                result["local_tls_ok"] = True
    except OSError as exc:
        result["error"] = str(exc)
    return result


def default_wdtt_settings() -> dict[str, Any]:
    """Глобальные настройки ядра WDTT (вкладка «WDTT» в панели).

    Значения по умолчанию повторяют хардкоды ядра и deploy.sh:
    DTLS 56000, WG 56001, admin 56002, DNS 1.1.1.1, MTU 1280, keepalive 25.
    """
    return {
        "listen_host": "0.0.0.0",
        "dtls_port": 56000,
        "wg_port": 56001,
        "dns": "1.1.1.1",
        "direct_port": 0,
        "raw_port": 0,
        "max_users": MAX_USERS,
        "admin_listen": "0.0.0.0:56002",
        "handshake_timeout_s": 60,
        "first_packet_timeout_s": 30,
        "wg_keepalive_s": 25,
        "wg_mtu": 1280,
        "stats_interval_s": 10,
        "max_dtls_per_device": 0,
        "online_window_s": 75,
    }


def _normalize_wdtt_listen_host(value: Any) -> str:
    host = str(value or "").strip()
    if not host:
        return "0.0.0.0"
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise ValidationError("Адрес прослушивания должен быть IPv4") from exc
    if address.version != 4:
        raise ValidationError("Адрес прослушивания должен быть IPv4")
    return str(address)


def _normalize_wdtt_port(value: Any, name: str, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} должен быть числом") from exc
    if not 1 <= port <= 65535:
        raise ValidationError(f"{name} должен быть в диапазоне 1-65535")
    return port


def _normalize_wdtt_optional_port(value: Any) -> int:
    if value is None or str(value).strip() == "":
        return 0
    try:
        port = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError("Опциональный порт должен быть числом") from exc
    if not 0 <= port <= 65535:
        raise ValidationError("Опциональный порт должен быть в диапазоне 0-65535 (0 — выключено)")
    return port


def _normalize_wdtt_dns(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValidationError("Укажите DNS для клиентов")
    servers = [item.strip() for item in raw.replace(";", ",").split(",") if item.strip()]
    if not 1 <= len(servers) <= 3:
        raise ValidationError("Укажите от 1 до 3 DNS-серверов через запятую")
    return ",".join(validate_public_host(server) for server in servers)


def _normalize_wdtt_int(value: Any, name: str, minimum: int, maximum: int, default: int) -> int:
    if value is None or str(value).strip() == "":
        return default
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{name} должен быть целым числом") from exc
    if not minimum <= number <= maximum:
        raise ValidationError(f"{name} должен быть в диапазоне {minimum}-{maximum}")
    return number


def _normalize_wdtt_admin_listen(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    host, separator, port = raw.rpartition(":")
    if not separator or not host:
        raise ValidationError("Admin HTTP задаётся как адрес:порт")
    return f"{_normalize_wdtt_listen_host(host)}:{_normalize_wdtt_port(port, 'Admin HTTP порт', 56002)}"


def normalize_wdtt_settings(payload: dict[str, Any], base: dict[str, Any] | None = None) -> dict[str, Any]:
    current = dict(base) if isinstance(base, dict) else default_wdtt_settings()
    merged = {**default_wdtt_settings(), **current, **(payload or {})}
    return {
        "listen_host": _normalize_wdtt_listen_host(merged.get("listen_host")),
        "dtls_port": _normalize_wdtt_port(merged.get("dtls_port"), "DTLS-порт", 56000),
        "wg_port": _normalize_wdtt_port(merged.get("wg_port"), "WireGuard-порт", 56001),
        "dns": _normalize_wdtt_dns(merged.get("dns")),
        "direct_port": _normalize_wdtt_optional_port(merged.get("direct_port")),
        "raw_port": _normalize_wdtt_optional_port(merged.get("raw_port")),
        "max_users": _normalize_wdtt_int(merged.get("max_users"), "Лимит пользователей", 1, MAX_USERS, MAX_USERS),
        "admin_listen": _normalize_wdtt_admin_listen(merged.get("admin_listen")),
        "handshake_timeout_s": _normalize_wdtt_int(merged.get("handshake_timeout_s"), "DTLS handshake", 5, 300, 60),
        "first_packet_timeout_s": _normalize_wdtt_int(
            merged.get("first_packet_timeout_s"), "Онлайн-таймаут", 5, 120, 30
        ),
        "wg_keepalive_s": _normalize_wdtt_int(merged.get("wg_keepalive_s"), "WG keepalive", 0, 300, 25),
        "wg_mtu": _normalize_wdtt_int(merged.get("wg_mtu"), "MTU", 576, 9000, 1280),
        "stats_interval_s": _normalize_wdtt_int(merged.get("stats_interval_s"), "Интервал stats", 2, 3600, 10),
        "max_dtls_per_device": _normalize_wdtt_int(
            merged.get("max_dtls_per_device"), "DTLS на device", 0, 100, 0
        ),
        "online_window_s": _normalize_wdtt_int(merged.get("online_window_s"), "Окно онлайна", 10, 600, 75),
    }


def _seed_wdtt_settings_from_unit() -> dict[str, Any]:
    """Первичные значения из текущего ExecStart wdtt.service (best effort)."""
    seed: dict[str, Any] = {}
    try:
        lines = WDTT_UNIT_FILE.read_text(encoding="utf-8").splitlines()
    except OSError:
        return seed
    for line in lines:
        if not line.startswith("ExecStart="):
            continue
        try:
            tokens = shlex.split(line.split("=", 1)[1])
        except ValueError:
            continue
        args = tokens[1:]
        index = 0
        while index < len(args):
            token = args[index]
            name, equals, inline = token.partition("=")
            value = inline if equals else (args[index + 1] if index + 1 < len(args) else "")
            if not equals:
                index += 1
            if name in {"-listen", "--listen"} and ":" in value:
                host, _, port = value.rpartition(":")
                seed["listen_host"] = host
                seed["dtls_port"] = port
            elif name in {"-wg-port", "--wg-port"}:
                seed["wg_port"] = value
            elif name in {"-dns", "--dns"}:
                seed["dns"] = value
            elif name in {"-listen-direct", "--listen-direct"} and ":" in value:
                seed["direct_port"] = value.rpartition(":")[2]
            elif name in {"-listen-raw", "--listen-raw"} and ":" in value:
                seed["raw_port"] = value.rpartition(":")[2]
            elif name in {"-admin-listen", "--admin-listen"}:
                seed["admin_listen"] = value
            elif name == "-handshake-timeout":
                seed["handshake_timeout_s"] = value.rstrip("s")
            elif name == "-first-packet-timeout":
                seed["first_packet_timeout_s"] = value.rstrip("s")
            elif name == "-wg-keepalive":
                seed["wg_keepalive_s"] = value
            elif name == "-wg-mtu":
                seed["wg_mtu"] = value
            elif name == "-stats-interval":
                seed["stats_interval_s"] = value.rstrip("s")
            elif name == "-max-dtls-per-device":
                seed["max_dtls_per_device"] = value
            index += 1
    return seed


def load_wdtt_settings() -> dict[str, Any]:
    try:
        stored = json.loads(WDTT_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    if not stored and not WDTT_SETTINGS_FILE.is_file():
        stored = _seed_wdtt_settings_from_unit()
    try:
        return normalize_wdtt_settings(stored)
    except ValidationError:
        return normalize_wdtt_settings({})


def wdtt_effective_user_limit() -> int:
    return min(MAX_USERS, int(load_wdtt_settings().get("max_users") or MAX_USERS))


def _set_wdtt_service_flag(
    tokens: list[str], names: set[str], canonical: str, value: str | None
) -> list[str]:
    """Заменить/добавить/убрать флаг в ExecStart. value None — убрать флаг."""
    result: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        name, equals, _ = token.partition("=")
        if name in names:
            if equals:
                index += 1
                continue
            index += 2
            continue
        result.append(token)
        index += 1
    if value is not None:
        result.extend([canonical, value])
    return result


def set_wdtt_service_inbound(settings: dict[str, Any], runtime_tunables: bool) -> bool:
    """Переписать известные флаги ExecStart wdtt.service из настроек панели.

    Неизвестные аргументы (admin-токены, bot-token-file, пути) сохраняются.
    Возвращает True, если файл изменился.
    """
    if not WDTT_UNIT_FILE.is_file():
        raise AdminError(f"Не найден {WDTT_UNIT_FILE}; настройте wdtt.service вручную")
    lines = WDTT_UNIT_FILE.read_text(encoding="utf-8").splitlines()
    changed = False
    found = False
    next_lines: list[str] = []
    for line in lines:
        if not line.startswith("ExecStart="):
            next_lines.append(line)
            continue
        found = True
        try:
            tokens = shlex.split(line.split("=", 1)[1])
        except ValueError as exc:
            raise AdminError(f"Не удалось разобрать ExecStart в {WDTT_UNIT_FILE}: {exc}") from exc
        if not tokens:
            raise AdminError(f"Пустой ExecStart в {WDTT_UNIT_FILE}")
        binary, args = tokens[0], tokens[1:]
        args = _set_wdtt_service_flag(
            args, {"-listen", "--listen"}, "-listen", f"{settings['listen_host']}:{settings['dtls_port']}"
        )
        args = _set_wdtt_service_flag(args, {"-wg-port", "--wg-port"}, "-wg-port", str(settings["wg_port"]))
        args = _set_wdtt_service_flag(args, {"-dns", "--dns"}, "-dns", str(settings["dns"]))
        args = _set_wdtt_service_flag(
            args,
            {"-listen-direct", "--listen-direct"},
            "-listen-direct",
            f"0.0.0.0:{settings['direct_port']}" if settings["direct_port"] else None,
        )
        args = _set_wdtt_service_flag(
            args,
            {"-listen-raw", "--listen-raw"},
            "-listen-raw",
            f"0.0.0.0:{settings['raw_port']}" if settings["raw_port"] else None,
        )
        args = _set_wdtt_service_flag(
            args,
            {"-admin-listen", "--admin-listen"},
            "-admin-listen",
            str(settings["admin_listen"] or "") or None,
        )
        if runtime_tunables:
            args = _set_wdtt_service_flag(
                args, {"-handshake-timeout"}, "-handshake-timeout", f"{settings['handshake_timeout_s']}s"
            )
            args = _set_wdtt_service_flag(
                args,
                {"-first-packet-timeout"},
                "-first-packet-timeout",
                f"{settings['first_packet_timeout_s']}s",
            )
            args = _set_wdtt_service_flag(
                args, {"-wg-keepalive"}, "-wg-keepalive", str(settings["wg_keepalive_s"])
            )
            args = _set_wdtt_service_flag(args, {"-wg-mtu"}, "-wg-mtu", str(settings["wg_mtu"]))
            args = _set_wdtt_service_flag(
                args, {"-stats-interval"}, "-stats-interval", f"{settings['stats_interval_s']}s"
            )
            args = _set_wdtt_service_flag(
                args, {"-max-dtls-per-device"}, "-max-dtls-per-device", str(settings["max_dtls_per_device"])
            )
        next_line = "ExecStart=" + " ".join(systemd_exec_token(token) for token in [binary, *args])
        changed = changed or next_line != line
        next_lines.append(next_line)
    if not found:
        raise AdminError(f"В {WDTT_UNIT_FILE} не найден ExecStart")
    if changed:
        write_systemd_unit(WDTT_UNIT_FILE, "\n".join(next_lines) + "\n")
        reloaded = run(["systemctl", "daemon-reload"], timeout=45)
        if reloaded.returncode != 0:
            raise AdminError(reloaded.stderr.strip() or "Не удалось обновить systemd после настройки WDTT")
    return changed


def save_wdtt_settings(payload: dict[str, Any]) -> dict[str, Any]:
    settings = normalize_wdtt_settings(payload, load_wdtt_settings())
    save_private_json(WDTT_SETTINGS_FILE, settings)
    if SKIP_SYSTEMD:
        return {"saved": settings, "applied": False, "state": "test"}
    # Новые флаги понимает только ядро с расширением v10; на старом бинарнике
    # неизвестный флаг уронит старт — пишем только legacy-флаги и просим обновиться.
    tunables = wdtt_extensions_are_verified()
    changed = set_wdtt_service_inbound(settings, tunables)
    was_active = service_active()
    restarted = False
    if was_active:
        result = run(["systemctl", "restart", SERVICE], timeout=60)
        if result.returncode != 0:
            raise AdminError(result.stderr.strip() or "Не удалось перезапустить WDTT с новыми настройками")
        restarted = True
    return {
        "saved": settings,
        "applied": changed or restarted,
        "restarted": restarted,
        "active": service_active(),
        "runtime_tunables": tunables,
        "extension_pending": not tunables,
    }


def change_panel_domain(payload: dict[str, Any]) -> dict[str, Any]:
    host = validate_public_host(str(payload.get("host") or ""))
    email = str(payload.get("email") or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValidationError("Некорректный email для Let's Encrypt")
    if SKIP_SYSTEMD:
        # В Docker нет systemd-run: тяжёлую работу (сертификат, nginx,
        # рестарт панели) делает фоновый поток, фронт опрашивает overview.
        worker = threading.Thread(target=_apply_docker_domain_change, args=(host, email), daemon=True)
        worker.start()
        return {"scheduled": True, "docker": True, "host": host}
    unit = f"wdtt-panel-domain-{int(time.time())}"
    command = [
        "systemd-run",
        "--quiet",
        "--collect",
        f"--unit={unit}",
        "--on-active=2s",
        f"--setenv=PANEL_HOST={host}",
        str(PANEL_RENEW_COMMAND),
        "change-domain",
    ]
    if email:
        command.insert(-2, f"--setenv=PANEL_EMAIL={email}")
    result = run_transient_unit(command, timeout=20)
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or "Не удалось запланировать смену домена")
    return {"scheduled": True, "unit": unit, "host": host}


def _docker_domain_paths() -> dict[str, Any]:
    """Пути Docker-окружения (через env — чтобы тесты подменяли на tmp)."""
    return {
        "config": Path(os.environ.get("WDTT_PANEL_CONFIG", "/etc/wdtt-panel/config.json")),
        "acme": Path(os.environ.get("WDTT_ACME_DIR", "/var/lib/wdtt-panel/acme")),
        "tls_dir": Path(os.environ.get("WDTT_DOCKER_TLS_DIR", "/etc/wdtt-panel/tls")),
        "le_live": Path(os.environ.get("WDTT_LE_LIVE_DIR", "/etc/letsencrypt/live")),
        "certbot": os.environ.get("WDTT_CERTBOT_BIN", "/opt/wdtt-panel/certbot/bin/certbot"),
        "nginx_gen": os.environ.get("WDTT_NGINX_GEN", "/usr/local/sbin/wdtt-gen-nginx-conf"),
        "backups": Path(
            os.environ.get("WDTT_DOCKER_CONFIG_BACKUPS", "/var/lib/wdtt-panel-private/config-backups")
        ),
    }


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _backup_docker_panel_config(config_path: Path, backups: Path) -> None:
    backups.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    destination = backups / f"config-{stamp}.json"
    shutil.copy2(config_path, destination)
    os.chmod(destination, 0o600)
    for stale in sorted(backups.glob("config-*.json"))[:-20]:
        stale.unlink(missing_ok=True)


def _obtain_docker_letsencrypt(host: str, email: str, paths: dict[str, Any]) -> bool:
    """Выпуск LE через работающий nginx (webroot уже раздаётся на :80)."""
    candidates = [paths["certbot"], shutil.which("certbot") or ""]
    binary = next(
        (c for c in candidates if c and Path(c).is_file() and os.access(c, os.X_OK)),
        "",
    )
    if not binary:
        return False
    command = [binary, "certonly", "--non-interactive", "--agree-tos"]
    if email:
        command += ["--email", email]
    else:
        command += ["--register-unsafely-without-email"]
    command += ["-d", host, "--webroot", "--webroot-path", str(paths["acme"])]
    try:
        result = run(command, timeout=180)
    except (OSError, subprocess.TimeoutExpired):
        return False
    live = Path(paths["le_live"]) / host / "fullchain.pem"
    return result.returncode == 0 and live.is_file()


def _issue_docker_self_signed(host: str, paths: dict[str, Any]) -> tuple[str, str]:
    """Self-signed с актуальным SAN (как issue_self_signed в entrypoint)."""
    tls_dir = Path(paths["tls_dir"])
    tls_dir.mkdir(parents=True, exist_ok=True)
    cert, key = tls_dir / "fullchain.pem", tls_dir / "privkey.pem"
    san = f"IP:{host}" if _looks_like_ip(host) else f"DNS:{host}"
    result = run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:3072", "-sha256",
            "-days", "365", "-nodes",
            "-keyout", str(key), "-out", str(cert),
            "-subj", f"/CN={host}", "-addext", f"subjectAltName={san}",
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise AdminError("Не удалось выпустить самоподписанный сертификат")
    os.chmod(key, 0o600)
    os.chmod(cert, 0o644)
    return str(cert), str(key)


def _write_docker_panel_config(config_path: Path, config: dict[str, Any]) -> None:
    encoded = json.dumps(config, ensure_ascii=False, indent=2).encode("utf-8") + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix="config.", suffix=".tmp", dir=config_path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp_name, 0o640)
        os.replace(temp_name, config_path)
        os.chmod(config_path, 0o640)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def _reload_docker_nginx(
    paths: dict[str, Any], config: dict[str, Any], host: str, cert: str, key: str, tls_mode: str
) -> None:
    """Перерендер nginx-конфига из нового config и reload без дауна."""
    env = {
        "PANEL_HOST": host,
        "PANEL_PATH": str(config.get("base_path") or "/"),
        "PANEL_HTTPS_PORT": str(int(config.get("https_port") or 9999)),
        "PANEL_LISTEN_PORT": str(int(config.get("listen_port") or 8787)),
        "CERTIFICATE_PATH": cert,
        "PRIVATE_KEY_PATH": key,
        "TLS_MODE": tls_mode,
    }
    generated = run([str(paths["nginx_gen"])], timeout=30, env=env)
    if generated.returncode != 0:
        raise AdminError("nginx не принял новую конфигурацию")
    reloaded = run(["nginx", "-s", "reload"], timeout=30)
    if reloaded.returncode != 0:
        raise AdminError("nginx не перезагрузился")


def _apply_docker_domain_change(host: str, email: str) -> dict[str, Any]:
    """Фоновая смена домена в Docker: volume → сертификат → nginx → рестарт панели."""
    paths = _docker_domain_paths()
    config_path = paths["config"]
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AdminError(f"Не удалось прочитать {config_path}: {exc}") from exc
    if not isinstance(config, dict):
        raise AdminError("config.json имеет неверный формат")
    _backup_docker_panel_config(config_path, paths["backups"])
    config["public_host"] = host
    if email:
        config["certificate_email"] = email
    tls_mode = str(config.get("tls_mode") or "self-signed")
    if tls_mode == "letsencrypt" and not _looks_like_ip(host):
        live = paths["le_live"] / host
        cert, key = str(live / "fullchain.pem"), str(live / "privkey.pem")
        if not _obtain_docker_letsencrypt(host, email, paths):
            cert, key = _issue_docker_self_signed(host, paths)
            tls_mode = "self-signed"
    else:
        # IP или self-signed режим: LE по entrypoint для IP запрещён — только self-signed.
        tls_mode = "self-signed"
        cert, key = _issue_docker_self_signed(host, paths)
    config["certificate_path"] = cert
    config["tls_mode"] = tls_mode
    _write_docker_panel_config(config_path, config)
    _reload_docker_nginx(paths, config, host, cert, key, tls_mode)
    # Панель читает config на старте: рестарт через супервизор (~3с), фронт пережидает опросом.
    try:
        os.kill(os.getpid(), getattr(signal, "SIGTERM", 15))
    except (OSError, ValueError):
        pass
    return {"scheduled": True, "docker": True, "host": host, "tls_mode": tls_mode}


def save_private_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
        os.chmod(temp_name, 0o600)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def normalize_tailscale_auth_key(value: str) -> str:
    value = (value or "").strip()
    if value and not re.fullmatch(r"tskey-auth-[A-Za-z0-9-]{10,200}", value):
        raise ValidationError("Tailscale Auth Key должен быть в формате tskey-auth-...")
    return value


def mask_tailscale_auth_key(value: str) -> str:
    value = str(value or "")
    if not value:
        return ""
    if len(value) <= 16:
        return "***"
    return f"{value[:10]}...{value[-4:]}"


def normalize_tailscale_hostname(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"[^a-z0-9-]+", "-", value).strip("-")
    if len(value) > 63:
        value = value[:63].rstrip("-")
    if value and not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", value):
        raise ValidationError("Имя машины в Tailscale: латиница, цифры и дефис, не длиннее 63 символов")
    return value


def default_tailscale_hostname() -> str:
    try:
        local = socket.gethostname().split(".")[0]
    except OSError:
        local = ""
    cleaned = normalize_tailscale_hostname(local)
    return cleaned or "wdtt-server"


def load_tailscale_settings() -> dict[str, Any]:
    try:
        raw = TAILSCALE_SETTINGS.read_text(encoding="utf-8")
    except OSError:
        return {}
    try:
        data = json.loads(raw)
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def save_tailscale_settings(settings: dict[str, Any]) -> None:
    save_private_json(TAILSCALE_SETTINGS, settings)


TAILSCALE_DOCKER_HINT = (
    "В Docker Tailscale в контейнер не входит: он работает на хосте, и контейнер "
    "ходит в tailnet через маршрутизацию хоста. Управляйте Tailscale на самом "
    "хосте (tailscale up, systemctl status tailscaled) — здесь настраивать нечего."
)


def tailscale_require_available() -> None:
    """В контейнере компонента нет: операции Tailscale адресуют хост."""
    if is_docker():
        raise ValidationError(TAILSCALE_DOCKER_HINT)


def tailscale_status_raw() -> dict[str, Any]:
    """Сырой `tailscale status --json` + признак доступности демона.

    Работает и в Docker (SKIP_SYSTEMD): демон слушает локальный сокет,
    systemd для опроса не нужен.
    """
    if not shutil.which("tailscale"):
        return {"installed": False, "daemon": False, "online": False, "raw": {}}
    try:
        completed = run(["tailscale", "status", "--json"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return {"installed": True, "daemon": False, "online": False, "raw": {}}
    if completed.returncode != 0:
        return {"installed": True, "daemon": False, "online": False, "raw": {}}
    try:
        data = json.loads(completed.stdout)
    except ValueError:
        return {"installed": True, "daemon": True, "online": False, "raw": {}}
    if not isinstance(data, dict):
        return {"installed": True, "daemon": True, "online": False, "raw": {}}
    backend = str(data.get("BackendState") or "")
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    addresses = self_node.get("TailscaleIPs") if isinstance(self_node.get("TailscaleIPs"), list) else []
    addresses = [str(item) for item in addresses if str(item)]
    online = bool(addresses or str(self_node.get("DNSName") or "")) and backend in {"", "Running"}
    return {"installed": True, "daemon": True, "online": online, "raw": data}


def tailscale_status(payload: dict[str, Any]) -> dict[str, Any]:
    if is_docker():
        # Контейнер не держит свой tailscaled (см. docker/Dockerfile): tailnet —
        # на хосте. Отдаём понятное состояние, чтобы вкладка не врала «не установлен».
        return {
            "docker": True,
            "installed": False,
            "daemon": False,
            "online": False,
            "hostname": "",
            "tailscale_ips": [],
            "dns_name": "",
            "tailnet": "",
            "authkey_set": False,
            "authkey_hint": "",
            "accept_dns": True,
            "hint": TAILSCALE_DOCKER_HINT,
        }
    raw = tailscale_status_raw()
    data = raw["raw"] if isinstance(raw["raw"], dict) else {}
    self_node = data.get("Self") if isinstance(data.get("Self"), dict) else {}
    settings = load_tailscale_settings()
    try:
        stored_key = read_private_text(TAILSCALE_AUTHKEY_FILE) or ""
    except OSError:
        stored_key = ""
    return {
        "installed": raw["installed"],
        "daemon": raw["daemon"],
        "online": raw["online"],
        "hostname": str(self_node.get("HostName") or settings.get("hostname") or ""),
        "tailscale_ips": [str(item) for item in (self_node.get("TailscaleIPs") or []) if str(item)],
        "dns_name": str(self_node.get("DNSName") or ""),
        "tailnet": str(data.get("MagicDNSSuffix") or ""),
        "authkey_set": bool(stored_key),
        "authkey_hint": mask_tailscale_auth_key(stored_key),
        "accept_dns": bool(settings.get("accept_dns", True)),
    }


def schedule_tailscale_runtime(payload: dict[str, Any]) -> dict[str, Any]:
    tailscale_require_available()
    if SKIP_SYSTEMD:
        return {"scheduled": True, "state": "test"}
    unit = f"wdtt-tailscale-install-{int(time.time())}"
    result = run_transient_unit(
        ["systemd-run", "--quiet", "--collect", f"--unit={unit}", "--on-active=2s", str(TAILSCALE_INSTALL_COMMAND), "install-tailscale-runtime"],
        timeout=20,
    )
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or "Не удалось запланировать установку Tailscale")
    return {"scheduled": True, "unit": unit}


def ensure_tailscaled_running() -> None:
    """Демон должен отвечать до `tailscale up`: на хосте будим systemd-юнит,
    в Docker его крутит супервизор — там только проверяем сокет."""
    if SKIP_SYSTEMD:
        return
    if not shutil.which("systemctl"):
        return
    run(["systemctl", "enable", "--now", TAILSCALED_SERVICE], timeout=60)


def tailscale_connect(payload: dict[str, Any]) -> dict[str, Any]:
    tailscale_require_available()
    if not shutil.which("tailscale"):
        raise ValidationError("Сначала установите компонент Tailscale")
    try:
        stored_key = read_private_text(TAILSCALE_AUTHKEY_FILE) or ""
    except OSError:
        stored_key = ""
    stored_key = normalize_tailscale_auth_key(stored_key)
    settings = load_tailscale_settings()
    hostname = normalize_tailscale_hostname(
        str(payload.get("hostname") or settings.get("hostname") or default_tailscale_hostname())
    ) or default_tailscale_hostname()
    accept_dns = payload.get("accept_dns", settings.get("accept_dns", True))
    accept_dns = bool(accept_dns) if isinstance(accept_dns, bool) else str(accept_dns).lower() not in {"0", "false", "no", "off", ""}
    fresh_key = normalize_tailscale_auth_key(str(payload.get("auth_key") or ""))
    auth_key = fresh_key or stored_key
    ensure_tailscaled_running()
    raw = tailscale_status_raw()
    if not raw["daemon"]:
        raise AdminError("tailscaled не отвечает: на хосте проверьте юнитом tailscaled, в Docker — перезапустите контейнер")
    # Ключ едет аргументом (виден только root в ps) — файла --auth-key-file у CLI нет.
    # Уже в сети и свежего ключа не дали — только обновляем настройки
    # (имя/DNS): повторный `up` со старым одноразовым ключом упал бы целиком.
    command = ["tailscale", "up", f"--hostname={hostname}", f"--accept-dns={'true' if accept_dns else 'false'}"]
    if fresh_key or not raw["online"]:
        if not auth_key:
            raise ValidationError("Вставьте Auth Key из admin console Tailscale (reusable, с тегом ephemeral при желании)")
        command.append(f"--auth-key={auth_key}")
    connected = run(command, timeout=90)
    if connected.returncode != 0:
        detail = (connected.stderr or connected.stdout or "").strip()
        raise AdminError(detail or "Tailscale не принял Auth Key (одноразовый ключ уже использован?)")
    TAILSCALE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(TAILSCALE_DIR, 0o700)
    if fresh_key or not raw["online"]:
        # Ключ реально участвовал во входе — обновляем сохранённый.
        # В режиме «только настройки» файл не трогаем, иначе сотрём ключ.
        write_private_text(TAILSCALE_AUTHKEY_FILE, auth_key + "\n")
    save_tailscale_settings({"hostname": hostname, "accept_dns": accept_dns})
    return {"connected": True, **tailscale_status({})}


def tailscale_disconnect(payload: dict[str, Any]) -> dict[str, Any]:
    tailscale_require_available()
    if not shutil.which("tailscale"):
        raise ValidationError("Сначала установите компонент Tailscale")
    stopped = run(["tailscale", "down"], timeout=30)
    if stopped.returncode != 0:
        raise AdminError((stopped.stderr or stopped.stdout or "").strip() or "Не удалось остановить Tailscale")
    return {"connected": False, **tailscale_status({})}


def tailscale_logout(payload: dict[str, Any]) -> dict[str, Any]:
    tailscale_require_available()
    if not shutil.which("tailscale"):
        raise ValidationError("Сначала установите компонент Tailscale")
    forgotten = run(["tailscale", "logout"], timeout=30)
    if forgotten.returncode != 0:
        raise AdminError((forgotten.stderr or forgotten.stdout or "").strip() or "Не удалось выйти из tailnet")
    return {"connected": False, **tailscale_status({})}


def tailscale_check(payload: dict[str, Any]) -> dict[str, Any]:
    tailscale_require_available()
    if not shutil.which("tailscale"):
        raise ValidationError("Сначала установите компонент Tailscale")
    if not tailscale_status_raw()["online"]:
        raise ValidationError("Tailscale не в сети — сначала подключитесь")
    try:
        probed = run(["tailscale", "netcheck", "--format=json"], timeout=45)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdminError(f"Проверка Tailscale не выполнена: {exc}") from exc
    try:
        report = json.loads(probed.stdout) if probed.returncode == 0 else {}
    except ValueError:
        report = {}
    if not isinstance(report, dict) or not report:
        # Старый tailscale без --format=json: подтверждаем фактом сети.
        current = tailscale_status({})
        return {"ok": True, "tailscale_ips": current["tailscale_ips"], "dns_name": current["dns_name"], "note": "netcheck без JSON в этой версии"}
    latencies = report.get("DERPLatency") if isinstance(report.get("DERPLatency"), dict) else {}
    best_ms: float | None = None
    for value in latencies.values():
        try:
            millis = float(value) * 1000
        except (TypeError, ValueError):
            continue
        best_ms = millis if best_ms is None else min(best_ms, millis)
    return {
        "ok": True,
        "udp": bool(report.get("UDP")),
        "nearest_derp": str(report.get("NearestDERP") or ""),
        "latency_ms": round(best_ms, 1) if best_ms is not None else None,
        **{key: current_value for key, current_value in tailscale_status({}).items()
           if key in {"tailscale_ips", "dns_name", "hostname"}},
    }


def overview(payload: dict[str, Any]) -> dict[str, Any]:
    data = load_database()
    passwords = data.get("passwords", {})
    user_device_ids = {
        device_id
        for entry in passwords.values()
        if isinstance(entry, dict)
        for device_id in entry_device_ids(entry)
    }
    admin_devices = sum(
        1
        for device_id, device in data.get("devices", {}).items()
        if device_id not in user_device_ids and isinstance(device, dict)
    )
    connection_state = list_users()
    online_user_devices = sum(1 for user in connection_state["users"] if user.get("connected") and user.get("device_id"))
    online_admin_devices = sum(
        1
        for admin in connection_state["admins"]
        for device in (admin.get("devices") or [])
        if device.get("connected")
    )
    stats = read_stats()
    if SKIP_SYSTEMD:
        # В Docker systemd нет: liveness определяем как healthcheck
        # (процесс ядра + отвечающая панель), наличие — по бинарнику.
        # service_active()/service_exists() осознанно не трогаем: от них
        # зависит путь записи БД (mutate_database) — это чинит PR-B.
        wdtt_active = docker_service_active(
            int(payload.get("https_port") or 443),
            str(payload.get("base_path") or "/"),
        )
        runtime_present = Path("/usr/local/bin/wdtt-server").is_file()
    else:
        wdtt_active = service_active()
        runtime_present = service_exists()
    ip_forward = read_ip_forward()
    disk = shutil.disk_usage("/")
    disk_percent = round(disk.used * 100 / disk.total, 1) if disk.total else 0.0
    wdtt_settings = load_wdtt_settings()
    certificate = certificate_info(str(payload.get("certificate_path") or ""))
    certificate.update(
        {
            "mode": str(payload.get("tls_mode") or "unknown"),
            "host": str(payload.get("public_host") or ""),
            "port": int(payload.get("https_port") or 443),
        }
    )
    certificate.update(local_tls_status(certificate["host"], certificate["port"]))
    return {
        "service": {
            "exists": runtime_present,
            "active": wdtt_active,
            "docker": is_docker(),
            "ip_forward": ip_forward,
            "binary": Path("/usr/local/bin/wdtt-server").is_file(),
        },
        "stats": stats,
        "users": len(passwords) + (1 if data.get("main_password") else 0),
        "managed_users": len(passwords),
        "devices": len(data.get("devices", {})),
        "admin_devices": admin_devices,
        "online_devices": online_user_devices + online_admin_devices,
        "online_admin_devices": online_admin_devices,
        "system": {
            "cpu_percent": cpu_usage(),
            "memory": memory_usage(),
            "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else [0, 0, 0],
        },
        "disk": {"total": disk.total, "used": disk.used, "free": disk.free, "percent": disk_percent},
        "certificate": certificate,
        "wdtt": {
            "settings": wdtt_settings,
            "direct_listening": wdtt_listener_open(wdtt_settings.get("direct_port") or 0),
            "raw_listening": wdtt_listener_open(wdtt_settings.get("raw_port") or 0),
            "extension_current": wdtt_extensions_are_verified(),
            "extension_marker": WDTT_EXTENSION_MARKER,
        },
        "timestamp": int(time.time()),
    }


# ---------------------------------------------------------------------------
# wdtt-proxy: панель выступает пультом для systemd-юнита wdtt-proxy.service
# (sing-box tun + fwmark/policy routing). Конфиг — /root/wdtt-proxy/config.env.
# ---------------------------------------------------------------------------
PROXY_EDITABLE_KEYS = (
    "PROXY_SCHEME",
    "PROXY_HOST",
    "PROXY_PORT",
    "PROXY_USER",
    "PROXY_PASS",
    "CAPTURE_IF",
    "SOURCE_CIDR",
    "EXCLUDE_CIDRS",
    "TUN_MTU",
    "LOG_LEVEL",
)
PROXY_SCHEME_VALUES = {"socks5"}
PROXY_LOG_LEVELS = {"trace", "debug", "info", "warn", "error"}
PROXY_STACK_VALUES = {"gvisor", "system", "mixed"}
PROXY_INTERFACE_RE = re.compile(r"^[a-zA-Z0-9._-]{1,15}$")


def proxy_config_path() -> Path:
    return PROXY_DIR / "config.env"


def proxy_installer_candidates() -> tuple[Path, ...]:
    """Пути, откуда панель может запустить установщик (install.sh).

    Первый — переопределяемый (`WDTT_PROXY_INSTALL_COMMAND`), затем штатный
    /opt/wdtt-panel/install.sh. Дубликаты убираем: окружение часто совпадает
    с умолчанием.
    """
    candidates: list[Path] = []
    for candidate in (PROXY_INSTALL_COMMAND, PROXY_INSTALL_FALLBACK_COMMAND):
        if candidate not in candidates:
            candidates.append(candidate)
    return tuple(candidates)


def proxy_installer_path() -> Path | None:
    """Первый доступный установщик панели или None, если его нет вовсе."""
    for candidate in proxy_installer_candidates():
        if candidate.is_file():
            return candidate
    return None


def proxy_installer_missing_message() -> str:
    if is_docker():
        return (
            "Установщик не найден: "
            + " / ".join(str(path) for path in proxy_installer_candidates())
            + ". Образ собран до 2.2.0 (ни установщика, ни sing-box в нём нет) — "
            "обновите образ: `git pull` в репозитории, затем `docker compose up -d --build`."
        )
    return (
        "Установщик не найден: "
        + " / ".join(str(path) for path in proxy_installer_candidates())
        + ". Обновите панель (install.sh update)."
    )


def proxy_installed() -> bool:
    return (PROXY_DIR / "wdtt-proxy.sh").is_file()


def proxy_uses_systemd() -> bool:
    """Есть ли на хосте systemd. В Docker (WDTT_SKIP_SYSTEMD=1) — нет:
    там шлюзом управляет сам wdtt-proxy.sh (nohup + pidfile)."""
    if SKIP_SYSTEMD:
        return False
    return shutil.which("systemctl") is not None and Path("/run/systemd/system").is_dir()


def proxy_script_path() -> Path:
    return PROXY_DIR / "wdtt-proxy.sh"


def proxy_script_active() -> bool:
    """Запущен ли sing-box шлюза — для режима без systemd (Docker)."""
    try:
        result = run(["pgrep", "-f", "sing-box run -c"], timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def proxy_script_run(action: str, timeout: int = 180) -> subprocess.CompletedProcess[str]:
    script = proxy_script_path()
    return run([str(script), action], timeout=timeout)


def read_proxy_config() -> tuple[str, dict[str, str]]:
    """Считывает config.env (shell-формат KEY="VALUE") в словарь.

    Первое значение возвращает вместе с исходником, чтобы save мог
    сохранить комментарии при точечной замене значений.
    """
    path = proxy_config_path()
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise AdminError(f"Не удалось прочитать {path}: {exc}") from exc
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            tokens = shlex.split(stripped, comments=True)
        except ValueError:
            continue
        if not tokens or "=" not in tokens[0]:
            continue
        key, _, value = tokens[0].partition("=")
        key = key.strip()
        if key and key not in values:
            values[key] = value
    return text, values


def proxy_password_hint(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 3:
        return "***"
    return f"{value[:2]}***{value[-2:]}"


def validate_proxy_cidrs(value: str, field: str) -> str:
    cidrs = []
    for item in value.replace(",", " ").split():
        if item in {"255.255.255.255/32", "0.0.0.0/0"}:
            cidrs.append(item)
            continue
        try:
            network = ipaddress.ip_network(item, strict=False)
        except ValueError as exc:
            raise ValidationError(f"{field}: некорректная подсеть «{item}»") from exc
        if network.version != 4:
            raise ValidationError(f"{field}: пока поддерживаются только IPv4-подсети ({item})")
        cidrs.append(str(network))
    return " ".join(cidrs)


def validate_proxy_config(values: dict[str, Any]) -> dict[str, str]:
    """Валидирует поля вкладки «Прокси», возвращает нормализованные значения."""
    out: dict[str, str] = {}
    scheme = str(values.get("PROXY_SCHEME") or "socks5").strip().lower()
    if scheme not in PROXY_SCHEME_VALUES:
        raise ValidationError("PROXY_SCHEME: поддерживается socks5")
    out["PROXY_SCHEME"] = scheme
    host = str(values.get("PROXY_HOST") or "").strip()
    if not host or re.search(r"[\s\"'`$;&|<>\n\r]", host):
        raise ValidationError("PROXY_HOST: укажите хост или IP SOCKS5-сервера")
    if host.count("/") or ":" in host:
        # IPv6-адрес без скобок в shell-строке безопасен, но сокеты socks5
        # в sing-box ждут host отдельно от порта; скобки запрещаем.
        raise ValidationError("PROXY_HOST: указывайте IPv4/имя хоста без порта и скобок")
    out["PROXY_HOST"] = host
    port_raw = str(values.get("PROXY_PORT") or "").strip()
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValidationError("PROXY_PORT: должно быть число 1-65535") from exc
    if not 1 <= port <= 65535:
        raise ValidationError("PROXY_PORT: должно быть числом 1-65535")
    out["PROXY_PORT"] = str(port)
    for key in ("PROXY_USER", "PROXY_PASS"):
        value = str(values.get(key) or "")
        if len(value) > 255 or re.search(r"[\r\n\"]", value):
            raise ValidationError(f"{key}: значение слишком длинное или содержит кавычки/перевод строки")
        out[key] = value
    capture_if = str(values.get("CAPTURE_IF") or "").strip()
    if not PROXY_INTERFACE_RE.match(capture_if):
        raise ValidationError("CAPTURE_IF: имя интерфейса вида wdtt0")
    out["CAPTURE_IF"] = capture_if
    out["SOURCE_CIDR"] = validate_proxy_cidrs(str(values.get("SOURCE_CIDR") or ""), "SOURCE_CIDR") if str(values.get("SOURCE_CIDR") or "").strip() else ""
    if not out["SOURCE_CIDR"]:
        raise ValidationError("SOURCE_CIDR: укажите хотя бы одну подсеть клиентов")
    out["EXCLUDE_CIDRS"] = validate_proxy_cidrs(str(values.get("EXCLUDE_CIDRS") or "").strip(), "EXCLUDE_CIDRS")
    mtu_raw = str(values.get("TUN_MTU") or "").strip()
    try:
        mtu = int(mtu_raw)
    except ValueError as exc:
        raise ValidationError("TUN_MTU: целое число 600-9000") from exc
    if not 600 <= mtu <= 9000:
        raise ValidationError("TUN_MTU: допустимый диапазон 600-9000")
    out["TUN_MTU"] = str(mtu)
    log_level = str(values.get("LOG_LEVEL") or "warn").strip().lower()
    if log_level not in PROXY_LOG_LEVELS:
        raise ValidationError("LOG_LEVEL: trace|debug|info|warn|error")
    out["LOG_LEVEL"] = log_level
    return out


def shq(value: str) -> str:
    """Кавычки для config.env: двойные кавычки с экранированием shell-символов."""
    escaped = (
        value.replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("$", "\\$")
        .replace("`", "\\`")
    )
    return f'"{escaped}"'


def write_proxy_config(current: str, normalized: dict[str, str]) -> None:
    """Точечно заменяет значения редактируемых ключей, комментарии сохраняются."""
    replaced: set[str] = set()
    lines = current.splitlines()
    rebuilt = []
    for line in lines:
        stripped = line.strip()
        matched_key = ""
        try:
            tokens = shlex.split(stripped, comments=True) if stripped and not stripped.startswith("#") else []
        except ValueError:
            tokens = []
        if tokens and "=" in tokens[0]:
            candidate = tokens[0].partition("=")[0].strip()
            if candidate in PROXY_EDITABLE_KEYS:
                matched_key = candidate
        if matched_key:
            rebuilt.append(f"{matched_key}={shq(normalized[matched_key])}")
            replaced.add(matched_key)
        else:
            rebuilt.append(line)
    missing = [key for key in PROXY_EDITABLE_KEYS if key not in replaced]
    if missing and normalized.get(missing[0]):
        rebuilt.append("")
        rebuilt.append("# --- добавлено панелью WDTT ---")
        for key in missing:
            rebuilt.append(f"{key}={shq(normalized[key])}")
    path = proxy_config_path()
    tmp = path.with_suffix(".env.tmp")
    try:
        tmp.write_text("\n".join(rebuilt) + "\n", encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        os.chmod(path, 0o600)
    except OSError as exc:
        raise AdminError(f"Не удалось записать {path}: {exc}") from exc


def proxy_unit_installed() -> bool:
    if not proxy_uses_systemd():
        # Без systemd «юнит» не нужен: шлюзом управляет wdtt-proxy.sh.
        return proxy_installed()
    try:
        result = run(["systemctl", "show", PROXY_SERVICE, "--property=LoadState", "--value"], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and result.stdout.strip() not in {"", "not-found", "not-found "}


def proxy_unit_active() -> bool:
    if not proxy_uses_systemd():
        return proxy_script_active()
    try:
        result = run(["systemctl", "is-active", "--quiet", PROXY_SERVICE], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def proxy_restart() -> None:
    """Перезапуск шлюза после правки config.env (systemd либо скрипт)."""
    if not proxy_uses_systemd():
        if not proxy_installed():
            return
        try:
            result = proxy_script_run("restart")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdminError(f"Не удалось перезапустить wdtt-proxy: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise AdminError(detail or "Конфиг сохранён, но перезапуск wdtt-proxy не удался")
        return
    if not proxy_unit_installed():
        return
    result = run(["systemctl", "restart", PROXY_SERVICE], timeout=60)
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or f"Конфиг сохранён, но systemctl restart {PROXY_SERVICE} не удался")


def proxy_runtime_snapshot(mark: str = "0x100", tun: str = "sbtun0", table: str = "100") -> dict[str, Any]:
    """Живая картина: tun-интерфейс, ip rule и таблица policy-routing."""
    snapshot = {"tun_present": False, "rules": [], "table_routes": []}
    if shutil.which("ip") is None:
        return snapshot
    try:
        iface = run(["ip", "link", "show", tun], timeout=10)
        snapshot["tun_present"] = iface.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        pass
    command_result: dict[str, str] = {}
    for name, command in (("rules", ["ip", "rule", "show"]), ("table_routes", ["ip", "route", "show", "table", table])):
        try:
            result = run(command, timeout=10)
            command_result[name] = result.stdout if result.returncode == 0 else ""
        except (OSError, subprocess.TimeoutExpired):
            command_result[name] = ""
    marker = (mark or "").split("/")[0]
    snapshot["rules"] = [line for line in command_result["rules"].splitlines() if marker and marker in line]
    snapshot["table_routes"] = [line for line in command_result["table_routes"].splitlines() if line.strip()]
    return snapshot


def proxy_install_hint() -> str:
    if is_docker():
        return (
            "Установите компонент кнопкой «Установить wdtt-proxy». Если она отвечает "
            "«Установщик не найден» — контейнер поднят по старому compose-файлу: "
            "выполните `git pull` и затем `docker compose up -d`."
        )
    return (
        "Установите компонент кнопкой «Установить wdtt-proxy» "
        "(или вручную: sudo /opt/wdtt-panel/install.sh install-wdtt-proxy "
        "либо переустановка панели ./install.sh update)"
    )


def proxy_status(payload: dict[str, Any]) -> dict[str, Any]:
    installed = proxy_installed()
    systemd_mode = proxy_uses_systemd()
    unit_installed = proxy_unit_installed() if installed else False
    unit_active = proxy_unit_active() if unit_installed else False
    try:
        _, values = read_proxy_config()
    except AdminError:
        values = {}
    defaults: dict[str, str] = {
        "PROXY_SCHEME": "socks5",
        "PROXY_HOST": "",
        "PROXY_PORT": "26666",
        "PROXY_USER": "",
        "PROXY_PASS": "",
        "CAPTURE_IF": "wdtt0",
        "SOURCE_CIDR": "10.66.0.0/16",
        "EXCLUDE_CIDRS": "",
        "TUN_MTU": "1280",
        "LOG_LEVEL": "warn",
    }
    config = {key: values.get(key, default) for key, default in defaults.items()}
    # Пароль наружу не уходит целиком: только признак «задан» и короткий хинт.
    password = config.pop("PROXY_PASS", "")
    mark = str(values.get("MARK") or "0x100")
    tun = str(values.get("TUN_NAME") or "sbtun0")
    table = str(values.get("ROUTE_TABLE") or "100")
    singbox_bin = str(values.get("SINGBOX_BIN") or "/usr/local/bin/sing-box")
    snapshot = proxy_runtime_snapshot(mark, tun, table) if unit_active and installed else {"tun_present": False, "rules": [], "table_routes": []}
    return {
        "installed": installed,
        "unit_installed": unit_installed,
        "active": unit_active,
        "mode": "systemd" if systemd_mode else "script",
        "autostart": proxy_autostart_enabled() if installed else False,
        "install_hint": "" if installed else proxy_install_hint(),
        "install_command": str(proxy_installer_path() or PROXY_INSTALL_COMMAND),
        "config_dir": str(PROXY_DIR),
        "service": PROXY_SERVICE,
        "singbox_found": shutil.which(singbox_bin) is not None or Path(singbox_bin).is_file(),
        "singbox_bin": singbox_bin,
        "config": config,
        "proxy_pass_set": bool(password),
        "proxy_pass_hint": proxy_password_hint(password),
        "extra": {"MARK": mark, "TUN_NAME": tun, "ROUTE_TABLE": table, "TUN_ADDRESS": str(values.get("TUN_ADDRESS") or "")},
        "runtime": snapshot,
    }


def proxy_autostart_enabled() -> bool:
    """Автозапуск: systemd is-enabled либо маркер .enabled (Docker)."""
    if not proxy_uses_systemd():
        return (PROXY_DIR / ".enabled").is_file()
    try:
        result = run(["systemctl", "is-enabled", "--quiet", PROXY_SERVICE], timeout=15)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def proxy_action(payload: dict[str, Any]) -> dict[str, Any]:
    action = str(payload.get("action") or "")
    if action not in {"start", "stop", "restart"}:
        raise ValidationError("Недопустимое действие прокси")
    if not proxy_installed():
        raise ValidationError("wdtt-proxy не установлен на этом хосте: " + proxy_install_hint())
    if not proxy_uses_systemd():
        # Docker/без systemd: шлюзом управляет собственный скрипт (nohup + pidfile),
        # а «включённость» хранится маркером .enabled — его читает entrypoint.
        try:
            result = proxy_script_run(action)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdminError(f"Не удалось выполнить wdtt-proxy.sh {action}: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip().splitlines()
            raise AdminError(detail[-1] if detail else f"wdtt-proxy.sh {action} вернул ошибку")
        marker = PROXY_DIR / ".enabled"
        try:
            if action == "start":
                marker.touch()
            elif action == "stop" and marker.exists():
                marker.unlink()
        except OSError:
            pass
        return {"action": action, "mode": "script", "active": proxy_unit_active()}
    if not proxy_unit_installed():
        raise AdminError(f"Юнит {PROXY_SERVICE} не установлен: выполните sudo {PROXY_INSTALL_COMMAND} install-wdtt-proxy")
    # «Запустить» и «Остановить» — это ещё и автозапуск при перезагрузке.
    if action == "start":
        command = ["systemctl", "enable", "--now", PROXY_SERVICE]
    elif action == "stop":
        command = ["systemctl", "disable", "--now", PROXY_SERVICE]
    else:
        command = ["systemctl", "restart", PROXY_SERVICE]
    result = run(command, timeout=60)
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or f"Не удалось выполнить {' '.join(command[1:])}")
    return {"action": action, "mode": "systemd", "active": proxy_unit_active()}


def proxy_install(payload: dict[str, Any]) -> dict[str, Any]:
    """Ставит шлюз монолитно: файлы в /opt/wdtt-proxy + sing-box + юнит."""
    if proxy_installed() and payload.get("force") is not True:
        return {"installed": True, "already": True, "state": "present", **proxy_status({})}
    command = proxy_installer_path()
    if command is None:
        raise AdminError(proxy_installer_missing_message())
    if not proxy_uses_systemd():
        # Docker: systemd-run нет — ставим прямо в текущем окружении.
        try:
            result = run(["bash", str(command), "install-wdtt-proxy"], timeout=900)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdminError(f"Не удалось запустить установку wdtt-proxy: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            raise AdminError(detail or "Установка wdtt-proxy завершилась ошибкой")
        return {"installed": proxy_installed(), "scheduled": False, **proxy_status({})}
    unit = f"wdtt-proxy-install-{int(time.time())}"
    result = run_transient_unit(
        ["systemd-run", "--quiet", "--collect", f"--unit={unit}", "--on-active=2s", str(command), "install-wdtt-proxy"],
        timeout=20,
    )
    if result.returncode != 0:
        raise AdminError(result.stderr.strip() or "Не удалось запланировать установку wdtt-proxy")
    return {"scheduled": True, "unit": unit}


def proxy_config_save(payload: dict[str, Any]) -> dict[str, Any]:
    if not proxy_installed():
        raise ValidationError("wdtt-proxy не установлен на этом хосте: " + proxy_install_hint())
    text, values = read_proxy_config()
    update: dict[str, str] = {}
    for key in PROXY_EDITABLE_KEYS:
        if payload.get(key) is None:
            update[key] = values.get(key) or ""
        elif key == "PROXY_PASS":
            raw = str(payload.get("PROXY_PASS"))
            # Пустое значение = не менять (в форме поле не заполняют),
            # "-" = намеренно убрать авторизацию на SOCKS5.
            stripped = raw.strip()
            if stripped in {"", "--"}:
                update[key] = values.get("PROXY_PASS") or ""
            else:
                update[key] = "" if stripped == "-" else stripped
        else:
            update[key] = str(payload.get(key))
    normalized = validate_proxy_config(update)
    write_proxy_config(text, normalized)
    # Если шлюз запущен (или есть юнит) — перезапускаем с новыми настройками.
    if proxy_uses_systemd():
        if proxy_unit_installed():
            proxy_restart()
    elif proxy_unit_active():
        proxy_restart()
    return {"saved": True, "active": proxy_unit_active(), "config": normalized}


OPERATIONS: dict[str, Callable[[dict[str, Any]], Any]] = {
    "overview": overview,
    "users.list": lambda payload: list_users(),
    "users.create": create_user,
    "users.create_bulk": create_users_bulk,
    "users.update": update_user,
    "users.delete": delete_user,
    "users.unbind": unbind_user,
    "users.reset_traffic": reset_traffic,
    "users.renew": renew_user,
    "users.add_traffic": add_user_traffic,
    "users.adjust_plan": adjust_user_plan,
    "users.bulk_action": bulk_user_action,
    "users.fill_hashes": fill_missing_hashes,
    "service.action": lambda payload: service_action(str(payload.get("service_action") or "")),
    "logs": journal_logs,
    "cleanup.preview": lambda payload: cleanup_system(payload, False),
    "cleanup.apply": lambda payload: cleanup_system(payload, True),
    "autoclean.status": lambda payload: autoclean_status(),
    "autoclean.settings": save_autoclean_settings,
    "autoclean.run": run_autoclean,
    "backups.list": lambda payload: list_backups(),
    "backups.create": create_manual_backup,
    "backups.delete": delete_backup,
    "backups.restore": restore_backup,
    "backups.export": export_backup,
    "backups.import": import_backup,
    "backups.schedule": lambda payload: save_backup_schedule(payload) if payload else backup_schedule_status(),
    "panel.version": panel_version,
    "panel.check": schedule_panel_check,
    "panel.update": start_panel_update,
    "certificate.export": export_certificate,
    "certificate.renew": schedule_certificate_renew,
    "telegram.status": lambda payload: telegram_status(payload),
    "telegram.save": configure_telegram,
    "telegram.test": telegram_test,
    "tailscale.status": tailscale_status,
    "tailscale.install": schedule_tailscale_runtime,
    "tailscale.connect": tailscale_connect,
    "tailscale.disconnect": tailscale_disconnect,
    "tailscale.logout": tailscale_logout,
    "tailscale.check": tailscale_check,
    "wdtt.settings": lambda payload: load_wdtt_settings(),
    "wdtt.save": save_wdtt_settings,
    "proxy.status": proxy_status,
    "proxy.install": proxy_install,
    "proxy.action": proxy_action,
    "proxy.config.save": proxy_config_save,
    # Алиасы: панели 2.1.0–2.2.1 шлют короткие имена — не ломаем их.
    "proxy": proxy_status,
    "proxy.config": proxy_config_save,
    "certificate.change_domain": change_panel_domain,
}


def dispatch(request: dict[str, Any]) -> Any:
    action = str(request.get("action") or "")
    handler = OPERATIONS.get(action)
    if handler is None:
        raise ValidationError("Неизвестная административная операция")
    payload = request.get("payload") or {}
    if not isinstance(payload, dict):
        raise ValidationError("payload должен быть объектом")
    return handler(payload)


def main() -> int:
    raw = sys.stdin.buffer.read(MAX_INPUT + 1)
    if len(raw) > MAX_INPUT:
        print(json.dumps({"ok": False, "error": "Запрос слишком большой"}))
        return 2
    try:
        request = json.loads(raw or b"{}")
        if not isinstance(request, dict):
            raise ValidationError("Запрос должен быть JSON-объектом")
        if os.name == "posix":
            import fcntl

            LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
            with LOCK_FILE.open("a", encoding="utf-8") as lock:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
                result = dispatch(request)
        else:
            result = dispatch(request)
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False))
        return 0
    except (ValidationError, AdminError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    except Exception as exc:
        print(json.dumps({"ok": False, "error": f"Внутренняя ошибка: {exc}"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
