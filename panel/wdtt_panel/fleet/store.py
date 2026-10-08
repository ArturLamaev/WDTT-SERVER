"""Хранилище нод контроллера: один JSON-файл с правами 0600.

Путь: ``$WDTT_FLEET_STORE`` или ``~/.wdtt-fleet/nodes.json``.
Запись атомарная (tmp + replace).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Node

DEFAULT_DIR_NAME = ".wdtt-fleet"
DEFAULT_FILE_NAME = "nodes.json"


def default_store_path() -> Path:
    override = os.environ.get("WDTT_FLEET_STORE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / DEFAULT_DIR_NAME / DEFAULT_FILE_NAME


class FleetStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_store_path()
        self._nodes: dict[str, Node] = {}
        self.reload()

    def reload(self) -> None:
        self._nodes = {}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        items = raw if isinstance(raw, list) else raw.get("nodes", [])
        if not isinstance(items, list):
            return
        for item in items:
            if isinstance(item, dict):
                try:
                    node = Node.from_dict(item)
                except (TypeError, ValueError):
                    continue
                self._nodes[node.id] = node

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            [n.to_dict() for n in self._nodes.values()],
            ensure_ascii=False,
            indent=2,
        ).encode() + b"\n"
        tmp = self.path.with_suffix(".tmp")
        tmp.write_bytes(payload)
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)
        os.chmod(self.path, 0o600)

    def add(self, node: Node) -> Node:
        if node.id in self._nodes:
            raise ValueError(f"Нода {node.id!r} уже добавлена (сначала удалите её)")
        self._nodes[node.id] = node
        self.save()
        return node

    def remove(self, node_id: str) -> bool:
        if node_id not in self._nodes:
            return False
        del self._nodes[node_id]
        self.save()
        return True

    def get(self, node_id: str) -> Node | None:
        return self._nodes.get(node_id)

    def update(self, node: Node) -> None:
        self._nodes[node.id] = node
        self.save()

    def all(self) -> list[Node]:
        return sorted(self._nodes.values(), key=lambda n: n.id)
