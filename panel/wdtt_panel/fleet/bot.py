"""Telegram-бот контроллера: те же операции, что в CLI, но с телефона.

Только stdlib (urllib long-poll ``getUpdates``), без зависимостей.
Доступ — только ``admin_ids`` (чужим отвечает «Нет доступа» и пишет в лог).

Команды:
  /nodes              реестр нод
  /status [node]      overview с одной/всех нод
  /keys KEY           найти ключ на всех нодах
  /create KEY [label] создать на всех нодах (или «KEY nodeID» — на одной)
  /delete KEY all     удалить со всех (с подтверждением кнопкой)
  /delete KEY nodeID  удалить с одной (с подтверждением кнопкой)
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.parse
import urllib.request
from typing import Any

from .client import NodeClient, find_user
from .fanout import fanout, fanout_route, summarize
from .store import FleetStore

log = logging.getLogger("wdtt-fleet-bot")

TG_API = os.environ.get("WDTT_FLEET_TG_API", "https://api.telegram.org").rstrip("/")
MAX_TEXT = 4000


class TelegramAPI:
    """Тонкий клиент Bot API. ``base`` переопределяется тестами."""

    def __init__(self, token: str, base: str = TG_API, timeout: float = 40.0) -> None:
        self.token = token
        self.base = base.rstrip("/")
        self.timeout = timeout

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        url = f"{self.base}/bot{self.token}/{method}"
        body = json.dumps(payload, ensure_ascii=False).encode()
        request = urllib.request.Request(url, data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
        if not isinstance(data, dict) or not data.get("ok"):
            raise OSError(f"Telegram API: {data}")
        return data.get("result")

    def get_me(self) -> dict[str, Any]:
        """Проверка токена: возвращает info бота (username, id). Ошибка → OSError."""
        result = self._post("getMe", {})
        return result if isinstance(result, dict) else {}

    def get_updates(self, offset: int = 0, timeout_sec: int = 25) -> list[dict]:
        result = self._post("getUpdates", {"offset": offset, "timeout": timeout_sec,
                                           "allowed_updates": ["message", "callback_query"]})
        return result if isinstance(result, list) else []

    def send_message(self, chat_id: int, text: str, markup: dict | None = None) -> None:
        for chunk in _split(text):
            payload: dict[str, Any] = {"chat_id": chat_id, "text": chunk}
            if markup is not None:
                payload["reply_markup"] = markup
            self._post("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self._post("answerCallbackQuery", {"callback_query_id": callback_id, "text": text[:200]})


def _split(text: str) -> list[str]:
    if len(text) <= MAX_TEXT:
        return [text]
    parts, current = [], []
    size = 0
    for line in text.splitlines(keepends=True):
        if size + len(line) > MAX_TEXT and current:
            parts.append("".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line)
    if current:
        parts.append("".join(current))
    return parts or [""]


def _confirm_markup(key: str, scope: str) -> dict:
    scope_token = "all" if scope == "all" else scope
    return {"inline_keyboard": [[
        {"text": "Да, удалить", "callback_data": f"del:{key}:{scope_token}"},
        {"text": "Отмена", "callback_data": "noop"},
    ]]}


def _cmd_text(message: dict) -> str:
    text = str((message.get("text") or "")).strip()
    if "@" in text.split()[0]:
        # срезаем @имябота: «/status@mybot» → «/status»
        first, *rest = text.split()
        first = first.split("@")[0]
        text = " ".join([first, *rest])
    return text


class FleetBot:
    def __init__(self, api: TelegramAPI, store: FleetStore, admin_ids: set[int],
                 workers: int = 10, timeout: float = 30.0,
                 client_cls: type | None = None) -> None:
        self.api = api
        self.store = store
        self.admin_ids = set(admin_ids)
        self.workers = workers
        self.timeout = timeout
        self.client_cls = client_cls or NodeClient

    # --- каркас ---

    def authorized(self, user_id: int) -> bool:
        return bool(self.admin_ids) and user_id in self.admin_ids

    def run(self, poll_timeout: int = 25) -> None:
        log.warning("fleet-bot запущен, админов: %d", len(self.admin_ids))
        offset = 0
        while True:
            try:
                updates = self.api.get_updates(offset=offset, timeout_sec=poll_timeout)
            except (OSError, ValueError) as exc:
                log.warning("getUpdates: %s — повтор через 5с", exc)
                time.sleep(5)
                continue
            for update in updates:
                offset = max(offset, int(update.get("update_id") or 0) + 1)
                try:
                    self.handle_update(update)
                except Exception as exc:  # noqa: BLE001 — один апдейт не роняет бота
                    log.warning("update %s: %s", update.get("update_id"), exc)

    def handle_update(self, update: dict) -> None:
        if "callback_query" in update:
            self._handle_callback(update["callback_query"])
            return
        message = update.get("message") or {}
        chat = message.get("chat") or {}
        sender = message.get("from") or {}
        chat_id = chat.get("id")
        if chat_id is None:
            return
        if not self.authorized(int(sender.get("id") or 0)):
            log.warning("чужой доступ: user=%s chat=%s text=%r",
                        sender.get("id"), chat_id, _cmd_text(message)[:60])
            self.api.send_message(int(chat_id), "Нет доступа.")
            return
        self._handle_command(int(chat_id), _cmd_text(message))

    # --- команды ---

    def _handle_command(self, chat_id: int, text: str) -> None:
        if not text.startswith("/"):
            self.api.send_message(chat_id, "Команды через /: " + self._help_brief())
            return
        parts = text[1:].split()
        cmd, args = parts[0].lower(), parts[1:]
        handler = {"start": self._cmd_start, "help": self._cmd_start,
                   "nodes": self._cmd_nodes, "status": self._cmd_status,
                   "keys": self._cmd_keys, "create": self._cmd_create,
                   "delete": self._cmd_delete}.get(cmd)
        if handler is None:
            self.api.send_message(chat_id, f"Не знаю /{cmd}. " + self._help_brief())
            return
        handler(chat_id, args)

    @staticmethod
    def _help_brief() -> str:
        return "/nodes /status [node] /keys KEY /create KEY [label] /delete KEY all|node"

    def _cmd_start(self, chat_id: int, args: list[str]) -> None:
        self.api.send_message(chat_id,
                              "WDTT Fleet: управление нодами.\n"
                              "/nodes — реестр\n"
                              "/status [node] — состояние\n"
                              "/keys KEY — где есть ключ\n"
                              "/create KEY [label] — создать везде (или «KEY node»)\n"
                              "/delete KEY all|node — удалить (спрошу подтверждение)")

    def _cmd_nodes(self, chat_id: int, args: list[str]) -> None:
        nodes = self.store.all()
        if not nodes:
            self.api.send_message(chat_id, "Реестр пуст. Ноды добавляются через CLI: fleet add …")
            return
        lines = [f"{'🟢' if n.online else '⚪'} {n.id} — {n.api_root()}" +
                 (f" ({n.last_error[:60]})" if not n.online and n.last_error else "")
                 for n in nodes]
        self.api.send_message(chat_id, "Ноды:\n" + "\n".join(lines))

    def _scoped(self, args: list[str]) -> tuple[list, str]:
        """(ноды, подпись_области) по хвосту «nodeID» / пустому (= все)."""
        nodes = self.store.all()
        if not nodes:
            raise ValueError("Реестр пуст")
        if args and args[0] != "all":
            node = self.store.get(args[0])
            if node is None:
                raise ValueError(f"Ноды {args[0]!r} нет в реестре")
            return [node], node.id
        return nodes, "все"

    def _cmd_status(self, chat_id: int, args: list[str]) -> None:
        try:
            nodes, _ = self._scoped(args)
        except ValueError as exc:
            self.api.send_message(chat_id, str(exc))
            return
        results = fanout(nodes, lambda c: c.call("overview"),
                         workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        summary = summarize(results)
        lines = []
        for nid, entry in results.items():
            if entry.get("ok"):
                stats = (entry.get("result") or {}).get("stats") or {}
                lines.append(f"🟢 {nid}: {stats.get('active', '?')}/{stats.get('total', '?')} "
                             f"({entry.get('latency', 0):.1f}s)")
            else:
                lines.append(f"🔴 {nid}: {entry.get('error', '?')}")
        lines.append(f"Итог: ok {summary['ok']}/{summary['total']}")
        self.api.send_message(chat_id, "\n".join(lines))

    def _cmd_keys(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.api.send_message(chat_id, "Использование: /keys KEY")
            return
        key = args[0]
        nodes = self.store.all()
        if not nodes:
            self.api.send_message(chat_id, "Реестр пуст")
            return
        results = fanout(nodes, lambda c: c.call("users"),
                         workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        lines = [f"Ключ {key}:"]
        for nid, entry in results.items():
            if not entry.get("ok"):
                lines.append(f"⚪ {nid}: ошибка ноды")
                continue
            user = find_user(entry.get("result") or {}, key)
            lines.append(f"{'🟢' if user else '⚪'} {nid}: " + ("ЕСТЬ" if user else "нет"))
        self.api.send_message(chat_id, "\n".join(lines))

    def _cmd_create(self, chat_id: int, args: list[str]) -> None:
        if not args:
            self.api.send_message(chat_id, "Использование: /create KEY [label] [node|all]")
            return
        key = args[0]
        rest = args[1:]
        try:
            if rest and (node := self.store.get(rest[-1])):
                nodes, scope = [node], node.id
                label = " ".join(rest[:-1])
            elif rest and rest[-1] == "all":
                nodes, scope = self.store.all(), "все"
                label = " ".join(rest[:-1])
            else:
                nodes, scope = self.store.all(), "все"
                label = " ".join(rest)
            if not nodes:
                raise ValueError("Реестр пуст")
        except ValueError as exc:
            self.api.send_message(chat_id, str(exc))
            return
        payload: dict[str, Any] = {"password": key}
        if label:
            payload["label"] = label
        results = fanout_route(nodes, "users/create", payload,
                              workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        summary = summarize(results)
        lines = [f"Создание {key} ({scope}): ok {summary['ok']}/{summary['total']}"]
        for nid, entry in results.items():
            if not entry.get("ok"):
                lines.append(f"🔴 {nid}: {entry.get('error', '?')}")
        self.api.send_message(chat_id, "\n".join(lines))

    def _cmd_delete(self, chat_id: int, args: list[str]) -> None:
        if len(args) < 2:
            self.api.send_message(chat_id, "Использование: /delete KEY all|nodeID")
            return
        key, scope_arg = args[0], args[1]
        if scope_arg == "all":
            scope_desc = f"ВСЕХ {len(self.store.all())} нод"
        else:
            if self.store.get(scope_arg) is None:
                self.api.send_message(chat_id, f"Ноды {scope_arg!r} нет в реестре")
                return
            scope_desc = f"ноды {scope_arg}"
        self.api.send_message(chat_id, f"Удалить {key} с {scope_desc}?",
                              markup=_confirm_markup(key, scope_arg))

    # --- колбэки ---

    def _handle_callback(self, query: dict) -> None:
        sender = query.get("from") or {}
        message = query.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = chat.get("id")
        query_id = str(query.get("id") or "")
        if chat_id is None:
            return
        if not self.authorized(int(sender.get("id") or 0)):
            self.api.answer_callback(query_id, "Нет доступа.")
            return
        data = str(query.get("data") or "")
        if data == "noop":
            self.api.answer_callback(query_id, "Отменено.")
            return
        if data.startswith("del:"):
            _, key, scope = data.split(":", 2)
            self.api.answer_callback(query_id, "Выполняю…")
            self._do_delete(int(chat_id), key, scope)
            return
        self.api.answer_callback(query_id, "Неизвестная кнопка.")

    def _do_delete(self, chat_id: int, key: str, scope: str) -> None:
        if scope == "all":
            nodes = self.store.all()
        else:
            node = self.store.get(scope)
            if node is None:
                self.api.send_message(chat_id, f"Ноды {scope!r} уже нет в реестре")
                return
            nodes = [node]
        if not nodes:
            self.api.send_message(chat_id, "Реестр пуст")
            return
        results = fanout(nodes, lambda c: c.call("users/delete", {"password": key}),
                         workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        summary = summarize(results)
        lines = [f"Удаление {key}: ok {summary['ok']}/{summary['total']}"]
        for nid, entry in results.items():
            if not entry.get("ok"):
                lines.append(f"🔴 {nid}: {entry.get('error', '?')}")
        self.api.send_message(chat_id, "\n".join(lines))


def resolve_admins(raw: str) -> set[int]:
    admins = set()
    for token in (raw or "").replace(";", ",").split(","):
        token = token.strip()
        if token.isdigit():
            admins.add(int(token))
    return admins


def run_bot(store: FleetStore, token: str, admin_ids: set[int],
            workers: int = 10, timeout: float = 30.0, poll_timeout: int = 25) -> None:
    if not token:
        raise ValueError("Нужен токен бота (--token или WDTT_FLEET_BOT_TOKEN)")
    if not admin_ids:
        raise ValueError("Нужен хотя бы один admin id (--admin или WDTT_FLEET_ADMINS)")
    api = TelegramAPI(token)
    FleetBot(api, store, admin_ids, workers=workers, timeout=timeout).run(poll_timeout=poll_timeout)
