# Происхождение вычислений

Источник: TauricResearch/Trading-R1, ветка main, получена 2026-11-06.
Copyright 2026 Tauric Research. Apache-2.0, полный текст в ../LICENSE.
Уведомление сохранено в ../NOTICE и заголовках перенесённых файлов.

Идентификация полученных исходников (SHA-256 текста UTF-8):

 - [optimization.py](https://github.com/TauricResearch/Trading-R1/blob/main/trading_r1/tools/compute/portfolio/optimization.py): `0f6b02910975d745bd979b039942e2ed8c56a9db31e7bdaa6d61aea5ce7cb0dc`
 - [moments.py](https://github.com/TauricResearch/Trading-R1/blob/main/trading_r1/tools/compute/portfolio/moments.py): `b561ce3bf790c74fa44f77af12b39210e96a0be2296716bd61384c676a4a75de`
 - [rebalance.py](https://github.com/TauricResearch/Trading-R1/blob/main/trading_r1/tools/compute/portfolio/rebalance.py): `dbedb94b3cb2de7210a865b30029acc27b885d13300900d37ca5cbc1878bd6af`

## Изменения при переносе

- Удалены ToolSpec, обработчики агентного runtime, форматирование evidence и зависимость от терминала. Публичный интерфейс — модели запроса и Python-функции.
- Сохранены четыре алгоритма оптимизации и проверки сходимости; ToolError заменён ValueError.
- Добавлены строгие календарные длины, положительные конечные цены, проверка вырожденной ковариации и ограничений результата решателя.
- В moments.py удалено обрезание рядов до общей длины: вызывающий код обязан передать одинаковые даты.
- Арифметика rebalance.py переведена на Decimal, добавлен явный cash. Пропущенная цель больше не означает ликвидацию позиции: неполные цели отвергаются.
- equal_budgets, модели снимка, политика, консультант и проверка приказа — новая логика этого пакета, не функция исходного Trading-R1.

Методы оптимизации не являются рекомендацией использовать исторические средние как надёжный прогноз. Статистические ограничения описаны в tools.md.
