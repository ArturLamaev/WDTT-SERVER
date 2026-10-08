"""HTTP-клиент одной ноды: только stdlib (urllib), без зависимостей.

Протокол панели (см. ``wdtt_panel/app.py``):
- ``POST {root}api/v1/auth/login`` ``{"username","password"}`` →
  ``{"ok": true, "result": {"token": ...}}``;
- дальше ``Authorization: Bearer <token>``;
- конверты везде ``{"ok","result"/"error"}``;
- ``info`` / ``overview`` / ``users`` (список) — GET,
  ``users/create|update|delete`` и остальные — POST;
- ноды почти всегда на self-signed → проверка TLS отключена,
  подлинность даёт пароль панели (как в старом cluster.py).
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from typing import Any

from .models import Node

GET_ROUTES = {"info", "auth/session", "overview", "users"}


class FleetError(OSError):
    """Сеть/протокол/нода недоступна."""


class AuthError(FleetError):
    """401: неверный логин/пароль или протухший токен без пароля для обновления."""


def _insecure_context() -> ssl.SSLContext:
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


_INSECURE_CONTEXT = _insecure_context()


def _http_json(
    url: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> tuple[int, dict[str, Any]]:
    """Возвращает (http_status, parsed_json). 401 → AuthError."""
    body = json.dumps(payload or {}, ensure_ascii=False).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/json")
    if body is not None:
        request.add_header("Content-Type", "application/json")
    for key, value in (headers or {}).items():
        request.add_header(key, value)
    context = _INSECURE_CONTEXT if url.startswith("https://") else None
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            raw = response.read()
            status = int(response.status or 200)
    except urllib.error.HTTPError as exc:
        raw = exc.read() if hasattr(exc, "read") else b""
        try:
            parsed = json.loads((raw or b"{}").decode("utf-8") or "{}")
            detail = str(parsed.get("error") or exc.reason)
        except (json.JSONDecodeError, AttributeError):
            detail = str(exc.reason)
        if exc.code == 401:
            raise AuthError(detail or "Неверный логин или пароль") from exc
        raise FleetError(f"HTTP {exc.code}: {detail}") from exc
    except (OSError, ValueError, TimeoutError) as exc:
        raise FleetError(str(exc) or "Сетевой сбой") from exc
    try:
        parsed = json.loads(raw.decode("utf-8") or "{}")
    except json.JSONDecodeError as exc:
        raise FleetError(f"Нода вернула не-JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise FleetError("Нода вернула неверный ответ")
    return status, parsed


class NodeClient:
    """Клиент одной ноды. Токен хранит в ``node.token`` (сохраните стор после login)."""

    def __init__(self, node: Node, timeout: float = 30.0) -> None:
        self.node = node
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        headers: dict[str, str] = {}
        if self.node.token:
            headers["Authorization"] = f"Bearer {self.node.token}"
        return headers

    def login(self) -> dict[str, Any]:
        """Обмен логин/пароль → Bearer-токен. Пароль по сети уходит только сюда."""
        _, data = _http_json(
            self.node.api_url("auth/login"),
            method="POST",
            payload={"username": self.node.username, "password": self.node.password},
            timeout=self.timeout,
        )
        if not data.get("ok"):
            raise AuthError(str(data.get("error") or "Нода отклонила логин"))
        result = data.get("result") or {}
        token = str(result.get("token") or "")
        if not token:
            raise FleetError("Нода не вернула токен")
        self.node.token = token
        return result

    def probe(self) -> dict[str, Any]:
        """Публичный ``api/v1/info`` без авторизации: проверка связности + версия."""
        _, data = _http_json(self.node.api_url("info"), method="GET", timeout=self.timeout)
        if not data.get("ok"):
            raise FleetError(str(data.get("error") or "info вернул !ok"))
        return data.get("result") or {}

    def call(self, route: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        """Один вызов API. Без payload → GET, с payload → POST.

        При 401 один раз перелогинивается по сохранённому паролю и повторяет.
        Возвращает ``result`` при ``ok``, иначе бросает FleetError с текстом ноды.
        """
        route = route.strip("/")
        method = "GET" if payload is None else "POST"
        retried = False
        while True:
            try:
                _, data = _http_json(
                    self.node.api_url(route),
                    method=method,
                    payload=payload,
                    headers=self._headers(),
                    timeout=self.timeout,
                )
            except AuthError:
                if retried or not self.node.password:
                    raise
                self.login()
                retried = True
                continue
            if not data.get("ok"):
                raise FleetError(str(data.get("error") or f"{route} вернул !ok"))
            return data.get("result") if isinstance(data.get("result"), dict) else {"value": data.get("result")}

    # --- короткие обёртки под частые операции ---

    def overview(self) -> dict[str, Any]:
        return self.call("overview")

    def users_list(self) -> dict[str, Any]:
        return self.call("users")

    def user_create(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("users/create", payload)

    def user_update(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.call("users/update", payload)

    def user_delete(self, password: str) -> dict[str, Any]:
        return self.call("users/delete", {"password": password})


def find_user(users_result: dict[str, Any], password: str) -> dict[str, Any] | None:
    """Найти пользователя в ответе ``users`` по ключу (поле ``password``).

    Ответ ноды: ``{"users": [...], ...}`` — каждый элемент с ``password``.
    """
    wanted = (password or "").strip()
    if not wanted:
        return None
    entries = users_result.get("users")
    if not isinstance(entries, list):
        return None
    for item in entries:
        if isinstance(item, dict) and str(item.get("password") or "") == wanted:
            return item
    return None


def touch_online(node: Node, latency: float) -> None:
    node.online = True
    node.last_seen = int(time.time())
    node.last_latency = latency
    node.last_error = ""


def touch_offline(node: Node, error: str) -> None:
    node.online = False
    node.last_error = (error or "нет ответа")[:300]
