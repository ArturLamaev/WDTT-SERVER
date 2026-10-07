import os
import re
from pathlib import Path
import shutil
import tempfile
import unittest

from wdtt_panel.wdtt_server_patch import EXTENSION_MARKER, patch_spaceneurox_tree


class WdttServerPatchTests(unittest.TestCase):
    def test_rejects_an_unknown_source_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "missing server/main.go"):
                patch_spaceneurox_tree(Path(directory))

    def test_patches_bundled_source_tree_idempotently(self):
        source = Path(__file__).resolve().parents[2] / "src" / "proxy-turn-vk-android-1.4.3"
        if not (source / "server" / "main.go").is_file():
            self.skipTest("bundled qWDTT source is absent")
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "qwdtt"
            shutil.copytree(source, work, ignore=shutil.ignore_patterns(".git", "panel_extension.go", "panel_extension_test.go"))
            patch_spaceneurox_tree(work)
            patch_spaceneurox_tree(work)

            server = work / "server"
            texts = {name: (server / name).read_text(encoding="utf-8") for name in ("core.go", "main.go", "connections.go", "database_bot.go")}
            extension = (server / "panel_extension.go").read_text(encoding="utf-8")
            self.assertIn(EXTENSION_MARKER, extension)
            self.assertIn('json:"max_down_mbps,omitempty"', texts["database_bot.go"])
            self.assertIn("applySpeedLimitForEntryUnlocked(entry)", texts["connections.go"])
            self.assertIn("syncAllSpeedLimits()", texts["main.go"])
            # Каждый флаг рантайма из main.go должен присваиваться в реально
            # объявленную переменную (ловит опечатки вида wgKeepalive vs keepalive).
            assigned = re.findall(r"^\t([A-Za-z_]\w*) = \*\w+Flag$", texts["main.go"], re.MULTILINE)
            self.assertTrue(assigned)
            for name in assigned:
                self.assertTrue(
                    name == "dns" or f"\n\t{name}" in texts["core.go"],
                    f"{name} is assigned in main.go but not declared in core.go",
                )

    @unittest.skipUnless(os.environ.get("QWDTT_SOURCE"), "set QWDTT_SOURCE for upstream integration test")
    def test_patches_the_official_v1_4_3_tree_idempotently(self):
        source = Path(os.environ["QWDTT_SOURCE"]).resolve()
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory) / "qwdtt"
            shutil.copytree(source, work, ignore=shutil.ignore_patterns(".git", "panel_extension.go", "panel_extension_test.go"))
            patch_spaceneurox_tree(work)
            patch_spaceneurox_tree(work)

            extension = (work / "server" / "panel_extension.go").read_text(encoding="utf-8")
            database = (work / "server" / "database_bot.go").read_text(encoding="utf-8")
            connections = (work / "server" / "connections.go").read_text(encoding="utf-8")
            main = (work / "server" / "main.go").read_text(encoding="utf-8")
            raw = (work / "server" / "raw.go").read_text(encoding="utf-8")
            wireguard = (work / "server" / "wireguard.go").read_text(encoding="utf-8")
            self.assertIn(EXTENSION_MARKER, extension)
            self.assertIn('json:"traffic_operations,omitempty"', database)
            self.assertIn('json:"main_down_bytes,omitempty"', database)
            self.assertIn('json:"max_down_mbps,omitempty"', database)
            self.assertIn('json:"max_up_mbps,omitempty"', database)
            self.assertIn("applyPasswordRestrictionsLocked", database)
            self.assertIn("syncAllSpeedLimits", extension)
            self.assertIn("applyClientSpeedLimits", extension)
            self.assertIn("dtlsDeviceConnectionsLocked", extension)
            self.assertIn("DENIED:traffic_limit", connections)
            self.assertIn("DENIED:too_many_connections", connections)
            self.assertIn("applySpeedLimitForEntryUnlocked(entry)", connections)
            self.assertIn("handshake-timeout", main)
            self.assertIn("syncAllSpeedLimits()", main)
            self.assertIn("createBasicTUNFile", raw)
            self.assertIn("createBasicTUNFile", wireguard)


if __name__ == "__main__":
    unittest.main()
