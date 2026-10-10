"""PR-C 1.22.0: рабочая смена домена в Docker (env каноничен, кнопка не no-op)."""
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

from wdtt_panel import admin

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def completed(args, code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(args, code, stdout, stderr)


def base_config(**overrides):
    config = {
        "version": "1.22.0",
        "username": "admin",
        "password_hash": "x",
        "session_secret": "y",
        "base_path": "/secret-path/",
        "public_host": "old.example.com",
        "https_port": 9999,
        "listen_host": "127.0.0.1",
        "listen_port": 8787,
        "certificate_path": "",
        "tls_mode": "letsencrypt",
        "certificate_email": "",
    }
    config.update(overrides)
    return config


class ChangeDomainThreadTests(unittest.TestCase):
    def test_docker_branch_spawns_worker(self):
        started = {}

        class FakeThread:
            def __init__(self, target=None, args=(), daemon=None):
                started.update(target=target, args=args, daemon=daemon)

            def start(self):
                started["started"] = True

        with mock.patch.object(admin, "SKIP_SYSTEMD", True), mock.patch.object(
            admin.threading, "Thread", FakeThread
        ):
            result = admin.change_panel_domain({"host": "New.Example.COM", "email": ""})
        self.assertEqual(result, {"scheduled": True, "docker": True, "host": "new.example.com"})
        self.assertTrue(started.get("started") and started.get("daemon"))
        self.assertIs(started.get("target"), admin._apply_docker_domain_change)
        self.assertEqual(started.get("args"), ("new.example.com", ""))

    def test_validation_still_raises_in_docker(self):
        with mock.patch.object(admin, "SKIP_SYSTEMD", True):
            with self.assertRaises(admin.ValidationError):
                admin.change_panel_domain({"host": "", "email": ""})
            with self.assertRaises(admin.ValidationError):
                admin.change_panel_domain({"host": "ok.example.com", "email": "not-an-email"})


class ApplyDomainChangeTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.cfg = root / "config.json"
        self.cfg.write_text(json.dumps(base_config()), encoding="utf-8")
        self.env = {
            "WDTT_PANEL_CONFIG": str(self.cfg),
            "WDTT_ACME_DIR": str(root / "acme"),
            "WDTT_DOCKER_TLS_DIR": str(root / "tls"),
            "WDTT_LE_LIVE_DIR": str(root / "live"),
            "WDTT_CERTBOT_BIN": sys.executable,
            "WDTT_NGINX_GEN": str(root / "gen-nginx-conf"),
            "WDTT_DOCKER_CONFIG_BACKUPS": str(root / "cfgback"),
        }
        (root / "live" / "new.example.com").mkdir(parents=True)
        (root / "live" / "new.example.com" / "fullchain.pem").write_text("cert", encoding="utf-8")
        self.calls = []

    def tearDown(self):
        self.temp.cleanup()

    def _run(self, fail_certbot=False):
        def fake_run(command, timeout=20, check=False, cwd=None, env=None):
            self.calls.append(" ".join(command))
            if "certonly" in command:
                return completed(command, 0 if not fail_certbot else 1, "", "boom" if fail_certbot else "")
            if command and command[0] == "openssl":
                # Настоящий openssl создал бы файлы — материализуем их за мок.
                for flag, name in (("-keyout", None), ("-out", None)):
                    if flag in command:
                        target = Path(command[command.index(flag) + 1])
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(b"dummy")
                return completed(command, 0, "", "")
            return completed(command, 0, "", "")

        return fake_run

    def _apply(self, host, email="", fail_certbot=False):
        with mock.patch.dict("os.environ", self.env, clear=False), mock.patch.object(
            admin, "run", side_effect=self._run(fail_certbot)
        ), mock.patch.object(admin.os, "kill") as kill:
            result = admin._apply_docker_domain_change(host, email)
        saved = json.loads(self.cfg.read_text(encoding="utf-8"))
        return result, saved, kill

    def test_letsencrypt_path_updates_volume_and_reloads(self):
        result, saved, kill = self._apply("new.example.com", "admin@example.com")
        self.assertEqual(saved["public_host"], "new.example.com")
        self.assertEqual(saved["certificate_email"], "admin@example.com")
        self.assertEqual(saved["tls_mode"], "letsencrypt")
        self.assertTrue(
            saved["certificate_path"].replace("\\", "/").endswith("/live/new.example.com/fullchain.pem")
        )
        self.assertTrue(any("certonly" in c and "-d new.example.com" in c for c in self.calls))
        self.assertTrue(any("reload" in c for c in self.calls))
        kill.assert_called_once()
        backups = list(Path(self.env["WDTT_DOCKER_CONFIG_BACKUPS"]).glob("config-*.json"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(result["host"], "new.example.com")

    def test_letsencrypt_failure_falls_back_to_self_signed(self):
        result, saved, kill = self._apply("new.example.com", "", fail_certbot=True)
        self.assertEqual(saved["tls_mode"], "self-signed")
        self.assertTrue(saved["certificate_path"].replace("\\", "/").endswith("/tls/fullchain.pem"))
        self.assertTrue(any(c.startswith("openssl req") for c in self.calls))
        kill.assert_called_once()
        self.assertEqual(result["tls_mode"], "self-signed")

    def test_ip_host_forces_self_signed(self):
        result, saved, kill = self._apply("203.0.113.10")
        self.assertEqual(saved["tls_mode"], "self-signed")
        self.assertFalse(any("certonly" in c for c in self.calls))
        self.assertTrue(any(c.startswith("openssl req") for c in self.calls))
        kill.assert_called_once()
        self.assertEqual(result["host"], "203.0.113.10")

    def test_missing_config_raises(self):
        self.cfg.unlink()
        with mock.patch.dict("os.environ", self.env, clear=False):
            with self.assertRaises(admin.AdminError):
                admin._apply_docker_domain_change("new.example.com", "")


class EntrypointAndDocsTests(unittest.TestCase):
    def test_entrypoint_syncs_env_to_volume(self):
        script = (REPO / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("SYNC_NEEDED", script)
        self.assertIn("config-backups", script)
        self.assertIn("синхронизирован с .env", script)

    def test_entrypoint_reissues_self_signed_on_san_mismatch(self):
        script = (REPO / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("subjectAltName", script)
        self.assertIn("SAN сертификата", script)

    def test_docker_shim_forwards_compose(self):
        shim = (REPO / "docker.sh").read_text(encoding="utf-8")
        self.assertIn("docker compose", shim)
        self.assertIn("docker", shim)

    def test_docs_describe_canonical_env(self):
        readme = (REPO / "docker" / "README.md").read_text(encoding="utf-8")
        self.assertIn("PANEL_HOST", readme)
        self.assertIn(".env", readme)


if __name__ == "__main__":
    unittest.main()
