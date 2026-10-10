"""PR-A 1.21.3: честный статус сервиса в Docker (pidof+HTTPS вместо systemctl)."""
import socket
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from wdtt_panel import admin

ROOT = Path(__file__).resolve().parents[1]


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


class DockerProcessTests(unittest.TestCase):
    def test_process_running_when_pidof_finds_it(self):
        with mock.patch.object(
            admin, "run", return_value=completed(["pidof", "wdtt-server"], 0, "118\n", "")
        ):
            self.assertTrue(admin.docker_wdtt_process_running())

    def test_process_not_running_when_pidof_fails(self):
        with mock.patch.object(
            admin, "run", return_value=completed(["pidof", "wdtt-server"], 1, "", "")
        ):
            self.assertFalse(admin.docker_wdtt_process_running())

    def test_process_not_running_when_pidof_missing(self):
        with mock.patch.object(admin, "run", side_effect=FileNotFoundError("pidof")):
            self.assertFalse(admin.docker_wdtt_process_running())


class DockerHttpsTests(unittest.TestCase):
    def _fake_tls(self, payload: bytes):
        tls = mock.MagicMock()
        chunks = [payload[i:i + 1024] for i in range(0, len(payload), 1024)] + [b""]
        tls.recv.side_effect = chunks
        tls.__enter__.return_value = tls
        tls.__exit__.return_value = False
        return tls

    def _check(self, status: bytes):
        raw = mock.MagicMock()
        raw.__enter__.return_value = raw
        raw.__exit__.return_value = False
        context = mock.MagicMock()
        context.wrap_socket.return_value = self._fake_tls(b"HTTP/1.0 " + status + b" OK\r\n\r\n")
        with mock.patch.object(admin.socket, "create_connection", return_value=raw), mock.patch.object(
            admin.ssl, "_create_unverified_context", return_value=context
        ):
            return admin.docker_panel_https_ok(9999, "/secret-path/")

    def test_https_ok_on_200(self):
        self.assertTrue(self._check(b"200"))

    def test_https_ok_on_login_redirect(self):
        self.assertTrue(self._check(b"303"))

    def test_https_not_ok_on_404(self):
        self.assertFalse(self._check(b"404"))

    def test_https_not_ok_on_refused(self):
        with mock.patch.object(
            admin.socket, "create_connection", side_effect=ConnectionRefusedError
        ), mock.patch.object(admin.ssl, "_create_unverified_context", return_value=mock.MagicMock()):
            self.assertFalse(admin.docker_panel_https_ok(9999, "/x/"))

    def test_https_rejects_bad_port(self):
        self.assertFalse(admin.docker_panel_https_ok(0, "/x/"))
        self.assertFalse(admin.docker_panel_https_ok(70000, "/x/"))


class DockerServiceActiveTests(unittest.TestCase):
    def _active(self, process, https):
        with mock.patch.object(admin, "docker_wdtt_process_running", return_value=process), mock.patch.object(
            admin, "docker_panel_https_ok", return_value=https
        ):
            return admin.docker_service_active(9999, "/x/")

    def test_active_needs_process_and_https(self):
        self.assertTrue(self._active(True, True))

    def test_inactive_without_process(self):
        self.assertFalse(self._active(False, True))

    def test_inactive_without_https(self):
        self.assertFalse(self._active(True, False))


class IpForwardTests(unittest.TestCase):
    def test_sysctl_first(self):
        with mock.patch.object(admin, "run", return_value=completed(["sysctl"], 0, "1\n", "")):
            self.assertEqual(admin.read_ip_forward(), "1")

    def test_proc_fallback_when_sysctl_fails(self):
        with mock.patch.object(admin, "run", return_value=completed(["sysctl"], 1, "", "err")), mock.patch.object(
            admin.Path, "read_text", return_value="0\n"
        ):
            self.assertEqual(admin.read_ip_forward(), "0")

    def test_unknown_when_nothing_available(self):
        with mock.patch.object(admin, "run", side_effect=FileNotFoundError("sysctl")), mock.patch.object(
            admin.Path, "read_text", side_effect=OSError
        ):
            self.assertEqual(admin.read_ip_forward(), "unknown")


class LocalTlsTests(unittest.TestCase):
    def test_checks_listener_without_systemd_gate(self):
        # Раньше при SKIP_SYSTEMD был ранний return False — теперь ss проверяем всегда.
        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            self.assertEqual(command[0], "ss")
            return completed(command, 0, "LISTEN 0 4096 0.0.0.0:9999", "")

        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin, "run", side_effect=fake_run
        ), mock.patch.object(admin.socket, "create_connection", side_effect=ConnectionRefusedError):
            result = admin.local_tls_status("example.com", 9999)
        self.assertTrue(result["listening"])
        self.assertFalse(result["local_tls_ok"])

    def test_empty_host_or_port(self):
        self.assertEqual(
            admin.local_tls_status("", 9999),
            {"local_tls_ok": False, "listening": False, "error": ""},
        )


class OverviewDockerTests(unittest.TestCase):
    def _overview(self, active: bool):
        payload = {
            "certificate_path": "",
            "tls_mode": "self-signed",
            "public_host": "vpn.example.com",
            "https_port": 9999,
            "base_path": "/secret/",
        }
        fake_db = {"passwords": {}, "devices": {}, "main_password": "x"}
        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin, "load_database", return_value=fake_db
        ), mock.patch.object(admin, "list_users", return_value={"users": [], "admins": []}), mock.patch.object(
            admin, "read_stats", return_value={}
        ), mock.patch.object(
            admin,
            "local_tls_status",
            return_value={"local_tls_ok": True, "listening": True, "error": ""},
        ), mock.patch.object(
            admin, "docker_service_active", return_value=active
        ) as probe, mock.patch.object(
            admin, "load_wdtt_settings", return_value={}
        ), mock.patch.object(
            admin, "wdtt_extensions_are_verified", return_value=False
        ):
            result = admin.overview(payload)
        return result, probe

    def test_overview_uses_docker_probe_instead_of_systemctl(self):
        result, probe = self._overview(True)
        probe.assert_called_once_with(9999, "/secret/")
        self.assertTrue(result["service"]["active"])
        self.assertIn("docker", result["service"])
        self.assertIn(result["service"]["ip_forward"], {"0", "1", "unknown"})

    def test_overview_inactive_when_probe_fails(self):
        result, _ = self._overview(False)
        self.assertFalse(result["service"]["active"])

    def test_mutate_path_still_uses_systemd_truth(self):
        # PR-A не меняет was_active: при SKIP service_active() всё ещё False (это чинит PR-B).
        with mock.patch.object(admin, "SKIP_SYSTEMD", True):
            self.assertFalse(admin.service_active())
            self.assertFalse(admin.service_exists())

    def test_is_docker_flag(self):
        with mock.patch.dict("os.environ", {"WDTT_DOCKER": "1"}):
            self.assertTrue(admin.is_docker())
        with mock.patch.dict("os.environ", {}, clear=True):
            self.assertFalse(admin.is_docker())


class WiringTests(unittest.TestCase):
    def test_app_passes_base_path_to_overview(self):
        src = (ROOT / "wdtt_panel" / "app.py").read_text(encoding="utf-8")
        self.assertIn('payload["base_path"]', src)

    def test_frontend_labels_docker_runtime(self):
        js = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn("runtime (docker)", js)
        self.assertIn("systemd unit", js)

    def test_gitattributes_enforces_lf(self):
        # CRLF из Windows-checkout ломает sed в docker/entrypoint.sh.
        attrs = (ROOT.parent / ".gitattributes").read_text(encoding="utf-8")
        self.assertIn("eol=lf", attrs)
        self.assertIn("*.png binary", attrs)


if __name__ == "__main__":
    unittest.main()
