"""Настройки контроллера: один JSON-файл с правами 0600.

Лежит рядом с реестром нод: ``<store-dir>/config.json``
(переопределение — ``$WDTT_FLEET_CONFIG``).
Сейчас хранит Telegram-бота; дальше сюда же ляжет веб-авторизация.

Порядок источников для запуска бота:
флаги CLI → env (WDTT_FLEET_BOT_TOKEN / WDTT_FLEET_ADMINS) → файл.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .store import default_store_path

CONFIG_FILE_NAME = "config.json"


def default_config_path(store_path: Path | None = None) -> Path:
    override = os.environ.get("WDTT_FLEET_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    base = store_path or default_store_path()
    return base.parent / CONFIG_FILE_NAME


@dataclass
class FleetSettings:
    bot_token: str = ""
    admin_ids: list[int] = field(default_factory=list)
    poll_timeout: int = 25
    updated_at: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "FleetSettings":
        if not isinstance(data, dict):
            return FleetSettings()
        admins: list[int] = []
        raw_admins = data.get("admin_ids", [])
        if isinstance(raw_admins, list):
            for item in raw_admins:
                try:
                    admins.append(int(item))
                except (TypeError, ValueError):
                    continue
        try:
            poll = int(data.get("poll_timeout") or 25)
        except (TypeError, ValueError):
            poll = 25
        poll = min(120, max(1, poll))
        try:
            updated = int(data.get("updated_at") or 0)
        except (TypeError, ValueError):
            updated = 0
        return FleetSettings(
            bot_token=str(data.get("bot_token") or ""),
            admin_ids=sorted(set(admins)),
            poll_timeout=poll,
            updated_at=updated,
        )


def load_settings(path: Path | None = None) -> tuple[Path, FleetSettings]:
    """Возвращает (путь, настройки). Нет файла — дефолт."""
    file = path or default_config_path()
    try:
        return file, FleetSettings.from_dict(json.loads(file.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return file, FleetSettings()


def save_settings(settings: FleetSettings, path: Path | None = None) -> Path:
    import time

    file = path or default_config_path()
    settings.updated_at = int(time.time())
    file.parent.mkdir(parents=True, exist_ok=True)
    tmp = file.with_suffix(".tmp")
    tmp.write_text(json.dumps(settings.to_dict(), ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, file)
    os.chmod(file, 0o600)
    return file


def parse_admins(raw: str) -> list[int]:
    import re

    return sorted({int(n) for n in re.findall(r"\d+", raw or "")})


def resolve_bot_config(cli_token: str = "", cli_admins: list[str] | None = None,
                       config_path: Path | None = None) -> tuple[str, list[int], str, Path]:
    """(token, admins, источник, путь_к_файлу). Источник: cli|env|file."""
    file = config_path or default_config_path()
    _, saved = load_settings(file)
    env_token = os.environ.get("WDTT_FLEET_BOT_TOKEN", "").strip()
    env_admins = parse_admins(os.environ.get("WDTT_FLEET_ADMINS", ""))
    if cli_token or cli_admins:
        return (cli_token or env_token or saved.bot_token,
                parse_admins(",".join(cli_admins or [])) or env_admins or saved.admin_ids,
                "cli", file)
    if env_token or env_admins:
        return env_token or saved.bot_token, env_admins or saved.admin_ids, "env", file
    return saved.bot_token, saved.admin_ids, "file", file
