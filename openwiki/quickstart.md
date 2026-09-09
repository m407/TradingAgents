---
type: руководство разработчика
title: Быстрый старт и навигация по задачам
description: Минимальный путь установки и запуска TradingAgents через CLI и Python API. Навигация по архитектуре, контрактам, интеграциям, настройкам, хранению и проверке изменений с учетом различий жизненного цикла CLI и API.
tags: [quickstart, developer-guide, task-routing, cli, python-api]
sources:
  - id: openwiki-source-5f5b95b3d6a215fa02ceb945
    resource: repo://.env.example
  - id: openwiki-source-164e2da859b5277df81c7d94
    resource: repo://.github/workflows/ci.yml
  - id: openwiki-source-93c4bf642ecfa683655a01ec
    resource: repo://cli/main.py
  - id: openwiki-source-b79fbbd921df689b4bbdc82f
    resource: repo://docker-compose.yml
  - id: openwiki-source-bb1ebe868e35e9e500714501
    resource: repo://Dockerfile
  - id: openwiki-source-833e692518af9eeaf8564cc6
    resource: repo://main.py
  - id: openwiki-source-05ccef8d4cf1698187f20464
    resource: repo://pyproject.toml
  - id: openwiki-source-23775c3de52f3ab95a13cb8b
    resource: repo://README.md
  - id: openwiki-source-f0a6e7dc03522b2682f88655
    resource: repo://tests/conftest.py
  - id: openwiki-source-ba3240e0743878ade1b72a2f
    resource: repo://tests/test_checkpoint_lifecycle.py
  - id: openwiki-source-c7c2a5bfd6e48d6b7e43a2ef
    resource: repo://tests/test_env_overrides.py
  - id: openwiki-source-cb0425810080c23ce91acd3d
    resource: repo://tests/test_per_tier_reasoning_effort.py
  - id: openwiki-source-a1e95c75f9ad7fc77f818a2e
    resource: repo://tests/test_signal_processing.py
  - id: openwiki-source-12060ccc88894da5cade1961
    resource: repo://tradingagents/__init__.py
  - id: openwiki-source-b7e067f817386094262aeb7b
    resource: repo://tradingagents/default_config.py
  - id: openwiki-source-4e072b0f954dc477bfc36fee
    resource: repo://tradingagents/graph/trading_graph.py
  - id: openwiki-source-a8ab50bd7f20e0d18e87ea5c
    resource: repo://tradingagents/llm_clients/anthropic_client.py
  - id: openwiki-source-2fc0864c9ebc478a8b00a4f2
    resource: repo://tradingagents/llm_clients/google_client.py
  - id: openwiki-source-068ad01d56c56086cdc4c402
    resource: repo://tradingagents/llm_clients/openai_client.py
generated: { by: "openwiki/0.5.0", at: "2026-09-09T08:26:19.375Z" }
verified:
  - by: openwiki/0.5.0
    at: 2026-09-09T08:26:19.375Z
---

# Быстрый старт и навигация по задачам

TradingAgents — многоагентная система анализа рынка на Python 3.10+ и LangGraph. Основные точки входа — интерактивная команда `tradingagents` и Python API `TradingAgentsGraph.propagate()`. Они используют общий граф, агентов, клиентов LLM и инструменты данных, но не одинаковую обвязку запуска: общие контрольные точки не означают общую память и журналирование.

> TradingAgents — исследовательский инструмент, а не финансовая, инвестиционная или торговая рекомендация. Результаты зависят от моделей, доступности провайдеров и рыночных данных.

## Локальная установка

Клонируйте репозиторий, создайте окружение с Python 3.10 или новее и установите пакет с инструментами разработки:

```bash
git clone https://github.com/TauricResearch/TradingAgents.git
cd TradingAgents
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
```

Дополнение `dev` содержит pytest и Ruff; для запуска без разработки достаточно `pip install .`. Консольная команда устанавливается как `cli.main:app`. Поддержка AWS Bedrock выделена в отдельное дополнение: `pip install ".[bedrock]"` ([метаданные пакета](repo://pyproject.toml#L5-L49)).

Создайте локальный файл учетных данных:

```bash
cp .env.example .env
```

Укажите ключ выбранного провайдера LLM, например `OPENAI_API_KEY`, `GOOGLE_API_KEY`, `ANTHROPIC_API_KEY`, `XAI_API_KEY` или `DEEPSEEK_API_KEY`. Для источников данных ключи нужны по выбранному маршруту: `ALPHA_VANTAGE_API_KEY` для Alpha Vantage, `FRED_API_KEY` для FRED. Базовые категории цен, индикаторов, фундаментальных данных и новостей используют `yfinance`, макроданные — `fred`, прогнозные рынки — `polymarket`. Не требуется заполнять все ключи из шаблона. Bedrock и локальные серверы имеют отдельные способы настройки — см. [провайдеры LLM](integrations/llm-providers.md).

При импорте пакета поиск `.env`, затем `.env.enterprise` идет вверх от текущего рабочего каталога. Уже экспортированные переменные не перезаписываются. `DEFAULT_CONFIG` применяет известные `TRADINGAGENTS_*` при импорте: задайте их **до запуска процесса или импорта**, а не после создания графа. Ошибки булевых значений и целочисленных настроек с типизированным значением по умолчанию вызывают `ValueError`; необязательные числовые параметры с исходным `None` преобразуются позднее при создании клиентов. Храните файлы ключей локально и не включайте их в коммиты. Подробности — [конфигурация и развертывание](operations/configuration-and-deployment.md).

Основания: [загрузка окружения](repo://tradingagents/__init__.py#L4-L17), [наложение окружения](repo://tradingagents/default_config.py#L10-L113), [шаблон ключей](repo://.env.example), [преобразование числовых параметров](repo://tradingagents/graph/trading_graph.py#L48-L76), [параметры клиентов](repo://tradingagents/graph/trading_graph.py#L170-L213).

## Запуск анализа

### Установленный CLI

```bash
tradingagents
# Equivalent explicit command:
tradingagents analyze
# Source-tree alternative:
python -m cli.main
```

CLI на Typer/Rich запрашивает тикер, дату, аналитиков, глубину исследования, провайдера, модели и язык результата, показывает ход анализа и предлагает сохранить итоговый отчет. Переменные окружения могут пропускать отдельные шаги выбора настроек, но это по-прежнему интерактивный интерфейс, а не универсальная команда пакетной обработки. Для запуска без диалога используйте Python API; подробнее — [сквозной анализ через CLI и API](workflows/analysis-entrypoints.md).

### Python API

Перед запуском настройте доступного вам провайдера и модели через `.env` или `config`. Пример использует текущий `DEFAULT_CONFIG`, а не гарантирует доступ к моделям в вашей учетной записи.

**Необязательно: независимая глубина рассуждения deep/quick.** До создания графа можно задать `config["deep_think_reasoning_effort"]` и `config["quick_think_reasoning_effort"]` либо `TRADINGAGENTS_DEEP_THINK_REASONING_EFFORT` и `TRADINGAGENTS_QUICK_THINK_REASONING_EFFORT` в окружении. Оба ключа по умолчанию равны `None`: каждый уровень наследует общую настройку активного провайдера (`openai_reasoning_effort`, `anthropic_effort` или `google_thinking_level`), если собственное значение отсутствует, равно `None` или пустой строке. Если не задано ни одно, граф не передает параметр рассуждения. Строка `"none"` не означает наследование. Это не меняет выбранные модели и не выбирает их автоматически.

Эти переопределения поддерживаются для OpenAI, Anthropic и Google; другие провайдеры не получают через них новый параметр. Допустимые значения и преобразования зависят от модели и адаптера: `high`/`low` в шаблоне — примеры, не универсальные значения и не новые значения по умолчанию. Приоритеты описаны в [конфигурации](operations/configuration-and-deployment.md), ограничения и адаптация — в [провайдерах LLM](integrations/llm-providers.md). Основания: [необязательные настройки](repo://.env.example#L68-L92), [раздельное создание клиентов и выбор параметров](repo://tradingagents/graph/trading_graph.py#L108-L128), [наследование](repo://tradingagents/graph/trading_graph.py#L170-L213).

```python
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.trading_graph import TradingAgentsGraph

config = DEFAULT_CONFIG.copy()
ta = TradingAgentsGraph(config=config)
final_state, rating = ta.propagate("NVDA", "2026-01-15")
print(rating)

# Optional human-readable markdown export:
report_file = ta.save_reports(final_state, "NVDA")
print(report_file)
```

`propagate()` возвращает итоговое состояние и один из рейтингов `Buy`, `Overweight`, `Hold`, `Underweight`, `Sell` либо **`REVIEW`**, если рейтинг не удалось извлечь. `REVIEW` — не `Hold` и не значение `PortfolioRating`; перед преобразованием проверяйте его через `tradingagents.agents.utils.rating.is_review`. Извлечение рейтинга детерминированное и не требует дополнительного вызова LLM.

Экспорт Markdown **явный**: `save_reports()` создает дерево отчетов под `results_dir/reports`, если не указан `save_path`. Сам `propagate()` записывает JSON состояния и память решения, но не это дерево Markdown. Для криптоактивов передайте `asset_type="crypto"` явно. Если меняете вложенные словари `data_vendors`, `tool_vendors` или `benchmark_map`, используйте глубокую копию вместо `DEFAULT_CONFIG.copy()`.

Основания: [контракт и жизненный цикл API](repo://tradingagents/graph/trading_graph.py#L404-L574), [проверки рейтинга](repo://tests/test_signal_processing.py#L75-L140).

Можно также выполнить пример из корня репозитория:

```bash
python main.py
```

Это запуск с `debug=True` для `NVDA` на `2024-05-10`, а не CLI. Закомментированный вызов `reflect_and_remember` в [main.py](repo://main.py) не следует использовать как подтвержденный текущий API памяти; действующий цикл описан ниже и на странице [хранения и восстановления](operations/persistence-and-recovery.md).

### Docker

```bash
cp .env.example .env  # add your API keys
docker compose run --rm tradingagents
```

Для профиля Ollama:

```bash
docker compose --profile ollama run --rm tradingagents-ollama
```

Compose сохраняет состояние `/home/appuser/.tradingagents` в именованном томе `tradingagents_data`. Образ использует Python 3.12, работает от непривилегированного `appuser` и запускает установленную команду `tradingagents`. Для Ollama заранее обеспечьте наличие выбранных моделей; настройки контейнеров и сервера — в [руководстве по развертыванию](operations/configuration-and-deployment.md).

## Проверка изменения

Сначала выполните профильные тесты, затем основные проверки репозитория. Например, для изменений запуска, контрольных точек и результата:

```bash
pytest -q tests/test_env_overrides.py tests/test_checkpoint_lifecycle.py tests/test_signal_processing.py
pytest -q
ruff check .
```

`test_env_overrides.py` проверяет наложение окружения и ошибочные значения; `test_checkpoint_lifecycle.py` — сохранение, сбой и возобновление на небольшом графе по схеме CLI; `test_signal_processing.py` — пять уровней рейтинга, `REVIEW` и отсутствие дополнительного обращения к LLM. Это изолированные проверки контрактов, а не доказательство работоспособности внешнего провайдера.

Для изменений независимого рассуждения дополнительно выполните `pytest -q tests/test_per_tier_reasoning_effort.py tests/test_cli_env_skip.py`. [Первый набор](repo://tests/test_per_tier_reasoning_effort.py) без сети проверяет наследование и оба вызова фабрики из конструктора с раздельными параметрами; [проверки окружения](repo://tests/test_env_overrides.py#L92-L160) охватывают также независимые переопределения deep/quick. Правила диалогов CLI и адаптерные проверки — в [руководстве по проверке изменений](testing/change-validation.md).

Чтобы исключить тесты с маркером внешней интеграции при наличии реальных ключей в окружении:

```bash
pytest -q -m "not integration"
```

CI выполняет `pytest -q` на Python 3.10–3.13, `ruff check .` и проверяет импорт `tradingagents` и `cli.main` после чистого `pip install .`. Матрица и команды заданы в [CI](repo://.github/workflows/ci.yml#L12-L61). Выбор остальных профильных наборов — [проверка изменений](testing/change-validation.md).

## Ориентиры выполнения

Конструктор `TradingAgentsGraph` передает конфигурацию общему слою данных, создает каталоги результатов и кэша, клиентов быстрого и глубокого рассуждения, группы инструментов аналитиков и компилирует выбранный граф. Поэтому настройка нужна до создания экземпляра, а не только перед `propagate()`. Основания: [конструктор](repo://tradingagents/graph/trading_graph.py#L82-L168), [группы инструментов](repo://tradingagents/graph/trading_graph.py#L215-L255).

```mermaid
flowchart TD
    API["Python: propagate()"] --> MEM["Разрешение прежних решений и контекст памяти"]
    MEM --> GRAPH["Общий граф агентов"]
    CLI["Интерактивный CLI"] --> STREAM["Прямой graph.graph.stream()"]
    STREAM --> GRAPH
    GRAPH --> APIRESULT["API: JSON, память решения, рейтинг"]
    GRAPH --> CLIRESULT["CLI: прогресс, журналы, разделы отчета"]
    APIRESULT --> EXPORT["Явный save_reports()"]
    CLIRESULT --> PROMPT["Запрос сохранения итогового отчета"]
```
Схема показывает разные обвязки общего графа; выходы API и CLI относятся к своей точке входа, а не выполняются одновременно.

Главная граница ответственности:

- **API:** `propagate()` разрешает ожидающие исхода записи того же тикера, добавляет контекст памяти и инструмента, выполняет граф, записывает JSON итогового состояния и новое решение в память, очищает успешную контрольную точку и возвращает рейтинг.
- **CLI:** создает начальное состояние самостоятельно и напрямую читает поток `graph.graph`. Он владеет отображением прогресса, журналом сообщений/инструментов, промежуточными Markdown-разделами и запросом итогового экспорта. Этот путь не вызывает обвязку API для разрешения и подстановки памяти, добавления решения в память и `_log_state()`.
- **Контрольные точки общие:** CLI использует `begin_checkpoint()`, `checkpoint_input()`, `clear_checkpoint_on_success()` и `end_checkpoint()`, а API — те же механизмы через `checkpoint_scope()`. При возобновлении передается `None`, чтобы не добавлять начальные сообщения повторно. Сбой потока оставляет контрольную точку для продолжения, успешное завершение очищает ее; ресурс закрывается и обычный граф восстанавливается.

Основания: [API и общие помощники](repo://tradingagents/graph/trading_graph.py#L404-L574), [начало потока CLI](repo://cli/main.py#L1113-L1144), [завершение CLI](repo://cli/main.py#L1244-L1301). Модель ответственности — [обзор системы](architecture/system-overview.md), последовательность агентов и дебатов — [выполнение графа](architecture/agent-runtime.md).

## Создаваемые данные и учетные данные

По умолчанию долговременное состояние расположено под `~/.tradingagents`:

| Назначение | Расположение по умолчанию |
|---|---|
| JSON состояния API и настроенные результаты | `~/.tradingagents/logs` |
| Кэш данных и необязательные базы контрольных точек | `~/.tradingagents/cache` |
| Память решений и рефлексий | `~/.tradingagents/memory/trading_memory.md` |
| Журнал сообщений CLI и промежуточные отчеты | `~/.tradingagents/logs/<ticker>/<analysis-date>/` |
| Итоговый экспорт CLI | После подтверждения; `./reports/<ticker>_<timestamp>/` |
| Markdown-экспорт API | Явный `save_reports()`; под `~/.tradingagents/logs/reports/` |
| Локальные ключи | `.env` и при необходимости `.env.enterprise` около рабочего дерева |

Корни переопределяются через `TRADINGAGENTS_RESULTS_DIR`, `TRADINGAGENTS_CACHE_DIR` и `TRADINGAGENTS_MEMORY_LOG_PATH`. Контрольные точки по умолчанию выключены. В API задайте `config["checkpoint_enabled"] = True`; в CLI используйте `tradingagents analyze --checkpoint` или `TRADINGAGENTS_CHECKPOINT_ENABLED=true`. Флаг `--no-checkpoint` явно выключает механизм, `--clear-checkpoints` удаляет сохраненные контрольные точки перед запуском. Эти флаги работают и при прямом потоковом выполнении CLI — прежняя оговорка о неработающем `--checkpoint` больше не применима.

Повторное использование контрольной точки зависит не только от тикера и даты, но и от выбранных аналитиков, глубины дебатов и режима актива. Подробности восстановления и безопасной очистки — [хранение, память и восстановление](operations/persistence-and-recovery.md).

## Навигация по задаче

Начинайте с поведения, которое хотите изменить, а не с перечня файлов:

| Задача | Куда перейти | Что уточнить |
|---|---|---|
| Определить владельца поведения между CLI, API, графом и хранилищем | [Обзор системы](architecture/system-overview.md) | Общие компоненты и границы обвязки запуска |
| Добавить аналитика, стадию дебатов или изменить переходы и цикл инструментов | [Выполнение графа](architecture/agent-runtime.md) | Сборка, маршрутизация, завершение и лимиты |
| Изменить поля состояния, итоговое решение, рейтинг или подписи Markdown | [Контракты состояния и результатов](concepts/state-and-output-contracts.md) | Совместимость агентов, парсинга, отображения и хранения |
| Изменить диалоги CLI, дату/тикер, порядок запуска или результат API | [Сквозной анализ](workflows/analysis-entrypoints.md) | Различия потокового CLI и программного жизненного цикла |
| Добавить провайдера LLM, модель, endpoint или параметр рассуждения | [Провайдеры LLM](integrations/llm-providers.md) | Аутентификация, SDK и совместимость протоколов |
| Изменить цены, новости, фундаментальные или макроданные | [Рыночные данные](integrations/market-data.md) | Маршруты поставщиков, качество, временные границы и отсутствие данных |
| Изменить окружение, значения по умолчанию, установку или контейнеры | [Конфигурация и развертывание](operations/configuration-and-deployment.md) | Приоритеты, импорт, каталоги и настройка запуска |
| Изменить память, журналы, отчеты или восстановление | [Хранение и восстановление](operations/persistence-and-recovery.md) | Независимые жизненные циклы сохраняемых артефактов |
| Выбрать минимальные регрессионные проверки | [Проверка изменений](testing/change-validation.md) | Профильные тесты, изоляция интеграций и проверки CI |

### Краткие правила расширения

- **Новый аналитик или стадия:** согласуйте сборку графа, состояние, маршруты, отображение CLI и тесты топологии.
- **Новый инструмент аналитика:** проверьте и привязку к агенту, и регистрацию в исполняемом `ToolNode`, затем маршрут данных и ошибки.
- **Новый провайдер:** начинайте со слоя совместимости клиентов, а не с ветвлений внутри агентов.
- **Новый поставщик данных:** сохраняйте явный порядок резервных источников; не подменяйте выбранный пользователем маршрут незаметно.
- **Новое сохраняемое поле или подпись отчета:** рассматривайте изменение как совместимость состояния, рендеринга, памяти и парсинга рейтинга.
- **Изменение жизненного цикла:** заранее решите, должно ли оно затронуть только `propagate()`, только CLI или общий слой ниже их разделения.
