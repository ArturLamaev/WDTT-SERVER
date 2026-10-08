#!/usr/bin/env python3
"""wdtt-exec-args — собрать argv для wdtt-server из настроек панели.

Читает /var/lib/wdtt-panel-private/wdtt-settings.json через normalize-логику
панели (wdtt_panel.admin.load_wdtt_settings — те же дефолты, что в админке:
DTLS 56000, WG 56001, admin 0.0.0.0:56002, DNS 1.1.1.1, MTU 1280, keepalive 25).

При первом старте (файла настроек нет) сидирует его из переменных окружения
WDTT_* — дальше настройками владеет вкладка «WDTT» в панели.

Использование:
    wdtt-exec-args.py --dump     # напечатать по одному аргументу на строку
    (без флага — только записать /run/wdtt/exec-args.json)

Флаги повторяют ExecStart из deploy.sh ядра + тюнабели расширения v10.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/opt/wdtt-panel")

from wdtt_panel.admin import (  # noqa: E402
    WDTT_SETTINGS_FILE,
    load_wdtt_settings,
    normalize_wdtt_settings,
    save_private_json,
    wdtt_extensions_are_verified,
)

CONFIG_DIR = Path("/etc/wdtt")
RUN_FILE = Path("/run/wdtt/exec-args.json")


def _env_port(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def seed_from_env() -> dict:
    return normalize_wdtt_settings(
        {
            "listen_host": "0.0.0.0",
            "dtls_port": _env_port("WDTT_DTLS_PORT", 56000),
            "wg_port": _env_port("WDTT_WG_PORT", 56001),
            "dns": os.environ.get("WDTT_DNS", "1.1.1.1"),
            "direct_port": _env_port("WDTT_DIRECT_PORT", 0),
            "raw_port": _env_port("WDTT_RAW_PORT", 0),
            "admin_listen": f"0.0.0.0:{_env_port('WDTT_ADMIN_PORT', 56002)}",
        }
    )


def build_argv(settings: dict) -> list[str]:
    db = {}
    try:
        db = json.loads(Path("/etc/wdtt/passwords.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        db = {}
    admin_id = str(db.get("admin_id") or os.environ.get("WDTT_TELEGRAM_ADMIN_ID") or "")

    argv = [
        "/usr/local/bin/wdtt-server",
        "-listen", f"{settings['listen_host']}:{settings['dtls_port']}",
        "-wg-port", str(settings["wg_port"]),
        "-config-dir", str(CONFIG_DIR),
        "-password-file", str(CONFIG_DIR / "main.password"),
    ]
    if admin_id:
        argv += ["-admin", admin_id]
    if (CONFIG_DIR / "bot.token").is_file():
        argv += ["-bot-token-file", str(CONFIG_DIR / "bot.token")]
    argv += ["-dns", str(settings["dns"])]
    if settings.get("direct_port"):
        argv += ["-listen-direct", f"0.0.0.0:{settings['direct_port']}"]
    if settings.get("raw_port"):
        argv += ["-listen-raw", f"0.0.0.0:{settings['raw_port']}"]
    argv += [
        "-admin-listen", str(settings["admin_listen"] or ""),
        "-admin-token-file", str(CONFIG_DIR / "admin.token"),
        "-admin-cert", str(CONFIG_DIR / "admin.crt"),
        "-admin-key", str(CONFIG_DIR / "admin.key"),
    ]
    if wdtt_extensions_are_verified():
        argv += [
            "-handshake-timeout", f"{settings['handshake_timeout_s']}s",
            "-first-packet-timeout", f"{settings['first_packet_timeout_s']}s",
            "-wg-keepalive", str(settings["wg_keepalive_s"]),
            "-wg-mtu", str(settings["wg_mtu"]),
            "-stats-interval", f"{settings['stats_interval_s']}s",
            "-max-dtls-per-device", str(settings["max_dtls_per_device"]),
        ]
    return [a for a in argv if a != ""]


def main() -> int:
    RUN_FILE.parent.mkdir(parents=True, exist_ok=True)
    if WDTT_SETTINGS_FILE.is_file():
        settings = load_wdtt_settings()
    else:
        settings = seed_from_env()
        save_private_json(WDTT_SETTINGS_FILE, settings)
    argv = build_argv(settings)
    RUN_FILE.write_text(json.dumps(argv, ensure_ascii=False) + "\n", encoding="utf-8")
    if "--dump" in sys.argv[1:]:
        print("\n".join(argv))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
