# Tasks

Перед apply выполнить `/prepare-task-agents add-combined-freedom-news`; pending задачи с `agent=missing` не выполнять. Каждая задача проходит scope-only review через `external-expert` перед отметкой выполнения. Команды тестов запускаются с `PYTHON_DOTENV_DISABLED=1`, mock credentials и запретом реальной сети.

## 1. Контракт данных и SDK

- [x] 1.1 [agent=missing; depends-on=none; parallel=yes] Добавить `dataflows/news.py` с записями статьи/статусов, UTC-фильтрацией, очисткой ограниченного текста, консервативной дедупликацией, детерминированным отбором и форматированием; покрыть поведение в `tests/test_news_merge.py`. Verification: `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_news_merge.py` проверяет границы даты, дубли, одинаковые заголовки, provenance, лимит 1/2 и частичное покрытие.
- [x] 1.2 [agent=missing; depends-on=none; parallel=yes] Проверить в SDK 2.2.0 конкретные transport hooks для timeout, запрета редиректов и безопасного logging, добавить зависимость в `pyproject.toml` и обновить только соответствующий lock-файл при наличии; описать подтверждённую настройку в `docs/tradernet-sdk.md`. Verification: импорт установленного `Tradernet` успешен, документация указывает фактические hooks и проверку без сети; при отсутствии подходящего hook задача блокируется для пересмотра дизайна.

## 2. Поставщики

- [x] 2.1 [agent=missing; depends-on=1.1; parallel=no] Выделить структурированную загрузку в `vendors/yahoo/news.py`, сохранив standalone функции и добавив проверки адаптерного контракта в `tests/test_yahoo_news_records.py`. Verification: `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_yahoo_news_records.py tests/test_news_lookahead.py tests/test_symbol_normalization_paths.py` сохраняет текущие гарантии Yahoo и не теряет различные статьи с одинаковым заголовком в combined-данных.
- [x] 2.2 [agent=missing; depends-on=1.1,1.2; parallel=no] Создать `vendors/freedom.py` поверх SDK с lazy credentials, доменом `.global`, фиксированным каналом, mapping, ограниченной пагинацией и карточками, санитизированными ошибками и timeout; добавить `tests/test_freedom_news.py` и дополнить `docs/tradernet-sdk.md` контрактом адаптера. Verification: `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_freedom_news.py` проверяет команды SDK, чужой provider, неизвестный тикер, timezone, повтор страницы, лимиты, None-defaults, 401/403/429, malformed JSON, частичный сбой и отсутствие секретов в логах.
- [x] 2.3 [agent=missing; depends-on=2.1,2.2; parallel=no] Добавить `vendors/combined_news.py` с независимым вызовом обоих источников и объединением статусов; добавить `tests/test_combined_news.py`. Verification: `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_combined_news.py` доказывает вызов обоих источников при успехе первого, две ошибки, пустую/частичную ленту, None-defaults и применение общего лимита после дедупликации.

## 3. Подключение и пользовательские настройки

- [x] 3.1 [agent=missing; depends-on=2.3; parallel=no] Зарегистрировать news vendors в `router.py`, добавить defaults и ENV для выбора, языка и JSON mapping в `default_config.py`, расширить `test_vendor_routing.py` и `test_env_overrides.py`; описать Python и fnox/CLI включение в README с сохранением существующего `fnox.toml`. Verification: `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_vendor_routing.py tests/test_env_overrides.py` подтверждает tool override, неизменный Yahoo default, валидацию mapping и неизменные insider/social/macro источники; README содержит полный пример с mapping и откат.
- [x] 3.2 [agent=missing; depends-on=2.3; parallel=no] Исправить атрибуцию и трактовку внешних данных в `sentiment_analyst.py`, обновить его docstring и добавить `tests/test_combined_news_consumers.py` с изолированными проверками обоих потребителей. Verification: `PYTHON_DOTENV_DISABLED=1 uv run pytest -q tests/test_combined_news_consumers.py tests/test_structured_agents.py tests/test_news_analyst_prompt.py` проверяет отсутствие ложной подписи Yahoo, прямой prefetch, дату состояния для tool-вызова и продолжение Sentiment при DATA_UNAVAILABLE.

## 4. Проверка связанного поведения

- [x] 4.1 [agent=missing; depends-on=3.1,3.2; parallel=no] Выполнить совместную offline проверку news-модулей, router/config и потребителей с запретом сети; проверить ruff на изменённых Python-файлах и соответствие документации фактическому API. Verification: все тесты задач 1–3 проходят вместе, `git diff --check` и адресный `uv run ruff check` успешны; результат фиксирует, что mocked тесты не подтверждают live доступ.

Live smoke-test не является обязательной задачей offline реализации: после отдельного разрешения пользователя выполнить providers → небольшая страница fbrokerkz → одна карточка через SDK и fnox без LLM и торговли. Отсутствие разрешения не подменять вымышленным успешным результатом.
