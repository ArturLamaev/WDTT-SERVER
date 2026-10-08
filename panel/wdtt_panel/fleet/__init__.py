"""Fleet-контроллер WDTT: управление несколькими нодами с одного места.

Нода никак не меняется — контроллер обычный HTTP-клиент к её
``api/v1`` (логин по паролю → Bearer-токен). Упал контроллер —
ноды живут дальше.
"""

from .models import Node, normalize_base_path, normalize_base_url, split_node_url
from .fanout import fanout, summarize
from .settings import FleetSettings, load_settings, save_settings, parse_admins, resolve_bot_config, load_controller_config
from .store import FleetStore, default_store_path

__all__ = [
    "Node",
    "FleetStore",
    "FleetSettings",
    "default_store_path",
    "load_settings",
    "save_settings",
    "parse_admins",
    "resolve_bot_config",
    "load_controller_config",
    "fanout",
    "summarize",
    "normalize_base_path",
    "normalize_base_url",
    "split_node_url",
]
