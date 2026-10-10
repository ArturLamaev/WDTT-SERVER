"""PR-B 1.21.4: SIGHUP ядру после записи БД в Docker (без затирания юзеров)."""
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from wdtt_panel import admin

NEEDS_POSIX = unittest.skipUnless(hasattr(signal, "SIGHUP"), "SIGHUP только на POSIX")


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class DockerPidTests(unittest.TestCase):
    def test_pid_from_pidof(self):
        with mock.patch.object(admin, "run", return_value=completed(["pidof"], 0, "118 120\n", "")):
            self.assertEqual(admin.docker_wdtt_pid(), 118)

    def test_pid_fallback_to_supervisor_file(self):
        with mock.patch.object(admin, "run", return_value=completed(["pidof"], 1, "", "")), mock.patch.object(
            admin.Path, "read_text", return_value="77\n"
        ):
            self.assertEqual(admin.docker_wdtt_pid(), 77)

    def test_no_pid_when_nothing_found(self):
        with mock.patch.object(admin, "run", side_effect=FileNotFoundError("pidof")), mock.patch.object(
            admin.Path, "read_text", side_effect=OSError
        ):
            self.assertIsNone(admin.docker_wdtt_pid())


class ReloadKernelTests(unittest.TestCase):
    @NEEDS_POSIX
    def test_hup_sent_to_kernel(self):
        with mock.patch.object(admin, "docker_wdtt_pid", return_value=118), mock.patch.object(
            admin.os, "kill"
        ) as kill:
            self.assertTrue(admin.reload_docker_kernel())
        kill.assert_called_once_with(118, signal.SIGHUP)

    def test_missing_kernel_is_not_fatal(self):
        with mock.patch.object(admin, "docker_wdtt_pid", return_value=None), mock.patch.object(
            admin.os, "kill"
        ) as kill:
            self.assertFalse(admin.reload_docker_kernel())
        kill.assert_not_called()

    @NEEDS_POSIX
    def test_failed_kill_is_not_fatal(self):
        with mock.patch.object(admin, "docker_wdtt_pid", return_value=118), mock.patch.object(
            admin.os, "kill", side_effect=OSError("denied")
        ):
            self.assertFalse(admin.reload_docker_kernel())

    def test_reload_without_sighup_support(self):
        stub = mock.Mock(spec=[])
        with mock.patch.object(admin, "signal", stub), mock.patch.object(
            admin, "docker_wdtt_pid", return_value=118
        ), mock.patch.object(admin.os, "kill") as kill:
            self.assertFalse(admin.reload_docker_kernel())
        kill.assert_not_called()


class MutateSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db_file = root / "passwords.json"
        self.backups = root / "backups"
        self.patchers = [
            mock.patch.object(admin, "DB_FILE", self.db_file),
            mock.patch.object(admin, "BACKUP_DIR", self.backups),
        ]
        for patcher in self.patchers:
            patcher.start()
        admin.save_database(admin.empty_database())

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp.cleanup()

    def test_docker_mutate_reloads_kernel(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin, "reload_docker_kernel", return_value=True
        ) as reload:
            result = admin.mutate_database("test", lambda data: data["passwords"].__setitem__("K1", {}) or "done")
        self.assertEqual(result, "done")
        reload.assert_called_once_with()

    def test_docker_mutate_survives_failed_reload(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin, "reload_docker_kernel", return_value=False
        ):
            result = admin.mutate_database(
                "test", lambda data: data["passwords"].__setitem__("K1", {}) or "kept"
            )
        self.assertEqual(result, "kept")
        self.assertIn("K1", admin.load_database()["passwords"])

    def test_bare_metal_mutate_does_not_touch_reload(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(
            admin, "service_active", return_value=True
        ), mock.patch.object(admin, "service_action") as action, mock.patch.object(
            admin, "reload_docker_kernel"
        ) as reload:
            result = admin.mutate_database("test", lambda data: "kept")
        self.assertEqual(result, "kept")
        reload.assert_not_called()
        action.assert_any_call("stop")
        action.assert_any_call("start")

    def test_create_user_reloads_kernel_in_docker(self):
        payload = {
            "password": "DockerUser123",
            "days": 30,
            "vk_hash": "hash_abc",
            "ports": "56000,56001,9000",
        }
        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin, "reload_docker_kernel", return_value=True
        ) as reload, mock.patch.object(
            admin, "PANEL_LABELS_FILE", Path(self.temp.name) / "labels.json"
        ):
            created = admin.create_user(payload)
        self.assertEqual(created["password"], "DockerUser123")
        reload.assert_called_once_with()
        self.assertIn("DockerUser123", admin.load_database()["passwords"])


if __name__ == "__main__":
    unittest.main()
