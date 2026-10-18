import json
import errno
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from wdtt_panel import admin


class AdminDatabaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_file = root / "etc" / "passwords.json"
        self.panel_labels = root / "user-labels.json"
        self.extension_state = root / "wdtt-extensions.json"
        self.backups = root / "backups"
        self.backup_schedule = root / "backup-schedule.json"
        self.lock_file = root / "admin.lock"
        self.install_log = root / "install.log"
        self.nginx_access_log = root / "nginx-access.log"
        self.nginx_error_log = root / "nginx-error.log"
        self.tailscale_dir = root / "tailscale"
        self.tailscale_settings = root / "tailscale-settings.json"
        self.tailscale_authkey = root / "tailscale-authkey"
        self.patchers = [
            mock.patch.object(admin, "DB_FILE", self.db_file),
            mock.patch.object(admin, "PANEL_LABELS_FILE", self.panel_labels),
            mock.patch.object(admin, "WDTT_EXTENSION_STATE", self.extension_state),
            mock.patch.object(admin, "BACKUP_DIR", self.backups),
            mock.patch.object(admin, "BACKUP_SCHEDULE_FILE", self.backup_schedule),
            mock.patch.object(admin, "LOCK_FILE", self.lock_file),
            mock.patch.object(admin, "INSTALL_LOG_FILE", self.install_log),
            mock.patch.object(admin, "NGINX_ACCESS_LOG", self.nginx_access_log),
            mock.patch.object(admin, "NGINX_ERROR_LOG", self.nginx_error_log),
            mock.patch.object(admin, "WDTT_UNIT_FILE", root / "wdtt.service"),
            mock.patch.object(admin, "WDTT_BOT_TOKEN_FILE", root / "etc" / "bot.token"),
            mock.patch.object(admin, "WDTT_SETTINGS_FILE", root / "wdtt-settings.json"),
            mock.patch.object(admin, "TAILSCALE_DIR", self.tailscale_dir),
            mock.patch.object(admin, "TAILSCALE_SETTINGS", self.tailscale_settings),
            mock.patch.object(admin, "TAILSCALE_AUTHKEY_FILE", self.tailscale_authkey),
            mock.patch.object(admin, "SKIP_SYSTEMD", True),
        ]
        for patcher in self.patchers:
            patcher.start()
        admin.save_database(admin.empty_database())

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_create_update_unbind_delete(self):
        created = admin.create_user(
            {
                "password": "PanelUser123",
                "days": 30,
                "vk_hash": "https://vk.com/call/join/hash_123",
                "ports": "56000,56001,9000",
            }
        )
        self.assertEqual(created["password"], "PanelUser123")

        data = admin.load_database()
        data["passwords"]["PanelUser123"]["device_id"] = "android-device"
        data["devices"]["android-device"] = {
            "device_id": "android-device",
            "ip": "10.66.66.2",
            "priv_key": "private",
            "pub_key": "public",
        }
        admin.save_database(data)

        unbound = admin.unbind_user({"password": "PanelUser123"})
        self.assertEqual(unbound["device_id"], "")
        self.assertNotIn("android-device", admin.load_database()["devices"])

        updated = admin.update_user(
            {
                "current_password": "PanelUser123",
                "password": "RenamedUser123",
                "unlimited": True,
                "vk_hash": "hash_456",
                "ports": "56100,56101,9100",
                "is_deactivated": True,
            }
        )
        self.assertEqual(updated["password"], "RenamedUser123")
        self.assertTrue(updated["is_deactivated"])
        self.assertEqual(updated["expires_at"], 0)

        admin.delete_user({"password": "RenamedUser123"})
        self.assertEqual(admin.load_database()["passwords"], {})
        self.assertTrue(list(self.backups.glob("passwords-*.json")))

    def test_default_quota_renewal_and_extra_traffic_are_idempotent(self):
        created = admin.create_user(
            {"password": "QuotaUser123", "months": 1, "vk_hash": "hash_123", "ports": "56000,56001,9000"}
        )
        self.assertTrue(created["traffic_managed"])
        self.assertEqual(created["traffic_primary_bytes"], 35 * admin.GIB)

        data = admin.load_database()
        entry = data["passwords"]["QuotaUser123"]
        entry["down_bytes"] = 5 * admin.GIB
        admin.save_database(data)

        payload = {"password": "QuotaUser123", "months": 1, "operation_id": "renew-test-0001"}
        renewed = admin.renew_user(payload)
        self.assertEqual(renewed["traffic_primary_bytes"], 65 * admin.GIB)
        duplicate = admin.renew_user(payload)
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["traffic_primary_bytes"], 65 * admin.GIB)

        extra = admin.add_user_traffic(
            {"password": "QuotaUser123", "gib": 10, "operation_id": "extra-test-0001"}
        )
        self.assertEqual(extra["traffic_extra_bytes"], 10 * admin.GIB)
        duplicate_extra = admin.add_user_traffic(
            {"password": "QuotaUser123", "gib": 10, "operation_id": "extra-test-0001"}
        )
        self.assertTrue(duplicate_extra["duplicate"])
        self.assertEqual(duplicate_extra["traffic_extra_bytes"], 10 * admin.GIB)

    def test_reactivation_burns_primary_and_keeps_extra(self):
        admin.create_user(
            {"password": "ExpiredUser123", "months": 1, "vk_hash": "hash_123", "ports": "56000,56001,9000"}
        )
        data = admin.load_database()
        entry = data["passwords"]["ExpiredUser123"]
        entry.update({"expires_at": 1, "traffic_primary_bytes": 20 * admin.GIB, "traffic_extra_bytes": 7 * admin.GIB})
        admin.save_database(data)
        renewed = admin.renew_user(
            {"password": "ExpiredUser123", "months": 1, "operation_id": "reactivate-0001"}
        )
        self.assertEqual(renewed["traffic_primary_bytes"], 35 * admin.GIB)
        self.assertEqual(renewed["traffic_extra_bytes"], 7 * admin.GIB)
        self.assertFalse(renewed["is_deactivated"])

    def test_manual_plan_adjustment_changes_days_and_traffic_safely(self):
        created = admin.create_user(
            {"password": "AdjustUser123", "months": 1, "traffic_primary_gib": 35, "vk_hash": "hash_123", "ports": "56000,56001,9000"}
        )
        old_expiry = created["expires_at"]
        adjusted = admin.adjust_user_plan(
            {
                "password": "AdjustUser123",
                "operation_id": "adjust-test-0001",
                "expiration_mode": "adjust_days",
                "days_delta": 7,
                "traffic_mode": "subtract",
                "traffic_gib": 5,
            }
        )
        self.assertEqual(adjusted["expires_at"], old_expiry + 7 * 86400)
        self.assertEqual(adjusted["traffic_remaining_bytes"], 30 * admin.GIB)

        duplicate = admin.adjust_user_plan(
            {
                "password": "AdjustUser123",
                "operation_id": "adjust-test-0001",
                "expiration_mode": "adjust_days",
                "days_delta": 7,
                "traffic_mode": "subtract",
                "traffic_gib": 5,
            }
        )
        self.assertTrue(duplicate["duplicate"])
        self.assertEqual(duplicate["expires_at"], old_expiry + 7 * 86400)
        self.assertEqual(duplicate["traffic_remaining_bytes"], 30 * admin.GIB)

        exact = admin.adjust_user_plan(
            {
                "password": "AdjustUser123",
                "operation_id": "adjust-test-0002",
                "expiration_mode": "keep",
                "traffic_mode": "set",
                "traffic_gib": 12,
            }
        )
        self.assertEqual(exact["traffic_remaining_bytes"], 12 * admin.GIB)
        with self.assertRaisesRegex(admin.ValidationError, "больше оставшегося"):
            admin.adjust_user_plan(
                {
                    "password": "AdjustUser123",
                    "operation_id": "adjust-test-0003",
                    "expiration_mode": "keep",
                    "traffic_mode": "subtract",
                    "traffic_gib": 13,
                }
            )

    def test_restore_backup(self):
        admin.create_user(
            {"password": "BackupUser123", "days": 7, "vk_hash": "hash_123", "ports": "56000,56001,9000"}
        )
        backup_name = admin.create_backup("manual")
        admin.delete_user({"password": "BackupUser123"})
        admin.restore_backup({"name": backup_name})
        self.assertIn("BackupUser123", admin.load_database()["passwords"])

    def test_full_backup_restores_users_statistics_devices_and_panel_settings(self):
        data = admin.load_database()
        data["custom_setting"] = "preserved"
        data["passwords"]["StatsUser123"] = {
            "device_id": "device-stats",
            "expires_at": 0,
            "down_bytes": 123456,
            "up_bytes": 654321,
            "vk_hash": "hash_stats",
            "ports": "56000,56001,9000",
            "is_deactivated": False,
        }
        data["devices"]["device-stats"] = {"device_id": "device-stats", "ip": "10.66.66.9"}
        admin.save_database(data)
        admin.save_tailscale_settings({"hostname": "restored-node", "accept_dns": False})

        backup = admin.create_manual_backup({"type": "full"})
        self.assertEqual(backup["type"], "full")
        changed = admin.load_database()
        changed["passwords"].clear()
        changed["devices"].clear()
        changed.pop("custom_setting")
        admin.save_database(changed)
        admin.save_tailscale_settings({"hostname": "changed-node", "accept_dns": True})

        admin.restore_backup({"name": backup["name"]})
        restored = admin.load_database()
        self.assertEqual(restored["passwords"]["StatsUser123"]["down_bytes"], 123456)
        self.assertEqual(restored["devices"]["device-stats"]["ip"], "10.66.66.9")
        self.assertEqual(restored["custom_setting"], "preserved")
        self.assertEqual(admin.load_tailscale_settings()["hostname"], "restored-node")

    def test_users_backup_does_not_replace_panel_settings(self):
        admin.create_user(
            {"password": "UserOnly123", "label": "Отдельно", "days": 30, "vk_hash": "hash_one", "ports": "56000,56001,9000"}
        )
        admin.save_tailscale_settings({"hostname": "kept-node", "accept_dns": False})
        backup = admin.create_manual_backup({"type": "users"})
        self.assertEqual(backup["type"], "users")

        admin.delete_user({"password": "UserOnly123"})
        admin.save_tailscale_settings({"hostname": "changed-node", "accept_dns": True})

        admin.restore_backup({"name": backup["name"]})
        self.assertIn("UserOnly123", admin.load_database()["passwords"])
        self.assertEqual(admin.load_tailscale_settings()["hostname"], "changed-node")

    def test_backups_can_be_deleted_and_scheduled_with_retention(self):
        schedule = admin.save_backup_schedule({"frequency": "daily", "time": "04:20", "type": "users", "keep": 1})
        self.assertEqual(schedule["settings"], {"frequency": "daily", "time": "04:20", "type": "users", "keep": 1})
        self.assertFalse(schedule["active"])
        self.assertEqual(admin.backup_schedule_status()["settings"]["time"], "04:20")

        first = admin.create_manual_backup({"type": "users", "scheduled": True})
        second = admin.create_manual_backup({"type": "users", "scheduled": True})
        scheduled = list(self.backups.glob("users-*-scheduled.json"))
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0].name, second["name"])
        self.assertFalse((self.backups / first["name"]).exists())

        manual = admin.create_manual_backup({"type": "full"})
        self.assertEqual(admin.delete_backup({"name": manual["name"]})["deleted"], manual["name"])
        self.assertFalse((self.backups / manual["name"]).exists())

    def test_bulk_create_assigns_shared_hashes(self):
        result = admin.create_users_bulk(
            {
                "count": 3,
                "days": 30,
                "vk_hash": "hash_one,hash_two",
                "hash_mode": "shared",
                "ports": "56000,56001,9000",
            }
        )
        self.assertEqual(result["count"], 3)
        self.assertEqual(len({item["password"] for item in result["users"]}), 3)
        self.assertTrue(all(item["vk_hash"] == "hash_one,hash_two" for item in result["users"]))

    def test_bulk_create_rotates_hashes(self):
        result = admin.create_users_bulk(
            {
                "count": 4,
                "unlimited": True,
                "vk_hash": "hash_one hash_two",
                "hash_mode": "rotate",
                "ports": "56000,56001,9000",
            }
        )
        self.assertEqual(
            [item["vk_hash"] for item in result["users"]],
            ["hash_one", "hash_two", "hash_one", "hash_two"],
        )
        self.assertTrue(all(item["expires_at"] == 0 for item in result["users"]))

    def test_labels_are_saved_for_single_and_bulk_users(self):
        created = admin.create_user(
            {
                "password": "NamedUser123",
                "label": "Иван — Pixel",
                "days": 30,
                "vk_hash": "hash_one",
                "ports": "56000,56001,9000",
            }
        )
        self.assertEqual(created["label"], "Иван — Pixel")
        updated = admin.update_user(
            {"current_password": "NamedUser123", "password": "NamedUser123", "label": "Иван дома"}
        )
        self.assertEqual(updated["label"], "Иван дома")
        result = admin.create_users_bulk(
            {
                "count": 2,
                "label_prefix": "Семья",
                "days": 30,
                "vk_hash": "hash_two",
                "ports": "56000,56001,9000",
            }
        )
        self.assertEqual([item["label"] for item in result["users"]], ["Семья 1", "Семья 2"])

    def test_max_devices_can_be_set_on_create_update_and_bulk(self):
        created = admin.create_user(
            {"password": "DeviceUser123", "days": 30, "vk_hash": "hash_123", "max_devices": 3}
        )
        self.assertEqual(created["max_devices"], 3)
        self.assertEqual(admin.load_database()["passwords"]["DeviceUser123"]["max_devices"], 3)

        zero = admin.create_user(
            {"password": "DeviceZero123", "days": 30, "vk_hash": "hash_123", "max_devices": 0}
        )
        self.assertEqual(zero["max_devices"], 1)

        defaulted = admin.create_user(
            {"password": "DeviceDef123", "days": 30, "vk_hash": "hash_123"}
        )
        self.assertEqual(defaulted["max_devices"], admin.DEFAULT_MAX_DEVICES)

        updated = admin.update_user(
            {"current_password": "DeviceUser123", "password": "DeviceUser123", "max_devices": 5}
        )
        self.assertEqual(updated["max_devices"], 5)
        self.assertEqual(admin.load_database()["passwords"]["DeviceUser123"]["max_devices"], 5)

        bulk = admin.create_users_bulk(
            {"count": 2, "vk_hash": "hash_123", "days": 30, "max_devices": 2}
        )
        self.assertTrue(all(user["max_devices"] == 2 for user in bulk["users"]))

    def test_max_devices_rejects_invalid_values(self):
        for bad in (-1, 10001, "abc"):
            with self.assertRaises(admin.ValidationError):
                admin.create_user(
                    {"password": "BadDevice123", "days": 30, "vk_hash": "hash_123", "max_devices": bad}
                )

    def write_hash_library(self, *values: str) -> Path:
        path = Path(self.temp.name) / "panel.db"
        connection = sqlite3.connect(path)
        try:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS vk_hash_library (value TEXT PRIMARY KEY, created_at INTEGER NOT NULL)"
            )
            for index, value in enumerate(values):
                connection.execute(
                    "INSERT OR REPLACE INTO vk_hash_library(value, created_at) VALUES(?, ?)",
                    (value, index),
                )
            connection.commit()
        finally:
            connection.close()
        return path

    def test_create_user_tops_up_hashes_from_the_library(self):
        library = self.write_hash_library("hash_bbb", "hash_ccc", "hash_ddd")
        with mock.patch.object(admin, "PANEL_STATE_DB", library):
            created = admin.create_user(
                {"password": "TopUpUser123", "days": 30, "vk_hash": "hash_aaa"}
            )
        self.assertEqual(created["vk_hash"], "hash_aaa,hash_bbb,hash_ccc,hash_ddd")

    def test_create_user_derives_hashes_from_the_library_when_none_given(self):
        library = self.write_hash_library("hash_aaa", "hash_bbb", "hash_ccc", "hash_dddd")
        with mock.patch.object(admin, "PANEL_STATE_DB", library):
            created = admin.create_user({"password": "DerivedUser123", "days": 30})
        self.assertEqual(created["vk_hash"], "hash_aaa,hash_bbb,hash_ccc,hash_dddd")

    def test_create_user_without_hashes_or_library_is_rejected(self):
        with mock.patch.object(admin, "PANEL_STATE_DB", Path(self.temp.name) / "missing.db"):
            with self.assertRaises(admin.ValidationError):
                admin.create_user({"password": "NoHashUser123", "days": 30})

    def test_fill_missing_hashes_backfills_existing_users(self):
        library = self.write_hash_library("hash_bbb", "hash_ccc", "hash_ddd")
        with mock.patch.object(admin, "PANEL_STATE_DB", Path(self.temp.name) / "missing.db"):
            admin.create_user({"password": "FirstFill123", "days": 30, "vk_hash": "hash_aaa"})
            admin.create_user({"password": "SecondFill12", "days": 30, "vk_hash": "hash_bbb,hash_ccc"})
        with mock.patch.object(admin, "PANEL_STATE_DB", library):
            result = admin.fill_missing_hashes({})
        self.assertEqual(result["updated"], 2)
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["library"], 3)
        data = admin.load_database()
        self.assertEqual(
            data["passwords"]["FirstFill123"]["vk_hash"], "hash_aaa,hash_bbb,hash_ccc,hash_ddd"
        )
        self.assertEqual(
            data["passwords"]["SecondFill12"]["vk_hash"], "hash_bbb,hash_ccc,hash_ddd"
        )

    def test_fill_missing_hashes_is_a_noop_when_nothing_to_add(self):
        library = self.write_hash_library("hash_aaa", "hash_bbb")
        with mock.patch.object(admin, "PANEL_STATE_DB", library):
            admin.create_user({"password": "FullUser1234", "days": 30})
            result = admin.fill_missing_hashes({})
        self.assertEqual(result["updated"], 0)
        self.assertEqual(result["library"], 2)

    def test_update_user_tops_up_hashes_from_the_library(self):
        library = self.write_hash_library("hash_bbb", "hash_ccc", "hash_ddd", "hash_eeee")
        with mock.patch.object(admin, "PANEL_STATE_DB", Path(self.temp.name) / "missing.db"):
            admin.create_user({"password": "UpdateFill12", "days": 30, "vk_hash": "hash_aaa"})
        with mock.patch.object(admin, "PANEL_STATE_DB", library):
            updated = admin.update_user(
                {
                    "current_password": "UpdateFill12",
                    "password": "UpdateFill12",
                    "vk_hash": "hash_ddd",
                }
            )
        self.assertEqual(updated["vk_hash"], "hash_ddd,hash_bbb,hash_ccc,hash_eeee")

    def test_legacy_telegram_label_is_shown_in_the_panel(self):
        data = admin.load_database()
        data["passwords"]["LegacyUser12"] = {
            "device_id": "",
            "expires_at": 0,
            "down_bytes": 0,
            "up_bytes": 0,
            "remark": "Старый бот — Ольга",
            "vk_hash": "hash_one",
            "ports": "56000,56001,9000",
        }
        admin.save_database(data)
        self.assertEqual(admin.list_users()["users"][0]["label"], "Старый бот — Ольга")

    def test_legacy_telegram_label_map_is_shown_in_the_panel(self):
        data = admin.load_database()
        data["passwords"]["MappedUser123"] = {
            "device_id": "",
            "expires_at": 0,
            "down_bytes": 0,
            "up_bytes": 0,
            "vk_hash": "hash_one",
            "ports": "56000,56001,9000",
        }
        data["labels"] = {"MappedUser123": "Telegram — Сергей"}
        admin.save_database(data)
        self.assertEqual(admin.list_users()["users"][0]["label"], "Telegram — Сергей")

    def test_panel_labels_survive_an_older_wdtt_rewriting_its_database(self):
        admin.create_user(
            {"password": "DurableUser12", "label": "Ноутбук Ольги", "days": 30, "vk_hash": "hash_one", "ports": "56000,56001,9000"}
        )
        data = admin.load_database()
        data["passwords"]["DurableUser12"].pop("label", None)
        admin.save_database(data)
        self.assertEqual(admin.list_users()["users"][0]["label"], "Ноутбук Ольги")

    def test_user_traffic_activity_is_returned_with_the_user(self):
        data = admin.load_database()
        data["passwords"]["ActiveUser123"] = {
            "device_id": "",
            "expires_at": 0,
            "down_bytes": 512,
            "up_bytes": 256,
            "last_upload_at": 1_700_000_001,
            "last_download_at": 1_700_000_002,
            "vk_hash": "hash_one",
            "ports": "56000,56001,9000",
        }
        admin.save_database(data)
        user = admin.list_users()["users"][0]
        self.assertEqual(user["last_upload_at"], 1_700_000_001)
        self.assertEqual(user["last_download_at"], 1_700_000_002)

    def test_telegram_settings_update_database_and_wdtt_unit(self):
        admin.WDTT_UNIT_FILE.write_text(
            "[Service]\nExecStart=/usr/local/bin/wdtt-server -listen 0.0.0.0:56000 -wg-port 56001 -config-dir /etc/wdtt -password MainPassword123\n",
            encoding="utf-8",
        )

        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            code = 1 if command[:3] == ["systemctl", "is-active", "--quiet"] else 0
            return subprocess.CompletedProcess(command, code, "", "")

        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(admin, "run", side_effect=fake_run):
            result = admin.configure_telegram(
                {
                    "enabled": True,
                    "admin_id": "123456789",
                    "bot_token": "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_test",
                }
            )

        data = admin.load_database()
        self.assertTrue(result["enabled"])
        self.assertEqual(data["admin_id"], "123456789")
        self.assertEqual(data["bot_token"], "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_test")
        unit = admin.WDTT_UNIT_FILE.read_text(encoding="utf-8")
        self.assertIn("-admin 123456789", unit)
        self.assertIn("-bot-token-file", unit)
        self.assertNotIn("ABCDEFGHIJKLMNOPQRSTUVWXYZ_test", unit)
        self.assertEqual(admin.WDTT_BOT_TOKEN_FILE.read_text(encoding="utf-8").strip(), "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_test")

        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(admin, "run", side_effect=fake_run):
            disabled = admin.configure_telegram({"enabled": False})
        self.assertFalse(disabled["enabled"])
        self.assertNotIn("-bot-token", admin.WDTT_UNIT_FILE.read_text(encoding="utf-8"))
        self.assertFalse(admin.WDTT_BOT_TOKEN_FILE.exists())

    def test_bulk_user_actions_apply_in_one_database_update(self):
        for password in ("FirstUser123", "SecondUser12"):
            admin.create_user(
                {"password": password, "days": 30, "vk_hash": "hash_one", "ports": "56000,56001,9000"}
            )
        data = admin.load_database()
        data["passwords"]["FirstUser123"].update({"device_id": "first-device", "down_bytes": 100, "up_bytes": 50})
        data["devices"]["first-device"] = {"device_id": "first-device", "ip": "10.66.66.2"}
        admin.save_database(data)

        result = admin.bulk_user_action(
            {"action": "deactivate", "passwords": ["FirstUser123", "SecondUser12"]}
        )
        self.assertEqual(result["count"], 2)
        self.assertTrue(all(entry["is_deactivated"] for entry in admin.load_database()["passwords"].values()))
        before_renewal = int(time.time())
        admin.bulk_user_action({"action": "set_expiration", "passwords": ["FirstUser123", "SecondUser12"], "days": 45})
        renewed = admin.load_database()["passwords"]
        self.assertTrue(all(entry["expires_at"] >= before_renewal + 44 * 86400 for entry in renewed.values()))
        admin.bulk_user_action({"action": "reset_traffic", "passwords": ["FirstUser123"]})
        self.assertEqual(admin.load_database()["passwords"]["FirstUser123"]["down_bytes"], 0)
        admin.bulk_user_action({"action": "unbind", "passwords": ["FirstUser123"]})
        self.assertEqual(admin.load_database()["passwords"]["FirstUser123"]["device_id"], "")
        self.assertNotIn("first-device", admin.load_database()["devices"])
        admin.bulk_user_action({"action": "delete", "passwords": ["SecondUser12"]})
        self.assertNotIn("SecondUser12", admin.load_database()["passwords"])

    def test_database_file_is_valid_json(self):
        parsed = json.loads(self.db_file.read_text(encoding="utf-8"))
        self.assertIn("passwords", parsed)
        self.assertIn("devices", parsed)

    def test_cleanup_preview_and_apply_only_known_safe_targets(self):
        self.install_log.write_text("install log\n", encoding="utf-8")
        preview = admin.cleanup_system({"targets": ["service_logs"], "keep_days": 7}, False)
        self.assertFalse(preview["applied"])
        self.assertGreater(preview["estimated_freed_bytes"], 0)
        self.assertGreater(preview["items"][0]["before_bytes"], 0)
        self.assertEqual(preview["items"][0]["remaining_bytes"], preview["items"][0]["before_bytes"])
        self.assertTrue(self.install_log.read_text(encoding="utf-8"))

        result = admin.cleanup_system({"targets": ["service_logs", "unknown"], "keep_days": 7}, True)
        self.assertTrue(result["applied"])
        self.assertGreater(result["items"][0]["freed_bytes"], 0)
        self.assertEqual(result["items"][0]["remaining_bytes"], 0)
        self.assertEqual(self.install_log.read_text(encoding="utf-8"), "")
        self.assertEqual(admin.load_database()["passwords"], {})

    def test_cleanup_apply_skips_read_only_log_without_failing(self):
        self.install_log.write_text("locked log\n", encoding="utf-8")
        original_open = type(self.install_log).open

        def guarded_open(path, *args, **kwargs):
            mode = args[0] if args else kwargs.get("mode", "r")
            if path == self.install_log and "w" in mode:
                raise OSError(errno.EROFS, "Read-only file system", str(path))
            return original_open(path, *args, **kwargs)

        with mock.patch.object(type(self.install_log), "open", new=guarded_open):
            result = admin.cleanup_system({"targets": ["service_logs"], "keep_days": 14}, True)

        service_logs = result["items"][0]
        files = {item["name"]: item for item in service_logs["files"]}
        self.assertTrue(result["applied"])
        self.assertTrue(files["installer"]["skipped"])
        self.assertIn("только для чтения", files["installer"]["error"])
        self.assertGreater(service_logs["remaining_bytes"], 0)
        self.assertEqual(self.install_log.read_text(encoding="utf-8"), "locked log\n")

    def test_cleanup_package_cache_reports_remaining_size_after_apply(self):
        sizes = [100, 0, 0, 0, 25, 0, 0, 0]
        with (
            mock.patch.object(admin, "directory_size", side_effect=sizes),
            mock.patch.object(admin.shutil, "which", side_effect=lambda name: "/usr/bin/apt-get" if name == "apt-get" else None),
            mock.patch.object(admin, "run", return_value=subprocess.CompletedProcess(["apt-get", "clean"], 0, "", "")),
        ):
            result = admin.cleanup_package_cache(True)

        self.assertEqual(result["before_bytes"], 100)
        self.assertEqual(result["freed_bytes"], 75)
        self.assertEqual(result["remaining_bytes"], 25)

    def test_journal_disk_usage_size_is_parsed(self):
        self.assertEqual(admin.parse_size_text("Archived and active journals take up 781.0M in the file system."), 781 * 1024 * 1024)
        self.assertEqual(admin.parse_size_text("8K"), 8 * 1024)

    def test_cleanup_target_error_is_reported_without_stopping_other_targets(self):
        self.install_log.write_text("install log\n", encoding="utf-8")
        with mock.patch.object(admin, "cleanup_package_cache", side_effect=admin.AdminError("cache locked")):
            result = admin.cleanup_system({"targets": ["package_cache", "service_logs"], "keep_days": 14}, True)

        items = {item["target"]: item for item in result["items"]}
        self.assertFalse(items["package_cache"]["available"])
        self.assertEqual(items["package_cache"]["error"], "cache locked")
        self.assertTrue(items["service_logs"]["files"][0]["cleared"])
        self.assertEqual(self.install_log.read_text(encoding="utf-8"), "")

    def test_autoclean_skips_when_disk_is_below_threshold(self):
        with mock.patch.object(admin, "load_autoclean_settings", return_value=admin.default_autoclean_settings()), \
             mock.patch.object(admin, "disk_usage_percent", return_value=42.0), \
             mock.patch.object(admin, "cleanup_system") as cleanup:
            result = admin.run_autoclean()
        self.assertFalse(result["ran"])
        self.assertEqual(result["reason"], "below_threshold")
        cleanup.assert_not_called()

    def test_autoclean_runs_cleanup_and_prunes_backups_above_threshold(self):
        settings = admin.default_autoclean_settings()
        with mock.patch.object(admin, "load_autoclean_settings", return_value=settings), \
             mock.patch.object(admin, "disk_usage_percent", return_value=95.0), \
             mock.patch.object(admin, "cleanup_system", return_value={"estimated_freed_bytes": 2048, "items": []}) as cleanup, \
             mock.patch.object(admin, "prune_backups_to", return_value=3) as prune:
            result = admin.run_autoclean()
        self.assertTrue(result["ran"])
        self.assertEqual(result["reason"], "cleaned")
        self.assertEqual(result["freed_bytes"], 2048)
        self.assertEqual(result["backups_removed"], 3)
        cleanup.assert_called_once()
        prune.assert_called_once_with(settings["keep_panel"], settings["keep_users"])

    def test_prune_backups_to_keeps_requested_panel_and_user_counts(self):
        self.backups.mkdir(parents=True, exist_ok=True)
        for index in range(3):
            (self.backups / f"panel-2026061{index}-000000-manual.json").write_text(
                json.dumps({"format": admin.BACKUP_FORMAT, "type": "full"}), encoding="utf-8"
            )
            (self.backups / f"passwords-2026061{index}-000000-manual.json").write_text(
                json.dumps({"passwords": {}}), encoding="utf-8"
            )
        removed = admin.prune_backups_to(2, 1)
        self.assertEqual(removed, 3)
        remaining = admin.list_backups()["backups"]
        self.assertEqual(sum(1 for item in remaining if item["type"] == "full"), 2)
        self.assertEqual(sum(1 for item in remaining if item["type"] == "users"), 1)

    def test_version_comparison_normalizes_short_versions(self):
        self.assertEqual(admin.version_parts("1.2"), admin.version_parts("1.2.0"))
        self.assertGreater(admin.version_parts("1.2.1"), admin.version_parts("1.2"))

    def test_backup_can_be_exported_and_uploaded(self):
        backup = admin.create_manual_backup({})
        exported = admin.export_backup({"name": backup["name"]})
        uploaded = admin.import_backup({"name": "local.json", "content": exported["content"]})
        self.assertTrue((self.backups / uploaded["name"]).is_file())
        self.assertEqual(json.loads(exported["content"])["database"]["passwords"], {})
        self.assertEqual(uploaded["type"], "full")

    def test_admin_device_is_not_online_from_a_stale_conntrack_entry(self):
        data = admin.load_database()
        data["main_down_bytes"] = 200
        data["main_up_bytes"] = 100
        data["devices"]["admin-phone"] = {"device_id": "admin-phone", "ip": "10.66.66.2", "pub_key": "admin-public"}
        admin.save_database(data)
        with mock.patch.object(admin, "wireguard_handshakes", return_value={}):
            result = admin.list_users()
        self.assertEqual(result["admins"][0]["role"], "admin")
        self.assertFalse(result["admins"][0]["connected"])
        self.assertEqual(result["admins"][0]["down_bytes"], 200)
        self.assertTrue(result["admins"][0]["traffic_supported"])

    def test_admin_device_is_online_from_embedded_wireguard_handshake(self):
        data = admin.load_database()
        data["devices"]["admin-phone"] = {"device_id": "admin-phone", "ip": "10.66.66.2", "pub_key": "admin-public"}
        admin.save_database(data)
        with mock.patch.object(admin, "wireguard_handshakes", return_value={"admin-public": int(time.time())}):
            result = admin.list_users()
        self.assertTrue(result["admins"][0]["connected"])

    def test_main_administrator_is_listed_without_a_bound_device(self):
        data = admin.load_database()
        data["main_password"] = "admin"
        admin.save_database(data)
        result = admin.list_users()
        self.assertEqual(result["admins"][0]["label"], "Администратор WDTT")
        self.assertEqual(result["admins"][0]["device_id"], "")
        self.assertIsNone(result["admins"][0]["device"])
        self.assertFalse(result["admins"][0]["connected"])

    def test_stale_wireguard_handshake_is_not_online(self):
        self.assertFalse(admin.handshake_is_active(int(time.time()) - 76))

    def test_overview_counts_the_main_administrator_and_its_device(self):
        data = admin.load_database()
        data["main_password"] = "admin"
        data["devices"]["admin-phone"] = {"device_id": "admin-phone", "ip": "10.66.66.2"}
        admin.save_database(data)
        disk = mock.Mock(total=100, used=10, free=90)
        with mock.patch.object(admin, "read_stats", return_value={}), mock.patch.object(admin.shutil, "disk_usage", return_value=disk), mock.patch.object(admin, "cpu_usage", return_value=0), mock.patch.object(admin, "memory_usage", return_value={}), mock.patch.object(admin.os, "getloadavg", return_value=(0, 0, 0), create=True):
            result = admin.overview({})
        self.assertEqual(result["users"], 1)
        self.assertEqual(result["devices"], 1)
        self.assertEqual(result["admin_devices"], 1)
        self.assertEqual(result["online_devices"], 0)
        self.assertEqual(result["online_admin_devices"], 0)

    def test_multiple_main_password_devices_are_merged_into_one_administrator(self):
        data = admin.load_database()
        data["main_password"] = "admin"
        data["devices"]["phone-a"] = {"device_id": "phone-a", "ip": "10.66.0.2", "pub_key": "pub-a"}
        data["devices"]["phone-b"] = {"device_id": "phone-b", "ip": "10.66.0.3", "pub_key": "pub-b"}
        admin.save_database(data)
        with mock.patch.object(admin, "wireguard_handshakes", return_value={"pub-a": int(time.time())}):
            result = admin.list_users()
        admins = result["admins"]
        self.assertEqual(len(admins), 1)
        self.assertEqual(admins[0]["device_id"], "multi")
        self.assertIsNone(admins[0]["device"])
        self.assertEqual([device["device_id"] for device in admins[0]["devices"]], ["phone-a", "phone-b"])
        self.assertTrue(admins[0]["devices"][0]["connected"])
        self.assertFalse(admins[0]["devices"][1]["connected"])
        self.assertTrue(admins[0]["connected"])

    def test_overview_counts_online_admin_devices_from_merged_administrator(self):
        data = admin.load_database()
        data["main_password"] = "admin"
        data["devices"]["phone-a"] = {"device_id": "phone-a", "ip": "10.66.0.2", "pub_key": "pub-a"}
        data["devices"]["phone-b"] = {"device_id": "phone-b", "ip": "10.66.0.3", "pub_key": "pub-b"}
        admin.save_database(data)
        disk = mock.Mock(total=100, used=10, free=90)
        with mock.patch.object(admin, "read_stats", return_value={}), mock.patch.object(admin.shutil, "disk_usage", return_value=disk), mock.patch.object(admin, "cpu_usage", return_value=0), mock.patch.object(admin, "memory_usage", return_value={}), mock.patch.object(admin.os, "getloadavg", return_value=(0, 0, 0), create=True), mock.patch.object(admin, "wireguard_handshakes", return_value={"pub-a": int(time.time())}):
            result = admin.overview({})
        self.assertEqual(result["admin_devices"], 2)
        self.assertEqual(result["online_admin_devices"], 1)
        self.assertEqual(result["online_devices"], 1)

    def test_multiple_devices_of_a_user_key_stay_on_the_user(self):
        data = admin.load_database()
        data["main_password"] = "admin"
        data["passwords"]["MultiUser123"] = {
            "label": "test",
            "device_id": "multi",
            "device_ids": ["phone-a", "phone-b"],
            "expires_at": 0,
        }
        data["devices"]["phone-a"] = {"device_id": "phone-a", "ip": "10.66.0.2", "pub_key": "pub-a"}
        data["devices"]["phone-b"] = {"device_id": "phone-b", "ip": "10.66.0.3", "pub_key": "pub-b"}
        admin.save_database(data)
        with mock.patch.object(admin, "wireguard_handshakes", return_value={"pub-a": int(time.time())}):
            result = admin.list_users()
        self.assertEqual(len(result["users"]), 1)
        user = result["users"][0]
        self.assertEqual(user["password"], "MultiUser123")
        self.assertEqual(user["device_id"], "multi")
        self.assertEqual([device["device_id"] for device in user["devices"]], ["phone-a", "phone-b"])
        self.assertTrue(user["devices"][0]["connected"])
        self.assertFalse(user["devices"][1]["connected"])
        self.assertTrue(user["connected"])
        self.assertEqual(result["admins"][0]["devices"], [])

    def test_overview_does_not_count_user_bound_devices_as_admin(self):
        data = admin.load_database()
        data["main_password"] = "admin"
        data["passwords"]["MultiUser123"] = {
            "device_id": "multi",
            "device_ids": ["phone-a", "phone-b"],
            "expires_at": 0,
        }
        data["devices"]["phone-a"] = {"device_id": "phone-a", "ip": "10.66.0.2", "pub_key": "pub-a"}
        data["devices"]["phone-b"] = {"device_id": "phone-b", "ip": "10.66.0.3", "pub_key": "pub-b"}
        admin.save_database(data)
        disk = mock.Mock(total=100, used=10, free=90)
        with mock.patch.object(admin, "read_stats", return_value={}), mock.patch.object(admin.shutil, "disk_usage", return_value=disk), mock.patch.object(admin, "cpu_usage", return_value=0), mock.patch.object(admin, "memory_usage", return_value={}), mock.patch.object(admin.os, "getloadavg", return_value=(0, 0, 0), create=True), mock.patch.object(admin, "wireguard_handshakes", return_value={"pub-a": int(time.time())}):
            result = admin.overview({})
        self.assertEqual(result["users"], 2)
        self.assertEqual(result["devices"], 2)
        self.assertEqual(result["admin_devices"], 0)
        self.assertEqual(result["online_admin_devices"], 0)
        self.assertEqual(result["online_devices"], 1)

    def test_unbind_clears_all_user_device_bindings(self):
        data = admin.load_database()
        data["passwords"]["MultiUser123"] = {
            "device_id": "multi",
            "device_ids": ["phone-a", "phone-b"],
            "expires_at": 0,
        }
        data["devices"]["phone-a"] = {"device_id": "phone-a", "ip": "10.66.0.2"}
        data["devices"]["phone-b"] = {"device_id": "phone-b", "ip": "10.66.0.3"}
        admin.save_database(data)
        result = admin.unbind_user({"password": "MultiUser123"})
        stored = admin.load_database()
        self.assertEqual(result["device_id"], "")
        self.assertEqual(stored["passwords"]["MultiUser123"]["device_id"], "")
        self.assertEqual(stored["passwords"]["MultiUser123"]["device_ids"], [])
        self.assertNotIn("phone-a", stored["devices"])
        self.assertNotIn("phone-b", stored["devices"])

    def test_userspace_wireguard_handshakes_are_used_when_wg_tools_are_missing(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(admin.shutil, "which", return_value=None), mock.patch.object(admin, "userspace_wireguard_handshakes", return_value={"public-key": 123}):
            self.assertEqual(admin.wireguard_handshakes(), {"public-key": 123})

    def test_full_diagnostics_log_sources_are_limited(self):
        result = admin.journal_logs({"source": "all", "limit": 9999})
        self.assertEqual(result["source"], "all")
        self.assertEqual(result["limit"], 5000)
        self.assertEqual(result["lines"], [])


class PanelUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.status = Path(self.temp.name) / "self-update-status.json"
        self.command = Path(self.temp.name) / "wdtt-panel-self-update"
        self.command.write_text("#!/bin/sh\n", encoding="utf-8")
        self.patchers = [
            mock.patch.object(admin, "PANEL_UPDATE_STATUS_FILE", self.status),
            mock.patch.object(admin, "PANEL_SELF_UPDATE_COMMAND", self.command),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_panel_version_reports_cached_status(self):
        self.status.write_text(
            json.dumps(
                {
                    "state": "ok",
                    "current": "1.2.0",
                    "latest": "1.3.0",
                    "update_available": True,
                    "message": "",
                    "checked_at": 1234,
                }
            ),
            encoding="utf-8",
        )
        result = admin.panel_version({"current_version": "1.2.0"})
        self.assertEqual(result["current"], "1.2.0")
        self.assertEqual(result["latest"], "1.3.0")
        self.assertTrue(result["update_available"])
        self.assertEqual(result["checked_at"], 1234)

    def test_panel_version_without_status_reports_no_update(self):
        result = admin.panel_version({"current_version": "1.2.0"})
        self.assertFalse(result["update_available"])
        self.assertEqual(result["latest"], "")
        self.assertEqual(result["state"], "unknown")

    def test_panel_check_and_update_schedule_systemd_units(self):
        calls = []

        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(admin, "run", side_effect=fake_run):
            self.assertTrue(admin.schedule_panel_check({})["scheduled"])
            self.assertTrue(admin.start_panel_update({})["scheduled"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][:2], ["systemd-run", "--quiet"])
        self.assertEqual(calls[1][:2], ["systemd-run", "--quiet"])
        self.assertIn("check", calls[0])
        self.assertIn("update", calls[1])

    def test_panel_update_requires_helper_command(self):
        with mock.patch.object(admin, "PANEL_SELF_UPDATE_COMMAND", Path(self.temp.name) / "missing"):
            with self.assertRaises(admin.AdminError):
                admin.start_panel_update({})

    def test_panel_update_retries_without_collect_on_old_systemd(self):
        calls = []

        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            calls.append(command)
            if "--collect" in command:
                return subprocess.CompletedProcess(command, 1, "", "systemd-run: unrecognized option '--collect'\n")
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(admin, "run", side_effect=fake_run):
            self.assertTrue(admin.start_panel_update({})["scheduled"])
        self.assertEqual(len(calls), 2)
        self.assertIn("--collect", calls[0])
        self.assertNotIn("--collect", calls[1])
        self.assertIn("update", calls[1])

    def test_panel_update_keeps_collect_on_modern_systemd(self):
        calls = []

        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(admin, "run", side_effect=fake_run):
            self.assertTrue(admin.start_panel_update({})["scheduled"])
        self.assertEqual(len(calls), 1)
        self.assertIn("--collect", calls[0])


class WdttInboundAndSpeedTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.patchers = [
            mock.patch.object(admin, "DB_FILE", root / "passwords.json"),
            mock.patch.object(admin, "WDTT_UNIT_FILE", root / "wdtt.service"),
            mock.patch.object(admin, "WDTT_BOT_TOKEN_FILE", root / "bot.token"),
            mock.patch.object(admin, "WDTT_SETTINGS_FILE", root / "wdtt-settings.json"),
            mock.patch.object(admin, "WDTT_EXTENSION_STATE", root / "wdtt-extensions.json"),
            mock.patch.object(admin, "SKIP_SYSTEMD", True),
        ]
        for patcher in self.patchers:
            patcher.start()
        admin.save_database(admin.empty_database())

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_create_update_speed_limits(self):
        created = admin.create_user(
            {
                "password": "SpeedUser123",
                "days": 30,
                "vk_hash": "hash_one",
                "ports": "56000,56001,9000",
                "max_down_mbps": "10.5",
                "max_up_mbps": 5,
            }
        )
        self.assertEqual(created["max_down_mbps"], 10.5)
        self.assertEqual(created["max_up_mbps"], 5.0)
        updated = admin.update_user(
            {"current_password": "SpeedUser123", "max_down_mbps": 0, "max_up_mbps": "20"}
        )
        self.assertEqual(updated["max_down_mbps"], 0.0)
        self.assertEqual(updated["max_up_mbps"], 20.0)
        stored = admin.load_database()["passwords"]["SpeedUser123"]
        self.assertEqual(stored["max_down_mbps"], 0.0)
        self.assertEqual(stored["max_up_mbps"], 20.0)

    def test_rejects_bad_speed(self):
        with self.assertRaises(admin.ValidationError):
            admin.create_user(
                {
                    "password": "BadSpeedUser1",
                    "days": 30,
                    "vk_hash": "hash_one",
                    "ports": "56000,56001,9000",
                    "max_down_mbps": "-3",
                }
            )

    def test_bulk_create_with_speed_limits(self):
        result = admin.create_users_bulk(
            {
                "count": 2,
                "vk_hash": "hash_one",
                "days": 30,
                "ports": "56000,56001,9000",
                "max_down_mbps": 7,
                "max_up_mbps": 3,
            }
        )
        self.assertEqual(result["count"], 2)
        self.assertTrue(all(user["max_down_mbps"] == 7.0 for user in result["users"]))
        self.assertTrue(all(user["max_up_mbps"] == 3.0 for user in result["users"]))

    def test_wdtt_settings_defaults(self):
        settings = admin.load_wdtt_settings()
        self.assertEqual(settings["dtls_port"], 56000)
        self.assertEqual(settings["wg_port"], 56001)
        self.assertEqual(settings["wg_mtu"], 1280)
        self.assertEqual(settings["handshake_timeout_s"], 60)
        self.assertEqual(settings["max_dtls_per_device"], 0)

    def test_wdtt_settings_validation(self):
        with self.assertRaises(admin.ValidationError):
            admin.normalize_wdtt_settings({"dtls_port": 70000})
        with self.assertRaises(admin.ValidationError):
            admin.normalize_wdtt_settings({"dns": ""})
        with self.assertRaises(admin.ValidationError):
            admin.normalize_wdtt_settings({"wg_mtu": 100})
        with self.assertRaises(admin.ValidationError):
            admin.normalize_wdtt_settings({"admin_listen": "not-a-listen"})
        merged = admin.normalize_wdtt_settings({"wg_mtu": 1300, "max_dtls_per_device": 3})
        self.assertEqual(merged["wg_mtu"], 1300)
        self.assertEqual(merged["max_dtls_per_device"], 3)
        self.assertEqual(
            admin.normalize_wdtt_settings({"admin_listen": ""})["admin_listen"], ""
        )

    def test_wdtt_settings_seed_from_unit(self):
        admin.WDTT_UNIT_FILE.write_text(
            "[Service]\nExecStart=/usr/local/bin/wdtt-server -listen 127.0.0.1:57000 -wg-port 57001 -dns 9.9.9.9 -wg-mtu 1400\n",
            encoding="utf-8",
        )
        settings = admin.load_wdtt_settings()
        self.assertEqual(settings["listen_host"], "127.0.0.1")
        self.assertEqual(settings["dtls_port"], 57000)
        self.assertEqual(settings["wg_port"], 57001)
        self.assertEqual(settings["dns"], "9.9.9.9")
        self.assertEqual(settings["wg_mtu"], 1400)

    def _fake_systemctl(self, active=True):
        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            if command[:2] == ["systemctl", "is-active"]:
                return subprocess.CompletedProcess(command, 0 if active else 1, "", "")
            return subprocess.CompletedProcess(command, 0, "", "")

        return fake_run

    def test_wdtt_settings_save_rewrites_unit(self):
        admin.WDTT_EXTENSION_STATE.write_text(
            json.dumps({"marker": "wdtt-panel-extension-v10"}), encoding="utf-8"
        )
        admin.WDTT_UNIT_FILE.write_text(
            "[Service]\nExecStart=/usr/local/bin/wdtt-server -listen 0.0.0.0:56000 -wg-port 56001 -config-dir /etc/wdtt -password-file /etc/wdtt/main.password -dns 8.8.8.8 -admin-listen 0.0.0.0:56002 -admin-token-file /etc/wdtt/admin.token\n",
            encoding="utf-8",
        )
        payload = {
            "listen_host": "0.0.0.0",
            "dtls_port": 56000,
            "wg_port": 56001,
            "dns": "1.1.1.1",
            "direct_port": 0,
            "raw_port": 0,
            "max_users": 100,
            "admin_listen": "",
            "handshake_timeout_s": 45,
            "first_packet_timeout_s": 20,
            "wg_keepalive_s": 15,
            "wg_mtu": 1300,
            "stats_interval_s": 5,
            "max_dtls_per_device": 3,
            "online_window_s": 60,
        }
        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(
            admin, "run", side_effect=self._fake_systemctl(active=True)
        ):
            result = admin.save_wdtt_settings(payload)
        unit = admin.WDTT_UNIT_FILE.read_text(encoding="utf-8")
        self.assertIn("-dns 1.1.1.1", unit)
        self.assertIn("-handshake-timeout 45s", unit)
        self.assertIn("-wg-mtu 1300", unit)
        self.assertIn("-max-dtls-per-device 3", unit)
        self.assertNotIn("-admin-listen", unit)
        self.assertNotIn("-listen-direct", unit)
        self.assertIn("-password-file /etc/wdtt/main.password", unit)
        self.assertIn("-admin-token-file /etc/wdtt/admin.token", unit)
        self.assertTrue(result["restarted"])
        self.assertFalse(result["extension_pending"])
        self.assertEqual(admin.load_wdtt_settings()["max_users"], 100)

    def test_wdtt_settings_save_skips_tunables_without_v10(self):
        admin.WDTT_UNIT_FILE.write_text(
            "[Service]\nExecStart=/usr/local/bin/wdtt-server -listen 0.0.0.0:56000 -wg-port 56001\n",
            encoding="utf-8",
        )
        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(
            admin, "run", side_effect=self._fake_systemctl(active=False)
        ):
            result = admin.save_wdtt_settings({"wg_mtu": 1400, "handshake_timeout_s": 45})
        unit = admin.WDTT_UNIT_FILE.read_text(encoding="utf-8")
        self.assertNotIn("-wg-mtu", unit)
        self.assertNotIn("-handshake-timeout", unit)
        self.assertTrue(result["extension_pending"])
        self.assertFalse(result["restarted"])

    def test_change_panel_domain_schedules(self):
        calls = []

        def fake_transient(command, timeout=20):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0, "", "")

        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(
            admin, "run_transient_unit", side_effect=fake_transient
        ):
            result = admin.change_panel_domain({"host": "panel.example.com", "email": "admin@example.com"})
        self.assertTrue(result["scheduled"])
        self.assertEqual(result["host"], "panel.example.com")
        self.assertIn("--setenv=PANEL_HOST=panel.example.com", calls[0])
        self.assertIn("--setenv=PANEL_EMAIL=admin@example.com", calls[0])
        self.assertEqual(calls[0][-1], "change-domain")

    def test_change_panel_domain_rejects_bad_input(self):
        with self.assertRaises(admin.ValidationError):
            admin.change_panel_domain({"host": "http://bad host"})
        with self.assertRaises(admin.ValidationError):
            admin.change_panel_domain({"host": "panel.example.com", "email": "not-an-email"})


class ProxyPanelTests(unittest.TestCase):
    SAMPLE_CONFIG = """# wdtt-proxy — конфигурация
# комментарий, который должен сохраниться

PROXY_SCHEME="socks5"
PROXY_HOST="100.66.0.5"
PROXY_PORT="26666"
PROXY_USER="proxyuser"
PROXY_PASS="qwerty777LAMBERTED"
CAPTURE_IF="wdtt0"
SOURCE_CIDR="10.66.0.0/16"
EXCLUDE_CIDRS="10.66.0.0/16 127.0.0.0/8"
MARK="0x100"
TUN_NAME="sbtun0"
TUN_MTU="1280"
STACK="gvisor"
ROUTE_TABLE="100"
LOG_LEVEL="warn"
"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.proxy_dir = root / "wdtt-proxy"
        self.proxy_dir.mkdir()
        (self.proxy_dir / "wdtt-proxy.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        (self.proxy_dir / "config.env").write_text(self.SAMPLE_CONFIG, encoding="utf-8")
        self.script_calls = []

        def fake_script_run(action, timeout=180):
            self.script_calls.append(action)
            return subprocess.CompletedProcess([str(self.proxy_dir / "wdtt-proxy.sh"), action], 0, "ok", "")

        self.patchers = [
            mock.patch.object(admin, "PROXY_DIR", self.proxy_dir),
            mock.patch.object(admin, "SKIP_SYSTEMD", True),
            mock.patch.object(admin, "proxy_script_active", return_value=True),
            mock.patch.object(admin, "proxy_script_run", side_effect=fake_script_run),
        ]
        for patcher in self.patchers:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_status_parses_config_and_masks_password(self):
        status = admin.proxy_status({})
        self.assertTrue(status["installed"])
        self.assertTrue(status["active"])
        config = status["config"]
        self.assertEqual(config["PROXY_HOST"], "100.66.0.5")
        self.assertEqual(config["PROXY_PORT"], "26666")
        self.assertEqual(config["CAPTURE_IF"], "wdtt0")
        self.assertNotIn("PROXY_PASS", config)
        self.assertTrue(status["proxy_pass_set"])
        self.assertNotEqual(status["proxy_pass_hint"], "qwerty777LAMBERTED")
        self.assertNotIn("qwerty777LAMBERTED", admin.proxy_password_hint("qwerty777LAMBERTED"))
        self.assertEqual(status["extra"]["TUN_NAME"], "sbtun0")

    def test_status_when_not_installed(self):
        (self.proxy_dir / "wdtt-proxy.sh").unlink()
        status = admin.proxy_status({})
        self.assertFalse(status["installed"])
        self.assertFalse(status["active"])

    def test_dispatch_unknown_action(self):
        with self.assertRaises(admin.ValidationError):
            admin.dispatch({"action": "proxy.nope", "payload": {}})

    def test_save_updates_values_and_keeps_comments(self):
        result = admin.proxy_config_save(
            {
                "PROXY_HOST": "10.66.0.5",
                "PROXY_PORT": "8443",
                "PROXY_USER": "another",
                "PROXY_PASS": "",
                "CAPTURE_IF": "wgtest0",
                "SOURCE_CIDR": "10.77.0.0/16",
                "EXCLUDE_CIDRS": "10.77.0.0/16 169.254.0.0/16,10.10.10.1/32",
                "TUN_MTU": "1300",
                "LOG_LEVEL": "debug",
                "PROXY_SCHEME": "socks5",
            }
        )
        self.assertTrue(result["saved"])
        text = (self.proxy_dir / "config.env").read_text(encoding="utf-8")
        self.assertIn("PROXY_HOST=\"10.66.0.5\"", text)
        self.assertIn("PROXY_PORT=\"8443\"", text)
        self.assertIn("CAPTURE_IF=\"wgtest0\"", text)
        self.assertIn("SOURCE_CIDR=\"10.77.0.0/16\"", text)
        self.assertEqual(text.count('EXCLUDE_CIDRS='), 1)
        self.assertIn('EXCLUDE_CIDRS="10.77.0.0/16 169.254.0.0/16 10.10.10.1/32"', text)
        self.assertIn("TUN_MTU=\"1300\"", text)
        self.assertIn("LOG_LEVEL=\"debug\"", text)
        self.assertIn("PROXY_PASS=\"qwerty777LAMBERTED\"", text)
        self.assertIn("# комментарий, который должен сохраниться", text)
        self.assertIn('MARK="0x100"', text)
        if os.name == "posix":
            mode = (self.proxy_dir / "config.env").stat().st_mode & 0o777
            self.assertEqual(mode, 0o600)

    def test_save_without_password_updates_keeps_value(self):
        admin.proxy_config_save({"PROXY_HOST": "1.2.3.4"})
        text = (self.proxy_dir / "config.env").read_text(encoding="utf-8")
        self.assertIn('PROXY_HOST="1.2.3.4"', text)
        self.assertIn('PROXY_PASS="qwerty777LAMBERTED"', text)
        admin.proxy_config_save({"PROXY_PASS": "-", "PROXY_HOST": "1.2.3.4"})
        text = (self.proxy_dir / "config.env").read_text(encoding="utf-8")
        self.assertIn('PROXY_PASS=""', text)

    def test_save_validation_errors(self):
        cases = [
            {"PROXY_PORT": "99999"},
            {"PROXY_PORT": "abc"},
            {"PROXY_HOST": "1.2.3.4;5"},
            {"CAPTURE_IF": "bad iface"},
            {"SOURCE_CIDR": "not-a-cidr"},
            {"EXCLUDE_CIDRS": "10.0.0.0/16 9999.0.0.0/8"},
            {"EXCLUDE_CIDRS": "2001:db8::/32"},
            {"TUN_MTU": "400"},
            {"LOG_LEVEL": "loud"},
            {"PROXY_SCHEME": "shadowsocks"},
        ]
        for payload in cases:
            with self.assertRaises(admin.ValidationError, msg=payload):
                admin.proxy_config_save({**{"PROXY_HOST": "1.2.3.4"}, **payload})

    def test_save_requires_installation(self):
        (self.proxy_dir / "wdtt-proxy.sh").unlink()
        with self.assertRaises(admin.ValidationError):
            admin.proxy_config_save({"PROXY_HOST": "1.2.3.4"})
        with self.assertRaises(admin.ValidationError):
            admin.proxy_action({"action": "start"})

    def test_action_validates(self):
        with self.assertRaises(admin.ValidationError):
            admin.proxy_action({"action": "flush"})
        result = admin.proxy_action({"action": "restart"})
        self.assertEqual(result["action"], "restart")
        self.assertEqual(result["mode"], "script")
        self.assertEqual(self.script_calls, ["restart"])

    def test_action_uses_systemd_when_available(self):
        with mock.patch.object(admin, "proxy_uses_systemd", return_value=True), mock.patch.object(
            admin, "proxy_unit_installed", return_value=True
        ), mock.patch.object(admin, "run", return_value=subprocess.CompletedProcess([], 0, "", "")):
            result = admin.proxy_action({"action": "start"})
        self.assertEqual(result["mode"], "systemd")
        self.assertEqual(self.script_calls, [])

    def test_status_marks_script_mode_in_docker(self):
        status = admin.proxy_status({})
        self.assertEqual(status["mode"], "script")
        self.assertEqual(status["install_hint"], "")
        self.assertTrue(status["unit_installed"])

    def test_status_hints_installation_when_absent(self):
        (self.proxy_dir / "wdtt-proxy.sh").unlink()
        status = admin.proxy_status({})
        self.assertFalse(status["installed"])
        self.assertIn("install-wdtt-proxy", status["install_hint"])

    def test_install_schedules_transient_unit_on_systemd_host(self):
        (self.proxy_dir / "wdtt-proxy.sh").unlink()
        installer = self.proxy_dir / "install.sh"
        installer.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        with mock.patch.object(admin, "PROXY_INSTALL_COMMAND", installer), mock.patch.object(
            admin, "proxy_uses_systemd", return_value=True
        ), mock.patch.object(admin, "run_transient_unit", return_value=subprocess.CompletedProcess([], 0, "", "")) as transient:
            result = admin.proxy_install({})
        self.assertTrue(result["scheduled"])
        command = transient.call_args.args[0]
        self.assertEqual(command[-1], "install-wdtt-proxy")
        self.assertIn(str(installer), command)

    def test_install_runs_installer_in_script_mode(self):
        (self.proxy_dir / "wdtt-proxy.sh").unlink()
        installer = self.proxy_dir / "install.sh"
        installer.write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        def fake_run(command, **kwargs):
            (self.proxy_dir / "wdtt-proxy.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")
            return subprocess.CompletedProcess(command, 0, "installed", "")

        with mock.patch.object(admin, "PROXY_INSTALL_COMMAND", installer), mock.patch.object(admin, "run", side_effect=fake_run):
            result = admin.proxy_install({})
        self.assertFalse(result["scheduled"])
        self.assertTrue(result["installed"])

    def test_install_is_idempotent(self):
        result = admin.proxy_install({})
        self.assertTrue(result["already"])
        self.assertEqual(result["state"], "present")

    def test_install_requires_installer_file(self):
        (self.proxy_dir / "wdtt-proxy.sh").unlink()
        missing = self.proxy_dir / "nope-install.sh"
        with mock.patch.object(admin, "PROXY_INSTALL_COMMAND", missing):
            with self.assertRaises(admin.AdminError):
                admin.proxy_install({})

    def test_logs_include_proxy_source(self):
        payload = {"limit": 20, "source": "proxy"}
        data = admin.journal_logs(payload)
        self.assertEqual(data["source"], "proxy")
        self.assertEqual(data["units"], [])
        self.assertEqual(data["lines"], [])
        with self.assertRaises(admin.ValidationError):
            admin.journal_logs({"source": "bogus"})

    def test_logs_read_singbox_file_without_systemd(self):
        runtime = Path(self.temp.name) / "run"
        runtime.mkdir()
        (runtime / "sing-box.log").write_text("первая\nвторая\n", encoding="utf-8")
        config = self.proxy_dir / "config.env"
        config.write_text(
            config.read_text(encoding="utf-8") + f'RUNTIME_DIR="{runtime.as_posix()}"\n', encoding="utf-8"
        )
        data = admin.journal_logs({"limit": 20, "source": "proxy"})
        self.assertEqual(data["lines"], ["первая", "вторая"])
        self.assertEqual(data["units"], [])


if __name__ == "__main__":
    unittest.main()
