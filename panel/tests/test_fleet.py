"""Тесты fleet-контроллера: модель, стор, клиент, fan-out, CLI.

Фейковая нода эмулирует настоящий протокол панели (app.py):
POST api/v1/auth/login → Bearer, GET info/overview/users,
POST users/create|delete, конверты {"ok","result"/"error"}.
"""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

from wdtt_panel.fleet import (
    FleetStore,
    Node,
    fanout,
    normalize_base_path,
    normalize_base_url,
    split_node_url,
    summarize,
)
from wdtt_panel.fleet.client import AuthError, FleetError, NodeClient, find_user
from wdtt_panel.fleet.cli import main as fleet_main

BASE_PATH = "/s3cr3t/"
FAKE_PASSWORD = "node-secret"
FAKE_TOKEN = "tok-abc-123"


class FakeNodeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # тишина в тестах
        pass

    def _send(self, code, payload):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            data = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _route(self):
        prefix = BASE_PATH + "api/v1/"
        path = self.path.split("?", 1)[0]
        if not path.startswith(BASE_PATH):
            return None
        return path[len(prefix):]

    def _authorized(self):
        return self.headers.get("Authorization") == f"Bearer {FAKE_TOKEN}"

    def do_GET(self):
        route = self._route()
        if route == "info":
            return self._send(200, {"ok": True, "result": {
                "name": "WDTT Control Panel", "api_version": 1, "panel_version": "1.12.5"}})
        if route in ("overview", "users"):
            if not self._authorized():
                return self._send(401, {"ok": False, "error": "Требуется bearer-токен"})
            if route == "overview":
                return self._send(200, {"ok": True, "result": {
                    "stats": {"active": 3, "total": 7}, "users": 7}})
            return self._send(200, {"ok": True, "result": {
                "users": [{"password": "KEY1", "label": "test", "link": "vk://KEY1"}],
                "total": 1}})
        return self._send(404, {"error": "API endpoint не найден"})

    def do_POST(self):
        route = self._route()
        payload = self._read_json()
        if route == "auth/login":
            if payload.get("password") == FAKE_PASSWORD:
                return self._send(200, {"ok": True, "result": {"token": FAKE_TOKEN}})
            return self._send(401, {"ok": False, "error": "Неверный пароль"})
        if not self._authorized():
            return self._send(401, {"ok": False, "error": "Требуется bearer-токен"})
        if route == "users/create":
            return self._send(200, {"ok": True, "result": {"password": payload.get("password")}})
        if route == "users/delete":
            if payload.get("password") == "KEY1":
                return self._send(200, {"ok": True, "result": {"deleted": True}})
            return self._send(400, {"ok": False, "error": "Пользователь не найден"})
        return self._send(404, {"error": "API endpoint не найден"})


class FleetLiveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), FakeNodeHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def make_node(self, node_id="n1", **kw):
        params = {"id": node_id,
                  "base_url": f"http://127.0.0.1:{self.port}",
                  "base_path": BASE_PATH,
                  "username": "admin",
                  "password": FAKE_PASSWORD}
        params.update(kw)
        return Node(**params)

    # --- модель ---

    def test_split_node_url(self):
        base, path = split_node_url("https://1.2.3.4:9999/s3cr3t")
        self.assertEqual(base, "https://1.2.3.4:9999")
        self.assertEqual(path, "/s3cr3t/")
        base, path = split_node_url("1.2.3.4:9999")
        self.assertEqual(base, "https://1.2.3.4:9999")
        self.assertEqual(path, "/")

    def test_normalize_helpers(self):
        self.assertEqual(normalize_base_url("  EXAMPLE.com:9999/ignored  "), "https://example.com:9999")
        self.assertEqual(normalize_base_path("abc"), "/abc/")
        self.assertEqual(normalize_base_path("/abc/"), "/abc/")
        with self.assertRaises(ValueError):
            normalize_base_url("ftp://x")
        with self.assertRaises(ValueError):
            normalize_base_url("")

    def test_node_validation(self):
        with self.assertRaises(ValueError):
            Node(id="bad id!", base_url="http://x")
        with self.assertRaises(ValueError):
            Node(id="ok", base_url="")
        node = self.make_node()
        self.assertTrue(node.api_url("users").endswith("/s3cr3t/api/v1/users"))

    def test_node_roundtrip(self):
        node = self.make_node()
        node.token = "t"
        clone = Node.from_dict(node.to_dict())
        self.assertEqual(clone.to_dict(), node.to_dict())

    # --- стор ---

    def test_store_roundtrip(self):
        with TemporaryDirectory() as tmp:
            store = FleetStore(Path(tmp) / "nodes.json")
            self.assertEqual(store.all(), [])
            store.add(self.make_node("a"))
            store.add(self.make_node("b"))
            with self.assertRaises(ValueError):
                store.add(self.make_node("a"))
            self.assertEqual([n.id for n in store.all()], ["a", "b"])
            self.assertTrue(store.remove("a"))
            self.assertFalse(store.remove("a"))
            reloaded = FleetStore(Path(tmp) / "nodes.json")
            self.assertEqual([n.id for n in reloaded.all()], ["b"])
            self.assertEqual(reloaded.get("b").password, FAKE_PASSWORD)

    # --- клиент ---

    def test_probe_login_and_get_calls(self):
        client = NodeClient(self.make_node())
        info = client.probe()
        self.assertEqual(info["panel_version"], "1.12.5")
        client.login()
        self.assertEqual(client.node.token, FAKE_TOKEN)
        users = client.users_list()
        self.assertEqual(users["total"], 1)
        overview = client.overview()
        self.assertEqual(overview["stats"]["active"], 3)

    def test_post_calls(self):
        client = NodeClient(self.make_node())
        created = client.user_create({"password": "NEW"})
        self.assertEqual(created["password"], "NEW")
        deleted = client.user_delete("KEY1")
        self.assertTrue(deleted["deleted"])
        with self.assertRaises(FleetError):
            client.user_delete("NOPE")

    def test_auto_relogin_on_401(self):
        node = self.make_node()
        node.token = "stale-token"
        client = NodeClient(node)
        users = client.users_list()  # 401 → login → повтор
        self.assertEqual(users["total"], 1)
        self.assertEqual(node.token, FAKE_TOKEN)

    def test_wrong_password(self):
        client = NodeClient(self.make_node(password="wrong"))
        with self.assertRaises(AuthError):
            client.login()

    def test_unreachable_node(self):
        client = NodeClient(self.make_node(base_url="http://127.0.0.1:1", password="x"))
        with self.assertRaises(FleetError):
            client.probe()

    def test_find_user(self):
        users = {"users": [{"password": "KEY1", "link": "vk://KEY1"}]}
        self.assertEqual(find_user(users, "KEY1")["link"], "vk://KEY1")
        self.assertIsNone(find_user(users, "MISSING"))
        self.assertIsNone(find_user({}, "KEY1"))

    # --- fan-out ---

    def test_fanout_partial_success(self):
        nodes = [self.make_node("good1"), self.make_node("good2"),
                 self.make_node("dead", base_url="http://127.0.0.1:1", password="x")]
        results = fanout(nodes, lambda c: c.probe(), workers=3, timeout=5)
        summary = summarize(results)
        self.assertEqual(summary, {"total": 3, "ok": 2, "fail": 1,
                                   "errors": {"dead": results["dead"]["error"]}})
        self.assertTrue(results["good1"]["ok"])
        self.assertFalse(results["dead"]["ok"])

    # --- CLI ---

    def run_cli(self, store_path, *argv):
        return fleet_main(["--store", str(store_path), *argv])

    def test_cli_add_list_keys_delete_remove(self):
        with TemporaryDirectory() as tmp:
            store_path = str(Path(tmp) / "nodes.json")
            url = f"http://127.0.0.1:{self.port}{BASE_PATH}"
            rc = self.run_cli(store_path, "add", "--id", "n1", "--url", url,
                              "--password", FAKE_PASSWORD, "--json")
            self.assertEqual(rc, 0)
            store = FleetStore(Path(store_path))
            self.assertEqual(store.get("n1").token, FAKE_TOKEN)
            self.assertEqual(self.run_cli(store_path, "list"), 0)
            self.assertEqual(self.run_cli(store_path, "status", "--node", "n1"), 0)
            self.assertEqual(self.run_cli(store_path, "keys", "--password", "KEY1"), 0)
            self.assertEqual(self.run_cli(store_path, "keys", "--password", "MISSING"), 0)
            self.assertEqual(self.run_cli(store_path, "create", "--password", "NEW2",
                                           "--node", "n1"), 0)
            self.assertEqual(self.run_cli(store_path, "delete", "--password", "KEY1",
                                           "--node", "n1"), 0)
            self.assertEqual(self.run_cli(store_path, "remove", "--id", "n1"), 0)
            self.assertEqual(self.run_cli(store_path, "remove", "--id", "n1"), 1)

    def test_cli_add_wrong_password(self):
        with TemporaryDirectory() as tmp:
            url = f"http://127.0.0.1:{self.port}{BASE_PATH}"
            rc = self.run_cli(str(Path(tmp) / "n.json"), "add", "--id", "bad",
                              "--url", url, "--password", "wrong")
            self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
