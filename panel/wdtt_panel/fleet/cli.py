"""CLI контроллера: ``python -m wdtt_panel.fleet ...``.

Команды:
  add            вбить URL ноды + логин/пароль (проверка связности + login сразу)
  remove         убрать ноду из реестра
  list           показать реестр
  status         overview с одной/всех нод
  user keys      найти ключ на всех нодах (ссылки + трафик)
  user create    создать пользователя на одной/всех нодах
  user delete    удалить пользователя с одной/всех нод
  bot            запустить Telegram-бота контроллера
  web            запустить веб-панель контроллера

Глобально: ``--store`` (путь к реестру), ``--json`` (машиночитаемый вывод
для будущего бота/API), ``--timeout``, ``--workers``.
Коды выхода: 0 — всё ок, 1 — частичный/полный провал.
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import secrets
import sys
from pathlib import Path
from typing import Any

from .bot import resolve_admins, run_bot
from .client import AuthError, FleetError, NodeClient, find_user
from .fanout import fanout, fanout_route, summarize
from .models import Node, split_node_url
from .store import FleetStore, default_store_path
from ..security import hash_password

EXIT_OK = 0
EXIT_FAIL = 1


def _emit(payload: Any, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(payload)


def _table(rows: list[list[str]], headers: list[str]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    lines.append("  ".join("-" * w for w in widths))
    for row in rows:
        lines.append("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row)))
    return "\n".join(lines)


def _open_store(args: argparse.Namespace) -> FleetStore:
    path = Path(args.store).expanduser() if args.store else default_store_path()
    return FleetStore(path)


def _pick_nodes(store: FleetStore, args: argparse.Namespace) -> list[Node]:
    if getattr(args, "node", None):
        node = store.get(args.node)
        if node is None:
            raise FleetError(f"Нода {args.node!r} нет в реестре (сначала add)")
        return [node]
    nodes = store.all()
    if not nodes:
        raise FleetError("Реестр пуст — сначала добавьте ноду командой add")
    return nodes


def cmd_add(args: argparse.Namespace) -> int:
    base_url, base_path = split_node_url(args.url)
    if args.path:
        from .models import normalize_base_path

        base_path = normalize_base_path(args.path)
    password = args.password if args.password is not None else getpass.getpass("Пароль ноды: ")
    node = Node(
        id=args.id,
        base_url=base_url,
        base_path=base_path,
        username=args.username or "admin",
        password=password,
        name=args.name or "",
    )
    client = NodeClient(node, timeout=args.timeout)
    try:
        info = client.probe()
        client.login()
    except (FleetError, AuthError) as exc:
        _emit(f"Нода недоступна или пароль неверный: {exc}", args.json and False)
        return EXIT_FAIL
    store = _open_store(args)
    try:
        store.add(node)
    except ValueError as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    panel_version = ((info.get("panel_version") or info.get("server") or {}) if isinstance(info, dict) else {})
    summary = {
        "ok": True,
        "id": node.id,
        "api_root": node.api_root(),
        "server": info if args.json else {k: info.get(k) for k in ("name", "panel_version", "api_version") if k in info},
        "note": f"панель: {panel_version}" if isinstance(panel_version, str) else "",
    }
    if args.json:
        _emit(summary, True)
    else:
        _emit(f"Добавлена нода {node.id} → {node.api_root()} (login ok)", False)
    return EXIT_OK


def cmd_remove(args: argparse.Namespace) -> int:
    store = _open_store(args)
    if store.remove(args.id):
        _emit({"ok": True, "removed": args.id}, args.json)
        return EXIT_OK
    _emit(f"Ноды {args.id!r} нет в реестре", False)
    return EXIT_FAIL


def cmd_list(args: argparse.Namespace) -> int:
    store = _open_store(args)
    nodes = store.all()
    if args.json:
        _emit({"ok": True, "nodes": [n.to_dict() for n in nodes]}, True)
        return EXIT_OK
    if not nodes:
        print("Реестр пуст. Добавьте ноду: fleet add --id ... --url ...")
        return EXIT_OK
    print(_table(
        [[n.id, n.display_name, n.api_root(), "online" if n.online else (n.last_error or "—")] for n in nodes],
        ["ID", "Имя", "URL", "Статус"],
    ))
    return EXIT_OK


def cmd_status(args: argparse.Namespace) -> int:
    store = _open_store(args)
    try:
        nodes = _pick_nodes(store, args)
    except FleetError as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    results = fanout(nodes, lambda c: c.overview(), workers=args.workers, timeout=args.timeout)
    store.save()
    summary = summarize(results)
    if args.json:
        _emit({"ok": summary["fail"] == 0, "summary": summary, "nodes": results}, True)
        return EXIT_OK if summary["fail"] == 0 else EXIT_FAIL
    rows = []
    for nid, entry in results.items():
        if entry.get("ok"):
            res = entry.get("result") or {}
            stats = res.get("stats") or {}
            rows.append([nid, "online",
                         f"{stats.get('active', '?')}/{stats.get('total', '?')}",
                         f"{entry.get('latency', 0):.1f}s"])
        else:
            rows.append([nid, "FAIL", str(entry.get("error", "?"))[:60], "—"])
    print(_table(rows, ["Нода", "Статус", "Активно/Всего", "Пинг"]))
    print(f"Итог: ok {summary['ok']}/{summary['total']}", end="")
    if summary["fail"]:
        print(f", провалов: {summary['fail']}")
        for nid, err in summary["errors"].items():
            print(f"  {nid}: {err}")
    else:
        print()
    return EXIT_OK if summary["fail"] == 0 else EXIT_FAIL


def cmd_user_keys(args: argparse.Namespace) -> int:
    store = _open_store(args)
    try:
        nodes = _pick_nodes(store, args)
    except FleetError as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    results = fanout(nodes, lambda c: c.users_list(), workers=args.workers, timeout=args.timeout)
    store.save()
    found: dict[str, Any] = {}
    for nid, entry in results.items():
        if not entry.get("ok"):
            found[nid] = {"ok": False, "error": entry.get("error")}
            continue
        user = find_user(entry.get("result") or {}, args.password)
        if user is None:
            found[nid] = {"ok": True, "present": False}
        else:
            keep = {k: user.get(k) for k in ("password", "label", "link", "url", "vk_hash",
                                             "up_bytes", "down_bytes", "connected",
                                             "last_handshake", "expires_at") if k in user}
            found[nid] = {"ok": True, "present": True, "user": keep}
    summary = {"total": len(found),
               "present": sum(1 for v in found.values() if v.get("present")),
               "node_errors": sum(1 for v in found.values() if not v.get("ok"))}
    if args.json:
        _emit({"ok": True, "key": args.password, "summary": summary, "nodes": found}, True)
        return EXIT_OK
    print(f"Ключ {args.password!r}: найден на {summary['present']}/{summary['total']} нод")
    for nid, item in found.items():
        if not item.get("ok"):
            print(f"  {nid}: ОШИБКА Ноды: {item.get('error')}")
        elif not item.get("present"):
            print(f"  {nid}: нет")
        else:
            user = item["user"]
            extra = f" label={user['label']!r}" if user.get("label") else ""
            print(f"  {nid}: ЕСТЬ{extra} link={user.get('link') or user.get('url') or '—'}")
    return EXIT_OK


def _user_payload(args: argparse.Namespace) -> dict[str, Any]:
    payload: dict[str, Any] = {"password": args.password}
    if getattr(args, "label", None):
        payload["label"] = args.label
    if getattr(args, "vk_hash", None):
        payload["vk_hash"] = args.vk_hash
    if getattr(args, "max_devices", None) is not None:
        payload["max_devices"] = args.max_devices
    if getattr(args, "expires", None):
        payload["expires_at"] = args.expires
    return payload


def cmd_user_create(args: argparse.Namespace) -> int:
    store = _open_store(args)
    try:
        nodes = _pick_nodes(store, args)
    except FleetError as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    payload = _user_payload(args)
    results = fanout_route(nodes, "users/create", payload, workers=args.workers, timeout=args.timeout)
    store.save()
    summary = summarize(results)
    if args.json:
        _emit({"ok": summary["fail"] == 0, "summary": summary, "nodes": results}, True)
        return EXIT_OK if summary["fail"] == 0 else EXIT_FAIL
    print(f"Создание {args.password!r}: ok {summary['ok']}/{summary['total']}")
    for nid, entry in results.items():
        print(f"  {nid}: {'OK' if entry.get('ok') else 'ОШИБКА: ' + str(entry.get('error'))}")
    return EXIT_OK if summary["fail"] == 0 else EXIT_FAIL


def cmd_user_delete(args: argparse.Namespace) -> int:
    store = _open_store(args)
    try:
        nodes = _pick_nodes(store, args)
    except FleetError as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    if len(nodes) > 1 and not args.everywhere and not args.node:
        _emit("Больше одной ноды: укажите --node ID или --everywhere", False)
        return EXIT_FAIL
    results = fanout(nodes, lambda c: c.user_delete(args.password),
                     workers=args.workers, timeout=args.timeout)
    store.save()
    summary = summarize(results)
    if args.json:
        _emit({"ok": summary["fail"] == 0, "summary": summary, "nodes": results}, True)
        return EXIT_OK if summary["fail"] == 0 else EXIT_FAIL
    print(f"Удаление {args.password!r}: ok {summary['ok']}/{summary['total']}")
    for nid, entry in results.items():
        print(f"  {nid}: {'OK' if entry.get('ok') else 'ОШИБКА: ' + str(entry.get('error'))}")
    return EXIT_OK if summary["fail"] == 0 else EXIT_FAIL


def cmd_bot(args: argparse.Namespace) -> int:
    from .settings import default_config_path, load_settings, resolve_bot_config, save_settings

    cfg_path = default_config_path(Path(args.store).expanduser() if args.store else None)
    token, admins, source, _ = resolve_bot_config(args.token, args.admin, cfg_path)
    if args.save:
        _, saved = load_settings(cfg_path)
        if args.token:
            saved.bot_token = args.token
        if args.admin:
            saved.admin_ids = sorted(resolve_admins(",".join(args.admin)))
        if args.poll != 25:
            saved.poll_timeout = args.poll
        save_settings(saved, cfg_path)
        print(f"Сохранено в {cfg_path}")
    if not token:
        _emit("Нужен токен: --token, $WDTT_FLEET_BOT_TOKEN или страница «Бот» в панели", False)
        return EXIT_FAIL
    if not admins:
        _emit("Нужен хотя бы один admin id: --admin, $WDTT_FLEET_ADMINS или страница «Бот»", False)
        return EXIT_FAIL
    print(f"Источник настроек: {source} ({cfg_path})")
    try:
        run_bot(_open_store(args), token, set(admins),
                workers=args.workers, timeout=args.timeout, poll_timeout=args.poll)
    except (ValueError, FleetError) as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("Остановлен.")
    return EXIT_OK


def cmd_web(args: argparse.Namespace) -> int:
    from .settings import load_controller_config
    from .web import serve

    username = args.username or os.environ.get("WDTT_FLEET_USER", "admin")
    password = args.password or os.environ.get("WDTT_FLEET_PASSWORD", "")
    secret = os.environ.get("WDTT_FLEET_SECRET", "") or secrets.token_urlsafe(32)
    listen, port, base = args.listen, args.port, args.path
    password_hash = ""
    if args.config:
        try:
            cfg = load_controller_config(args.config)
        except ValueError as exc:
            _emit(str(exc), False)
            return EXIT_FAIL
        username, password_hash, secret = cfg["username"], cfg["password_hash"], cfg["secret"]
        listen, port, base = cfg["listen_host"], cfg["listen_port"], cfg["base_path"]
        for key, env in (("store_path", "WDTT_FLEET_STORE"),
                         ("store_config_path", "WDTT_FLEET_CONFIG")):
            if cfg[key] and not os.environ.get(env):
                os.environ[env] = cfg[key]
    else:
        if password is None:
            password = ""
        if not password and sys.stdin.isatty():
            password = getpass.getpass("Пароль веб-панели контроллера: ")
        if len(password) < 12:
            _emit("Пароль веб-панели: минимум 12 символов (--password или $WDTT_FLEET_PASSWORD)", False)
            return EXIT_FAIL
        try:
            password_hash = hash_password(password)
        except ValueError as exc:
            _emit(str(exc), False)
            return EXIT_FAIL
    print(f"Fleet web: http://{listen}:{port}/ (Ctrl+C — стоп)")
    try:
        serve(_open_store(args), listen, port, username,
              password_hash, secret, base=base)
    except OSError as exc:
        _emit(f"Не могу слушать {listen}:{port}: {exc}", False)
        return EXIT_FAIL
    except KeyboardInterrupt:
        print("Остановлен.")
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="wdtt-fleet", description="Управление нодами WDTT с одного места")
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--store", default=argparse.SUPPRESS,
                        help="Путь к реестру нод (иначе $WDTT_FLEET_STORE или ~/.wdtt-fleet/nodes.json)")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS,
                        help="Вывод JSON (для бота/API)")
    common.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                        help="Таймаут одного запроса, сек")
    common.add_argument("--workers", type=int, default=argparse.SUPPRESS,
                        help="Параллельных запросов к нодам")
    parser = argparse.ArgumentParser(prog="wdtt-fleet", description="Управление нодами WDTT с одного места")
    parser.add_argument("--store", default="", help="Путь к реестру нод (иначе $WDTT_FLEET_STORE или ~/.wdtt-fleet/nodes.json)")
    parser.add_argument("--json", action="store_true", help="Вывод JSON (для бота/API)")
    parser.add_argument("--timeout", type=float, default=30.0, help="Таймаут одного запроса, сек")
    parser.add_argument("--workers", type=int, default=10, help="Параллельных запросов к нодам")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", parents=[common], help="Добавить ноду: URL + логин/пароль")
    p_add.add_argument("--id", required=True, help="Короткий ID ноды (латиница)")
    p_add.add_argument("--url", required=True, help="URL панели ноды, можно с секретным путём")
    p_add.add_argument("--path", default="", help="Секретный путь панели (если не входит в --url)")
    p_add.add_argument("--username", default="admin")
    p_add.add_argument("--password", default=None, help="Если нет — спросит скрытно")
    p_add.add_argument("--name", default="", help="Человеческое имя (дефолт = ID)")
    p_add.set_defaults(func=cmd_add)

    p_rm = sub.add_parser("remove", parents=[common], help="Убрать ноду из реестра")
    p_rm.add_argument("--id", required=True)
    p_rm.set_defaults(func=cmd_remove)

    p_list = sub.add_parser("list", parents=[common], help="Показать реестр нод")
    p_list.set_defaults(func=cmd_list)

    p_status = sub.add_parser("status", parents=[common], help="Статус одной/всех нод (overview)")
    p_status.add_argument("--node", default="", help="Только эта нода (иначе все)")
    p_status.set_defaults(func=cmd_status)

    p_keys = sub.add_parser("keys", parents=[common], help="Найти ключ на всех нодах")
    p_keys.add_argument("--password", required=True, help="Ключ пользователя")
    p_keys.add_argument("--node", default="")
    p_keys.set_defaults(func=cmd_user_keys)

    p_create = sub.add_parser("create", parents=[common], help="Создать пользователя на одной/всех нодах")
    p_create.add_argument("--password", required=True, help="Ключ (пароль) нового пользователя")
    p_create.add_argument("--label", default="")
    p_create.add_argument("--vk-hash", default="")
    p_create.add_argument("--max-devices", type=int, default=None)
    p_create.add_argument("--expires", default="", help="Срок жизни (формат панели)")
    p_create.add_argument("--node", default="", help="Только эта нода (иначе все)")
    p_create.set_defaults(func=cmd_user_create)

    p_del = sub.add_parser("delete", parents=[common], help="Удалить пользователя с одной/всех нод")
    p_del.add_argument("--password", required=True)
    p_del.add_argument("--node", default="")
    p_del.add_argument("--everywhere", action="store_true", help="Подтверждаю удаление со всех нод")
    p_del.set_defaults(func=cmd_user_delete)

    p_bot = sub.add_parser("bot", parents=[common], help="Запустить Telegram-бота контроллера")
    p_bot.add_argument("--token", default="", help="Токен бота (иначе $WDTT_FLEET_BOT_TOKEN)")
    p_bot.add_argument("--admin", action="append", default=[], help="Telegram ID админа (можно несколько раз, иначе $WDTT_FLEET_ADMINS)")
    p_bot.add_argument("--poll", type=int, default=25, help="Long-poll таймаут, сек")
    p_bot.add_argument("--save", action="store_true", help="Сохранить --token/--admin/--poll в конфиг-файл")
    p_bot.set_defaults(func=cmd_bot)

    p_web = sub.add_parser("web", parents=[common], help="Запустить веб-панель контроллера")
    p_web.add_argument("--listen", default="127.0.0.1", help="Адрес (иначе только локально)")
    p_web.add_argument("--port", type=int, default=9999, help="Порт веб-панели")
    p_web.add_argument("--path", default="/", help="Базовый путь (по умолчанию корень)")
    p_web.add_argument("--username", default="", help="Логин (иначе $WDTT_FLEET_USER или admin)")
    p_web.add_argument("--password", default="", help="Пароль, мин. 12 символов (иначе $WDTT_FLEET_PASSWORD)")
    p_web.add_argument("--config", default="", help="Конфиг контроллера из установщика (/etc/wdtt-panel/config.json): логин/хеш/секрет/порт берутся из него")
    p_web.set_defaults(func=cmd_web)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args))
    except (FleetError, AuthError) as exc:
        _emit(str(exc), False)
        return EXIT_FAIL
    except KeyboardInterrupt:
        return EXIT_FAIL


if __name__ == "__main__":
    sys.exit(main())
