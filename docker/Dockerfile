# syntax=docker/dockerfile:1
# =============================================================================
# WDTT-SERVER — образ «всё-в-одном» (ядро + веб-панель + nginx) для docker compose.
#
# Сборка ядра — из замороженных локальных исходников форка (src/), как в
# build.sh и install.sh: поверх накладывается расширение панели
# (wdtt_server_patch.py). Панель — чистый Python (wsgiref), внешних
# pip-зависимостей не требует (certbot живёт в собственном venv, как в install.sh).
#
# Сборка:
#   docker build -t wdtt-server:local -f docker/Dockerfile .
#   docker build -t wdtt-server:local --build-arg BASE_IMAGE=debian:13 -f docker/Dockerfile .
#
# Запуск: см. docker-compose.yml и docker/README.md.
# =============================================================================
ARG BASE_IMAGE=ubuntu:24.04

# ───────────────────── Stage 1: сборка ядра (Go 1.25.0) ─────────────────────
FROM golang:1.25.0 AS kernel-build

RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Замороженный форк ядра qWDTT v1.4.3 + патч «расширение панели».
# Патчу нужны соседние файлы скрипта (wdtt_server_extension.go — шаблон,
# wdtt_server_extension_test.go): он читает их через Path(__file__).with_name().
COPY src/proxy-turn-vk-android-1.4.3 ./wdtt
COPY panel/wdtt_panel/wdtt_server_patch.py \
     panel/wdtt_panel/wdtt_server_extension.go \
     panel/wdtt_panel/wdtt_server_extension_test.go \
     ./build-tools/

# Накладываем расширение панели: метки Telegram, трафик/активность юзеров,
# квоты, runtime-тюнабели (маркер wdtt-panel-extension-v10).
RUN python3 /workspace/build-tools/wdtt_server_patch.py /workspace/wdtt

WORKDIR /workspace/wdtt/server
ENV CGO_ENABLED=0 GOFLAGS=-mod=mod GOCACHE=/root/.cache/go-build
RUN mkdir -p /out && go build -trimpath -ldflags='-s -w' -o /out/wdtt-server .

# ───────────────────────── Stage 2: runtime ─────────────────────────────────
FROM ${BASE_IMAGE}

ARG DEBIAN_FRONTEND=noninteractive
ENV DEBIAN_FRONTEND=noninteractive

# Системные пакеты — объединение списков install.sh панели и deploy.sh ядра.
# python3-venv/pip нужны только для certbot (в собственном venv).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        python3 python3-venv python3-pip \
        nginx openssl ca-certificates curl \
        iproute2 iptables nftables conntrack \
        unzip procps psmisc iputils-ping tzdata \
 && rm -rf /var/lib/apt/lists/*

# Tailscale в контейнер НЕ ставится: у Docker-хоста (и у машины, где уже есть
# tailnet) он свой, а контейнеру достаётся маршрут через хост. Второй tailscaled
# в контейнере только плодил «Logged out»-ноды и ломал доступ к ресурсам
# tailnet (например, к SOCKS5-серверу на 100.66.0.3). Вкладка Tailscale в
# панели под Docker показывает, что управление — на хосте.

# certbot в venv — ровно как делает install.sh (install_certbot).
RUN python3 -m venv /opt/wdtt-panel/certbot \
 && /opt/wdtt-panel/certbot/bin/pip install --no-cache-dir --upgrade pip \
 && /opt/wdtt-panel/certbot/bin/pip install --no-cache-dir 'certbot>=5.4,<6' \
 && rm -rf /root/.cache/pip

# Каркас каталогов проекта (совпадает с путями оригинальной установки)
RUN mkdir -p /etc/wdtt /etc/wdtt-panel/tls \
             /var/lib/wdtt-panel/acme /var/lib/wdtt-panel-private/backups \
             /opt/wdtt-panel /opt/userdata /var/log/wdtt /run \
 && rm -f /etc/nginx/conf.d/default.conf /etc/nginx/sites-enabled/default

# Ядро WDTT с расширением панели
COPY --from=kernel-build /out/wdtt-server /usr/local/bin/wdtt-server

# Веб-панель (pure-stdlib Python, без зависимостей)
COPY panel/wdtt_panel /opt/wdtt-panel/wdtt_panel

# wdtt-proxy — монолитный SOCKS5-шлюз (sing-box tun + policy routing).
# sing-box ставим фиксированной версии туда же, куда и install.sh хоста.
ARG SINGBOX_VERSION=1.14.3
RUN set -e; arch="$(dpkg --print-architecture)"; \
  case "$arch" in \
    amd64) sb=amd64 ;; \
    arm64) sb=arm64 ;; \
    armhf) sb=armv7 ;; \
    *) echo "sing-box: неподдерживаемая архитектура $arch" >&2; exit 1 ;; \
  esac; \
  curl -fsSL --retry 3 --max-time 600 \
    "https://github.com/SAGernet/sing-box/releases/download/v${SINGBOX_VERSION}/sing-box-${SINGBOX_VERSION}-linux-${sb}.tar.gz" \
    -o /tmp/sing-box.tar.gz; \
  tar -xzf /tmp/sing-box.tar.gz -C /tmp; \
  install -m 0755 "/tmp/sing-box-${SINGBOX_VERSION}-linux-${sb}/sing-box" /usr/local/bin/sing-box; \
  rm -rf /tmp/sing-box.tar.gz "/tmp/sing-box-${SINGBOX_VERSION}-linux-${sb}"; \
  sing-box version

# Файлы шлюза: рабочий каталог /opt/wdtt-proxy + копия исходников рядом с
# панелью, чтобы `install.sh install-wdtt-proxy` работал и внутри контейнера.
COPY wdtt-proxy /opt/wdtt-proxy
COPY wdtt-proxy /opt/wdtt-panel/wdtt-proxy
COPY panel/install.sh /opt/wdtt-panel/install.sh
RUN chmod 0755 /opt/wdtt-proxy/wdtt-proxy.sh /opt/wdtt-panel/wdtt-proxy/wdtt-proxy.sh /opt/wdtt-panel/install.sh \
 && chmod 0600 /opt/wdtt-proxy/config.env /opt/wdtt-panel/wdtt-proxy/config.env \
 && chmod 0644 /opt/wdtt-proxy/wdtt-proxy.service /opt/wdtt-proxy/README.md

# Сид VK-хешей из репозитория (userdata/vk-hash.txt)
COPY userdata /opt/userdata

# Админ-обёртка панели: WDTT_PANEL_ADMIN указывает сюда, панель шлёт сюда
# JSON-действия (как оригинальный /usr/local/sbin/wdtt-panel-admin)
RUN printf '#!/bin/sh\ncd /opt/wdtt-panel || exit 1\nexec python3 -m wdtt_panel.admin\n' \
        > /usr/local/bin/wdtt-panel-admin \
 && chmod 0755 /usr/local/bin/wdtt-panel-admin

# Обвязка Docker: entrypoint, супервизор, генераторы конфигов, утилиты
COPY docker/nginx.conf.tmpl        /etc/wdtt-docker/nginx.conf.tmpl
COPY docker/entrypoint.sh          /usr/local/sbin/wdtt-entrypoint
COPY docker/supervisord.sh         /usr/local/sbin/wdtt-supervisord
COPY docker/gen-nginx-conf.sh      /usr/local/sbin/wdtt-gen-nginx-conf
COPY docker/wdtt-exec-args.py      /usr/local/sbin/wdtt-exec-args
COPY docker/healthcheck.sh         /usr/local/sbin/wdtt-healthcheck
COPY docker/change-password.sh     /usr/local/sbin/wdtt-change-password
RUN chmod 0755 \
        /usr/local/sbin/wdtt-entrypoint \
        /usr/local/sbin/wdtt-supervisord \
        /usr/local/sbin/wdtt-gen-nginx-conf \
        /usr/local/sbin/wdtt-exec-args \
        /usr/local/sbin/wdtt-healthcheck \
        /usr/local/sbin/wdtt-change-password

# Healthcheck образа (дублируется в docker-compose при необходимости)
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=5 \
  CMD /usr/local/sbin/wdtt-healthcheck

STOPSIGNAL SIGTERM

ENTRYPOINT ["/usr/local/sbin/wdtt-entrypoint"]
CMD ["/usr/local/sbin/wdtt-supervisord"]