"""Xray в Docker: демон под супервизором, честный статус/логи, рестарт при сейве."""
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from wdtt_panel import admin

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class DockerXrayPidTests(unittest.TestCase):
    def test_pid_from_supervisor_file(self):
        with TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "xray.pid"
            pid_file.write_text("4242\n", encoding="utf-8")
            with mock.patch.object(admin, "XRAY_PID_FILE", pid_file), mock.patch.object(
                admin, "_proc_cmdline", return_value="xray\x00run\x00-c\x00cfg"
            ), mock.patch.object(admin, "run") as probed:
                self.assertEqual(admin.docker_xray_pid(), 4242)
            probed.assert_not_called()

    def test_stale_pidfile_falls_back_to_pidof(self):
        with TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "xray.pid"
            pid_file.write_text("9999\n", encoding="utf-8")
            with mock.patch.object(admin, "XRAY_PID_FILE", pid_file), mock.patch.object(
                admin, "_proc_cmdline", return_value=""
            ), mock.patch.object(
                admin, "run", return_value=completed(["pidof", "xray"], 0, "555\n", "")
            ):
                self.assertEqual(admin.docker_xray_pid(), 555)

    def test_no_pid_when_nothing_found(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(admin, "XRAY_PID_FILE", Path(tmp) / "nope.pid"), mock.patch.object(
                admin, "run", return_value=completed(["pidof", "xray"], 1, "", "")
            ):
                self.assertIsNone(admin.docker_xray_pid())
                self.assertFalse(admin.docker_xray_active())


class DockerXrayLogTests(unittest.TestCase):
    def test_tail_reads_last_lines(self):
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "xray.log"
            log.write_text("\n".join(f"line{i}" for i in range(100)) + "\n", encoding="utf-8")
            with mock.patch.object(admin, "XRAY_LOG_FILE", log):
                tail = admin.xray_docker_log_tail(5)
        self.assertEqual(tail, [f"line{i}" for i in range(95, 100)])

    def test_missing_log_is_empty(self):
        with TemporaryDirectory() as tmp:
            with mock.patch.object(admin, "XRAY_LOG_FILE", Path(tmp) / "nope.log"):
                self.assertEqual(admin.xray_docker_log_tail(), [])


class DockerXrayVersionTests(unittest.TestCase):
    def test_version_from_binary(self):
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/xray"), mock.patch.object(
            admin, "run", return_value=completed(["xray", "version"], 0, "Xray 25.1.1\nsecond", "")
        ):
            self.assertEqual(admin.xray_version_string(), "Xray 25.1.1")

    def test_no_binary_no_version(self):
        with mock.patch.object(admin.shutil, "which", return_value=None):
            self.assertEqual(admin.xray_version_string(), "")


class RestartDockerXrayTests(unittest.TestCase):
    def test_restart_sends_sigterm(self):
        import signal as signal_module

        with mock.patch.object(admin, "docker_xray_pid", return_value=4242), mock.patch.object(
            admin.os, "kill"
        ) as kill:
            self.assertTrue(admin.restart_docker_xray())
        kill.assert_called_once_with(4242, signal_module.SIGTERM)

    def test_no_pid_no_kill(self):
        with mock.patch.object(admin, "docker_xray_pid", return_value=None), mock.patch.object(
            admin.os, "kill"
        ) as kill:
            self.assertFalse(admin.restart_docker_xray())
        kill.assert_not_called()

    def test_failed_kill_is_not_fatal(self):
        with mock.patch.object(admin, "docker_xray_pid", return_value=4242), mock.patch.object(
            admin.os, "kill", side_effect=OSError("denied")
        ):
            self.assertFalse(admin.restart_docker_xray())


class DockerXrayStatusTests(unittest.TestCase):
    def test_status_reports_daemon_version_and_logs(self):
        with TemporaryDirectory() as tmp:
            log = Path(tmp) / "xray.log"
            log.write_text("2026/10/14 started\n", encoding="utf-8")
            cfg = Path(tmp) / "xray-config.json"
            cfg.write_text("{}", encoding="utf-8")
            with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
                admin, "docker_xray_active", return_value=True
            ), mock.patch.object(admin.shutil, "which", return_value="/usr/bin/xray"), mock.patch.object(
                admin, "run", return_value=completed(["xray", "version"], 0, "Xray 25.1.1\n", "")
            ), mock.patch.object(admin, "XRAY_CONFIG", cfg), mock.patch.object(
                admin, "XRAY_LOG_FILE", log
            ), mock.patch.object(admin, "xray_gateway_status", return_value={"enabled": False}):
                result = admin.xray_status({})
        self.assertTrue(result["active"])
        self.assertTrue(result["installed"])
        self.assertEqual(result["version"], "Xray 25.1.1")
        self.assertTrue(result["config_exists"])
        self.assertIn("2026/10/14 started", result["logs"])


class PersistDockerXrayTests(unittest.TestCase):
    def test_save_restarts_daemon_in_docker(self):
        with TemporaryDirectory() as tmp:
            settings_file = Path(tmp) / "xray-settings.json"
            config_file = Path(tmp) / "xray-config.json"
            with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
                admin.shutil, "which", return_value="/usr/bin/xray"
            ), mock.patch.object(admin, "XRAY_SETTINGS", settings_file), mock.patch.object(
                admin, "XRAY_CONFIG", config_file
            ), mock.patch.object(admin, "restart_docker_xray", return_value=True) as restarted, mock.patch.object(
                admin, "run", return_value=completed(["xray", "run"], 0, "", "")
            ):
                admin.persist_xray_configuration({"enabled": True}, {"inbounds": []})
            restarted.assert_called_once_with()
            self.assertTrue(config_file.is_file())

    def test_validate_runs_test_in_docker(self):
        with TemporaryDirectory() as tmp:
            config_file = Path(tmp) / "xray-config.json"
            with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
                admin.shutil, "which", return_value="/usr/bin/xray"
            ), mock.patch.object(admin, "XRAY_CONFIG", config_file), mock.patch.object(
                admin, "run", return_value=completed(["xray", "run"], 1, "", "bad inbound")
            ):
                with self.assertRaises(admin.ValidationError):
                    admin.xray_validate_config({"inbounds": []})


class SupervisordXrayTests(unittest.TestCase):
    def test_supervisord_runs_xray_loop(self):
        text = (REPO / "docker" / "supervisord.sh").read_text(encoding="utf-8")
        self.assertIn("run_xray", text)
        self.assertIn("xray run -c", text)
        self.assertIn("xray.pid", text)
        self.assertIn("xray.log", text)


if __name__ == "__main__":
    unittest.main()
