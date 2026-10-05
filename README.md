# WDTT-SERVER

Монолитный форк WDTT (VPN-сервер qWDTT + веб-панель) с **поднятыми лимитами**:

| Лимит | Оригинал | Форк WDTT-SERVER |
|-------|----------|------------------|
| Ключей (паролей) | 10 | **10000** (`maxGeneratedPasswords`) |
| Пользователей в панели | 10 | **10000** (`MAX_USERS`) |
| Устройств на ключ | 1 по умолчанию | **10000** по умолчанию; `max_devices <= 0` = безлимит |

Всё, что нужно для установки, лежит в этом репозитории:
ядро заморожено в [`src/`](src/proxy-turn-vk-android-1.4.3), панель — в [`panel/`](panel).
Установщик собирает/ставит **только локальные исходники форка** и не качает
оригинальный проект с GitHub.

## Состав

```
├── src/proxy-turn-vk-android-1.4.3/   замороженное ядро qWDTT v1.4.3 (форк с лимитами)
├── panel/                             замороженная панель 0.12.3 (форк) + её install.sh
├── patches/                            диффы изменений относительно upstream
├── scripts/                           вспомогательные скрипты (max_devices и т.п.)
├── conf/example.env                   переменные установки (пример)
├── install.sh  build.sh  update.sh  uninstall.sh
└── VERSIONS.md                        заморозка версий + список изменений
```

## Быстрая установка (Ubuntu 22.04+/Debian 12+/Astra Linux)

```bash
git clone https://git.a9fm.best/a9fm/WDTT-SERVER.git && cd WDTT-SERVER

# интерактивно (всё объяснит сам)
sudo ./install.sh

# или полностью автоматически
sudo ./install.sh install --domain panel.example.com \
  --password 'Long-Random-Panel-Password' --non-interactive
```

Если панель уже установлена, `install.sh` покажет её текущую версию и версию из
репозитория и предложит обновить с сохранением конфига: перед обновлением
создаётся снимок `config.json`, пароль, URL-путь, порт и сертификаты
переносятся на новую версию. `--force-clean` вместо этого сносит установку для
чистой, `--non-interactive` обновляет без вопросов.

В мастере установки без параметров можно указать домен/IP, логин, пароль,
HTTPS-порт, секретный URL-путь; опционально — главный пароль WDTT и Telegram-бот.

Что делает установщик:

1. ставит системные зависимости (`python3`, `nginx`, `certbot`, `iptables`, …);
2. скачивает фиксированный Go toolchain в `env/` (только для сборки);
3. берёт ядро из `src/`, накладывает расширение панели, собирает `wdtt-server`;
4. запускает официальный `deploy.sh` ядра (systemd `wdtt.service`, `passwords.json`);
5. ставит панель в `/opt/wdtt-panel` + локальную копию ядра в `/opt/wdtt-panel/src`;
6. получает Let's Encrypt (или self-signed) сертификат, настраивает nginx;
7. включает Telegram-бот, авто-резервные копии и таймер продления сертификатов.

> Первая сборка требует интернет только для пакетов apt, Go toolchain и Go-модулей.
> Сам проект (ядро/панель) нигде не скачивается — всё уже в репозитории.

## Проверка после установки

```bash
systemctl status wdtt wdtt-panel
grep maxGeneratedPasswords /opt/wdtt-panel/src/proxy-turn-vk-android-1.4.3/server/database_bot.go
grep MAX_USERS /opt/wdtt-panel/wdtt_panel/core.py
```

В панели: **Пользователи → отсутствует счётчик 0/10**, показывается «N / 10000».
В Telegram-боте: `/list` покажет «Активно: N/10000».

## Обслуживание

```bash
sudo ./install.sh status                    # статус панели и WDTT
sudo ./install.sh renew-cert                # продление сертификата
sudo ./install.sh change-password           # смена пароля панели
sudo ./install.sh update                    # обновление панели/ядра из локального форка
sudo ./uninstall.sh                         # удаление панели (ядро и пользователи сохраняются)
```

### Управление лимитом устройств существующих ключей

Новым ключам ядро и панель сами ставят `max_devices=10000`. Для старых баз:

```bash
sudo scripts/wdtt-maxdevices-check.sh run      # поднять поле max_devices до 10000
sudo scripts/wdtt-maxdevices-check.sh verify   # проверка
```

### Сборка ядра отдельно (для CI/лаборатории)

```bash
./build.sh            # linux/amd64 или linux/arm64 по факту
./build.sh amd64      # явно
sudo install -m 0755 bin/wdtt-server /usr/local/bin/wdtt-server
sudo systemctl restart wdtt
```

## Требования

- Ubuntu 22.04/24.04, Debian 12+ или Astra Linux (на базе Debian, clean VPS, root);
- публичный IPv4 (для панели и VPN), открытые TCP 80/443/9999 и UDP 56000-56001/9000;
- интернет при первой сборке (Go toolchain + Go-модули).
  Повторные сборки используют кэш `env/`.

## Безопасность и бэкапы

- панель слушает только `127.0.0.1:8787`, наружу — Nginx по случайному URL;
- пароли панели — PBKDF2-HMAC-SHA256 (600 000 итераций), cookie Secure/HttpOnly;
- каждое изменение базы предваряется резервной копией (`/var/lib/wdtt-panel-private/backups`).

## Лицензия

Ядро — GNU GPL v3 (см. `src/.../LICENSE`). Панель — форк
[lebrit/wdtt-control-panel](https://github.com/lebrit/wdtt-control-panel) (0.12.3),
автор лицензии не объявлял; форк сохраняет авторские права и используется для
собственного хостинга. Подробнее: [VERSIONS.md](VERSIONS.md).

## Ссылки на upstream

- Ядро: https://github.com/SpaceNeuroX/proxy-turn-vk-android (v1.4.3)
- Панель: https://github.com/lebrit/wdtt-control-panel (0.12.3)
- Проект-предок (svariant): https://github.com/ildarmaga/wdtt

## Авторы

Полный список — в [CREDITS.md](CREDITS.md): оригинальный WDTT (amurcanov),
ядро qWDTT (SpaceNeuroX), панель (lebrit), проект-предок (ildarmaga),
maintainer форка WDTT-SERVER — [a9fm](https://git.a9fm.best/a9fm).