"""2.3.0: Caddy как альтернатива nginx перед панелью (install.sh и Docker)."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


class InstallerCaddyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "install.sh").read_text(encoding="utf-8")

    def test_installer_parses_and_validates_web_server_flag(self):
        self.assertIn("--web-server", self.script)
        self.assertIn("parse_web_server_arg()", self.script)
        self.assertIn("validate_web_server()", self.script)
        self.assertIn("--web-server должен быть nginx или caddy", self.script)
        self.assertIn('WEB_SERVER="${WDTT_WEB_SERVER:-nginx}"', self.script)
        # Старые установки (config.json без ключа) остаются на nginx.
        self.assertIn('("web_server", "nginx")', self.script)
        self.assertIn('[ "${#PANEL_CONFIG_VALUES[@]}" -eq 9 ]', self.script)

    def test_installer_installs_caddy_like_singbox_fixed_binary(self):
        self.assertIn("install_caddy_binary()", self.script)
        self.assertIn('CADDY_BIN="/usr/local/bin/caddy"', self.script)
        self.assertIn('CADDY_VERSION="${WDTT_CADDY_VERSION:-2.11.7}"', self.script)
        # Контрольные суммы официального релиза, как у Go toolchain.
        self.assertIn("caddy_${CADDY_VERSION}_checksums.txt", self.script)
        self.assertIn("sha256sum -c", self.script)

    def test_caddy_runs_as_its_own_unit_and_config(self):
        self.assertIn('CADDY_SERVICE="wdtt-caddy.service"', self.script)
        self.assertIn('CADDY_CONFIG="/etc/wdtt-panel/caddy/Caddyfile"', self.script)
        self.assertIn("write_caddy_service()", self.script)
        self.assertIn("$CADDY_BIN run --config $CADDY_CONFIG --adapter caddyfile", self.script)
        self.assertIn("$CADDY_BIN reload --config $CADDY_CONFIG --adapter caddyfile", self.script)

    def test_caddyfile_mirrors_nginx_contract(self):
        # Секретный путь + /client/ уходит в панель, остальное — 404;
        # :80 — редиректы как в write_final_nginx.
        self.assertIn('path ${PANEL_PATH%/}', self.script)
        self.assertIn("path $PANEL_PATH*", self.script)
        self.assertIn("path /client/*", self.script)
        self.assertIn("respond 404", self.script)
        # Адрес клиента: затираем XFF адресом реального соединения.
        self.assertIn("header_up X-Forwarded-For {remote_host}", self.script)

    def test_tls_modes_acme_and_internal(self):
        self.assertIn("caddy_select_tls_mode()", self.script)
        self.assertIn('TLS_MODE="$WDTT_CADDY_TLS"', self.script)
        self.assertIn('TLS_MODE="internal"', self.script)
        self.assertIn('TLS_MODE="acme"', self.script)
        self.assertIn("tls internal", self.script)
        self.assertIn('Strict-Transport-Security "max-age=31536000"', self.script)

    def test_certbot_skipped_for_caddy(self):
        # renew_certificates при caddy — только проверка службы.
        start = self.script.index("renew_certificates() {")
        block = self.script[start:self.script.index("if [ \"$TLS_MODE\" = \"letsencrypt\" ]; then", start)]
        self.assertIn('if [ "$WEB_SERVER" = "caddy" ]; then', block)
        self.assertIn("Caddy: авто-продление TLS активно", block)

    def test_web_server_dispatchers_replaced_all_nginx_writers(self):
        self.assertIn("write_final_web()", self.script)
        self.assertIn("write_fleet_web()", self.script)
        # Прямой вызов остаётся только внутри диспетчера-обёртки.
        self.assertEqual(self.script.count("\n    write_final_nginx\n"), 1)
        self.assertEqual(self.script.count("\n    write_fleet_nginx\n"), 1)

    def test_panel_service_env_carries_web_server(self):
        self.assertIn("Environment=WDTT_WEB_SERVER=$WEB_SERVER", self.script)


class DockerCaddyTests(unittest.TestCase):
    def test_docker_files_support_caddy(self):
        dockerfile = (REPO / "docker" / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("caddy_${CADDY_VERSION}_checksums.txt", dockerfile)
        self.assertIn("caddy.conf.tmpl", dockerfile)
        self.assertIn("gen-caddy-conf.sh", dockerfile)
        tmpl = (REPO / "docker" / "caddy.conf.tmpl").read_text(encoding="utf-8")
        self.assertIn("storage file_system /etc/wdtt-panel/caddy", tmpl)
        self.assertIn("@@PANEL_PATH@@", tmpl)
        self.assertIn("reverse_proxy 127.0.0.1:@@PANEL_LISTEN_PORT@@", tmpl)
        gen = (REPO / "docker" / "gen-caddy-conf.sh").read_text(encoding="utf-8")
        self.assertIn("caddy validate", gen)
        self.assertIn("TLS_MODE", gen)
        entrypoint = (REPO / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn('WDTT_WEB_SERVER:=nginx', entrypoint)
        self.assertIn("wdtt-gen-caddy-conf", entrypoint)
        supervisord = (REPO / "docker" / "supervisord.sh").read_text(encoding="utf-8")
        self.assertIn("run_caddy()", supervisord)
        self.assertIn("run_web()", supervisord)
        healthcheck = (REPO / "docker" / "healthcheck.sh").read_text(encoding="utf-8")
        self.assertIn("pidof caddy", healthcheck)
        env = (REPO / "docker" / ".env.example").read_text(encoding="utf-8")
        self.assertIn("WDTT_WEB_SERVER=nginx", env)

    def test_docker_caddy_ignores_tls_mode_letsencrypt(self):
        entrypoint = (REPO / "docker" / "entrypoint.sh").read_text(encoding="utf-8")
        self.assertIn("TLS_MODE=letsencrypt игнорируется caddy-режимом", entrypoint)
        self.assertIn('elif [ "$TLS_MODE" = "letsencrypt" ]; then', entrypoint)


class AdminWebServerTests(unittest.TestCase):
    def test_admin_reads_web_server_env(self):
        source = (ROOT / "wdtt_panel" / "admin.py").read_text(encoding="utf-8")
        self.assertIn('WEB_SERVER = os.environ.get("WDTT_WEB_SERVER", "nginx")', source)
        self.assertIn('WEB_SERVICE = "wdtt-caddy.service" if WEB_SERVER == "caddy"', source)

    def test_logs_source_uses_web_service(self):
        source = (ROOT / "wdtt_panel" / "admin.py").read_text(encoding="utf-8")
        self.assertIn('"nginx": ([WEB_SERVICE], WEB_TITLE),', source)
