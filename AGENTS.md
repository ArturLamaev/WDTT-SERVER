# WDTT

Сборка WDTT-SERVER + панель управления (lebrit/wdtt-control-panel, форк).

## Git-remotes и пуш

- ОСНОВНОЙ remote: `origin` = `https://rpi4b.tailb2d7b7.ts.net/a9fm/WDTT-SERVER.git`
- ЖЕРКАЛО: `mirror` = `https://git.a9fm.best/a9fm/WDTT-SERVER.git`
- Коммитать и пушить ТОЛЬКО по явной просьбе пользователя.
- Пуш всегда направлять в `origin` (rpi4b). Если основная недоступна (timeout/hang/нет сети до tailnet) — пушить в `mirror` (git.a9fm.best) и предупредить, куда ушло.
- `git.a9fm.best` отдаёт 429 при частых push — после такого ответа выждать 30–60 c и повторить.
- Пользователь имеет обыкновение пушить самостоятельно через свой терминал/GCM — перед лишними повторами сначала свериться `git ls-remote`, не сделал ли это уже он.

## Тесты/проверки

- Машина Windows, bash/bats отсутствуют. Юнит-тесты панели:
  `$env:PYTHONPATH="C:\My_Data\Scripts\Telegram\WDTT\panel"; python -m pytest panel/tests -q`
- Ожидание: 109 passed, 1 skipped.

## Версионирование

Любое изменение кода = бамп версии (обязательно, при каждом коммите):

- багфиксы → патч (`+0.0.1`), напр. `1.0.0 → 1.0.1`;
- новый функционал/фича → минор (`+0.1`), напр. `1.0.0 → 1.1.0`;
- мажорная обнова → мажор (`+1.0`), напр. `1.0.0 → 2.0.0`.

Версия хранится синхронно в `panel/install.sh` (`PANEL_VERSION`),
`panel/wdtt_panel/__init__.py` (`__version__`) и `panel/README.md`
(«Текущая версия»); согласованность проверяет `test_version_is_consistent`.
Историю вести в `VERSIONS.md`. Текущая версия — `1.8.0`.