"""Тесты выбора режима node|controller в установщиках (grep-стиль: bash на Windows нет).

Проверяет наличие вопроса, ветвлений и fleet-юнитов, не запуская bash.
"""
import unittest
from pathlib import Path

from wdtt_panel.fleet.settings import load_controller_config

ROOT = Path(__file__).resolve().parents[1]
PANEL_INSTALL = (ROOT / "install.sh").read_text(encoding="utf-8")
ROOT_INSTALL = (ROOT.parent / "install.sh").read_text(encoding="utf-8")
BOOTSTRAP = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")


class InstallModeTests(unittest.TestCase):
    def test_panel_installer_has_mode_plumbing(self):
        for name in ("parse_mode_arg()", "validate_mode()", "ask_install_mode()",
                     "resolve_install_mode()", "installed_mode()",
                     "prepare_controller_secrets()", "write_fleet_config()",
                     "seed_fleet_bot_settings()", "fleet_bot_token_available()",
                     "write_fleet_service()", "write_fleet_bot_service()",
                     "write_fleet_nginx()", "install_controller()",
                     "update_controller()", "update_controller_config_metadata()"):
            self.assertIn(name, PANEL_INSTALL, name)
        self.assertIn('WDTT_MODE="${WDTT_MODE:-node}"', PANEL_INSTALL)
        self.assertIn("Что устанавливаем?", PANEL_INSTALL)
        self.assertIn("Панель управления", PANEL_INSTALL)
        self.assertIn("FILTERED_ARGS", PANEL_INSTALL)
        self.assertIn("[--mode node|controller]", PANEL_INSTALL)

    def test_panel_install_branches_by_mode(self):
        self.assertIn("resolve_install_mode", PANEL_INSTALL)
        start = PANEL_INSTALL.index("resolve_install_mode() {")
        self.assertIn("MODE_FROM_ARGS", PANEL_INSTALL[start:])
        self.assertIn("WDTT_MODE_ENV_GIVEN", PANEL_INSTALL)
        self.assertIn('if [ "$WDTT_MODE" = "controller" ]; then\n    install_controller',
                      PANEL_INSTALL)
        self.assertIn("update_controller", PANEL_INSTALL)
        self.assertIn('if [ "$(installed_mode)" = "controller" ]; then\n    update_controller',
                      PANEL_INSTALL)

    def test_fleet_units_and_nginx(self):
        self.assertIn('FLEET_SERVICE="wdtt-fleet.service"', PANEL_INSTALL)
        self.assertIn('FLEET_BOT_SERVICE="wdtt-fleet-bot.service"', PANEL_INSTALL)
        self.assertIn("wdtt_panel.fleet web --config", PANEL_INSTALL)
        self.assertIn("wdtt_panel.fleet bot", PANEL_INSTALL)
        self.assertIn('"mode": "controller"', PANEL_INSTALL)
        self.assertIn("proxy_pass http://127.0.0.1:$FLEET_LISTEN_PORT;", PANEL_INSTALL)
        self.assertIn("FLEET_LISTEN_PORT", PANEL_INSTALL)

    def test_panel_existing_flows_know_controller(self):
        self.assertIn('"$FLEET_SERVICE"', PANEL_INSTALL)
        self.assertIn('"$FLEET_BOT_SERVICE"', PANEL_INSTALL)
        # change-password / change-domain рестартуют fleet в режиме контроллера,
        # но старые литералы для ноды на месте (их проверяют другие тесты).
        self.assertIn('systemctl restart "$PANEL_SERVICE"', PANEL_INSTALL)
        self.assertIn('systemctl restart "$FLEET_SERVICE"', PANEL_INSTALL)
        self.assertIn("write_fleet_nginx", PANEL_INSTALL)

    def test_root_installer_asks_and_forwards_mode(self):
        self.assertIn("resolve_root_mode()", ROOT_INSTALL)
        self.assertIn("Что устанавливаем?", ROOT_INSTALL)
        self.assertIn("--mode", ROOT_INSTALL)
        self.assertIn('bash "$PANEL_INSTALL" "$@" --mode "$WDTT_MODE"', ROOT_INSTALL)
        self.assertIn("wdtt-fleet", ROOT_INSTALL)
        self.assertIn("Режим установки: $WDTT_MODE", ROOT_INSTALL)

    def test_bootstrap_supports_mode(self):
        self.assertIn("--mode", BOOTSTRAP)
        self.assertIn('export WDTT_MODE=', BOOTSTRAP)
        self.assertIn("Что устанавливаем?", BOOTSTRAP)
        self.assertIn("controller", BOOTSTRAP)

    def test_load_controller_config(self):
        import json
        import tempfile
        good = {"mode": "controller", "username": "admin",
                "password_hash": "h", "session_secret": "s",
                "fleet_listen_port": 8790, "base_path": "/p/",
                "fleet_store": "/tmp/n.json", "fleet_config": "/tmp/c.json"}
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / "config.json")
            Path(path).write_text(json.dumps(good), encoding="utf-8")
            cfg = load_controller_config(path)
            self.assertEqual(cfg["username"], "admin")
            self.assertEqual(cfg["listen_port"], 8790)
            self.assertEqual(cfg["base_path"], "/p/")
            bad = dict(good, mode="node")
            Path(path).write_text(json.dumps(bad), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_controller_config(path)
            bad2 = dict(good)
            del bad2["password_hash"]
            Path(path).write_text(json.dumps(bad2), encoding="utf-8")
            with self.assertRaises(ValueError):
                load_controller_config(path)
            with self.assertRaises(ValueError):
                load_controller_config(str(Path(tmp) / "missing.json"))

    def test_web_config_flag_parses(self):
        from wdtt_panel.fleet.cli import build_parser
        args = build_parser().parse_args(["web", "--config", "/etc/wdtt-panel/config.json"])
        self.assertEqual(args.config, "/etc/wdtt-panel/config.json")


class InstallModeSwitchTests(unittest.TestCase):
    """Битая установка и смена режима: update вместо die, снос с таймаутами."""

    def test_root_handles_broken_and_mode_switch(self):
        root = (ROOT.parent / "install.sh").read_text(encoding="utf-8")
        self.assertIn("installed_panel_mode()", root)
        self.assertIn("битая установка", root)
        self.assertIn("Смена режима", root)
        self.assertIn("--force-clean для чистой установки", root)
        self.assertIn("sys_timeout()", root)

    def test_panel_uninstall_has_stages_and_guards(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("sys_timeout()", script)
        self.assertIn("Останавливаю и отключаю юниты панели", script)
        self.assertIn("Чищу правила фаервола", script)
        self.assertIn("sys_timeout 180 systemctl disable --now", script)
        self.assertIn("sys_timeout 60 systemctl daemon-reload", script)


if __name__ == "__main__":
    unittest.main()
