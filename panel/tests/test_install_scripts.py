import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


class InstallScriptTests(unittest.TestCase):
    def test_version_is_consistent(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        package = (ROOT / "wdtt_panel" / "__init__.py").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('PANEL_VERSION="1.12.4"', installer)
        self.assertIn('__version__ = "1.12.4"', package)
        self.assertIn("Текущая версия: 1.12.4", readme)

    def test_bootstrap_has_interactive_management_menu(self):
        script = (ROOT / "bootstrap.sh").read_text(encoding="utf-8")
        for action in ("install", "update", "rollback", "status", "renew-cert", "change-password", "uninstall"):
            self.assertIn(action, script)
        self.assertIn("/dev/tty", script)
        self.assertIn("--domain", script)
        self.assertIn("--password", script)
        self.assertIn("--telegram-token", script)
        self.assertIn("--telegram-admin-id", script)
        self.assertIn("prompt_wdtt_main_password()", script)
        self.assertIn("prompt_telegram_settings()", script)
        self.assertIn("clean-system", script)
        self.assertIn("normalize_wdtt_main_password", script)
        self.assertIn("wdtt_main_password_is_valid", script)
        self.assertIn("telegram_settings_are_valid", script)
        self.assertIn("while true", script)
        self.assertIn("github_versions()", script)
        self.assertIn("refs/tags/${ROLLBACK_VERSION}", script)

    def test_installer_has_update_and_certificate_renewal(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("update_panel()", script)
        self.assertIn("backup_panel_config_before_update()", script)
        self.assertIn("migrate_panel_config()", script)
        self.assertIn("renew_certificates()", script)
        self.assertIn("OnUnitActiveSec=12h", script)
        self.assertIn("write_maintenance_scripts", script)
        self.assertIn("install_xray_runtime()", script)
        self.assertIn("install_warp_runtime()", script)
        self.assertIn("install_wdtt_extensions()", script)
        self.assertIn("schedule_wdtt_extensions", script)
        self.assertIn("write_wdtt_extensions_timer", script)
        self.assertIn("TimeoutStartSec=20min", script)
        self.assertIn("OnUnitActiveSec=10min", script)
        self.assertIn("OnUnitInactiveSec=10min", script)
        self.assertIn("Restart=on-failure", script)
        self.assertIn('systemctl restart --no-block "$WDTT_EXTENSIONS_SERVICE"', script)
        self.assertIn("WDTT_EXTENSION_MARKER", script)
        self.assertIn('WDTT_REPOSITORY="${WDTT_REPOSITORY:-SpaceNeuroX/proxy-turn-vk-android}"', script)
        self.assertIn('WDTT_REF="${WDTT_REF:-v1.4.3}"', script)
        self.assertIn('WDTT_EXTENSION_MARKER="wdtt-panel-extension-v10"', script)
        self.assertIn("download_wdtt_archive", script)
        self.assertIn("https://github.com/${WDTT_REPOSITORY}/archive", script)
        self.assertIn("refs/${kind}/${WDTT_REF}.zip", script)
        self.assertIn("wdtt_extensions_binary_is_current", script)
        self.assertIn("backup_wdtt_database_before_update", script)
        update_start = script.index("update_panel() {")
        update_block = script[update_start:script.index("install_panel() {", update_start)]
        self.assertLess(update_block.index("backup_wdtt_database_before_update"), update_block.index("schedule_wdtt_extensions"))
        self.assertIn('grep -aFq "$WDTT_EXTENSION_MARKER"', script)
        self.assertIn("awk 'NR == 1 { print $1; exit }'", script)
        self.assertIn("Некорректная контрольная сумма Go", script)
        self.assertIn('https://dl.google.com/go/${go_tarball}.sha256', script)
        self.assertIn("build -mod=mod", script)
        self.assertIn('GOMODCACHE="$work/gopath/pkg/mod"', script)
        self.assertIn('json:"label,omitempty"', script)
        self.assertIn('json:"main_down_bytes,omitempty"', script)
        self.assertIn('json:"last_upload_at,omitempty"', script)
        self.assertIn('"activity"', script)
        self.assertIn('"spaceneurox_v1_4_3"', script)
        self.assertIn('[ -f "$source/server/main.go" ]', script)
        self.assertIn('-o "$work/wdtt-server" ./server', script)
        self.assertIn('/tmp/wdtt-admin.token', script)
        self.assertIn('"$WDTT_REPOSITORY" "$WDTT_REF" <<\'PY\'', script)
        self.assertIn('state.get("wdtt_ref") == sys.argv[4]', script)
        self.assertIn("wdtt_server_patch.py", script)
        self.assertIn("wdtt_database_preserved", script)
        self.assertIn("restore_wdtt_extension_backup", script)
        self.assertIn('"command":"settings"', script)
        self.assertIn("label prompt on Telegram creation", script)
        self.assertIn("label before password in Telegram list", script)
        self.assertIn("wdtt-xray-cascade.service", script)
        self.assertIn("wdtt-xray-gateway.service", script)
        self.assertIn("wdtt-panel-geofiles-update.timer", script)
        self.assertIn("wdtt-panel-backup.timer", script)
        self.assertIn("wdtt-panel-backup", script)
        self.assertIn("validate_wdtt_main_password()", script)
        self.assertIn("normalize_wdtt_main_password", script)
        self.assertIn("validate_telegram_settings()", script)
        self.assertIn("apply_telegram_settings()", script)
        self.assertIn("-bot-token-file", script)
        self.assertIn("/etc/wdtt/bot.token", script)

        panel_files_start = script.index("install_panel_files() {")
        panel_files = script[panel_files_start:script.index("write_maintenance_scripts() {", panel_files_start)]
        self.assertIn('if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then', panel_files)
        self.assertIn('install -d -o wdtt-panel -g wdtt-panel -m 0755 "$STATE_DIR"', panel_files)
        self.assertLess(
            panel_files.index('if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then'),
            panel_files.index('rm -rf "$INSTALL_DIR/wdtt_panel"'),
        )

        patcher = (ROOT / "wdtt_panel" / "wdtt_server_patch.py").read_text(encoding="utf-8")
        self.assertIn('EXTENSION_MARKER = "wdtt-panel-extension-v10"', patcher)
        self.assertIn('json:"traffic_primary_bytes,omitempty"', patcher)
        self.assertIn("trafficQuotaExhausted", patcher)
        self.assertIn('json:"main_down_bytes,omitempty"', patcher)
        self.assertIn('json:"last_upload_at,omitempty"', patcher)
        self.assertIn('json:"max_down_mbps,omitempty"', patcher)
        self.assertIn('json:"max_up_mbps,omitempty"', patcher)
        self.assertIn("handshake-timeout", patcher)
        self.assertIn("syncAllSpeedLimits", patcher)
        self.assertIn("applySpeedLimitForEntryUnlocked", patcher)
        self.assertIn('cmd == "/settings"', patcher)
        self.assertIn('Отправьте метку нового пользователя', patcher)
        self.assertIn('telegramLabel(label), p, expiry', patcher)

    def test_wdtt_update_guard_rejects_lost_users_devices_or_quota_data(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        start_marker = 'wdtt_database_preserved() {\n  python3 - "$1" "$2" <<\'PY\'\n'
        start = installer.index(start_marker) + len(start_marker)
        guard = installer[start:installer.index("\nPY\n}", start)]
        before = {
            "main_password": "MainPassword123",
            "passwords": {
                "UserPassword123": {
                    "label": "Пользователь",
                    "expires_at": 1800000000,
                    "traffic_managed": True,
                    "traffic_primary_bytes": 35 * 1024**3,
                    "traffic_extra_bytes": 5 * 1024**3,
                    "traffic_operations": [{"kind": "extra", "bytes": 5 * 1024**3}],
                }
            },
            "devices": {"device-1": {"ip": "10.66.0.2"}},
            "main_down_bytes": 123456,
        }
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            before_path = root / "before.json"
            after_path = root / "after.json"
            before_path.write_text(json.dumps(before), encoding="utf-8")
            after_path.write_text(json.dumps(before), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["guard", str(before_path), str(after_path)]):
                exec(guard, {"__name__": "__main__"})

            after_path.write_text(
                json.dumps({"main_password": "MainPassword123", "passwords": {}, "devices": {}}),
                encoding="utf-8",
            )
            with mock.patch.object(sys, "argv", ["guard", str(before_path), str(after_path)]):
                with self.assertRaises(SystemExit):
                    exec(guard, {"__name__": "__main__"})

            changed_quota = json.loads(json.dumps(before))
            changed_quota["passwords"]["UserPassword123"]["traffic_extra_bytes"] = 0
            after_path.write_text(json.dumps(changed_quota), encoding="utf-8")
            with mock.patch.object(sys, "argv", ["guard", str(before_path), str(after_path)]):
                with self.assertRaises(SystemExit):
                    exec(guard, {"__name__": "__main__"})

    def test_installer_keeps_telegram_token_out_of_systemd_unit(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        start_marker = (
            'python3 - /etc/wdtt/passwords.json "/etc/systemd/system/$WDTT_SERVICE" '
            '/etc/wdtt/bot.token "$WDTT_TELEGRAM_ADMIN_ID" "$WDTT_TELEGRAM_BOT_TOKEN" <<\'PY\'\n'
        )
        start = installer.index(start_marker) + len(start_marker)
        script = installer[start:installer.index("\nPY\n  systemctl daemon-reload", start)]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "passwords.json"
            unit_path = root / "wdtt.service"
            token_path = root / "bot.token"
            token = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZ_test"
            db_path.write_text('{"passwords": {}, "devices": {}}', encoding="utf-8")
            unit_path.write_text(
                "[Service]\nExecStart=/usr/local/bin/wdtt-server -bot-token OldSecret -bot-token-file /old/token\n",
                encoding="utf-8",
            )
            argv = ["telegram.py", str(db_path), str(unit_path), str(token_path), "123456789", token]
            with mock.patch.object(sys, "argv", argv):
                exec(script, {"__name__": "__main__"})
            unit = unit_path.read_text(encoding="utf-8")
            self.assertIn("-bot-token-file", unit)
            self.assertNotIn(token, unit)
            self.assertNotIn("OldSecret", unit)
            self.assertEqual(token_path.read_text(encoding="utf-8").strip(), token)

    def test_installer_recovers_legacy_labels_from_private_backups(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        start_marker = 'python3 - /etc/wdtt/passwords.json "$PRIVATE_STATE_DIR/user-labels.json" <<\'PY\'\n'
        start = installer.index(start_marker) + len(start_marker)
        end = installer.index('\nPY\n  fi\n  install -m 0755 "$work/wdtt-server"', start)
        migration = installer[start:end]
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "etc" / "passwords.json"
            labels_path = root / "private" / "user-labels.json"
            backup_path = root / "private" / "backups" / "passwords-legacy.json"
            db_path.parent.mkdir(parents=True)
            backup_path.parent.mkdir(parents=True)
            db_path.write_text(json.dumps({"passwords": {"LegacyUser123": {}}, "devices": {}}), encoding="utf-8")
            labels_path.write_text("{}", encoding="utf-8")
            backup_path.write_text(json.dumps({"passwords": {"LegacyUser123": {"label": "Старое имя"}}}, ensure_ascii=False), encoding="utf-8")
            namespace = {"__name__": "__main__"}
            with mock.patch("sys.argv", ["migration.py", str(db_path), str(labels_path)]):
                exec(compile(migration, "label-migration.py", "exec"), namespace)
            restored = json.loads(db_path.read_text(encoding="utf-8"))
            self.assertEqual(restored["passwords"]["LegacyUser123"]["label"], "Старое имя")

    def test_extensions_service_loads_on_old_systemd(self):
        # systemd 232 запрещает Restart= для Type=oneshot вообще (даже с
        # RemainAfterExit — «isn't allowed for Type=oneshot services»):
        # update_panel падал до применения обновления. Повторы сборки
        # остаются за таймером (каждые 10 минут), поэтому в юните расширений
        # не должно быть Restart=/RestartSec=.
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        start = script.index("ExecStart=/bin/bash $INSTALL_DIR/install.sh enable-wdtt-extensions")
        block = script[script.rindex("[Service]", 0, start):start]
        self.assertIn("Type=oneshot", block)
        self.assertIn("RemainAfterExit=yes", block)
        self.assertNotIn("Restart=", block)
        self.assertNotIn("RestartSec=", block)

    def test_installer_can_change_the_panel_login_password(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("change_panel_password()", script)
        self.assertIn('change-password|--change-password) change_panel_password ;;', script)
        self.assertIn('data["session_secret"] = session_secret', script)

    def test_installer_can_change_the_panel_domain(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("change_panel_domain()", script)
        self.assertIn('change-domain|--change-domain) change_panel_domain ;;', script)
        self.assertIn("change-domain|clean-system", script)
        self.assertIn('data["public_host"] = public_host', script)
        self.assertIn("backup_panel_config_before_update", script.split("change_panel_domain()")[1])
        change = script[script.index("change_panel_domain()"):]
        self.assertIn("request_certificate", change)
        self.assertIn("create_self_signed_certificate", change)
        self.assertIn("write_final_nginx", change)
        self.assertIn('systemctl restart "$PANEL_SERVICE"', change)

    def test_acme_opens_port_80_before_certbot(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        request_start = script.index("request_certificate()")
        request_end = script.index("run_certbot_request()", request_start)
        request = script[request_start:request_end]
        self.assertIn("open_acme_firewall", request)
        self.assertIn("open_acme_firewall()", script)
        self.assertIn("--standalone --preferred-challenges http", script)
        self.assertIn("authenticator = standalone", script)

    def test_status_uses_the_saved_secret_path(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn(
            "status|--status|-s) require_root; load_panel_config; status_panel ;;",
            script,
        )
        self.assertIn("curl --noproxy '*' -kfsS", script)
        self.assertIn("for ((attempt = 1; attempt <= 10; attempt++))", script)

    def test_certificate_upgrade_reports_the_reason_for_a_failure(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("Публичный сертификат не запрошен: TCP 80 занят не Nginx", script)
        self.assertIn("Не удалось получить Let's Encrypt: проверьте DNS", script)

    def test_manager_wrapper_is_replaced_during_an_update(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('rm -f "$MANAGER_WRAPPER" /usr/local/sbin/wddt-panel /usr/local/sbin/wdtt-pane', script)
        self.assertIn('install -m 0755 "$INSTALL_DIR/bootstrap.sh" "$MANAGER_WRAPPER"', script)
        self.assertNotIn("MANAGER_ALIAS_ONE", script)
        self.assertNotIn("MANAGER_ALIAS_TWO", script)

    def test_hsts_is_only_enabled_for_a_public_certificate(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('if [ "$TLS_MODE" = "letsencrypt" ]; then', script)
        self.assertIn("HSTS_HEADER=", script)

    def test_panel_exposes_full_backup_controls(self):
        html = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="create-full-backup"', html)
        self.assertIn('id="create-users-backup"', html)
        self.assertIn('id="save-backup-schedule"', html)
        self.assertIn('api("backups/create"', script)
        self.assertIn('api("backups/delete"', script)
        self.assertIn('api("backups/schedule"', script)
        self.assertIn('id="tab-xray"', html)
        self.assertIn('id="install-xray"', html)
        self.assertIn('api("xray/save"', script)
        self.assertIn('id="install-warp"', html)
        self.assertIn('id="ping-warp"', html)
        self.assertIn('id="save-cascade"', html)
        self.assertIn('api("cascade/save"', script)
        self.assertIn('id="add-xray-vless-route"', html)
        self.assertIn('id="add-xray-friendly-rule"', html)
        self.assertIn('id="xray-route-dialog"', html)
        self.assertIn('id="xray-rule-dialog"', html)
        self.assertIn('id="xray-rule-preset"', html)
        self.assertIn('id="xray-access-log"', html)
        self.assertIn('id="xray-gateway-enabled"', html)
        self.assertIn('<option value="xray-access">', html)
        self.assertIn('<option value="xray-errors">', html)
        self.assertIn('collectFriendlyRules()', script)
        self.assertIn('ROUTE_PRESETS', script)
        self.assertIn('ru_blocked', script)
        self.assertIn('Meta: Facebook, Instagram, Threads', html)
        self.assertIn('eu-vless', script)
        self.assertIn('restoreSidebarState()', script)
        self.assertIn('id="log-source"', html)
        self.assertIn('id="sidebar-toggle"', html)
        self.assertIn('id="theme-toggle"', html)
        self.assertNotIn('id="refresh"', html)
        self.assertIn('id="log-limit"', html)
        self.assertIn('id="download-logs"', html)
        self.assertIn('id="cleanup-preview"', html)
        self.assertIn('"cleanup/preview"', script)
        self.assertIn('"cleanup/apply"', script)
        self.assertIn('user-actions-popover', script)
        self.assertIn('data-actions-toggle', script)
        self.assertNotIn('class="user-actions-menu"', script)
        self.assertIn('logs?source=', script)
        self.assertIn('static/app.js?v={{VERSION}}', html)
        self.assertNotIn('id="repair-wdtt"', html)
        self.assertNotIn('api("service/repair"', script)

    def test_accent_scheme_picker_is_exposed(self):
        html = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
        css = (ROOT / "wdtt_panel" / "static" / "app.css").read_text(encoding="utf-8")
        self.assertIn('id="accent-picker"', html)
        for accent in ("red", "orange", "yellow", "green", "blue", "purple"):
            self.assertIn(f'data-accent-option="{accent}"', html)
            self.assertIn(f'data-accent="{accent}"', css)
        self.assertNotIn("ukraine", html)
        self.assertNotIn("ukraine", script)
        self.assertNotIn("ukraine", css)
        self.assertIn("wdtt-accent", script)
        self.assertIn("restoreAccent()", script)
        self.assertIn("applyAccent", script)

    def test_user_labels_and_bulk_actions_are_exposed(self):
        html = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
        self.assertIn('id="edit-label"', html)
        self.assertIn('id="auto-user"', html)
        self.assertIn('id="auto-user-dialog"', html)
        self.assertIn('api("users/create-auto"', script)
        self.assertIn('id="bulk-label-prefix"', html)
        self.assertIn('id="select-all-users"', html)
        self.assertIn('id="bulk-user-action"', html)
        self.assertIn('id="bulk-user-days"', html)
        self.assertIn('value="set_expiration"', html)
        self.assertIn('id="user-activity-dialog"', html)
        self.assertIn('id="user-auto-refresh-interval"', html)
        self.assertIn('data-user-sort="traffic"', html)
        self.assertIn('data-user-sort="speed"', html)
        self.assertIn('data-user-sort="activity"', html)
        self.assertIn('data-user-sort="label"', html)
        self.assertIn('data-user-sort="devices"', html)
        self.assertIn("Устройства", html)
        self.assertIn("Скорость", html)
        self.assertIn('lastUserActivity', script)
        self.assertIn('devicesCell', script)
        self.assertIn('user.max_devices', script)
        self.assertIn('user.max_down_mbps', script)
        self.assertIn('colspan="11"', script)
        self.assertIn('api("users/bulk-action"', script)
        self.assertIn('openUserActivity', script)
        self.assertIn('restoreActiveTab()', script)
        self.assertIn('setUserAutoRefresh', script)
        self.assertIn('sortedUsers', script)
        self.assertNotIn('id="enable-wdtt-extensions"', html)
        self.assertNotIn('api("wdtt/extensions/enable"', script)

    def test_admin_lock_is_in_writable_private_state(self):
        script = (ROOT / "wdtt_panel" / "admin.py").read_text(encoding="utf-8")
        self.assertIn("/var/lib/wdtt-panel-private/admin.lock", script)
        self.assertNotIn("/run/lock/wdtt-panel-admin.lock", script)

    def test_panel_service_allows_netlink_for_tproxy_policy_routing(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK", script)

    def test_panel_runs_as_root_to_bypass_forced_no_new_privileges(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('PANEL_RUN_AS_ROOT="${WDTT_PANEL_RUN_AS_ROOT:-1}"', script)
        self.assertNotIn("detect_no_new_privileges", script)

        panel_files = script[script.index("install_panel_files() {"):script.index("write_maintenance_scripts() {")]
        self.assertIn('rm -f "$SUDOERS_FILE"', panel_files)

        panel_service = script[script.index("write_panel_service() {"):script.index("remove_obsolete_fleet_agent() {")]
        self.assertIn("User=root", panel_service)
        self.assertIn("Environment=WDTT_PANEL_ADMIN=$ADMIN_WRAPPER", panel_service)
        self.assertIn("$panel_admin_env", panel_service)
        self.assertIn("RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK", panel_service)
        self.assertIn("-/etc/systemd/system", panel_service)

    def test_installer_seeds_vk_hash_library_from_userdata(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('USERDATA_DIR="${WDTT_USERDATA_DIR:-$SCRIPT_DIR/../userdata}"', script)
        self.assertIn('SEED_HASHES_FILE="$CONFIG_DIR/vk-hash.txt"', script)
        panel_files = script[script.index("install_panel_files() {"):script.index("write_maintenance_scripts() {")]
        self.assertIn("install_vk_hash_seed", panel_files)
        seed = ROOT.parent / "userdata" / "vk-hash.txt"
        self.assertTrue(seed.is_file())
        self.assertTrue(all(line.strip() for line in seed.read_text(encoding="utf-8").splitlines()))

    def test_installer_removes_obsolete_fleet_agent(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("remove_obsolete_fleet_agent()", script)
        self.assertIn("wdtt-fleet-agent.service", script)
        self.assertIn('$STATE_DIR/fleet-agent.json', script)
        self.assertNotIn("write_fleet_agent_service()", script)

    def test_installer_removes_obsolete_openwrt_podkop_xray_config(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("remove_obsolete_openwrt_podkop()", script)
        self.assertIn('"podkop_native_enabled"', script)
        self.assertIn('"podkop-plus-in"', script)
        self.assertIn('systemctl restart "$XRAY_SERVICE"', script)

    def test_dialog_cancel_buttons_skip_required_field_validation(self):
        html = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
        self.assertEqual(html.count('value="cancel" formnovalidate'), 8)

    def test_distro_detection_supports_astra_and_id_like(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("astra", installer)
        self.assertIn("ID_LIKE", installer)
        self.assertIn("ensure_modern_python", installer)
        self.assertIn("IS_ASTRA", installer)
        self.assertIn("(3, 8)", installer)
        self.assertIn("3.9.22", installer)
        self.assertIn("PYTHON3_BIN", installer)
        self.assertIn("exec $PYTHON3_BIN -m wdtt_panel.admin", installer)
        self.assertIn("ExecStart=$PYTHON3_BIN -m wdtt_panel.app", installer)
        self.assertNotIn("exec /usr/bin/python3", installer)
        deploy = (ROOT.parent / "src" / "proxy-turn-vk-android-1.4.3" / "app" / "src" / "main" / "assets" / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("astra", deploy)
        self.assertIn("ID_LIKE", deploy)

    def test_uninstall_closes_wdtt_ports_but_keeps_80_443(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('local port="$1" proto="${2:-tcp}"', installer)
        self.assertIn('for kernel_port in 56000 56001 56002', installer)
        self.assertIn('remove_firewall_rule "$panel_port" tcp', installer)
        self.assertIn('remove_firewall_rule "$kernel_port" tcp', installer)
        self.assertIn('remove_firewall_rule "$kernel_port" udp', installer)
        self.assertIn("80/443", installer)
        self.assertNotIn('remove_firewall_rule "80', installer)
        self.assertNotIn("remove_firewall_rule '80", installer)
        self.assertNotIn('remove_firewall_rule "443', installer)
        self.assertNotIn("remove_firewall_rule '443", installer)

    def test_firewall_helpers_support_old_ufw_without_comment(self):
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("ufw_allow()", installer)
        self.assertIn('ufw allow "$spec" comment "$comment"', installer)
        self.assertIn('ufw allow "$spec"', installer)
        self.assertIn('ufw_allow "80/tcp"', installer)
        self.assertIn('ufw_allow "$PANEL_HTTPS_PORT/tcp"', installer)
        self.assertNotIn("ufw allow 80/tcp comment", installer)
        deploy = (ROOT.parent / "src" / "proxy-turn-vk-android-1.4.3" / "app" / "src" / "main" / "assets" / "deploy.sh").read_text(encoding="utf-8")
        self.assertIn("ufw_is_active()", deploy)
        self.assertIn("ufw_allow_port()", deploy)
        self.assertIn('ufw_allow_port udp "$port"', deploy)
        self.assertIn('ufw_allow_port tcp "$port"', deploy)

    def test_root_uninstall_removes_kernel_via_deploy(self):
        root_installer = (ROOT.parent / "install.sh").read_text(encoding="utf-8")
        self.assertIn("uninstall_wdtt_kernel()", root_installer)
        self.assertIn("uninstall_wdtt_kernel() {", root_installer)
        self.assertIn("app/src/main/assets/deploy.sh", root_installer)
        self.assertIn('"$deploy" uninstall', root_installer)

    def test_panel_self_update_wrapper_uses_git_and_install(self):
        script = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn("SELF_UPDATE_WRAPPER=", script)
        self.assertIn("SOURCE_CONF_FILE=", script)
        self.assertIn("record_source_repo()", script)
        self.assertIn("write_self_update_wrapper()", script)
        self.assertIn("restart_services()", script)
        self.assertIn("restart|--restart) restart_services ;;", script)
        self.assertIn("WDTT_REPO_DIR", script)
        self.assertIn("robust_fetch()", script)
        self.assertIn('fetch --force --prune origin "+refs/heads/*:refs/remotes/origin/*"', script)
        self.assertIn('git -C "$REPO" reset --hard "$UPSTREAM"', script)
        self.assertIn("cannot lock ref", script)
        self.assertNotIn('merge --ff-only "$UPSTREAM"', script)
        self.assertIn('bash "$REPO/install.sh" update', script)
        self.assertIn('bash "$REPO/install.sh" restart', script)
        self.assertIn("self-update-status.json", script)
        self.assertIn("WDTT_SELF_UPDATE_RELOCATED", script)
        self.assertIn("mktemp", script)

    def test_root_installer_exposes_restart(self):
        script = (ROOT.parent / "install.sh").read_text(encoding="utf-8")
        self.assertIn('restart|--restart) require_local_sources; exec bash "$PANEL_INSTALL" "$@" ;;', script)

    def test_panel_exposes_self_update_controls(self):
        html = (ROOT / "wdtt_panel" / "templates" / "index.html").read_text(encoding="utf-8")
        script = (ROOT / "wdtt_panel" / "static" / "app.js").read_text(encoding="utf-8")
        app = (ROOT / "wdtt_panel" / "app.py").read_text(encoding="utf-8")
        admin = (ROOT / "wdtt_panel" / "admin.py").read_text(encoding="utf-8")
        self.assertIn('id="update-panel"', html)
        self.assertIn('id="check-panel-update"', html)
        self.assertIn('id="panel-version-info"', html)
        self.assertIn('api("panel/update"', script)
        self.assertIn('api("panel/check"', script)
        self.assertIn('api("panel/version"', script)
        self.assertIn('"panel/version": "panel.version"', app)
        self.assertIn('"panel/check": "panel.check"', app)
        self.assertIn('"panel/update": "panel.update"', app)
        self.assertIn('"panel.version": panel_version', admin)
        self.assertIn('"panel.check": schedule_panel_check', admin)
        self.assertIn('"panel.update": start_panel_update', admin)
        self.assertIn('"systemd-run"', admin)
        self.assertIn("schedulePanelVersionPoll(0, Number", script)
        self.assertIn('state: "checking"', script)
        installer = (ROOT / "install.sh").read_text(encoding="utf-8")
        self.assertIn('set_status checking', installer)


if __name__ == "__main__":
    unittest.main()
