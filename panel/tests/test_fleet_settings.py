"""Тесты настроек контроллера: файл, порядок источников, веб-страница «Бот»."""
import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from wdtt_panel.fleet import (
    FleetSettings,
    FleetStore,
    Node,
    load_settings,
    parse_admins,
    resolve_bot_config,
    save_settings,
)
from wdtt_panel.fleet.web import FleetWeb
from wdtt_panel.security import hash_password

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_fleet_web import Browser, StubClient, PASSWORD  # noqa: E402


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.cfg = Path(self.tmp.name) / "config.json"

    def tearDown(self):
        self.tmp.cleanup()

    def test_roundtrip(self):
        saved = FleetSettings(bot_token="123:ABC", admin_ids=[3, 1, 1], poll_timeout=10)
        save_settings(saved, self.cfg)
        _, loaded = load_settings(self.cfg)
        self.assertEqual(loaded.bot_token, "123:ABC")
        self.assertEqual(loaded.admin_ids, [1, 3])
        self.assertEqual(loaded.poll_timeout, 10)
        self.assertGreater(loaded.updated_at, 0)
        if os.name != "nt":  # на Windows chmod 0600 не отображается в ACL так же
            mode = (self.cfg.stat().st_mode & 0o777)
            self.assertEqual(mode, 0o600)

    def test_missing_file_defaults(self):
        _, loaded = load_settings(self.cfg)
        self.assertEqual(loaded, FleetSettings())

    def test_from_dict_tolerates_junk(self):
        loaded = FleetSettings.from_dict({"admin_ids": ["a", 5, None],
                                          "poll_timeout": "zzz"})
        self.assertEqual(loaded.admin_ids, [5])
        self.assertEqual(loaded.poll_timeout, 25)

    def test_parse_admins(self):
        self.assertEqual(parse_admins("1, 2;3 +4"), [1, 2, 3, 4])
        self.assertEqual(parse_admins("abc"), [])

    def test_resolve_cli_wins(self):
        save_settings(FleetSettings(bot_token="file-tok", admin_ids=[9]), self.cfg)
        with mock.patch.dict("os.environ", {"WDTT_FLEET_BOT_TOKEN": "env-tok",
                                            "WDTT_FLEET_ADMINS": "7"}):
            token, admins, source, _ = resolve_bot_config("cli-tok", ["5"], self.cfg)
            self.assertEqual((token, admins, source), ("cli-tok", [5], "cli"))

    def test_resolve_env_over_file(self):
        save_settings(FleetSettings(bot_token="file-tok", admin_ids=[9]), self.cfg)
        with mock.patch.dict("os.environ", {"WDTT_FLEET_BOT_TOKEN": "env-tok",
                                            "WDTT_FLEET_ADMINS": "7"}):
            token, admins, source, _ = resolve_bot_config("", [], self.cfg)
            self.assertEqual((token, admins, source), ("env-tok", [7], "env"))

    def test_resolve_file_fallback(self):
        save_settings(FleetSettings(bot_token="file-tok", admin_ids=[9]), self.cfg)
        with mock.patch.dict("os.environ", {}, clear=False):
            with mock.patch.dict("os.environ", {"WDTT_FLEET_BOT_TOKEN": "",
                                                "WDTT_FLEET_ADMINS": ""}):
                token, admins, source, _ = resolve_bot_config("", [], self.cfg)
                self.assertEqual((token, admins, source), ("file-tok", [9], "file"))


class BotPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.store = FleetStore(Path(cls.tmp.name) / "nodes.json")
        cls.store.add(Node(id="n1", base_url="http://x", password="secret"))
        os.environ["WDTT_FLEET_CONFIG"] = str(Path(cls.tmp.name) / "bot-config.json")
        app = FleetWeb(cls.store, "admin", hash_password(PASSWORD),
                       secret="test-secret-123", client_cls=StubClient)
        from wsgiref.simple_server import make_server
        import threading
        cls.server = make_server("127.0.0.1", 0, app)
        cls.root = f"http://127.0.0.1:{cls.server.server_address[1]}"
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()
        os.environ.pop("WDTT_FLEET_CONFIG", None)

    def browser(self):
        return Browser(self.root)

    def login(self, browser):
        return browser.post_form("/login", {"username": "admin", "password": PASSWORD})

    def csrf(self, browser, page="/bot"):
        import re
        _, _, body = browser.get(page)
        found = re.search(r"name=csrf value='([^']+)'", body)
        self.assertIsNotNone(found)
        return found.group(1)

    def auth_headers(self, browser):
        _, _, body = browser.post_json("/api/v1/auth/login",
                                       {"username": "admin", "password": PASSWORD})
        return {"Authorization": f"Bearer {json.loads(body)['result']['token']}"}

    def test_bot_page_requires_auth(self):
        code, url, body = self.browser().get("/bot")
        self.assertTrue(url.endswith("/login") or "Вход" in body)

    def test_bot_page_form(self):
        b = self.browser()
        self.login(b)
        code, _, body = b.get("/bot")
        self.assertEqual(code, 200)
        self.assertIn("bot_token", body)
        self.assertIn("admin_ids", body)

    def test_bot_save_via_web(self):
        b = self.browser()
        self.login(b)
        _, _, body = b.post_form("/bot/save", {
            "csrf": self.csrf(b), "bot_token": "999:WEBTOK",
            "admin_ids": "11, 22", "poll_timeout": "10"})
        self.assertIn("Сохранено", body)
        _, _, body = b.get("/bot")
        self.assertIn("999:WEBTOK", body)
        self.assertIn("11,22", body.replace(" ", ""))

    def test_bot_test_ok_and_fail(self):
        b = self.browser()
        self.login(b)
        b.post_form("/bot/save", {"csrf": self.csrf(b), "bot_token": "999:WEBTOK",
                                  "admin_ids": "11", "poll_timeout": "25"})
        with mock.patch("wdtt_panel.fleet.web.TelegramAPI.get_me",
                        return_value={"username": "mybot", "id": 999}):
            _, _, body = b.post_form("/bot/test", {"csrf": self.csrf(b)})
            self.assertIn("@mybot", body)
        with mock.patch("wdtt_panel.fleet.web.TelegramAPI.get_me",
                        side_effect=OSError("401 Unauthorized")):
            _, _, body = b.post_form("/bot/test", {"csrf": self.csrf(b)})
            self.assertIn("не работает", body)

    def test_bot_api(self):
        b = self.browser()
        heads = self.auth_headers(b)
        code, _, body = b.post_json("/api/v1/bot/save",
                                    {"bot_token": "111:APITOK", "admin_ids": [42]},
                                    heads)
        self.assertEqual(code, 200)
        code, _, body = b.get("/api/v1/bot", headers=heads)
        data = json.loads(body)["result"]
        self.assertTrue(data["token_set"])
        self.assertEqual(data["token_tail"], "ITOK")
        self.assertEqual(data["admin_ids"], [42])
        self.assertNotIn("111:APITOK", body)
        with mock.patch("wdtt_panel.fleet.web.TelegramAPI.get_me",
                        return_value={"username": "apibot", "id": 111}):
            code, _, body = b.post_json("/api/v1/bot/test", {}, heads)
            self.assertIn("apibot", body)


if __name__ == "__main__":
    unittest.main()
