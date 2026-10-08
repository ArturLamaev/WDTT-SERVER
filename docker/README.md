# WDTT-SERVER в Docker Compose

Запуск всего стека (ядро qWDTT + веб-панель + nginx) одним контейнером.
Ядро собирается **из замороженных исходников форка** (`src/` + расширение
панели), как в `build.sh` — с GitHub при сборке качаются только Go toolchain
и Go-модули. Панель — чистый Python без pip-зависимостей.

## Быстрый старт

Требуется Docker Engine + Compose **v2.24+** (нужен `env_file.required`).
Все команды — из каталога `docker/` (compose-файл ссылается на контекст `..`):

```bash
cd docker
cp .env.example .env   # впишите пароли/домен (или оставьте пустыми — сгенерируются)
docker compose up -d --build   # первый старт: сборка образа (~3-7 мин: Go-модули)
docker compose logs -f # здесь появятся панельный URL, логин и пароли
```

Повторные запуски — уже просто `docker compose up -d` (образ собран,
секреты и данные лежат в volumes).

Откройте `https://<PANEL_HOST>:9999/<секретный-путь>/` (точные значения — в логах
первого старта). Сертификат по умолчанию самоподписанный — браузер попросит
подтвердить исключение.

На Debian 13 вместо Ubuntu 24.04:

```bash
WDTT_BASE_IMAGE=debian:13 docker compose up -d --build
```

## Требования к хосту

- Linux с `/dev/net/tun` (модуль `tun` в ядре VPS). Docker Desktop
  (Windows/Mac) и подобное не подойдут — TUN там не пробросить;
- опубликованные порты (см. таблицу ниже) открыты и в фаерволе VPS;
- опубликованные порты (см. таблицу ниже) открыты и в фаерволе VPS;
- интернет при первой сборке (apt, Go, Go-модули) и при первом старте
  (публичный IP для `PANEL_HOST`, скачивание Xray/wgcf, Let's Encrypt).

## Порты

| Порт | Протокол | Назначение |
|------|----------|------------|
| `9999` (`PANEL_HTTPS_PORT`) | TCP | Панель (HTTPS, секретный путь) |
| `80` | TCP | ACME-проверка Let's Encrypt + редирект на HTTPS |
| `56000` (`WDTT_DTLS_PORT`) | UDP | DTLS-туннель ядра |
| `56000` | TCP | API ядра |
| `56001` (`WDTT_WG_PORT`) | UDP | WireGuard (только loopback внутри контейнера) |
| `56001` (`WDTT_WG_PORT`) | UDP | WireGuard ядра. Как и в оригинальном `deploy.sh`, внутри контейнера открыт только с loopback — внешние клиенты ходят через DTLS 56000; проброс оставлен для паритета с bare-metal |
| `56002` (`WDTT_ADMIN_PORT`) | TCP | Admin API ядра — **наружу не публикуется** (только loopback) |

Экспериментальные порты ядра (`WDTT_DIRECT_PORT`, `WDTT_RAW_PORT`) выключены
по умолчанию; чтобы открыть — задайте переменные и раскомментируйте строки
`ports:` в `docker-compose.yml`.

Внутри контейнера entrypoint сам прописывает iptables/NAT
(MASQUERADE `10.66.0.0/16`, MSS clamping, FORWARD для `wdtt0`) — как
`deploy.sh` ядра. Нужны `cap_add: NET_ADMIN/NET_RAW` (уже в compose).

## Переменные (.env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `PANEL_USER` | `admin` | Логин панели |
| `PANEL_PASSWORD` | *(генерируется)* | Пароль панели, мин. 12 символов |
| `PANEL_PATH` | *(генерируется)* | Секретный URL-путь панели |
| `PANEL_HOST` | *(авто-IP)* | Домен или публичный IPv4 |
| `PANEL_HTTPS_PORT` | `9999` | Внешний HTTPS-порт |
| `PANEL_EMAIL` | — | Email для Let's Encrypt |
| `TLS_MODE` | `self-signed` | `self-signed` \| `letsencrypt` (нужны домен + открытый `:80`) |
| `WDTT_MAIN_PASSWORD` | *(генерируется)* | Главный пароль ядра (12–64 `[A-Za-z0-9._~-]`) |
| `WDTT_DTLS_PORT` / `WDTT_WG_PORT` / `WDTT_ADMIN_PORT` | `56000/56001/56002` | Порты ядра |
| `WDTT_DNS` | `1.1.1.1,1.0.0.1` | DNS для клиентов |
| `WDTT_DIRECT_PORT` / `WDTT_RAW_PORT` | — | Экспериментальные порты ядра |
| `WDTT_TELEGRAM_BOT_TOKEN` / `WDTT_TELEGRAM_ADMIN_ID` | — | Telegram-бот ядра |
| `TZ` | `UTC` | Часовой пояс |
| `AUTO_RESTART_HOURS` | `6` | Авто-рестарт ядра+панели, `0` — выкл. (аналог `wdtt-auto-restart.timer`) |
| `WDTT_FETCH_RUNTIME` | `1` | Докачивать Xray/wgcf при старте, `0` — пропуск |
| `WDTT_BASE_IMAGE` | `ubuntu:24.04` | База образа (`ubuntu:24.04` или `debian:13`) |

## Данные (volumes)

| Volume | Содержимое |
|---|---|
| `wdtt-data` | `/etc/wdtt` — `passwords.json`, секреты ядра |
| `wdtt-panel-config` | `/etc/wdtt-panel` — `config.json`, self-signed TLS |
| `wdtt-panel-state` | `/var/lib/wdtt-panel` — sqlite-состояние панели |
| `wdtt-panel-private` | `/var/lib/wdtt-panel-private` — Xray/WARP/бэкапы/расписания |
| `wdtt-letsencrypt` | `/etc/letsencrypt` — сертификаты LE |

Свой сид VK-хешей вместо вшитого в образ: раскомментируйте монтирование
`../userdata:/opt/userdata:ro` в compose.

## Обслуживание

```bash
docker compose logs -f wdtt                 # логи всего стека
docker compose exec wdtt wdtt-change-password 'Новый-пароль-мин-12'
docker compose exec wdtt wdtt-healthcheck && echo OK
docker compose down && docker compose up -d --build   # обновление образа
```

Бэкап данных:

```bash
docker run --rm -v wdtt-server_wdtt-data:/d -v wdtt-server_wdtt-panel-config:/c \
  -v wdtt-server_wdtt-panel-state:/s -v wdtt-server_wdtt-panel-private:/p \
  -v wdtt-server_wdtt-letsencrypt:/l -v "$PWD":/b ubuntu:24.04 \
  tar -czf /b/wdtt-backup.tar.gz /d /c /s /p /l
```

## Как это работает внутри

`entrypoint.sh` (одноразовая подготовка) → `supervisord.sh` (PID 1):

- панель запускается от root с `WDTT_SKIP_SYSTEMD=1` (штатный режим панели без
  systemd) и `WDTT_PANEL_ADMIN=/usr/local/sbin/wdtt-panel-admin`;
- аргументы `wdtt-server` пересобирает `wdtt-exec-args.py` из
  `wdtt-settings.json` **перед каждым стартом**, поэтому смена настроек во
  вкладке «WDTT» применяется при следующем рестарте ядра. Если вкладка «WDTT»
  меняет порты/флаги — перезапустите контейнер, systemd-юнита в контейнере нет
  (`save_wdtt_settings` при этом честно сообщает `applied: false`);
- таймеры systemd заменены циклами: автобэкапы читают расписание из панели
  (`daily`/`weekly`, время `ЧЧ:ММ`, воскресенье — как `OnCalendar` в install.sh),
  автоочистка — при `enabled`, GeoFiles — раз в сутки, `certbot renew` — раз
  в неделю (только при `tls_mode=letsencrypt`);
- Xray Core и wgcf докачиваются в entrypoint (latest с GitHub) в
  `/usr/local/bin` контейнера — при пересоздании контейнера скачаются заново;
  geoip/geosite складываются в `xray-assets` (volume `wdtt-panel-private`).

## Диагностика

- `wdtt0: operation not permitted` / нет MASQUERADE — контейнеру не хватает
  привилегий: проверьте `cap_add`, `devices: /dev/net/tun`, на некоторых
  хостингах нужен `privileged: true`.
- Панель отвечает, а VPN не коннектится — проверьте UDP-порты наружу
  (`nc -u`), IP-forward (`docker compose exec wdtt sysctl net.ipv4.ip_forward`)
  и NAT-правила (`docker compose exec wdtt iptables -t nat -L POSTROUTING -n`).
- `HTTPS local check` падает — смотрите `docker compose exec wdtt nginx -t`
  и логи `docker compose logs wdtt`.
