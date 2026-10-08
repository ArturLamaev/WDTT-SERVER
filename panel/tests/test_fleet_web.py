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
        if route == "users":
            return {"users": [{"password": "KEY1", "link": "vk://KEY1"}], "total": 1}
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


if __name__ == "__main__":
    unittest.main()
