"""Docker-обновление: флаг docker в API, плашка во фронте, маунт в compose."""
import os
import unittest
from pathlib import Path
from unittest import mock

from wdtt_panel import admin

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
COMPOSE = (REPO / "docker" / "docker-compose.yml").read_text(encoding="utf-8")
SUPERVISORD = (REPO / "docker" / "supervisord.sh").read_text(encoding="utf-8")
APP_JS = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
HTML = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")


class DockerUpdateTests(unittest.TestCase):
    def panel_version(self, env):
        with mock.patch.dict(os.environ, {"_WDTT_TEST_SENTINEL": "1"}):
            os.environ.pop("WDTT_DOCKER", None)
            os.environ.update(env)
            return admin.panel_version({"current_version": "1.2.0"})

    def test_docker_flag_on(self):
        result = self.panel_version({"WDTT_DOCKER": "1"})
        self.assertTrue(result["docker"])

    def test_docker_flag_off(self):
        for env in ({}, {"WDTT_DOCKER": "0"}, {"WDTT_DOCKER": ""}):
            result = self.panel_version(env)
            self.assertFalse(result["docker"], env)

    def test_compose_mounts_panel_code(self):
        self.assertIn("../panel/wdtt_panel:/opt/wdtt-panel/wdtt_panel", COMPOSE)
        self.assertNotIn("wdtt_panel:ro", COMPOSE)
        self.assertIn('WDTT_DOCKER: "1"', COMPOSE)
        self.assertIn('PYTHONDONTWRITEBYTECODE: "1"', COMPOSE)

    def test_supervisord_exports_docker_flag(self):
        self.assertIn("WDTT_DOCKER", SUPERVISORD)

    def test_frontend_docker_banner(self):
        self.assertIn("result.docker", APP_JS)
        self.assertIn("docker compose restart", APP_JS)
        self.assertIn("checkButton", APP_JS)
        # кнопки остаются в HTML (прячутся динамически), тесты шаблонов целы
        self.assertIn('id="update-panel"', HTML)
        self.assertIn('id="check-panel-update"', HTML)


if __name__ == "__main__":
    unittest.main()
