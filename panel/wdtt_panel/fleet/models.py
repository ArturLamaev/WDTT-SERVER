"""Модель ноды и нормализация адресов.

Пользователь вбивает URL ноды как попало (с портом/путём или без) —
здесь всё приводится к паре ``base_url`` (scheme://host:port) +
``base_path`` (секретный путь панели, всегда ``/.../``).
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from urllib.parse import urlsplit

ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def normalize_base_url(value: str) -> str:
    """Проверка и чистка ``scheme://host[:port]``. Путь отбрасывается."""
    text = (value or "").strip()
    if not text:
        raise ValueError("Пустой адрес ноды")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise ValueError("Схема адреса должна быть http:// или https://")
    if not parts.hostname:
        raise ValueError("Не указан хост ноды")
    host = parts.hostname
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


def normalize_base_path(value: str) -> str:
    """Секретный путь панели к виду ``/.../``."""
    text = (value or "").strip() or "/"
    if not text.startswith("/"):
        text = "/" + text
    if not text.endswith("/"):
        text += "/"
    return text


def split_node_url(url: str) -> tuple[str, str]:
    """Полный URL (возможно с путём) → (base_url, base_path).

    Пример: ``https://1.2.3.4:9999/s3cr3t`` →
    ``("https://1.2.3.4:9999", "/s3cr3t/")``.
    """
    text = (url or "").strip()
    if not text:
        raise ValueError("Пустой адрес ноды")
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    base_url = normalize_base_url(f"{parts.scheme}://{parts.netloc}")
    return base_url, normalize_base_path(parts.path or "/")


def validate_node_id(value: str) -> str:
    text = (value or "").strip()
    if not ID_RE.match(text):
        raise ValueError(
            "ID ноды: латиница/цифры/._-, начинается с буквы или цифры, до 64 символов"
        )
    return text


@dataclass
class Node:
    """Одна управляемая нода.

    ``password`` хранится в файле ``0600`` и нужен только для
    повторного логина, когда Bearer-токен протух (401).
    """

    id: str
    base_url: str
    base_path: str = "/"
    username: str = "admin"
    password: str = ""
    token: str = ""
    name: str = ""
    online: bool = False
    last_seen: int = 0
    last_error: str = ""
    last_latency: float = 0.0

    def __post_init__(self) -> None:
        self.id = validate_node_id(self.id)
        self.base_url = normalize_base_url(self.base_url)
        self.base_path = normalize_base_path(self.base_path)
        self.username = (self.username or "admin").strip() or "admin"
        self.name = (self.name or "").strip()

    @property
    def display_name(self) -> str:
        return self.name or self.id

    def api_root(self) -> str:
        """Корень API: ``https://host:port/<путь>/``."""
        return self.base_url.rstrip("/") + self.base_path

    def api_url(self, route: str) -> str:
        return self.api_root() + "api/v1/" + route.strip("/")

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Node":
        allowed = {f for f in Node.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in allowed}
        for key in ("id", "base_url"):
            if not clean.get(key):
                raise ValueError(f"У ноды отсутствует поле {key!r}")
        node = Node(
            id=str(clean["id"]),
            base_url=str(clean["base_url"]),
            base_path=str(clean.get("base_path") or "/"),
            username=str(clean.get("username") or "admin"),
            password=str(clean.get("password") or ""),
            token=str(clean.get("token") or ""),
            name=str(clean.get("name") or ""),
        )
        node.online = bool(clean.get("online", False))
        try:
            node.last_seen = int(clean.get("last_seen") or 0)
        except (TypeError, ValueError):
            node.last_seen = 0
        try:
            node.last_latency = float(clean.get("last_latency") or 0.0)
        except (TypeError, ValueError):
            node.last_latency = 0.0
        node.last_error = str(clean.get("last_error") or "")
        return node
