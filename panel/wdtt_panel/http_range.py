"""Докачка статики кусками (HTTP Range).

Контекст: режущие провайдеры обрывают соединение после ~16 КБ, поэтому
файл больше лимита обычным запросом не забирается никогда (браузер
перезапрашивает с нуля и снова упирается в обрыв). Лечится так: клиент
просит куски `Range: bytes=start-end` по ~14 КБ и собирает файл сам.
Свежие соединения обеспечивают два механизма: nginx отдаёт статику с
`keepalive_requests 1` (каждый кусок — новое соединение с чистым лимитом),
а загрузчик `boot.js` повторяет битые куски (после RST браузер всё равно
открывает новое соединение).
"""

from __future__ import annotations


def parse_range(value: str, size: int) -> tuple[int, int] | None:
    """Разобрать `Range: bytes=start-end` под файл длиной `size`.

    Возвращает (start, end) включительно, обрезанные под размер файла,
    или None (нет/битый/множественный диапазон — отдавать 200 целиком).
    Поддержаны открытые `bytes=500-` и суффиксные `bytes=-500` формы.
    """
    if size <= 0:
        return None
    text = (value or "").strip()
    if not text.lower().startswith("bytes="):
        return None
    spec = text[len("bytes="):].strip()
    if not spec or "," in spec:
        return None
    first, sep, last = spec.partition("-")
    if not sep:
        return None
    first, last = first.strip(), last.strip()
    try:
        if not first:
            # Суффикс: последние N байт.
            count = int(last)
            if count <= 0:
                return None
            return (max(size - count, 0), size - 1)
        start = int(first)
        end = int(last) if last else size - 1
    except ValueError:
        return None
    if start < 0 or end < start:
        return None
    if start >= size:
        return None
    return (start, min(end, size - 1))
