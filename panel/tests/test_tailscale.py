"""Tailscale в панели: статус, подключение, проверка (mock'и, без демона)."""
import json
import subprocess
import unittest
from unittest import mock

from wdtt_panel import admin


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


ONLINE_STATUS = json.dumps({
    "BackendState": "Running",
    "MagicDNSSuffix": "tail12345.ts.net",
    "Self": {
        "HostName": "wdtt-server",
        "DNSName": "wdtt-server.tail12345.ts.net.",
        "TailscaleIPs": ["100.64.0.5", "fd7a:115c:a1e0::5"],
    },
})


class NormalizeTests(unittest.TestCase):
    def test_valid_auth_key_passes(self):
        key = "tskey-auth-abcDEF123-xyz789XYZ012"
        self.assertEqual(admin.normalize_tailscale_auth_key(key), key)

    def test_auth_key_rejects_garbage(self):
        for bad in ["secret", "tskey-auth-", "tskey-auth-a b", "bearer xyz", "tskey-auth-" + "x" * 201]:
            with self.assertRaises(admin.ValidationError, msg=bad):
                admin.normalize_tailscale_auth_key(bad)

    def test_empty_auth_key_means_keep_stored(self):
        self.assertEqual(admin.normalize_tailscale_auth_key(""), "")

    def test_mask_hides_middle(self):
        self.assertEqual(admin.mask_tailscale_auth_key(""), "")
        self.assertEqual(admin.mask_tailscale_auth_key("short"), "***")
        self.assertEqual(
            admin.mask_tailscale_auth_key("tskey-auth-abcdef123456"),
            "tskey-auth...3456",
        )

    def test_hostname_normalizes(self):
        self.assertEqual(admin.normalize_tailscale_hostname("WDTT_Server 1"), "wdtt-server-1")
        self.assertEqual(admin.normalize_tailscale_hostname(""), "")
        self.assertEqual(admin.normalize_tailscale_hostname("---"), "")

    def test_hostname_truncates_to_63(self):
        self.assertEqual(len(admin.normalize_tailscale_hostname("a" * 100)), 63)

    def test_default_hostname_is_sane(self):
        name = admin.default_tailscale_hostname()
        self.assertRegex(name, r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class StatusTests(unittest.TestCase):
    def test_not_installed(self):
        with mock.patch.object(admin.shutil, "which", return_value=None):
            result = admin.tailscale_status({})
        self.assertFalse(result["installed"])
        self.assertFalse(result["daemon"])
        self.assertFalse(result["online"])
        self.assertFalse(result["authkey_set"])

    def test_daemon_down(self):
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "run", return_value=completed(["tailscale", "status", "--json"], 1, "", "stopped")
        ), mock.patch.object(admin, "read_private_text", return_value=None):
            result = admin.tailscale_status({})
        self.assertTrue(result["installed"])
        self.assertFalse(result["daemon"])
        self.assertFalse(result["online"])

    def test_online_parses_self(self):
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "run", return_value=completed(["tailscale", "status", "--json"], 0, ONLINE_STATUS, "")
        ), mock.patch.object(admin, "read_private_text", return_value="tskey-auth-abcDEF123-xyz"):
            result = admin.tailscale_status({})
        self.assertTrue(result["online"])
        self.assertEqual(result["tailscale_ips"], ["100.64.0.5", "fd7a:115c:a1e0::5"])
        self.assertEqual(result["dns_name"], "wdtt-server.tail12345.ts.net.")
        self.assertEqual(result["tailnet"], "tail12345.ts.net")
        self.assertTrue(result["authkey_set"])
        self.assertIn("...", result["authkey_hint"])

    def test_stopped_backend_is_offline(self):
        stopped = json.dumps({"BackendState": "Stopped", "Self": {}})
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "run", return_value=completed(["tailscale", "status", "--json"], 0, stopped, "")
        ), mock.patch.object(admin, "read_private_text", return_value=None):
            result = admin.tailscale_status({})
        self.assertTrue(result["daemon"])
        self.assertFalse(result["online"])

    def test_broken_json_is_offline(self):
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "run", return_value=completed(["tailscale", "status", "--json"], 0, "not json", "")
        ), mock.patch.object(admin, "read_private_text", return_value=None):
            result = admin.tailscale_status({})
        self.assertTrue(result["daemon"])
        self.assertFalse(result["online"])


class ConnectTests(unittest.TestCase):
    def _run_ok(self, seen):
        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            seen.append(command)
            if command[:2] == ["tailscale", "status"]:
                return completed(command, 0, ONLINE_STATUS, "")
            return completed(command, 0, "", "")
        return fake_run

    def test_connect_without_binary(self):
        with mock.patch.object(admin.shutil, "which", return_value=None):
            with self.assertRaises(admin.ValidationError):
                admin.tailscale_connect({"auth_key": "tskey-auth-abcDEF123-xyz"})

    def test_connect_without_key(self):
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "read_private_text", return_value=None
        ):
            with self.assertRaises(admin.ValidationError):
                admin.tailscale_connect({})

    def test_connect_passes_key_hostname_dns(self):
        seen = []
        written = {}
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "read_private_text", return_value=None
        ), mock.patch.object(admin, "run", side_effect=self._run_ok(seen)), mock.patch.object(
            admin, "write_private_text", side_effect=lambda path, value: written.setdefault(str(path), value)
        ), mock.patch.object(admin, "save_tailscale_settings") as saved:
            result = admin.tailscale_connect({
                "auth_key": "tskey-auth-abcDEF123-xyz", "hostname": "My Node!", "accept_dns": False,
            })
        up = [command for command in seen if command[:2] == ["tailscale", "up"]]
        self.assertEqual(len(up), 1)
        self.assertIn("--auth-key=tskey-auth-abcDEF123-xyz", up[0])
        self.assertIn("--hostname=my-node", up[0])
        self.assertIn("--accept-dns=false", up[0])
        self.assertTrue(result["connected"])
        self.assertTrue(result["online"])
        self.assertIn("tskey-auth-abcDEF123-xyz", written.get(str(admin.TAILSCALE_AUTHKEY_FILE), ""))
        saved.assert_called_once_with({"hostname": "my-node", "accept_dns": False})

    def test_connect_reuses_stored_key(self):
        seen = []
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "read_private_text", return_value="tskey-auth-storedKEY-1234567890"
        ), mock.patch.object(admin, "run", side_effect=self._run_ok(seen)), mock.patch.object(
            admin, "write_private_text"
        ), mock.patch.object(admin, "save_tailscale_settings"):
            admin.tailscale_connect({})
        up = [command for command in seen if command[:2] == ["tailscale", "up"]]
        self.assertIn("--auth-key=tskey-auth-storedKEY-1234567890", up[0])

    def test_disconnect_and_logout(self):
        for action, argv in (("tailscale_disconnect", ["tailscale", "down"]),
                             ("tailscale_logout", ["tailscale", "logout"])):
            seen = []
            with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
                admin, "run", side_effect=self._run_ok(seen)
            ), mock.patch.object(admin, "tailscale_status", return_value={"online": False}):
                result = getattr(admin, action)({})
            self.assertIn(argv, seen)
            self.assertFalse(result["connected"])

    def test_disconnect_without_binary(self):
        with mock.patch.object(admin.shutil, "which", return_value=None):
            with self.assertRaises(admin.ValidationError):
                admin.tailscale_disconnect({})


class CheckTests(unittest.TestCase):
    def test_check_requires_online(self):
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "tailscale_status_raw", return_value={"online": False}
        ):
            with self.assertRaises(admin.ValidationError):
                admin.tailscale_check({})

    def test_check_parses_netcheck(self):
        report = json.dumps({"UDP": True, "NearestDERP": "hel", "DERPLatency": {"hel": 0.012, "fra": 0.03}})
        with mock.patch.object(admin.shutil, "which", return_value="/usr/bin/tailscale"), mock.patch.object(
            admin, "tailscale_status_raw", return_value={"online": True}
        ), mock.patch.object(admin, "run", return_value=completed(["tailscale", "netcheck"], 0, report, "")), mock.patch.object(
            admin, "tailscale_status", return_value={"tailscale_ips": ["100.64.0.5"], "dns_name": "n.", "hostname": "n"}
        ):
            result = admin.tailscale_check({})
        self.assertTrue(result["ok"])
        self.assertTrue(result["udp"])
        self.assertEqual(result["nearest_derp"], "hel")
        self.assertEqual(result["latency_ms"], 12.0)

    def test_check_without_binary(self):
        with mock.patch.object(admin.shutil, "which", return_value=None):
            with self.assertRaises(admin.ValidationError):
                admin.tailscale_check({})


class InstallTests(unittest.TestCase):
    def test_install_in_docker_returns_test_state(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", True):
            self.assertEqual(
                admin.schedule_tailscale_runtime({}), {"scheduled": True, "state": "test"}
            )

    def test_install_schedules_transient_unit(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", False), mock.patch.object(
            admin, "run_transient_unit", return_value=completed(["systemd-run"], 0, "", "")
        ) as scheduled:
            result = admin.schedule_tailscale_runtime({})
        self.assertTrue(result["scheduled"])
        self.assertIn("install-tailscale-runtime", scheduled.call_args[0][0])


if __name__ == "__main__":
    unittest.main()
