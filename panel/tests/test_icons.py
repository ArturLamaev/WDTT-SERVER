"""Tabler-иконки (инлайн-SVG, офлайн): fleet-панель и сайдбар ноды."""
import unittest
from pathlib import Path

from wdtt_panel.fleet.icons import icon

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
APP_CSS = (ROOT / "wdtt_panel" / "static" / "app.css").read_text(encoding="utf-8")
FLEET_CSS = (ROOT / "wdtt_panel" / "fleet" / "static" / "fleet.css").read_text(encoding="utf-8")


class IconsTests(unittest.TestCase):
    def test_fleet_icon_set(self):
        for name in ("server", "activity", "users", "robot", "plus",
                     "trash", "search", "check", "sun", "moon"):
            svg = icon(name)
            self.assertIn("<svg", svg, name)
            self.assertIn("<path", svg, name)
            self.assertIn('stroke="currentColor"', svg, name)
        self.assertEqual(icon("нет-такой"), "")

    def test_fleet_css_sizes_svgs(self):
        self.assertIn("nav.fleet-nav a svg", FLEET_CSS)
        self.assertIn("button svg", FLEET_CSS)
        self.assertIn("icon-moon", FLEET_CSS)

    def test_node_sidebar_has_icons(self):
        for tab in ("dashboard", "users", "wdtt", "xray", "logs", "system"):
            # кусок от начала кнопки (последний <button перед data-tab) до её конца
            start = HTML.split(f'data-tab="{tab}"')[0].rindex("<button")
            end = HTML.index("</button>", HTML.index(f'data-tab="{tab}"'))
            button = HTML[start:end]
            self.assertIn("<svg", button, tab)
            self.assertIn('stroke="currentColor"', button, tab)
        for label in ("Обзор", "Пользователи", "WDTT", "Xray", "Журналы", "Система"):
            self.assertIn(label, HTML)

    def test_node_theme_toggle_has_sun_and_moon(self):
        toggle = HTML[HTML.index('id="theme-toggle"'):HTML.index('id="theme-toggle"') + 1400]
        self.assertNotIn("☀", toggle)
        self.assertIn("icon-sun", toggle)
        self.assertIn("icon-moon", toggle)
        self.assertIn('id="theme-toggle-label"', toggle)

    def test_node_css_styles_nav_svgs(self):
        self.assertIn(".nav-item svg", APP_CSS)
        self.assertIn(".theme-toggle .icon-moon", APP_CSS)


if __name__ == "__main__":
    unittest.main()
