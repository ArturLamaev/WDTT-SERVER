"""Докачка статики кусками: парсер Range, 206 в нодовой панели и во флоте."""
import unittest
from pathlib import Path

from wdtt_panel.http_range import parse_range

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


class ParseRangeTests(unittest.TestCase):
    def test_plain_range(self):
        self.assertEqual(parse_range("bytes=0-13999", 136017), (0, 13999))

    def test_open_end(self):
        self.assertEqual(parse_range("bytes=14000-", 136017), (14000, 136016))

    def test_suffix(self):
        self.assertEqual(parse_range("bytes=-500", 136017), (135517, 136016))

    def test_end_clamped_to_size(self):
        self.assertEqual(parse_range("bytes=0-999999", 1000), (0, 999))

    def test_garbage_means_full_body(self):
        for bad in ("", "items=0-10", "bytes=", "bytes=10", "bytes=5-3",
                    "bytes=0-1,2-3", "bytes=abc-def", "bytes=1000-2000"):
            self.assertIsNone(parse_range(bad, 1000), bad)

    def test_start_past_end_means_full_body(self):
        self.assertIsNone(parse_range("bytes=1000-", 1000))

    def test_empty_file(self):
        self.assertIsNone(parse_range("bytes=0-", 0))


class NodeStaticRangeTests(unittest.TestCase):
    def test_full_body_without_range(self):
        from test_app import AppSmokeTests

        case = AppSmokeTests("test_api_v1_password_login_and_bearer_requests")
        case.setUp()
        try:
            captured, body = case.request("/private-panel-path/static/app.js")
        finally:
            case.tearDown()
        self.assertEqual(captured["status"], "200 OK")
        self.assertEqual(int(captured["headers"]["Content-Length"]), len(body))
        self.assertNotIn("Content-Range", captured["headers"])

    def test_partial_content_with_close(self):
        from test_app import AppSmokeTests

        case = AppSmokeTests("test_api_v1_password_login_and_bearer_requests")
        case.setUp()
        try:
            captured, body = case.request(
                "/private-panel-path/static/app.js", http_range="bytes=0-13999")
        finally:
            case.tearDown()
        self.assertEqual(captured["status"], "206 Partial Content")
        self.assertEqual(len(body), 14000)
        total = (ROOT / "wdtt_panel" / "static" / "app.js").stat().st_size
        self.assertEqual(captured["headers"]["Content-Range"], f"bytes 0-13999/{total}")
        self.assertEqual(captured["headers"]["Accept-Ranges"], "bytes")
        # Hop-by-hop заголовков быть не должно: прод сервится wsgiref,
        # который роняет 500 на Connection (свежие соединения даёт nginx).
        self.assertNotIn("Connection", captured["headers"])

    def test_chunks_reassemble_to_full_file(self):
        from test_app import AppSmokeTests

        case = AppSmokeTests("test_api_v1_password_login_and_bearer_requests")
        case.setUp()
        try:
            full = (ROOT / "wdtt_panel" / "static" / "app.js").read_bytes()
            parts, offset = [], 0
            while offset < len(full):
                captured, body = case.request(
                    "/private-panel-path/static/app.js",
                    http_range=f"bytes={offset}-{min(offset + 13999, len(full) - 1)}")
                self.assertEqual(captured["status"], "206 Partial Content")
                parts.append(body)
                offset += len(body)
        finally:
            case.tearDown()
        self.assertEqual(b"".join(parts), full)

    def test_boot_loader_is_served(self):
        from test_app import AppSmokeTests

        case = AppSmokeTests("test_api_v1_password_login_and_bearer_requests")
        case.setUp()
        try:
            captured, body = case.request("/private-panel-path/static/boot.js")
        finally:
            case.tearDown()
        self.assertEqual(captured["status"], "200 OK")
        self.assertIn(b"Range", body)
        self.assertIn(b"Blob", body)

    def test_index_embeds_loader_and_size(self):
        from test_app import AppSmokeTests

        case = AppSmokeTests("test_api_v1_password_login_and_bearer_requests")
        case.setUp()
        try:
            form = b"username=admin&password=Panel-password-12345"
            headers, _ = case.request("/private-panel-path/login", "POST", form)
            cookie = headers["headers"]["Set-Cookie"].split(";", 1)[0]
            captured, body = case.request("/private-panel-path/", cookie=cookie)
        finally:
            case.tearDown()
        self.assertTrue(captured["status"].startswith("200"))
        text = body.decode("utf-8")
        self.assertIn("static/boot.js", text)
        self.assertIn("app-js-size", text)
        # Прямой app.js остался только в meta для загрузчика.
        self.assertEqual(text.count("static/app.js?v="), 1)

    def test_csp_allows_blob_scripts(self):
        from test_app import AppSmokeTests

        case = AppSmokeTests("test_api_v1_password_login_and_bearer_requests")
        case.setUp()
        try:
            captured, _ = case.request("/private-panel-path/static/app.js")
        finally:
            case.tearDown()
        csp = captured["headers"].get("Content-Security-Policy", "")
        self.assertIn("script-src 'self' blob:", csp)


class FleetStaticRangeTests(unittest.TestCase):
    def test_fleet_css_partial(self):
        from test_fleet_web import FleetWebTests

        case = FleetWebTests("test_static_css")
        # Browser класса ходит по живому WSGI-серверу из setUpClass.
        FleetWebTests.setUpClass()
        try:
            browser = case.browser()
            code, _, body = browser.get(
                "/static/fleet.css", headers={"Range": "bytes=0-99"})
        finally:
            FleetWebTests.tearDownClass()
        full = (ROOT / "wdtt_panel" / "fleet" / "static" / "fleet.css").read_bytes()
        self.assertEqual(code, 206)
        self.assertEqual(body.encode("utf-8"), full[:100])


class NginxGzipTests(unittest.TestCase):
    def test_gzip_in_all_nginx_configs(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(installer.count("gzip on;"), 2)
        self.assertIn("gzip_proxied any;", installer)
        tmpl = (REPO / "docker" / "nginx.conf.tmpl").read_text(encoding="utf-8")
        self.assertIn("gzip on;", tmpl)
        self.assertIn("application/javascript", tmpl)

    def test_static_location_closes_connections(self):
        # Чанки boot.js: каждый кусок новым соединением (wsgiref такое
        # запрещает, поэтому рвёт соединения nginx).
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(installer.count("keepalive_requests 1;"), 2)
        self.assertIn("location ^~ ${PANEL_PATH}static/", installer)
        tmpl = (REPO / "docker" / "nginx.conf.tmpl").read_text(encoding="utf-8")
        self.assertIn("keepalive_requests 1;", tmpl)
        self.assertIn("location ^~ @@PANEL_PATH@@static/", tmpl)


if __name__ == "__main__":
    unittest.main()
