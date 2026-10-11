"""Direct/RAW-режимы ядра: ss-проверка листенеров, поля overview, проводка UI/docker."""
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from wdtt_panel import admin

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class ListenerProbeTests(unittest.TestCase):
    def test_open_when_ss_sees_unconn(self):
        out = "State  Recv-Q Send-Q Local Address:Port\nUNCONN 0 0 0.0.0.0:9000"
        with mock.patch.object(admin, "run", return_value=completed(["ss"], 0, out, "")):
            self.assertTrue(admin.wdtt_listener_open(9000))

    def test_closed_when_ss_finds_nothing(self):
        with mock.patch.object(admin, "run", return_value=completed(["ss"], 0, "", "")):
            self.assertFalse(admin.wdtt_listener_open(9000))

    def test_closed_when_ss_fails(self):
        with mock.patch.object(admin, "run", return_value=completed(["ss"], 1, "", "err")):
            self.assertFalse(admin.wdtt_listener_open(9000))

    def test_closed_when_ss_missing(self):
        with mock.patch.object(admin, "run", side_effect=FileNotFoundError("ss")):
            self.assertFalse(admin.wdtt_listener_open(9000))

    def test_disabled_ports_never_probe(self):
        with mock.patch.object(admin, "run") as probed:
            for bad in (0, -1, 70000, "9000x", None):
                self.assertFalse(admin.wdtt_listener_open(bad))
        probed.assert_not_called()


class OverviewModesTests(unittest.TestCase):
    def _overview(self):
        payload = {
            "certificate_path": "",
            "tls_mode": "self-signed",
            "public_host": "vpn.example.com",
            "https_port": 9999,
            "base_path": "/secret/",
        }
        fake_db = {"passwords": {}, "devices": {}, "main_password": "x"}

        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            if command[0] == "ss":
                port = command[-1].rpartition(":")[2]
                if port == "9000":
                    return completed(command, 0, "UNCONN 0 0 0.0.0.0:9000", "")
                return completed(command, 0, "", "")
            if command[0] == "sysctl":
                return completed(command, 0, "1\n", "")
            raise AssertionError(f"unexpected command {command}")

        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin, "load_database", return_value=fake_db
        ), mock.patch.object(admin, "list_users", return_value={"users": [], "admins": []}), mock.patch.object(
            admin, "read_stats", return_value={}
        ), mock.patch.object(
            admin,
            "local_tls_status",
            return_value={"local_tls_ok": True, "listening": True, "error": ""},
        ), mock.patch.object(
            admin, "docker_service_active", return_value=True
        ), mock.patch.object(
            admin, "load_wdtt_settings", return_value={"direct_port": 9000, "raw_port": 9001}
        ), mock.patch.object(
            admin, "wdtt_extensions_are_verified", return_value=False
        ), mock.patch.object(
            admin, "run", side_effect=fake_run
        ):
            return admin.overview(payload)

    def test_overview_reports_listener_states(self):
        result = self._overview()
        self.assertTrue(result["wdtt"]["direct_listening"])
        self.assertFalse(result["wdtt"]["raw_listening"])
        self.assertEqual(result["wdtt"]["settings"]["direct_port"], 9000)


class ModesWiringTests(unittest.TestCase):
    def test_frontend_renders_listener_rows_and_hints(self):
        js = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("wdttListenerRows", js)
        self.assertIn("renderWdttModes", js)
        self.assertIn("Direct UDP", js)
        html = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertIn("wdtt-modes-info", html)
        self.assertIn("wdtt-direct-port", html)
        self.assertIn("wdtt-raw-port", html)

    def test_entrypoint_opens_direct_raw_inputs(self):
        entrypoint = (REPO / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("WDTT_DIRECT_PORT", entrypoint)
        self.assertIn("WDTT_RAW_PORT", entrypoint)
        self.assertIn("--dport \"$extra_port\"", entrypoint)

    def test_compose_documents_direct_raw_publish(self):
        compose = (REPO / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
        self.assertIn("WDTT_DIRECT_PORT", compose)
        self.assertIn("WDTT_RAW_PORT", compose)


if __name__ == "__main__":
    unittest.main()
