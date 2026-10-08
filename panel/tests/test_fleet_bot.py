"""Тесты Telegram-бота контроллера: диспетчер команд без сети.

Telegram API подменён стабом, клиенты нод — стабом (client_cls в fanout).
"""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from wdtt_panel.fleet import FleetStore, Node
from wdtt_panel.fleet.bot import FleetBot, resolve_admins

ADMIN = 1001
CHAT = 55


class StubAPI:
    def __init__(self):
        self.sent = []  # (chat_id, text, markup)
        self.callbacks = []

    def send_message(self, chat_id, text, markup=None):
        self.sent.append((chat_id, text, markup))

    def answer_callback(self, callback_id, text=""):
        self.callbacks.append((callback_id, text))

    def last_text(self):
        return self.sent[-1][1] if self.sent else ""


class StubClient:
    """Клиент ноды-пустышки: поведение зависит от id ноды."""

    def __init__(self, node, timeout=30.0):
        self.node = node

    def call(self, route, payload=None):
        if route == "overview":
            return self.overview()
        if route == "users":
            return self.users_list()
        if route == "users/create":
            return self.user_create(payload or {})
        if route == "users/delete":
            return self.user_delete((payload or {}).get("password"))
        raise AssertionError(f"unexpected route {route}")

    def overview(self):
        if self.node.id == "dead":
            from wdtt_panel.fleet.client import FleetError
            raise FleetError("нет ответа")
        return {"stats": {"active": 1, "total": 2}}

    def users_list(self):
        return {"users": [{"password": "KEY1", "link": "vk://KEY1"}], "total": 1}

    def user_create(self, payload):
        return {"password": payload.get("password")}

    def user_delete(self, password):
        if password == "KEY1":
            return {"deleted": True}
        from wdtt_panel.fleet.client import FleetError
        raise FleetError("Пользователь не найден")


def msg(update_id, text, user=ADMIN):
    return {"update_id": update_id,
            "message": {"message_id": update_id, "chat": {"id": CHAT},
                        "from": {"id": user}, "text": text}}


def callback(update_id, data, user=ADMIN):
    return {"update_id": update_id,
            "callback_query": {"id": f"cb{update_id}", "from": {"id": user},
                               "message": {"message_id": 1, "chat": {"id": CHAT}},
                               "data": data}}


class FleetBotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.store = FleetStore(Path(self.tmp.name) / "nodes.json")
        self.store.add(Node(id="n1", base_url="http://x", password="p"))
        self.store.add(Node(id="n2", base_url="http://y", password="p"))
        self.api = StubAPI()
        self.bot = FleetBot(self.api, self.store, {ADMIN}, client_cls=StubClient)

    def tearDown(self):
        self.tmp.cleanup()

    def test_stranger_rejected(self):
        self.bot.handle_update(msg(1, "/nodes", user=999))
        self.assertIn("Нет доступа", self.api.last_text())

    def test_unknown_command(self):
        self.bot.handle_update(msg(1, "/nope"))
        self.assertIn("/nodes", self.api.last_text())

    def test_help(self):
        self.bot.handle_update(msg(1, "/start"))
        self.assertIn("/delete", self.api.last_text())

    def test_nodes(self):
        self.bot.handle_update(msg(1, "/nodes"))
        text = self.api.last_text()
        self.assertIn("n1", text)
        self.assertIn("n2", text)

    def test_status_all(self):
        self.bot.handle_update(msg(1, "/status"))
        text = self.api.last_text()
        self.assertIn("n1", text)
        self.assertIn("Итог: ok 2/2", text)

    def test_keys_found_and_missing(self):
        self.bot.handle_update(msg(1, "/keys KEY1"))
        self.assertIn("ЕСТЬ", self.api.last_text())
        self.bot.handle_update(msg(2, "/keys NOPE"))
        self.assertIn("нет", self.api.last_text())

    def test_keys_no_args(self):
        self.bot.handle_update(msg(1, "/keys"))
        self.assertIn("/keys KEY", self.api.last_text())

    def test_create_everywhere(self):
        self.bot.handle_update(msg(1, "/create NEWKEY"))
        self.assertIn("ok 2/2", self.api.last_text())

    def test_create_single_node(self):
        self.bot.handle_update(msg(1, "/create NEWKEY n1"))
        self.assertIn("(n1)", self.api.last_text())

    def test_delete_asks_confirmation(self):
        self.bot.handle_update(msg(1, "/delete KEY1 all"))
        chat_id, text, markup = self.api.sent[-1]
        self.assertIn("Удалить", text)
        self.assertIsNotNone(markup)
        data = markup["inline_keyboard"][0][0]["callback_data"]
        self.assertEqual(data, "del:KEY1:all")

    def test_delete_confirm_callback(self):
        self.bot.handle_update(callback(1, "del:KEY1:all"))
        self.assertIn("ok 2/2", self.api.last_text())
        self.assertTrue(self.api.callbacks)

    def test_delete_cancel_callback(self):
        self.bot.handle_update(callback(1, "noop"))
        self.assertIn("Отменено", self.api.callbacks[-1][1])
        self.assertEqual(len(self.api.sent), 0)

    def test_delete_callback_single_node(self):
        self.bot.handle_update(callback(1, "del:KEY1:n1"))
        text = self.api.last_text()
        self.assertIn("ok 1/1", text)

    def test_delete_missing_key_reports(self):
        self.bot.handle_update(callback(1, "del:NOPE:all"))
        self.assertIn("ok 0/2", self.api.last_text())

    def test_delete_no_args(self):
        self.bot.handle_update(msg(1, "/delete KEY1"))
        self.assertIn("/delete KEY", self.api.last_text())

    def test_delete_unknown_node(self):
        self.bot.handle_update(msg(1, "/delete KEY1 ghost"))
        self.assertIn("нет в реестре", self.api.last_text())

    def test_stranger_callback_rejected(self):
        self.bot.handle_update(callback(1, "del:KEY1:all", user=999))
        self.assertIn("Нет доступа", self.api.callbacks[-1][1])

    def test_bot_name_suffix_stripped(self):
        self.bot.handle_update(msg(1, "/status@mybot"))
        self.assertIn("Итог:", self.api.last_text())

    def test_resolve_admins(self):
        self.assertEqual(resolve_admins("1, 2;3"), {1, 2, 3})
        self.assertEqual(resolve_admins("abc"), set())
        self.assertEqual(resolve_admins(""), set())


if __name__ == "__main__":
    unittest.main()
