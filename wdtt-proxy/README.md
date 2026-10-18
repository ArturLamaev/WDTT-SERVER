# wdtt-proxy

Весь трафик, приходящий **с интерфейса `wdtt0`**, заворачивается в ваш
**SOCKS5-прокси**. Работает через [sing-box](https://sing-box.sagernet.org/)
(tun-интерфейс + policy routing), поэтому проксируются и TCP, и UDP (DNS).

> Компонент входит в состав **WDTT-SERVER** (монолит): каталог `wdtt-proxy/`
> в репозитории и есть этот шлюз. Установщик панели (`install.sh`) кладёт его
> в **`/opt/wdtt-proxy`** (рядом с панелью `/opt/wdtt-panel`) и ставит
> sing-box в `/usr/local/bin/sing-box`, а вкладка **«Прокси»** панели
> запускает/останавливает сервис и правит `config.env`. Ставить что-либо
> вручную не нужно.

Текущая настройка:

| | |
|---|---|
| Интерфейс-источник | `wdtt0` |
| SOCKS5 | `100.66.0.5:26666` (user `proxyuser`) |
| метка трафика | `fwmark 0x100` |
| tun sing-box | `sbtun0` (`10.200.0.1/30`, MTU 1280) |
| Таблица маршрутизации | `100` |

---

## Как это работает

1. `sing-box` создаёт tun-интерфейс `sbtun0` и общается с SOCKS5-прокси.
2. Весь трафик, **приходящий на интерфейс `wdtt0`**, помечается в
   `iptables -t mangle` меткой `fwmark 0x100` (правило `-i wdtt0`).
3. Правило policy-routing гонит помеченный трафик в таблицу `100`, где
   дефолт указывает на `sbtun0`. Захват идёт строго по интерфейсу, поэтому
   другие туннели с пересекающимися подсетями (например `wgtest0`) не задеты.
4. Дальше `sing-box` проксирует соединения в SOCKS5 и пишет ответы обратно.
5. Исключения (`EXCLUDE_CIDRS`) идут напрямую по основной таблице — по
   умолчанию это подсеть `10.66.0.0/16` (клиент↔клиент) и служебные диапазоны.
   Трафик самого хоста (в т.ч. к `100.66.0.5`) не затрагивается.

```
клиент wdtt0 ──► wdtt0 ──► [mark 0x100] ──► [policy route table 100] ──► sbtun0 ──► sing-box ──► SOCKS5 ──► интернет
```

---

## Настройка

Все параметры — в файле **`config.env`**. Меняете файл → `./wdtt-proxy.sh restart`.

```ini
PROXY_HOST="100.66.0.5"     # адрес прокси
PROXY_PORT="26666"
PROXY_USER="proxyuser"
PROXY_PASS="qwerty777LAMBERTED"

CAPTURE_IF="wdtt0"          # какой интерфейс заворачиваем
SOURCE_CIDR="10.66.0.0/16"  # подсеть его клиентов
```

## Управление

```bash
cd /opt/wdtt-proxy           # systemd-службой управляйте через systemctl
./wdtt-proxy.sh status      # состояние, правила, таблица
./wdtt-proxy.sh logs 100    # последние строки лога
./wdtt-proxy.sh gen-config  # показать сгенерированный конфиг sing-box
```

## Автозапуск (systemd)

На хосте, установленном штатным `install.sh` WDTT-SERVER, всё уже сделано:

```bash
systemctl start wdtt-proxy     # или кнопка «Запустить» во вкладке «Прокси»
systemctl status wdtt-proxy
```

Вручную (если ставите компонент отдельно):

```bash
install -m 0644 /opt/wdtt-proxy/wdtt-proxy.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now wdtt-proxy
systemctl status wdtt-proxy
```

После этого управление — через `systemctl {start,stop,restart} wdtt-proxy`
(скрипт `wdtt-proxy.sh` тогда не запускайте параллельно).

---

## Проверка

С хоста, находящегося в подсети `wdtt0` (реального клиента), проверьте IP выхода —
он должен совпадать с IP SOCKS5-прокси:

```bash
curl -s https://ifconfig.me       # на клиенте wdtt0 -> покажет IP прокси
```

Проверить, что правила и метка на месте:

```bash
iptables -t mangle -S WDTT_PROXY          # должно быть -j MARK --set-xmark 0x100/0xffffffff
iptables -t mangle -S PREROUTING | grep WDTT_PROXY   # -i wdtt0 ... -j WDTT_PROXY
ip rule show | grep 0x100                 # правила исключений + lookup 100
ip route show table 100                   # default dev sbtun0
```

## Диагностика

* Не работает → смотрите лог: `journalctl -u wdtt-proxy -n 100 --no-pager`
  (при запуске без systemd — `./wdtt-proxy.sh logs 100`).
* Перезапуск после правки `config.env`: `systemctl restart wdtt-proxy`.
* Прокси через tailscale: он должен быть доступен с хоста
  (`curl --socks5 ... https://ifconfig.me` без `--interface`).
* Меняли MTU `wdtt0` → поставьте такое же значение в `TUN_MTU`.
* Убрать всё: `./wdtt-proxy.sh stop` (снимает правила и останавливает sing-box).
