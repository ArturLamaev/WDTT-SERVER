"""Fan-out: одна операция параллельно по нескольким нодам.

Частичный успех — норма: результат всегда содержит итог по каждой
ноде отдельно, одна упавшая нода не валит остальные.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

from .client import FleetError, NodeClient, touch_offline, touch_online
from .models import Node

Entry = dict[str, Any]
Executor = Callable[[NodeClient], Any]


def _run_one(node: Node, func: Executor, timeout: float, client_cls: type = None) -> Entry:
    started = time.monotonic()
    cls = client_cls or NodeClient
    try:
        result = func(cls(node, timeout=timeout))
    except FleetError as exc:
        touch_offline(node, str(exc))
        return {"ok": False, "node": node.id, "error": str(exc) or "сбой",
                "latency": round(time.monotonic() - started, 3)}
    except Exception as exc:  # noqa: BLE001 — чужой колбэк не должен ронять весь прогон
        touch_offline(node, f"внутренняя ошибка: {exc}")
        return {"ok": False, "node": node.id, "error": f"внутренняя ошибка: {exc}",
                "latency": round(time.monotonic() - started, 3)}
    latency = round(time.monotonic() - started, 3)
    touch_online(node, latency)
    return {"ok": True, "node": node.id, "result": result, "latency": latency}


def fanout(
    nodes: list[Node],
    func: Executor,
    workers: int = 10,
    timeout: float = 30.0,
    client_cls: type | None = None,
) -> dict[str, Entry]:
    """Прогнать ``func(client)`` по всем нодам. Возвращает ``{node_id: entry}``.

    ``client_cls`` — подмена клиента (нужна тестам бота), по умолчанию NodeClient.
    """
    if not nodes:
        return {}
    workers = max(1, min(workers, len(nodes)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_run_one, node, func, timeout, client_cls): node.id for node in nodes}
        return {node_id: future.result() for future, node_id in futures.items()}


def fanout_route(
    nodes: list[Node],
    route: str,
    payload: dict[str, Any] | None = None,
    workers: int = 10,
    timeout: float = 30.0,
    client_cls: type | None = None,
) -> dict[str, Entry]:
    """Частый случай: один и тот же роут API на всех нодах."""
    return fanout(nodes, lambda client: client.call(route, payload),
                  workers=workers, timeout=timeout, client_cls=client_cls)


def summarize(results: dict[str, Entry]) -> dict[str, Any]:
    """Сводка: ``{total, ok, fail, errors: {node_id: error}}``."""
    errors = {nid: e.get("error", "?") for nid, e in results.items() if not e.get("ok")}
    return {
        "total": len(results),
        "ok": sum(1 for e in results.values() if e.get("ok")),
        "fail": sum(1 for e in results.values() if not e.get("ok")),
        "errors": errors,
    }
