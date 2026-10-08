"""Веб-панель контроллера: ноды, статус, ключи, создание/удаление.

Только stdlib (wsgiref-совместимый WSGI). Оформление — в цветах темы
нодовой панели: `fleet/static/fleet.css` поверх её `app.css`
(переменные, светлая тема, акценты и localStorage-ключи общие).
Авторизация: своя (не нодовая) — логин/пароль
контроллера, cookie-сессия + CSRF для форм, Bearer для JSON API.

HTML: GET /login, /nodes, /status, /user
API : POST /api/v1/auth/login, GET /api/v1/nodes,
      POST /api/v1/nodes/add|remove, /api/v1/status,
      /api/v1/keys|create|delete
"""
from __future__ import annotations

import hmac
import html
import json
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlsplit
from http import cookies as http_cookies
from wsgiref.simple_server import make_server

from .bot import TelegramAPI
from .client import NodeClient, find_user
from .fanout import fanout, fanout_route, summarize
from .icons import icon
from .models import Node, split_node_url, normalize_base_path
from ..security import create_session, csrf_token, read_session, verify_csrf, verify_password
from .settings import default_config_path, load_settings, parse_admins, save_settings
from .store import FleetStore
from pathlib import Path

SESSION_COOKIE = "fleet_session"
SESSION_TTL = 43_200

STATIC_DIR = Path(__file__).resolve().parent / "static"
NODE_STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
STATIC_FILES = {"fleet.css": STATIC_DIR / "fleet.css", "app.css": NODE_STATIC_DIR / "app.css"}

ACCENTS = (("red", "#f43f5e"), ("orange", "#f97316"), ("yellow", "#eab308"),
           ("green", "#22c55e"), ("blue", "#3b82f6"), ("purple", "#a855f7"))


def _h(text: Any) -> str:
    return html.escape(str(text if text is not None else ""))


def _hum(value: Any) -> str:
    try:
        size = float(value or 0)
    except (TypeError, ValueError):
        return "—"
    if size <= 0:
        return "0"
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if size < 1024 or unit == "ТБ":
            return f"{size:.0f} {unit}" if unit == "Б" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} ТБ"


def _short_link(link: str, limit: int = 26) -> str:
    link = link or ""
    return link if len(link) <= limit else link[:limit] + "…"


def _node_host(node: Node) -> str:
    """Хост VPN ноды: хост её панельного URL (обычно тот же публичный IP/домен)."""
    try:
        return urlsplit(node.base_url).hostname or node.base_url
    except ValueError:
        return node.base_url


def _user_link(node: Node, user: dict) -> str:
    """Ссылка подключения как в нодовой панели: wdtt://host:p1:p2:p3:key:hashes."""
    password = str(user.get("password") or "")
    if password:
        parts = [p.strip() for p in str(user.get("ports") or "56000,56001,9000").split(",")]
        parts += ["56000", "56001", "9000"]
        return (f"wdtt://{_node_host(node)}:{parts[0]}:{parts[1]}:{parts[2]}"
                f":{password}:{user.get('vk_hash') or ''}")
    for field in ("link", "url"):
        if user.get(field):
            return str(user[field])
    personal = str(user.get("personal_url") or "")
    if personal:
        return node.api_root() + personal.lstrip("/")
    return ""


def _copy_button(link: str) -> str:
    if not link:
        return "<span class=muted>—</span>"
    return (f"<code class=key title='{_h(link)}'>{_h(_short_link(link))}</code> "
            f"<button class=secondary data-copy='{_h(link)}'>{icon('copy')}Скопировать</button>")


def _page(title: str, body: str, active: str = "", nav: bool = True) -> str:
    dots = "".join(
        f"<button title='{_h(name)}' data-a='{_h(name)}' style='background:{_h(color)}'"
        f" onclick='fleetAccent(this.dataset.a)'></button>"
        for name, color in ACCENTS)
    nav_html = ""
    if nav:
        links = (("nodes", "Ноды", "server"), ("status", "Статус", "activity"),
                 ("user", "Пользователи", "users"), ("bot", "Бот", "robot"))
        items = "".join(
            f"<a href={_h(name)}{' class=active' if name == active else ''}>"
            f"{icon(icon_name)}{_h(label)}</a>"
            for name, label, icon_name in links)
        nav_html = (
            f"<header class=fleet-top><div class=brand>WDTT Fleet"
            f"<small>панель управления нодами</small></div>"
            f"<nav class=fleet-nav>{items}</nav>"
            f"<div class=fleet-tools><span class=accent-dots>{dots}</span>"
            f"<button class=theme-btn onclick='fleetTheme()'>"
            f"<span class=icon-sun>{icon('sun')}</span>"
            f"<span class=icon-moon>{icon('moon')}</span></button></div></header>")
    else:
        nav_html = ("<header class=fleet-top><div class=brand>WDTT Fleet"
                    "<small>панель управления нодами</small></div></header>")
    return (f"<!doctype html><html lang=ru><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>{_h(title)} — WDTT Fleet</title>"
            f"<link rel=stylesheet href='static/fleet.css'></head><body>"
            f"{nav_html}<div class=wrap><h1>{_h(title)}</h1>{body}</div>"
            f"<script>try{{if(localStorage.getItem('wdtt-theme')==='light')"
            f"document.body.classList.add('light-theme');var a=localStorage.getItem('wdtt-accent');"
            f"if(a)document.body.dataset.accent=a;}}catch(e){{}}"
            f"function fleetTheme(){{document.body.classList.toggle('light-theme');"
            f"try{{localStorage.setItem('wdtt-theme',document.body.classList.contains('light-theme')"
            f"?'light':'dark');}}catch(e){{}}}}"
            f"function fleetAccent(a){{document.body.dataset.accent=a;"
            f"try{{localStorage.setItem('wdtt-accent',a);}}catch(e){{}}"
            f"document.querySelectorAll('.accent-dots button').forEach(function(b)"
            f"{{b.classList.toggle('on',b.dataset.a===a);}});}}"
            f"document.addEventListener('click',function(e){{var b=e.target.closest('[data-copy]');"
            f"if(!b)return;var t=b.getAttribute('data-copy');"
            f"function done(){{var o=b.innerHTML;b.innerHTML='Скопировано';"
            f"setTimeout(function(){{b.innerHTML=o;}},1500);}}"
            f"if(navigator.clipboard&&navigator.clipboard.writeText)"
            f"{{navigator.clipboard.writeText(t).then(done,function(){{fallback();}});}}"
            f"else fallback();"
            f"function fallback(){{var ta=document.createElement('textarea');ta.value=t;"
            f"document.body.appendChild(ta);ta.select();"
            f"try{{document.execCommand('copy');done();}}catch(_c){{}}ta.remove();}}}});"
            f"</script></body></html>")


class FleetWeb:
    """WSGI-приложение. ``client_cls`` — подмена клиента ноды (тесты)."""

    def __init__(self, store: FleetStore, username: str, password_hash: str,
                 secret: str, base: str = "/", workers: int = 10,
                 timeout: float = 30.0, client_cls: type | None = None) -> None:
        self.store = store
        self.username = username
        self.password_hash = password_hash
        self.secret = secret
        base = "/" + (base or "/").strip("/") + "/"
        self.base = base.replace("//", "/")
        self.workers = workers
        self.timeout = timeout
        self.client_cls = client_cls or NodeClient
        self.config_path = default_config_path(store.path)

    # --- wsgi ---

    def __call__(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        path = str(environ.get("PATH_INFO") or "/")
        if self.base != "/" and not path.startswith(self.base.rstrip("/")):
            return self._text(start_response, "404 Not Found", "Not found")
        relative = path[len(self.base.rstrip("/")):] if self.base != "/" else path
        relative = relative.lstrip("/")
        method = environ.get("REQUEST_METHOD", "GET")

        if relative.startswith("static/") and method == "GET":
            return self._static(start_response, relative[7:])
        if relative == "api/v1/auth/login" and method == "POST":
            return self._api_login(environ, start_response)
        if relative.startswith("api/v1/"):
            session = self._bearer(environ)
            if session is None:
                return self._json(start_response, 401, {"ok": False, "error": "Требуется bearer-токен"})
            return self._api(environ, start_response, relative[7:], session)
        if relative in ("", "login") and method == "GET":
            session = self._session(environ)
            if session is not None and relative == "":
                return self._redirect(start_response, "nodes")
            if relative == "":
                return self._redirect(start_response, "login")
            return self._login_page(start_response)
        if relative == "login" and method == "POST":
            return self._do_login(environ, start_response)
        session = self._session(environ)
        if session is None:
            return self._redirect(start_response, "login")
        if relative == "logout" and method == "POST":
            if not self._csrf_ok(environ, session):
                return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
            return self._redirect(start_response, "login", clear_cookie=True)
        if relative == "nodes" and method == "GET":
            return self._nodes_page(start_response, session)
        if relative == "nodes/add" and method == "POST":
            return self._nodes_add(environ, start_response, session)
        if relative == "nodes/remove" and method == "POST":
            return self._nodes_remove(environ, start_response, session)
        if relative == "status" and method == "GET":
            return self._status_page(environ, start_response, session)
        if relative == "user" and method == "GET":
            return self._user_page(environ, start_response, session)
        if relative == "user/create" and method == "POST":
            return self._user_create(environ, start_response, session)
        if relative == "user/delete" and method == "POST":
            return self._user_delete(environ, start_response, session)
        if relative == "bot" and method == "GET":
            return self._bot_page(environ, start_response, session)
        if relative == "bot/save" and method == "POST":
            return self._bot_save(environ, start_response, session)
        if relative == "bot/test" and method == "POST":
            return self._bot_test(environ, start_response, session)
        return self._text(start_response, "404 Not Found", "Not found")

    # --- http helpers ---

    def _url(self, name: str) -> str:
        return (self.base.rstrip("/") + "/" + name).replace("//", "/")

    def _bytes(self, start_response: Callable, status: str, body: bytes,
               ctype: str = "text/html; charset=utf-8",
               headers: list | None = None) -> Iterable[bytes]:
        heads = [("Content-Type", ctype), ("Content-Length", str(len(body)))]
        heads.extend(headers or [])
        start_response(status, heads)
        return [body]

    def _text(self, start_response: Callable, status: str, body: str) -> Iterable[bytes]:
        return self._bytes(start_response, status, body.encode(), "text/plain; charset=utf-8")

    def _json(self, start_response: Callable, code: int, payload: dict) -> Iterable[bytes]:
        phrase = {200: "OK", 400: "Bad Request", 401: "Unauthorized",
                  404: "Not Found"}.get(code, "OK")
        body = json.dumps(payload, ensure_ascii=False).encode()
        return self._bytes(start_response, f"{code} {phrase}", body, "application/json",
                           [("Cache-Control", "no-store")])

    def _html(self, start_response: Callable, title: str, body: str,
              code: str = "200 OK", active: str = "", nav: bool = True) -> Iterable[bytes]:
        return self._bytes(start_response, code, _page(title, body, active, nav).encode())

    def _static(self, start_response: Callable, name: str) -> Iterable[bytes]:
        path = STATIC_FILES.get(name)
        if path is None or not path.is_file():
            return self._text(start_response, "404 Not Found", "Not found")
        return self._bytes(start_response, "200 OK", path.read_bytes(),
                           "text/css; charset=utf-8", [("Cache-Control", "max-age=3600")])

    def _redirect(self, start_response: Callable, name: str,
                  clear_cookie: bool = False) -> Iterable[bytes]:
        headers = [("Location", self._url(name))]
        if clear_cookie:
            headers.append(("Set-Cookie", f"{SESSION_COOKIE}=; Path=/; Max-Age=0"))
        start_response("302 Found", headers)
        return [b""]

    def _read_form(self, environ: dict) -> dict[str, str]:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        raw = environ["wsgi.input"].read(max(0, length)).decode("utf-8", "replace")
        return {k: v[0] for k, v in parse_qs(raw, keep_blank_values=True).items()}

    def _read_json(self, environ: dict) -> dict:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        try:
            data = json.loads(environ["wsgi.input"].read(max(0, length)) or b"{}")
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, ValueError):
            return {}

    # --- auth ---

    def _session(self, environ: dict) -> dict | None:
        jar = http_cookies.SimpleCookie(environ.get("HTTP_COOKIE", ""))
        item = jar.get(SESSION_COOKIE)
        if item is None:
            return None
        return read_session(item.value, self.secret)

    def _bearer(self, environ: dict) -> dict | None:
        header = str(environ.get("HTTP_AUTHORIZATION") or "").strip()
        if not header.lower().startswith("bearer "):
            return None
        token = header[7:].strip()
        return read_session(token, self.secret) if token else None

    def _csrf_ok(self, environ: dict, session: dict) -> bool:
        form = self._read_form(environ)
        environ["_fleet_form"] = form
        return verify_csrf(form.get("csrf", ""), session, self.secret)

    def _csrf_field(self, session: dict) -> str:
        token = csrf_token(str(session.get("n", "")), self.secret)
        return f"<input type=hidden name=csrf value='{_h(token)}'>"

    def _login_page(self, start_response: Callable, error: str = "") -> Iterable[bytes]:
        err = f"<p class=err>{_h(error)}</p>" if error else ""
        return self._html(start_response, "Вход",
                          f"<div class=login-wrap><div class=card>{err}"
                          f"<form method=post action=login>"
                          f"Логин<input name=username autocomplete=username><br>Пароль"
                          f"<input type=password name=password autocomplete=current-password><br>"
                          f"<button class=primary>Войти</button></form></div></div>",
                          nav=False)

    def _do_login(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        form = self._read_form(environ)
        user_ok = hmac.compare_digest(form.get("username", ""), self.username)
        password_ok = verify_password(form.get("password", ""), self.password_hash)
        if not (user_ok and password_ok):
            return self._login_page(start_response, "Неверный логин или пароль")
        token, _ = create_session(self.username, self.secret, SESSION_TTL)
        start_response("302 Found", [
            ("Location", self._url("nodes")),
            ("Set-Cookie", f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}"),
        ])
        return [b""]

    def _api_login(self, environ: dict, start_response: Callable) -> Iterable[bytes]:
        payload = self._read_json(environ)
        user_ok = hmac.compare_digest(str(payload.get("username") or ""), self.username)
        password_ok = verify_password(str(payload.get("password") or ""), self.password_hash)
        if not (user_ok and password_ok):
            return self._json(start_response, 401, {"ok": False, "error": "Неверный логин или пароль"})
        token, _ = create_session(self.username, self.secret, SESSION_TTL)
        return self._json(start_response, 200, {"ok": True, "result": {"token": token,
                                                                      "token_type": "Bearer",
                                                                      "expires_in": SESSION_TTL}})

    # --- nodes ---

    def _nodes_page(self, start_response: Callable, session: dict,
                    note: str = "") -> Iterable[bytes]:
        rows = "".join(
            f"<tr><td><strong>{_h(n.id)}</strong><br><span class=muted>{_h(n.display_name)}</span></td>"
            f"<td><code class=key>{_h(n.api_root())}</code></td>"
            f"<td>{'<span class=\"dot ok\"></span>online' if n.online else '<span class=\"dot idle\"></span>' + _h(n.last_error or '—')}</td>"
            f"<td><form class=inline-form method=post action=nodes/remove>{self._csrf_field(session)}"
            f"<input type=hidden name=id value='{_h(n.id)}'>"
            f"<button class=danger>{icon('trash')}Убрать</button></form></td></tr>"
            for n in self.store.all())
        if not rows:
            rows = "<tr><td colspan=4 class=muted>Реестр пуст — добавьте первую ноду ниже.</td></tr>"
        body = ((f"<p class=ok-text>{_h(note)}</p>" if note else "") +
                f"<div class=card><table class=grid><tr><th>Нода</th><th>URL</th><th>Статус</th><th></th></tr>{rows}</table></div>"
                f"<div class=card><h3>Добавить ноду</h3>"
                f"<form method=post action=nodes/add>{self._csrf_field(session)}"
                f"ID <input name=id required> URL панели <input name=url size=40 required "
                f"placeholder='https://IP:9999/путь'><br>Путь <input name=path> "
                f"Логин <input name=username value=admin> Пароль <input type=password name=password required> "
                f"Имя <input name=name><button class=primary>{icon('plus')}Добавить</button></form></div>")
        return self._html(start_response, "Ноды", body, active="nodes")

    def _add_node(self, node_id: str, url: str, path: str, username: str,
                  password: str, name: str) -> tuple[Node | None, str]:
        try:
            base_url, base_path = split_node_url(url)
        except ValueError as exc:
            return None, str(exc)
        if path.strip():
            base_path = normalize_base_path(path)
        node = Node(id=node_id, base_url=base_url, base_path=base_path,
                    username=username or "admin", password=password, name=name or "")
        try:
            self.client_cls(node, timeout=self.timeout).login()
        except Exception as exc:
            return None, f"Нода недоступна или пароль неверный: {exc}"
        try:
            self.store.add(node)
        except ValueError as exc:
            return None, str(exc)
        return node, ""

    def _nodes_add(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        if not self._csrf_ok(environ, session):
            return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
        form = environ["_fleet_form"]
        node, error = self._add_node(form.get("id", ""), form.get("url", ""),
                                     form.get("path", ""), form.get("username", ""),
                                     form.get("password", ""), form.get("name", ""))
        if node is None:
            return self._nodes_page(start_response, session, note=f"Ошибка: {error}")
        return self._redirect(start_response, "nodes")

    def _nodes_remove(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        if not self._csrf_ok(environ, session):
            return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
        self.store.remove(environ["_fleet_form"].get("id", ""))
        return self._redirect(start_response, "nodes")

    # --- status ---

    def _pick(self, node_id: str) -> tuple[list[Node], str]:
        nodes = self.store.all()
        if node_id:
            node = self.store.get(node_id)
            if node is None:
                raise ValueError(f"Ноды {node_id!r} нет в реестре")
            return [node], node.id
        if not nodes:
            raise ValueError("Реестр пуст — добавьте ноду")
        return nodes, "все"

    def _status_page(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        query = parse_qs(environ.get("QUERY_STRING") or "")
        node_id = (query.get("node") or [""])[0]
        try:
            nodes, _ = self._pick(node_id)
        except ValueError as exc:
            return self._html(start_response, "Статус", f"<p class=err>{_h(exc)}</p>", active="status")
        results = fanout(nodes, lambda c: c.call("overview"),
                         workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        rows = ""
        for nid, entry in results.items():
            if entry.get("ok"):
                stats = (entry.get("result") or {}).get("stats") or {}
                rows += (f"<tr><td><strong>{_h(nid)}</strong></td>"
                         f"<td><span class=\"dot ok\"></span><span class=ok-text>online</span></td>"
                         f"<td>{_h(stats.get('active', '?'))} / {_h(stats.get('total', '?'))}</td>"
                         f"<td>{entry.get('latency', 0):.1f} c</td></tr>")
            else:
                rows += (f"<tr><td><strong>{_h(nid)}</strong></td>"
                         f"<td><span class=\"dot bad\"></span><span class=err>FAIL</span></td>"
                         f"<td colspan=2 class=muted>{_h(entry.get('error', '?'))}</td></tr>")
        summary = summarize(results)
        return self._html(start_response, "Статус",
                          f"<p class=sub>ok {summary['ok']}/{summary['total']}</p>"
                          f"<div class=card><table class=grid><tr><th>Нода</th><th>Статус</th><th>Активно / Всего</th><th>Пинг</th></tr>"
                          f"{rows}</table></div>",
                          active="status")

    # --- user ---

    def _user_page(self, environ: dict, start_response: Callable, session: dict,
                   found: str = "") -> Iterable[bytes]:
        query = parse_qs(environ.get("QUERY_STRING") or "")
        key = (query.get("key") or [""])[0]
        nodes = self.store.all()
        by_id = {n.id: n for n in nodes}
        lookup = ""
        table = "<p class=muted>Реестр пуст — добавьте ноду.</p>"
        if nodes:
            results = fanout(nodes, lambda c: c.call("users"),
                             workers=self.workers, timeout=self.timeout,
                             client_cls=self.client_cls)
            self.store.save()
            if key:
                lookup = self._keys_block(by_id, key, results)
            elif found:
                lookup = f"<p class=ok-text>{_h(found)}</p>"
            table = self._all_users_block(by_id, session, results)
        elif found:
            lookup = f"<p class=ok-text>{_h(found)}</p>"
        body = (f"<div class=card><h3>Найти ключ</h3><form method=get action=user>"
                f"<input name=key size=30 value='{_h(key)}' placeholder='Ключ пользователя'>"
                f"<button class=primary>{icon('search')}Найти везде</button></form>{lookup}</div>"
                f"<div class=card><h3>Создать</h3><form method=post action=user/create>"
                f"{self._csrf_field(session)}Ключ <input name=password required> "
                f"Метка <input name=label> Нода <input name=node placeholder='пусто = все'>"
                f"<button class=primary>{icon('plus')}Создать</button></form></div>"
                f"<div class=card><h3>Удалить</h3><form method=post action=user/delete>"
                f"{self._csrf_field(session)}Ключ <input name=password required> "
                f"Нода <input name=node placeholder='пусто = все'> "
                f"<label><input type=checkbox name=confirm value=1 required> подтверждаю</label>"
                f"<button class=danger>{icon('trash')}Удалить</button></form></div>"
                f"<div class=card><h3>Все пользователи на нодах</h3>{table}</div>")
        return self._html(start_response, "Пользователи", body, active="user")

    def _keys_block(self, by_id: dict, key: str, results: dict | None = None) -> str:
        if results is None:
            nodes = self.store.all()
            if not nodes:
                return "<p class=err>Реестр пуст</p>"
            by_id = {n.id: n for n in nodes}
            results = fanout(nodes, lambda c: c.call("users"),
                             workers=self.workers, timeout=self.timeout,
                             client_cls=self.client_cls)
            self.store.save()
        rows = ""
        for nid, entry in results.items():
            if not entry.get("ok"):
                rows += (f"<tr><td><strong>{_h(nid)}</strong></td>"
                         f"<td><span class=\"dot bad\"></span><span class=err>ошибка ноды</span></td><td></td></tr>")
                continue
            user = find_user(entry.get("result") or {}, key)
            if user:
                node = by_id.get(nid)
                cell = _copy_button(_user_link(node, user)) if node else "<span class=muted>—</span>"
            else:
                cell = ""
            rows += (f"<tr><td><strong>{_h(nid)}</strong></td>"
                     f"<td><span class=\"dot ok\"></span><span class=ok-text>ЕСТЬ</span></td>"
                     f"<td>{cell}</td></tr>"
                     if user else f"<tr><td><strong>{_h(nid)}</strong></td>"
                     f"<td><span class=\"dot idle\"></span>нет</td><td></td></tr>")
        return (f"<table class=grid><tr><th>Нода</th><th>Ключ {_h(key)}</th><th>Ссылка</th></tr>{rows}</table>")

    def _all_users_block(self, by_id: dict, session: dict, results: dict) -> str:
        parts = []
        total = 0
        for nid, entry in results.items():
            if not entry.get("ok"):
                parts.append(f"<p><strong>{_h(nid)}</strong>: "
                             f"<span class=err>ошибка ноды — {_h(entry.get('error', '?'))}</span></p>")
                continue
            users = (entry.get("result") or {}).get("users") or []
            if not isinstance(users, list):
                users = []
            total += len(users)
            node = by_id.get(nid)
            rows = ""
            for user in users:
                if not isinstance(user, dict):
                    continue
                password = str(user.get("password") or "")
                label = str(user.get("label") or "")
                link = _user_link(node, user) if node else ""
                down = _hum(user.get("down_bytes"))
                up = _hum(user.get("up_bytes"))
                online = user.get("connected") or user.get("online")
                state = ("<span class=\"dot ok\"></span><span class=ok-text>online</span>"
                         if online else "<span class=\"dot idle\"></span><span class=muted>—</span>")
                rows += (f"<tr><td><code class=key>{_h(password)}</code>"
                         + (f"<br><span class=muted>{_h(label)}</span>" if label else "") + "</td>"
                         f"<td>{_copy_button(link)}</td>"
                         f"<td class=muted>↓ {down}<br>↑ {up}</td>"
                         f"<td>{state}</td>"
                         f"<td><form class=inline-form method=post action=user/delete>"
                         f"{self._csrf_field(session)}"
                         f"<input type=hidden name=password value='{_h(password)}'>"
                         f"<input type=hidden name=node value='{_h(nid)}'>"
                         f"<input type=hidden name=confirm value=1>"
                         f"<button class=danger>{icon('trash')}Удалить</button></form></td></tr>")
            if not rows:
                rows = "<tr><td colspan=5 class=muted>нет пользователей</td></tr>"
            parts.append(f"<h3>{_h(nid)} <span class=muted>({len(users)})</span></h3>"
                         f"<table class=grid><tr><th>Ключ</th><th>Ссылка</th>"
                         f"<th>Трафик</th><th></th><th></th></tr>{rows}</table>")
        head = f"<p class=sub>Всего: {total}</p>" if parts else ""
        return head + "".join(parts)

    def _user_create(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        if not self._csrf_ok(environ, session):
            return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
        form = environ["_fleet_form"]
        key = form.get("password", "").strip()
        if not key:
            return self._user_page(environ, start_response, session)
        try:
            nodes, _ = self._pick(form.get("node", "").strip())
        except ValueError as exc:
            return self._html(start_response, "Пользователи", f"<p class=err>{_h(exc)}</p>", active="user")
        payload: dict[str, Any] = {"password": key}
        if form.get("label", "").strip():
            payload["label"] = form["label"].strip()
        results = fanout_route(nodes, "users/create", payload,
                              workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        summary = summarize(results)
        return self._html(start_response, "Пользователи",
                          f"<div class=card><p>Создание {_h(key)}: ok {summary['ok']}/{summary['total']}</p>"
                          + "".join(f"<p class={'ok-text' if e.get('ok') else 'err'}>{_h(nid)}: "
                                    f"{_h('OK' if e.get('ok') else e.get('error', '?'))}</p>"
                                    for nid, e in results.items()) + "</div>",
                          active="user")

    def _user_delete(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        if not self._csrf_ok(environ, session):
            return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
        form = environ["_fleet_form"]
        key = form.get("password", "").strip()
        if not key or not form.get("confirm"):
            return self._html(start_response, "Пользователи",
                              "<p class=err>Нужен ключ и галочка подтверждения</p>", active="user")
        node_arg = form.get("node", "").strip()
        try:
            nodes = self.store.all()
            if node_arg:
                node = self.store.get(node_arg)
                if node is None:
                    raise ValueError(f"Ноды {node_arg!r} нет в реестре")
                nodes = [node]
            elif len(nodes) > 1:
                raise ValueError("Больше одной ноды: укажите ноду или удаляйте через API с everywhere")
        except ValueError as exc:
            return self._html(start_response, "Пользователи", f"<p class=err>{_h(exc)}</p>", active="user")
        results = fanout(nodes, lambda c: c.call("users/delete", {"password": key}),
                         workers=self.workers, timeout=self.timeout, client_cls=self.client_cls)
        self.store.save()
        summary = summarize(results)
        return self._html(start_response, "Пользователи",
                          f"<div class=card><p>Удаление {_h(key)}: ok {summary['ok']}/{summary['total']}</p>"
                          + "".join(f"<p class={'ok-text' if e.get('ok') else 'err'}>{_h(nid)}: "
                                    f"{_h('OK' if e.get('ok') else e.get('error', '?'))}</p>"
                                    for nid, e in results.items()) + "</div>",
                          active="user")

    # --- telegram-бот ---

    def _bot_state(self) -> dict:
        _, saved = load_settings(self.config_path)
        tail = saved.bot_token[-4:] if saved.bot_token else ""
        return {"token_set": bool(saved.bot_token), "token_tail": tail,
                "admin_ids": saved.admin_ids, "poll_timeout": saved.poll_timeout,
                "config_path": str(self.config_path)}

    def _bot_page(self, environ: dict, start_response: Callable, session: dict,
                  note: str = "", is_error: bool = False) -> Iterable[bytes]:
        _, saved = load_settings(self.config_path)
        cls = "err" if is_error else "ok-text"
        body = ((f"<p class={cls}>{_h(note)}</p>" if note else "") +
                "<div class=card><h3>Telegram-бот</h3>"
                f"<form method=post action=bot/save>{self._csrf_field(session)}"
                f"Токен <input name=bot_token size=50 value='{_h(saved.bot_token)}' "
                f"placeholder='123456:ABC...'><br>"
                f"Admin ID <input name=admin_ids size=40 value='{_h(','.join(map(str, saved.admin_ids)))}' "
                f"placeholder='111,222'><br>"
                f"Poll-таймаут <input name=poll_timeout size=4 value='{saved.poll_timeout}'> сек<br>"
                f"<button class=primary>{icon('check')}Сохранить</button></form>"
                f"<form method=post action=bot/test>{self._csrf_field(session)}"
                f"<button class=secondary>{icon('search')}Проверить токен (getMe)</button></form>"
                f"<p class=muted>Файл: {_h(self.config_path)}. После смены токена перезапустите "
                f"<code>fleet bot</code> — он подхватит настройки из этого файла. "
                f"CLI-флаги и env ($WDTT_FLEET_BOT_TOKEN, $WDTT_FLEET_ADMINS) "
                f"имеют приоритет над файлом.</p></div>")
        return self._html(start_response, "Бот", body, active="bot")

    def _bot_save(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        if not self._csrf_ok(environ, session):
            return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
        form = environ["_fleet_form"]
        _, saved = load_settings(self.config_path)
        saved.bot_token = form.get("bot_token", "").strip()
        saved.admin_ids = parse_admins(form.get("admin_ids", ""))
        try:
            saved.poll_timeout = min(120, max(1, int(form.get("poll_timeout") or 25)))
        except (TypeError, ValueError):
            saved.poll_timeout = 25
        save_settings(saved, self.config_path)
        return self._bot_page(environ, start_response, session, note="Сохранено. Перезапустите fleet bot.")

    def _bot_test(self, environ: dict, start_response: Callable, session: dict) -> Iterable[bytes]:
        if not self._csrf_ok(environ, session):
            return self._text(start_response, "403 Forbidden", "CSRF-проверка не пройдена")
        _, saved = load_settings(self.config_path)
        if not saved.bot_token:
            return self._bot_page(environ, start_response, session,
                                  note="Токен пуст — сначала сохраните.", is_error=True)
        try:
            me = TelegramAPI(saved.bot_token, timeout=15).get_me()
            note = f"Токен рабочий: @{(me.get('username') or '?')} (id {me.get('id')})"
            return self._bot_page(environ, start_response, session, note=note)
        except (OSError, ValueError) as exc:
            return self._bot_page(environ, start_response, session,
                                  note=f"Токен не работает: {exc}", is_error=True)

    # --- json api ---

    def _api(self, environ: dict, start_response: Callable, route: str,
             session: dict) -> Iterable[bytes]:
        method = environ.get("REQUEST_METHOD", "GET")
        if route == "nodes" and method == "GET":
            return self._json(start_response, 200,
                              {"ok": True, "result": {"nodes": [n.to_dict() for n in self.store.all()]}})
        payload = self._read_json(environ) if method == "POST" else {}
        try:
            if route == "nodes/add" and method == "POST":
                node, error = self._add_node(str(payload.get("id") or ""),
                                             str(payload.get("url") or ""),
                                             str(payload.get("path") or ""),
                                             str(payload.get("username") or "admin"),
                                             str(payload.get("password") or ""),
                                             str(payload.get("name") or ""))
                if node is None:
                    return self._json(start_response, 400, {"ok": False, "error": error})
                return self._json(start_response, 200, {"ok": True, "result": node.to_dict()})
            if route == "nodes/remove" and method == "POST":
                removed = self.store.remove(str(payload.get("id") or ""))
                return self._json(start_response, 200 if removed else 404,
                                  {"ok": removed,
                                   "error": None if removed else "Ноды нет в реестре"})
            if route == "status" and method == "POST":
                nodes, _ = self._pick(str(payload.get("node") or ""))
                results = fanout(nodes, lambda c: c.call("overview"), workers=self.workers,
                                 timeout=self.timeout, client_cls=self.client_cls)
                self.store.save()
                summary = summarize(results)
                return self._json(start_response, 200,
                                  {"ok": summary["fail"] == 0, "result": {"summary": summary,
                                                                          "nodes": results}})
            if route == "keys" and method == "POST":
                key = str(payload.get("password") or "")
                nodes, _ = self._pick(str(payload.get("node") or ""))
                results = fanout(nodes, lambda c: c.call("users"), workers=self.workers,
                                 timeout=self.timeout, client_cls=self.client_cls)
                self.store.save()
                found = {nid: (find_user(e.get("result") or {}, key) if e.get("ok") else None)
                         for nid, e in results.items()}
                return self._json(start_response, 200,
                                  {"ok": True, "result": {"key": key, "found": found,
                                                          "errors": summarize(results)["errors"]}})
            if route == "create" and method == "POST":
                if not str(payload.get("password") or ""):
                    return self._json(start_response, 400, {"ok": False, "error": "Нужен password"})
                nodes, _ = self._pick(str(payload.get("node") or ""))
                body = {"password": str(payload["password"])}
                for field in ("label", "vk_hash", "max_devices", "expires_at"):
                    if payload.get(field) not in (None, ""):
                        body[field] = payload[field]
                results = fanout_route(nodes, "users/create", body, workers=self.workers,
                                       timeout=self.timeout, client_cls=self.client_cls)
                self.store.save()
                summary = summarize(results)
                return self._json(start_response, 200,
                                  {"ok": summary["fail"] == 0,
                                   "result": {"summary": summary, "nodes": results}})
            if route == "delete" and method == "POST":
                key = str(payload.get("password") or "")
                node_arg = str(payload.get("node") or "")
                if not key:
                    return self._json(start_response, 400, {"ok": False, "error": "Нужен password"})
                nodes = self.store.all()
                if node_arg:
                    node = self.store.get(node_arg)
                    if node is None:
                        return self._json(start_response, 404, {"ok": False, "error": "Ноды нет в реестре"})
                    nodes = [node]
                elif len(nodes) > 1 and not payload.get("everywhere"):
                    return self._json(start_response, 400,
                                      {"ok": False,
                                       "error": "Больше одной ноды: укажите node или everywhere=true"})
                results = fanout(nodes, lambda c: c.call("users/delete", {"password": key}),
                                 workers=self.workers,
                                 timeout=self.timeout, client_cls=self.client_cls)
                self.store.save()
                summary = summarize(results)
                return self._json(start_response, 200,
                                  {"ok": summary["fail"] == 0,
                                   "result": {"summary": summary, "nodes": results}})
            if route == "bot" and method == "GET":
                return self._json(start_response, 200, {"ok": True, "result": self._bot_state()})
            if route == "bot/save" and method == "POST":
                _, saved = load_settings(self.config_path)
                if "bot_token" in payload:
                    saved.bot_token = str(payload["bot_token"] or "").strip()
                if "admin_ids" in payload:
                    raw = payload["admin_ids"]
                    saved.admin_ids = parse_admins(raw if isinstance(raw, str) else ",".join(map(str, raw or [])))
                if "poll_timeout" in payload:
                    try:
                        saved.poll_timeout = min(120, max(1, int(payload["poll_timeout"])))
                    except (TypeError, ValueError):
                        pass
                save_settings(saved, self.config_path)
                return self._json(start_response, 200, {"ok": True, "result": self._bot_state()})
            if route == "bot/test" and method == "POST":
                token = str(payload.get("bot_token") or "") or load_settings(self.config_path)[1].bot_token
                if not token:
                    return self._json(start_response, 400, {"ok": False, "error": "Токен пуст"})
                try:
                    me = TelegramAPI(token, timeout=15).get_me()
                    return self._json(start_response, 200, {"ok": True, "result": me})
                except (OSError, ValueError) as exc:
                    return self._json(start_response, 400, {"ok": False, "error": f"Токен не работает: {exc}"})
        except ValueError as exc:
            return self._json(start_response, 400, {"ok": False, "error": str(exc)})
        return self._json(start_response, 404, {"ok": False, "error": "API endpoint не найден"})


def serve(store: FleetStore, host: str, port: int, username: str,
          password_hash: str, secret: str, base: str = "/") -> None:
    """Блокирующий запуск веб-панели (wsgiref)."""
    app = FleetWeb(store, username, password_hash, secret, base=base)
    with make_server(host, port, app) as server:
        server.serve_forever()
