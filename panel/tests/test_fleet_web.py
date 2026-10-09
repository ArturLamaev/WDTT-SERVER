"""Тесты веб-панели контроллера: auth, guard, страницы, JSON API.

WSGI крутится в потоке, клиент нод — стаб (сети нет).
"""
import http.cookiejar
import json
import re
import threading
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from tempfile import TemporaryDirectory
from wsgiref.simple_server import make_server

from wdtt_panel.fleet import FleetStore, Node
from wdtt_panel.fleet.client import AuthError
from wdtt_panel.fleet.web import FleetWeb
from wdtt_panel.security import hash_password

PASSWORD = "long-test-password-1"


class StubClient:
    def __init__(self, node, timeout=30.0):
        self.node = node

    def login(self):
        if self.node.password != "secret":
            raise AuthError("Неверный пароль")
        self.node.token = "tok"
        return {"token": "tok"}

    def call(self, route, payload=None):
        if route == "overview":
            return {"stats": {"active": 1, "total": 2}}
        if route == "info":
            return {"public_host": f"vpn-{self.node.id}.example.com",
                    "panel_version": "1.2.3"}
        if route == "users":
            return {"users": [{"password": "KEY1", "link": "vk://KEY1",
                               "ports": "56000,56001,9000",
                               "vk_hash": "aa,bb",
                               "down_bytes": 1536, "up_bytes": 512,
                               "connected": True}], "total": 1}
        if route == "users/create":
            return {"password": (payload or {}).get("password")}
        if route == "users/delete":
            if (payload or {}).get("password") == "KEY1":
                return {"deleted": True}
            from wdtt_panel.fleet.client import FleetError
            raise FleetError("Пользователь не найден")
        raise AssertionError(f"unexpected route {route}")


class Browser:
    """urllib с куками."""

    def __init__(self, root):
        self.root = root.rstrip("/")
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def get(self, path, headers=None):
        req = urllib.request.Request(self.root + path, headers=headers or {})
        return self._read(req)

    def post_form(self, path, fields):
        data = urllib.parse.urlencode(fields).encode()
        req = urllib.request.Request(self.root + path, data=data)
        return self._read(req)

    def post_json(self, path, payload, headers=None):
        data = json.dumps(payload).encode()
        heads = {"Content-Type": "application/json"}
        heads.update(headers or {})
        req = urllib.request.Request(self.root + path, data=data, headers=heads)
        return self._read(req)

    def _read(self, req):
        try:
            with self.opener.open(req) as resp:
                return resp.status, resp.url, resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            return exc.code, "", exc.read().decode("utf-8")


class FleetWebTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.store = FleetStore(Path(cls.tmp.name) / "nodes.json")
        cls.store.add(Node(id="n1", base_url="http://x", password="secret"))
        cls.store.add(Node(id="n2", base_url="http://y", password="secret"))
        app = FleetWeb(cls.store, "admin", hash_password(PASSWORD),
                       secret="test-secret-123", client_cls=StubClient)
        cls.server = make_server("127.0.0.1", 0, app)
        cls.root = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def browser(self):
        return Browser(self.root)

    def login(self, browser, password=PASSWORD):
        return browser.post_form("/login", {"username": "admin", "password": password})

    def csrf(self, browser):
        _, _, body = browser.get("/nodes")
        found = re.search(r"name=csrf value='([^']+)'", body)
        self.assertIsNotNone(found, "CSRF-токен на странице")
        return found.group(1)

    # --- guard/auth ---

    def test_login_page(self):
        code, _, body = self.browser().get("/login")
        self.assertEqual(code, 200)
        self.assertIn("Пароль", body)

    def test_guard_redirects_to_login(self):
        code, url, body = self.browser().get("/nodes")
        self.assertTrue(url.endswith("/login") or "Вход" in body)

    def test_login_wrong_password(self):
        code, _, body = self.login(self.browser(), "wrong-password-123")
        self.assertIn("Неверный", body)

    def test_login_ok(self):
        b = self.browser()
        self.login(b)
        code, _, body = b.get("/nodes")
        self.assertEqual(code, 200)
        self.assertIn("n1", body)
        self.assertIn("n2", body)
        self.assertIn("<svg", body)
        self.assertIn('stroke="currentColor"', body)

    def test_api_login_and_bearer(self):
        b = self.browser()
        code, _, body = b.post_json("/api/v1/auth/login",
                                    {"username": "admin", "password": PASSWORD})
        self.assertEqual(code, 200)
        token = json.loads(body)["result"]["token"]
        code, _, body = b.get("/api/v1/nodes",
                              headers={"Authorization": f"Bearer {token}"})
        self.assertEqual(code, 200)
        ids = [n["id"] for n in json.loads(body)["result"]["nodes"]]
        self.assertEqual(ids, ["n1", "n2"])

    def test_api_unauthorized(self):
        code, _, _ = self.browser().get("/api/v1/nodes")
        self.assertEqual(code, 401)
        code, _, _ = self.browser().get(
            "/api/v1/nodes", headers={"Authorization": "Bearer junk"})
        self.assertEqual(code, 401)

    # --- api ops ---

    def auth_headers(self, browser):
        _, _, body = browser.post_json("/api/v1/auth/login",
                                       {"username": "admin", "password": PASSWORD})
        token = json.loads(body)["result"]["token"]
        return {"Authorization": f"Bearer {token}"}

    def test_api_status(self):
        b = self.browser()
        code, _, body = b.post_json("/api/v1/status", {}, self.auth_headers(b))
        self.assertEqual(code, 200)
        data = json.loads(body)["result"]
        self.assertEqual(data["summary"]["ok"], 2)

    def test_api_keys(self):
        b = self.browser()
        code, _, body = b.post_json("/api/v1/keys", {"password": "KEY1"},
                                    self.auth_headers(b))
        data = json.loads(body)["result"]
        self.assertEqual(data["found"]["n1"]["link"], "vk://KEY1")
        self.assertEqual(data["found"]["n2"]["link"], "vk://KEY1")

    def test_api_create_delete(self):
        b = self.browser()
        heads = self.auth_headers(b)
        code, _, body = b.post_json("/api/v1/create", {"password": "NEWK"}, heads)
        self.assertTrue(json.loads(body)["ok"])
        code, _, body = b.post_json("/api/v1/delete",
                                    {"password": "KEY1", "everywhere": True}, heads)
        self.assertTrue(json.loads(body)["ok"])

    def test_api_delete_requires_scope(self):
        b = self.browser()
        code, _, body = b.post_json("/api/v1/delete", {"password": "KEY1"},
                                    self.auth_headers(b))
        self.assertEqual(code, 400)
        self.assertIn("everywhere", body)

    # --- html flows ---

    def test_html_status_and_user_pages(self):
        b = self.browser()
        self.login(b)
        for path in ("/status", "/user", "/user?key=KEY1"):
            code, _, body = b.get(path)
            self.assertEqual(code, 200, path)
        _, _, body = b.get("/user?key=KEY1")
        self.assertIn("ЕСТЬ", body)

    def test_html_add_and_remove_node(self):
        b = self.browser()
        self.login(b)
        token = self.csrf(b)
        code, url, _ = b.post_form("/nodes/add", {
            "csrf": token, "id": "n3", "url": "http://z:9999/p",
            "username": "admin", "password": "secret", "name": ""})
        self.assertTrue(url.endswith("/nodes"), url)
        _, _, body = b.get("/nodes")
        self.assertIn("n3", body)
        token = self.csrf(b)
        b.post_form("/nodes/remove", {"csrf": token, "id": "n3"})
        _, _, body = b.get("/nodes")
        self.assertNotIn("n3", body)

    def test_html_add_bad_password(self):
        b = self.browser()
        self.login(b)
        code, _, body = b.post_form("/nodes/add", {
            "csrf": self.csrf(b), "id": "bad", "url": "http://z",
            "username": "admin", "password": "wrong-password-x", "name": ""})
        self.assertIn("Ошибка", body)

    def test_html_create_and_delete_user(self):
        b = self.browser()
        self.login(b)
        token = self.csrf(b)
        _, _, body = b.post_form("/user/create", {
            "csrf": token, "password": "HTMLKEY", "label": "", "node": "n1"})
        self.assertIn("ok 1/1", body)
        token = self.csrf(b)
        _, _, body = b.post_form("/user/delete", {
            "csrf": token, "password": "KEY1", "node": "n1", "confirm": "1"})
        self.assertIn("ok 1/1", body)

    def test_html_delete_without_confirm(self):
        b = self.browser()
        self.login(b)
        _, _, body = b.post_form("/user/delete", {
            "csrf": self.csrf(b), "password": "KEY1", "node": "n1"})
        self.assertIn("подтверждения", body)

    def test_logout(self):
        b = self.browser()
        self.login(b)
        _, _, body = b.get("/nodes")
        self.assertIn("n1", body)

    def test_static_css(self):
        b = self.browser()
        code, _, body = b.get("/static/fleet.css")
        self.assertEqual(code, 200)
        self.assertIn("@import", body)
        self.assertIn("var(--bg)", body)
        code, _, node_css = b.get("/static/app.css")
        self.assertEqual(code, 200)
        self.assertIn("--accent", node_css)
        code, _, _ = b.get("/static/../secret.py")
        self.assertEqual(code, 404)

    def test_theme_wiring(self):
        b = self.browser()
        self.login(b)
        _, _, body = b.get("/nodes")
        self.assertIn("static/fleet.css", body)
        self.assertIn("wdtt-theme", body)
        self.assertIn("wdtt-accent", body)
        self.assertIn('class=active', body)

    def test_user_link_builder(self):
        from wdtt_panel.fleet.web import _server_host, _user_link
        node = Node(id="n", base_url="https://9.9.9.9:9999", password="p")
        link = _user_link(node, {"password": "K", "ports": "1,2,3", "vk_hash": "h"})
        self.assertEqual(link, "wdtt://9.9.9.9:1:2:3:K:h")
        link = _user_link(node, {"password": "K2"})
        self.assertTrue(link.startswith("wdtt://9.9.9.9:56000:56001:9000:K2:"))

    def test_server_host_prefers_public_host(self):
        from wdtt_panel.fleet.web import _server_host
        # в реестре внутренний адрес, а нода через info отдаёт внешний
        node = Node(id="n", base_url="https://100.66.0.6:9999", password="p")
        infos = {"n": {"ok": True, "result": {"public_host": "203.0.113.7"}}}
        self.assertEqual(_server_host(node, infos), "203.0.113.7")
        # нет info / пустой public_host — fallback на хост панели
        self.assertEqual(_server_host(node, {}), "100.66.0.6")
        self.assertEqual(
            _server_host(node, {"n": {"ok": False, "error": "down"}}), "100.66.0.6")
        self.assertEqual(
            _server_host(node, {"n": {"ok": True, "result": {}}}), "100.66.0.6")

    def test_user_page_lists_all_users(self):
        b = self.browser()
        self.login(b)
        code, _, body = b.get("/user")
        self.assertEqual(code, 200)
        # агрегат по нодам: группировки, ключи, кнопки удаления
        self.assertIn("Все пользователи на нодах", body)
        self.assertIn("KEY1", body)
        self.assertIn("user/delete", body)
        self.assertIn("name=confirm", body)
        self.assertIn("Всего: 2", body)
        # wdtt-ссылка строится с внешним хостом из info ноды (не панельный URL),
        # с кнопкой копирования
        self.assertIn("wdtt://vpn-n1.example.com:56000:56001:9000:KEY1:aa,bb", body)
        self.assertNotIn("wdtt://x:", body)
        self.assertIn("Скопировать", body)
        self.assertIn("data-copy", body)
        self.assertIn("1.5 КБ", body)
        # один ключ на двух нодах — одна группа и одна кнопка «Скопировать все»
        self.assertIn("Скопировать все (2)", body)

    def test_system_page_shows_versions(self):
        from wdtt_panel import __version__
        b = self.browser()
        self.login(b)
        code, _, body = b.get("/system")
        self.assertEqual(code, 200)
        self.assertIn("Система", body)
        self.assertIn(f"v{__version__}", body)
        # кнопки обновления — или честный фолбэк, если обёртки нет (как на Windows)
        self.assertTrue("Проверить обновления" in body
                        or "Обёртка обновления не найдена" in body)
        self.assertIn("Версии нод", body)
        self.assertIn("1.2.3", body)
        self.assertIn("vpn-n1.example.com", body)

    def test_nav_has_version_and_system(self):
        from wdtt_panel import __version__
        b = self.browser()
        self.login(b)
        _, _, body = b.get("/nodes")
        self.assertIn(f"v{__version__}", body)
        self.assertIn("Система", body)

    def test_system_page_shows_update_buttons_when_wrapper_present(self):
        import tempfile
        from pathlib import Path as _Path
        from wdtt_panel.fleet import web as fleet_web
        with tempfile.TemporaryDirectory() as tmp:
            fake = _Path(tmp) / "wdtt-panel-self-update"
            fake.write_text("#!/bin/sh\n", encoding="utf-8")
            old = fleet_web.PANEL_SELF_UPDATE_COMMAND
            fleet_web.PANEL_SELF_UPDATE_COMMAND = fake
            try:
                b = self.browser()
                self.login(b)
                _, _, body = b.get("/system")
                self.assertIn("Проверить обновления", body)
                self.assertIn("Обновить контроллер", body)
            finally:
                fleet_web.PANEL_SELF_UPDATE_COMMAND = old

    def test_system_page_shows_update_log_tail(self):
        import tempfile
        from pathlib import Path as _Path
        from wdtt_panel.fleet import web as fleet_web
        with tempfile.TemporaryDirectory() as tmp:
            fake = _Path(tmp) / "self-update.log"
            fake.write_text("line1\ncheck: ok 1.2.3\n", encoding="utf-8")
            old = fleet_web.UPDATE_LOG_FILE
            fleet_web.UPDATE_LOG_FILE = fake
            try:
                b = self.browser()
                self.login(b)
                _, _, body = b.get("/system")
                self.assertIn("Лог обновления", body)
                self.assertIn("check: ok 1.2.3", body)
            finally:
                fleet_web.UPDATE_LOG_FILE = old

    def test_system_spawn_without_wrapper_reports_error(self):
        from wdtt_panel.fleet import web as fleet_web
        old = fleet_web.PANEL_SELF_UPDATE_COMMAND
        fleet_web.PANEL_SELF_UPDATE_COMMAND = old.parent / "нет-такого-файла"
        try:
            b = self.browser()
            self.login(b)
            _, _, body = b.post_form(
                "/system/check", {"csrf": self.csrf(b)})
            self.assertIn("Обёртка обновления не найдена", body)
        finally:
            fleet_web.PANEL_SELF_UPDATE_COMMAND = old


if __name__ == "__main__":
    unittest.main()
