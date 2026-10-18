import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from wsgiref.util import setup_testing_defaults

from wdtt_panel import app
from wdtt_panel.security import csrf_token, hash_password, read_session


class AppSmokeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.config = root / "config.json"
        self.state = root / "panel.db"
        self.config.write_text(
            json.dumps(
                {
                    "username": "admin",
                    "password_hash": hash_password("Panel-password-12345"),
                    "session_secret": "test-session-secret",
                    "base_path": "/private-panel-path/",
                    "public_host": "panel.example.com",
                    "https_port": 9999,
                    "listen_host": "127.0.0.1",
                    "listen_port": 8787,
                    "certificate_path": "",
                }
            ),
            encoding="utf-8",
        )
        fake_admin = Path(__file__).with_name("fake_admin.py")
        self.patchers = [
            mock.patch.object(app, "CONFIG_FILE", self.config),
            mock.patch.object(app, "STATE_DB", self.state),
            mock.patch.object(app, "ADMIN_COMMAND", [sys.executable, str(fake_admin)]),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.panel = app.Panel()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def request(self, path, method="GET", body=b"", cookie="", csrf="", auth=""):
        environ = {}
        setup_testing_defaults(environ)
        environ.update(
            {
                "PATH_INFO": path,
                "REQUEST_METHOD": method,
                "CONTENT_LENGTH": str(len(body)),
                "CONTENT_TYPE": "application/x-www-form-urlencoded",
                "wsgi.input": io.BytesIO(body),
                "REMOTE_ADDR": "127.0.0.1",
                "HTTP_COOKIE": cookie,
                "HTTP_X_CSRF_TOKEN": csrf,
                "HTTP_AUTHORIZATION": auth,
            }
        )
        captured = {}

        def start_response(status, headers):
            captured["status"] = status
            captured["headers"] = dict(headers)

        response = b"".join(self.panel(environ, start_response))
        return captured, response

    def test_api_v1_password_login_and_bearer_requests(self):
        headers, body = self.request("/private-panel-path/api/v1/info")
        self.assertTrue(headers["status"].startswith("200"))
        parsed = json.loads(body)
        self.assertEqual(parsed["result"]["api_version"], 1)
        self.assertEqual(parsed["result"]["auth"]["type"], "password")

        headers, body = self.request("/private-panel-path/api/v1/overview")
        self.assertTrue(headers["status"].startswith("401"))
        self.assertFalse(json.loads(body)["ok"])

        payload = json.dumps({"password": "Panel-password-12345"}).encode()
        headers, body = self.request("/private-panel-path/api/v1/auth/login", "POST", payload)
        self.assertTrue(headers["status"].startswith("200"))
        token = json.loads(body)["result"]["token"]

        headers, body = self.request("/private-panel-path/api/v1/overview", auth=f"Bearer {token}")
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["ok"])

        headers, body = self.request("/private-panel-path/api/v1/qwdtt/subscription", auth=f"Bearer {token}")
        self.assertTrue(headers["status"].startswith("200"))
        subscription = json.loads(body)["result"]
        self.assertEqual(subscription["subscriptionName"], "WDTT panel.example.com")
        self.assertEqual(subscription["profiles"][0]["peer"], "panel.example.com:56000")
        self.assertEqual(subscription["profiles"][0]["workers"], 16)

        payload = json.dumps({"label": "Mobile client"}).encode()
        headers, body = self.request(
            "/private-panel-path/api/v1/users/create",
            "POST",
            payload,
            auth=f"Bearer {token}",
        )
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["label"], "Mobile client")

        payload = json.dumps(
            {
                "password": "DemoUserA123",
                "operation_id": "app-adjust-test-0001",
                "expiration_mode": "adjust_days",
                "days_delta": 7,
                "traffic_mode": "subtract",
                "traffic_gib": 5,
            }
        ).encode()
        headers, body = self.request(
            "/private-panel-path/api/v1/users/adjust-plan",
            "POST",
            payload,
            auth=f"Bearer {token}",
        )
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["ok"])

        payload = json.dumps({"keep_days": 14, "targets": ["service_logs", "package_cache"]}).encode()
        headers, body = self.request(
            "/private-panel-path/api/v1/cleanup/preview",
            "POST",
            payload,
            auth=f"Bearer {token}",
        )
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["estimated_freed_bytes"], 1024)

    def test_autoclean_routes_report_and_save_settings(self):
        payload = json.dumps({"password": "Panel-password-12345"}).encode()
        _, body = self.request("/private-panel-path/api/v1/auth/login", "POST", payload)
        token = json.loads(body)["result"]["token"]
        auth = f"Bearer {token}"

        headers, body = self.request("/private-panel-path/api/v1/autoclean", auth=auth)
        self.assertTrue(headers["status"].startswith("200"))
        status = json.loads(body)["result"]
        self.assertEqual(status["settings"]["keep_panel"], 20)
        self.assertEqual(status["disk_percent"], 25.0)

        payload = json.dumps({"enabled": True, "disk_percent": 85, "keep_panel": 10, "keep_users": 12, "keep_days": 7}).encode()
        headers, body = self.request("/private-panel-path/api/v1/autoclean/settings", "POST", payload, auth=auth)
        self.assertTrue(headers["status"].startswith("200"))
        saved = json.loads(body)["result"]["settings"]
        self.assertEqual(saved["disk_percent"], 85)
        self.assertEqual(saved["keep_users"], 12)

        headers, body = self.request("/private-panel-path/api/v1/autoclean/run", "POST", b"{}", auth=auth)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["reason"], "below_threshold")

    def test_personal_page_and_subscription_do_not_expose_admin_path(self):
        token = self.panel.personal_token("DemoUserA123")
        headers, body = self.request(f"/client/{token}/")
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(headers["headers"]["X-Robots-Tag"], "noindex, nofollow, noarchive")
        self.assertIn("Действует до".encode(), body)
        self.assertNotIn(b"private-panel-path", body)

        headers, body = self.request(f"/client/{token}/subscription.json")
        self.assertTrue(headers["status"].startswith("200"))
        subscription = json.loads(body)
        self.assertEqual(subscription["profiles"][0]["password"], "DemoUserA123")
        self.assertGreater(subscription["trafficLimitMb"], subscription["trafficUsedMb"])

        headers, _ = self.request("/client/" + "0" * 64 + "/")
        self.assertTrue(headers["status"].startswith("404"))

    def test_user_statistics_accumulate_daily_and_total_traffic(self):
        first = {
            "users": [{"password": "TrafficUser123", "down_bytes": 100, "up_bytes": 50, "connected": False}],
            "admins": [],
        }
        with mock.patch.object(app.time, "time", return_value=1_800_000_000):
            enriched = self.panel.enrich_user_statistics(first)
        self.assertEqual(enriched["users"][0]["traffic_total_bytes"], 150)
        self.assertEqual(enriched["users"][0]["traffic_today_bytes"], 0)
        self.assertEqual(enriched["users"][0]["connection_state"], "offline")

        second = {
            "users": [
                {
                    "password": "TrafficUser123",
                    "down_bytes": 140,
                    "up_bytes": 70,
                    "connected": False,
                    "last_upload_at": 1_800_000_003,
                }
            ],
            "admins": [],
        }
        with mock.patch.object(app.time, "time", return_value=1_800_000_010):
            enriched = self.panel.enrich_user_statistics(second)
        self.assertEqual(enriched["users"][0]["traffic_today_bytes"], 60)
        self.assertEqual(enriched["users"][0]["traffic_total_bytes"], 210)
        self.assertTrue(enriched["users"][0]["recently_active"])
        self.assertEqual(enriched["users"][0]["connection_state"], "active")

        reset_counter = {
            "users": [{"password": "TrafficUser123", "down_bytes": 5, "up_bytes": 1, "connected": False}],
            "admins": [],
        }
        with mock.patch.object(app.time, "time", return_value=1_800_000_020):
            enriched = self.panel.enrich_user_statistics(reset_counter)
        self.assertEqual(enriched["users"][0]["traffic_today_bytes"], 66)
        self.assertEqual(enriched["users"][0]["traffic_total_bytes"], 216)

    def test_login_page_and_authenticated_overview(self):
        headers, body = self.request("/private-panel-path/")
        self.assertTrue(headers["status"].startswith("200"))
        self.assertIn(b"WDTT Control", body)

        form = b"username=admin&password=Panel-password-12345"
        headers, _ = self.request("/private-panel-path/login", "POST", form)
        self.assertTrue(headers["status"].startswith("303"))
        cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]

        headers, body = self.request("/private-panel-path/api/overview", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        parsed = json.loads(body)
        self.assertTrue(parsed["ok"])

        headers, body = self.request("/private-panel-path/api/logs", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["source"], "wdtt")
        self.assertEqual(parsed["result"]["stats"]["active"], 2)

    def test_wdtt_settings_and_domain_routes_call_root_helper(self):
        form = b"username=admin&password=Panel-password-12345"
        headers, _ = self.request("/private-panel-path/login", "POST", form)
        cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]
        token = cookie.split("=", 1)[1]
        session = read_session(token, "test-session-secret")
        csrf = csrf_token(session["n"], "test-session-secret")

        headers, body = self.request("/private-panel-path/api/wdtt", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["wg_mtu"], 1280)

        payload = json.dumps({"wg_mtu": 1300}).encode()
        headers, body = self.request("/private-panel-path/api/wdtt/save", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["scheduled"])

        payload = json.dumps({"host": "panel.example.com"}).encode()
        headers, body = self.request(
            "/private-panel-path/api/certificate/change-domain", "POST", payload, cookie, csrf
        )
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["scheduled"])

    def test_proxy_routes_call_root_helper(self):
        form = b"username=admin&password=Panel-password-12345"
        headers, _ = self.request("/private-panel-path/login", "POST", form)
        cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]
        token = cookie.split("=", 1)[1]
        session = read_session(token, "test-session-secret")
        csrf = csrf_token(session["n"], "test-session-secret")

        headers, body = self.request("/private-panel-path/api/proxy", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["installed"])

        payload = json.dumps({"action": "start"}).encode()
        headers, body = self.request("/private-panel-path/api/proxy/action", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["action"], "start")

        payload = json.dumps({}).encode()
        headers, body = self.request("/private-panel-path/api/proxy/install", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["installed"])

        payload = json.dumps({"PROXY_HOST": "100.66.0.5", "PROXY_PORT": "26666"}).encode()
        headers, body = self.request("/private-panel-path/api/proxy/config", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["saved"])

        # POST на GET-only маршрут статуса должен отклоняться
        payload = json.dumps({}).encode()
        headers, _ = self.request("/private-panel-path/api/proxy", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("405"))

    def test_tailscale_routes_call_root_helper(self):
        form = b"username=admin&password=Panel-password-12345"
        headers, _ = self.request("/private-panel-path/login", "POST", form)
        cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]
        token = cookie.split("=", 1)[1]
        session = read_session(token, "test-session-secret")
        csrf = csrf_token(session["n"], "test-session-secret")

        headers, body = self.request("/private-panel-path/api/tailscale", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["online"])

        payload = json.dumps({"hostname": "wdtt-server", "authkey": "tskey-auth-test"}).encode()
        headers, body = self.request("/private-panel-path/api/tailscale/connect", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["connected"])

        payload = json.dumps({}).encode()
        headers, body = self.request("/private-panel-path/api/tailscale/install", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["scheduled"])

        headers, body = self.request("/private-panel-path/api/tailscale/check", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["ok"])

    def test_vk_hash_library_is_managed_without_calling_root_helper(self):
        form = b"username=admin&password=Panel-password-12345"
        headers, _ = self.request("/private-panel-path/login", "POST", form)
        cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]
        token = cookie.split("=", 1)[1]
        session = read_session(token, "test-session-secret")
        csrf = csrf_token(session["n"], "test-session-secret")

        payload = json.dumps({"hashes": "https://vk.com/call/join/hash_one, hash_two"}).encode()
        headers, body = self.request("/private-panel-path/api/vk-hashes", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["hashes"], ["hash_one", "hash_two"])

        headers, body = self.request("/private-panel-path/api/vk-hashes", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["hashes"], ["hash_one", "hash_two"])

        payload = json.dumps({"hash": "hash_one"}).encode()
        headers, body = self.request("/private-panel-path/api/vk-hashes/delete", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["hashes"], ["hash_two"])

        headers, body = self.request("/private-panel-path/api/vk-hashes/export", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        exported = json.loads(body)["result"]
        self.assertEqual(exported["count"], 1)
        self.assertIn("wdtt-panel-vk-hash-library-v1", exported["content"])

        payload = json.dumps({"content": '{"format":"wdtt-panel-vk-hash-library-v1","hashes":["hash_two","hash_three"]}'}).encode()
        headers, body = self.request("/private-panel-path/api/vk-hashes/import", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        imported = json.loads(body)["result"]
        self.assertEqual(imported["imported"], 1)
        self.assertEqual(imported["hashes"], ["hash_two", "hash_three"])

        payload = json.dumps(
            {"count": 2, "vk_hash": "manual_one, manual_two", "ports": "56000,56001,9000"}
        ).encode()
        headers, _ = self.request("/private-panel-path/api/users/create-bulk", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        headers, body = self.request("/private-panel-path/api/vk-hashes", cookie=cookie)
        self.assertEqual(json.loads(body)["result"]["hashes"], ["hash_two", "hash_three", "manual_one", "manual_two"])

        payload = json.dumps({"label": "Авто клиент"}).encode()
        with mock.patch.object(app.secrets, "choice", return_value="manual_two"):
            headers, body = self.request("/private-panel-path/api/users/create-auto", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        created = json.loads(body)["result"]
        self.assertEqual(created["label"], "Авто клиент")
        self.assertEqual(created["vk_hash"], "manual_two")
        self.assertEqual(created["password"], "AutoDemoUser123")

    def test_vk_hash_library_is_seeded_from_userdata_file(self):
        seed = Path(self.temp.name) / "vk-hash.txt"
        seed.write_text(
            "seed_one\nhttps://vk.com/call/join/seed_two\nseed_one\n# comment\nbad!hash\n",
            encoding="utf-8",
        )
        with mock.patch.object(app, "SEED_HASHES_FILE", seed):
            app.Panel.seed_vk_hashes()
        self.assertEqual(app.Panel.list_vk_hashes()["hashes"], ["seed_one", "seed_two"])

        app.Panel.delete_vk_hash({"hash": "seed_one"})
        with mock.patch.object(app, "SEED_HASHES_FILE", seed):
            app.Panel.seed_vk_hashes()
        self.assertEqual(app.Panel.list_vk_hashes()["hashes"], ["seed_two"])

    def test_telegram_settings_routes_call_root_helper(self):
        form = b"username=admin&password=Panel-password-12345"
        headers, _ = self.request("/private-panel-path/login", "POST", form)
        cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]
        token = cookie.split("=", 1)[1]
        session = read_session(token, "test-session-secret")
        csrf = csrf_token(session["n"], "test-session-secret")

        headers, body = self.request("/private-panel-path/api/telegram", cookie=cookie)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["enabled"])

        payload = json.dumps({"enabled": True, "admin_id": "123456789", "bot_token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_test"}).encode()
        headers, body = self.request("/private-panel-path/api/telegram/save", "POST", payload, cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertEqual(json.loads(body)["result"]["admin_id"], "123456789")

        headers, body = self.request("/private-panel-path/api/telegram/test", "POST", b"{}", cookie, csrf)
        self.assertTrue(headers["status"].startswith("200"))
        self.assertTrue(json.loads(body)["result"]["sent"])


class RouteOperationParityTests(unittest.TestCase):
    """Панель и root-хелпер — два процесса; их связка только по именам операций.

    Расхождение имени (например панель шлёт `proxy.status`, а хелпер знает
    `proxy`) видно пользователю как «Неизвестная административная операция»,
    поэтому контракт проверяется автоматически.
    """

    def test_panel_routes_map_to_registered_operations(self):
        from wdtt_panel import admin

        # Не меньше 45 маршрутов: пустой/переименованный словарь сделал бы проверку ниже пустой.
        self.assertGreaterEqual(len(app.API_ROUTES), 45)
        missing = sorted({op for op in app.API_ROUTES.values() if op not in admin.OPERATIONS})
        self.assertEqual(missing, [], f"панель шлёт хелперу незарегистрированные операции: {missing}")

    def test_admin_retries_with_legacy_operation_name(self):
        """Панель обновилась, хелпер ещё старый: шлём старое имя операции."""
        calls = []

        def fake_run(command, input=None, text=None, capture_output=True, timeout=None):
            action = json.loads(input)["action"]
            calls.append((action, timeout))
            if action == "proxy.status":
                return mock.Mock(stdout=json.dumps({"ok": False, "error": "Неизвестная административная операция"}), stderr="")
            return mock.Mock(stdout=json.dumps({"ok": True, "result": {"installed": True}}), stderr="")

        with mock.patch.object(app.subprocess, "run", side_effect=fake_run):
            result = app.Panel.admin("proxy.status", {})
        self.assertTrue(result["ok"])
        self.assertEqual([action for action, _ in calls], ["proxy.status", "proxy"])

    def test_admin_install_operations_get_long_timeout(self):
        timeouts = {}

        def fake_run(command, input=None, text=None, capture_output=True, timeout=None):
            action = json.loads(input)["action"]
            timeouts[action] = timeout
            return mock.Mock(stdout=json.dumps({"ok": True, "result": {}}), stderr="")

        with mock.patch.object(app.subprocess, "run", side_effect=fake_run):
            app.Panel.admin("proxy.install", {})
            app.Panel.admin("proxy.status", {})
            app.Panel.admin("tailscale.install", {})
        self.assertEqual(timeouts["proxy.install"], 960)
        self.assertEqual(timeouts["proxy.status"], 60)
        self.assertEqual(timeouts["tailscale.install"], 240)

    def test_every_route_is_unique_and_dotted(self):
        for route, operation in app.API_ROUTES.items():
            self.assertTrue(operation, route)
            self.assertNotIn("/", operation, f"{route} -> {operation}")

    def test_proxy_and_tailscale_operations_are_reachable(self):
        from wdtt_panel import admin

        expected = [
            "proxy.status",
            "proxy.install",
            "proxy.action",
            "proxy.config.save",
            "tailscale.status",
            "tailscale.install",
            "tailscale.connect",
            "tailscale.disconnect",
            "tailscale.logout",
            "tailscale.check",
        ]
        for operation in expected:
            self.assertIn(operation, admin.OPERATIONS)
        # Алиасы для панелей 2.1.0–2.2.1 (короткие имена), чтобы обновление
        # хелпера не сломало уже установленную панель.
        self.assertIs(admin.OPERATIONS["proxy"], admin.OPERATIONS["proxy.status"])
        self.assertIs(admin.OPERATIONS["proxy.config"], admin.OPERATIONS["proxy.config.save"])

    def test_fake_helper_implements_new_operations(self):
        """Тестовый хелпер должен уметь всё, что шлёт панель в новых вкладках
        (прокси и Tailscale): иначе тесты этих маршрутов пройдут на пустом ответе."""
        fake = Path(__file__).with_name("fake_admin.py").read_text(encoding="utf-8")
        for operation in sorted(op for op in app.API_ROUTES.values() if op.startswith(("proxy.", "tailscale."))):
            self.assertIn(operation, fake, f"fake_admin.py не обрабатывает {operation}")


if __name__ == "__main__":
    unittest.main()
