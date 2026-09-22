#!/bin/bash
set -e  # остановить скрипт при любой ошибке

echo "[INFO] Начинаем установку WDTT..."

# ------------------------------------------------------------
# Проверка root прав
# ------------------------------------------------------------
if [ "$(id -u)" -ne 0 ]; then
    echo "[WARN] Требуются права root. Пытаюсь поднять через sudo..."
    # 1) passwordless sudo
    if sudo -n true 2>/dev/null; then
        echo "[INFO] sudo без пароля — перезапуск..."
        exec sudo bash "$0" "$@"
    fi
    # 2) обычный sudo (спросит пароль)
    echo "[INFO] sudo запросит пароль..."
    if sudo bash "$0" "$@" 2>/dev/null; then
        exit 0
    fi
    # 3) sudo не сработал — объясняем почему
    echo ""
    echo "[DEBUG] whoami=$(whoami) uid=$(id -u) NoNewPrivs=$(grep NoNewPrivs /proc/self/status 2>/dev/null | awk '{print $2}')"
    if sudo -n true 2>&1 | grep -qi "no new privileges"; then
        echo ""
        echo "[ERROR] Контейнер запущен с флагом no-new-privileges — sudo физически не может"
        echo "[ERROR] поднять права. Решение: пересоздай контейнер с опцией"
        echo "[ERROR]   --security-opt no-new-privileges=false"
        echo "[ERROR] или зайди в него от root: docker exec -u 0 -it <container> bash"
    else
        echo ""
        echo "[ERROR] sudo не сработал (нет прав в sudoers или нет tty)."
        echo "[ERROR] Выполни: su - && bash wdtt.sh"
    fi
    exit 1
else
    echo "[OK] Запущен от root"
fi

# ------------------------------------------------------------
# Проверка старой установки
# ------------------------------------------------------------
echo "[INFO] Проверяем старую установку WDTT..."

OLD_FOUND=0
for p in /opt/wdtt-panel /etc/wdtt-panel /var/lib/wdtt-panel /var/lib/wdtt-panel-private /etc/wdtt /etc/nginx/conf.d/wdtt-panel.conf /usr/local/sbin/wdtt-panel*; do
  [ -e "$p" ] && OLD_FOUND=1 && break
done
systemctl list-units --all 2>/dev/null | grep -qE 'wdtt-panel|wdtt\.service' && OLD_FOUND=1

if [ "$OLD_FOUND" -eq 1 ]; then
  echo "[INFO] Обнаружена старая панель WDTT / wdtt-server:"
  ls -ld /opt/wdtt-panel /etc/wdtt-panel /var/lib/wdtt-panel /etc/wdtt 2>/dev/null | sed 's/^/  /' || true
  if systemctl is-active wdtt 2>/dev/null | grep -q active; then
    echo "[INFO] Сервис wdtt: active"
  else
    echo "[INFO] Сервис wdtt: inactive"
  fi
  if systemctl is-active wdtt-panel 2>/dev/null | grep -q active; then
    echo "[INFO] Сервис wdtt-panel: active"
  else
    echo "[INFO] Сервис wdtt-panel: inactive"
  fi
  echo ""
  _ans=""
  if [ -r /dev/tty ] && [ -w /dev/tty ]; then
    printf "Снести старую панель и WDTT для чистой установки? [y/N]: " > /dev/tty
    IFS= read -r _ans </dev/tty || true
  else
    printf "Снести старую панель и WDTT для чистой установки? [y/N]: "
    IFS= read -r _ans || true
  fi
  case "${_ans:-N}" in
    y|Y|yes|YES|да|Да)
      echo "[INFO] Делаю полную очистку..."
      systemctl stop wdtt 2>/dev/null || true
      systemctl stop wdtt-panel 2>/dev/null || true
      systemctl disable wdtt 2>/dev/null || true
      systemctl disable wdtt-panel 2>/dev/null || true
      pkill -x wdtt-server 2>/dev/null || true
      pkill -x wdtt-app 2>/dev/null || true
      rm -f /etc/systemd/system/wdtt.service /etc/systemd/system/wdtt-panel.service /etc/systemd/system/wdtt-panel-wdtt-extensions.* 2>/dev/null || true
      systemctl daemon-reload 2>/dev/null || true
      rm -f /usr/local/bin/wdtt-server /usr/local/bin/wdtt-app /usr/local/bin/xray 2>/dev/null || true
      rm -rf /opt/wdtt-panel /etc/wdtt-panel /var/lib/wdtt-panel /var/lib/wdtt-panel-private 2>/dev/null || true
      rm -f /etc/nginx/conf.d/wdtt-panel.conf 2>/dev/null || true
      rm -f /usr/local/sbin/wdtt-panel* /etc/sudoers.d/wdtt-panel 2>/dev/null || true
      rm -f /etc/wdtt/passwords.json /etc/wdtt/panel.db /etc/wdtt/*.db /etc/wdtt/server.log /etc/wdtt/*.log 2>/dev/null || true
      rm -f /etc/wdtt/main.password /etc/wdtt/admin.token /etc/wdtt/admin.crt /etc/wdtt/admin.key /etc/wdtt/bot.token /etc/wdtt/wg-keys.dat 2>/dev/null || true
      rm -f /etc/wdtt/unlimited.key /tmp/wdtt-unlimited.env 2>/dev/null || true
      rm -f /var/log/wdtt-panel* 2>/dev/null || true
      echo "[OK] Старая установка снесена"
      ;;
    *)
      echo "[INFO] Пропускаю снос, ставлю поверх (может остаться мусор)"
      ;;
  esac
  echo ""
else
  echo "[OK] Старая панель не найдена — ставлю начисто"
fi

sleep 1

# ------------------------------------------------------------
# Установка WDTT через официальный bootstrap
# ------------------------------------------------------------
echo "[INFO] Устанавливаем WDTT (lebrit/wdtt-control-panel)..."
curl -fsSL https://raw.githubusercontent.com/lebrit/wdtt-control-panel/main/bootstrap.sh | bash

# ------------------------------------------------------------
# Проверка, что панель встала корректно (sudoers + врапперы)
# ------------------------------------------------------------
echo "[INFO] Проверяем установку панели..."
PANEL_BROKEN=0
for f in /etc/sudoers.d/wdtt-panel /usr/local/sbin/wdtt-panel-admin /opt/wdtt-panel; do
    if [ -e "$f" ]; then
        echo "[OK] Найден $f"
    else
        echo "[ERROR] Отсутствует $f — установка панели не завершена!"
        PANEL_BROKEN=1
    fi
done
if [ -f /etc/sudoers.d/wdtt-panel ]; then
    if visudo -cf /etc/sudoers.d/wdtt-panel 2>/dev/null; then
        echo "[OK] sudoers панели валиден"
    else
        echo "[ERROR] sudoers панели невалиден!"
        PANEL_BROKEN=1
    fi
fi
if ! id -u wdtt-panel >/dev/null 2>&1; then
    echo "[ERROR] Пользователь wdtt-panel не создан — установка не завершена!"
    PANEL_BROKEN=1
else
    echo "[OK] Пользователь wdtt-panel существует"
fi
if [ "$PANEL_BROKEN" -eq 1 ]; then
    echo "[ERROR] Панель встала криво. Смотри лог: tail -n 50 /var/log/wdtt-panel-install.log"
    exit 1
fi

# ------------------------------------------------------------
# Авто-рестарт панели и WDTT каждые 6 часов
# ------------------------------------------------------------
echo "[INFO] Настраиваем авто-рестарт панели и WDTT каждые 6 часов..."

cat > /etc/systemd/system/wdtt-auto-restart.service <<'EOF'
[Unit]
Description=Restart WDTT and panel every 6h (prevent silent crash)
After=network.target

[Service]
Type=oneshot
ExecStart=/bin/bash -c 'systemctl restart wdtt.service 2>/dev/null || true; systemctl restart wdtt-panel.service 2>/dev/null || true; systemctl restart wdtt-app.service 2>/dev/null || true'
EOF

cat > /etc/systemd/system/wdtt-auto-restart.timer <<'EOF'
[Unit]
Description=Restart WDTT and panel every 6 hours

[Timer]
OnBootSec=6h
OnUnitActiveSec=6h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload 2>/dev/null || true
systemctl enable --now wdtt-auto-restart.timer 2>/dev/null || true

if systemctl is-enabled wdtt-auto-restart.timer 2>/dev/null | grep -q enabled; then
  echo "[OK] Таймер wdtt-auto-restart.timer включён (каждые 6ч)"
else
  echo "[WARN] Таймер не включился, ставлю cron fallback..."
  (crontab -l 2>/dev/null | grep -v "wdtt.*restart"; echo "0 */6 * * * /bin/systemctl restart wdtt.service wdtt-panel.service 2>/dev/null || true") | crontab - 2>/dev/null || true
  echo "[OK] Cron fallback: 0 */6 * * * restart wdtt/panel"
fi

systemctl status wdtt-auto-restart.timer --no-pager 2>/dev/null | head -n 5 || true

# ------------------------------------------------------------
# Финальные инструкции
# ------------------------------------------------------------
echo ""
echo "======================================================"
echo "[✅] Установка WDTT завершена!"
echo ""
echo "📌 Действия после установки:"
echo "1. Проверь статус WDTT:"
echo "   systemctl status wdtt"
echo "   systemctl status wdtt-panel"
echo ""
echo "2. Проверь логи:"
echo "   journalctl -u wdtt -f"
echo "   journalctl -u wdtt-panel -f"
echo ""
echo "3. Проверь таймер авто-рестарта:"
echo "   systemctl status wdtt-auto-restart.timer"
echo "   systemctl list-timers wdtt-auto-restart.timer"
echo ""
echo "4. Открой панель в браузере:"
echo "   https://<IP>:8443/<path>/  (параметры из bootstrap)"
echo ""
echo "5. Перезапуск вручную:"
echo "   systemctl restart wdtt wdtt-panel"
echo "======================================================"
