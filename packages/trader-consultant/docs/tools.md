# Математический API

## compute_rebalance(RebalanceRequest)

Чистая арифметика идеальной перебалансировки, без LLM и сетевых запросов.

```python
from trader_consultant import RebalanceRequest, compute_rebalance

result = compute_rebalance(RebalanceRequest(
    holdings={"A": "600", "B": "400"},
    cash="1000",
    targets={"A": "0.25", "B": "0.25"},
    prices={"A": "10", "B": "20"},
))
assert result["total_value"] == 2000
assert result["cash_target_value"] == 1000
assert [p["trade_shares"] for p in result["positions"]] == [-10, 5]
```

holdings — стоимость каждой позиции в одной валюте, а не количества.
cash — свободные деньги в той же валюте. targets — доли от holdings + cash.
Все текущие позиции требуют явной цели; для сохранения используйте текущий вес,
для полного закрытия — ноль. Новые тикеры могут присутствовать только в targets.
Сумма целевых долей не больше единицы, остаток предназначен для денег.
prices необязателен; при наличии вычисляется идеальное количество.

Результат: total_value, cash_target_weight/value, positions с текущим/целевым
весом, drift, trade_value, action и необязательным trade_shares;
gross_trade_value, turnover_one_way, n_buys, n_sells.
Положительное trade_value означает покупку.
turnover_one_way = половина суммы абсолютных объёмов сделок / NAV;
это соглашение о метрике, не стоимость комиссий.

Расчёты — Decimal. На входе запрещены отрицательные стоимости/деньги,
неположительные цены, NaN/Infinity, неизвестные ценовые тикеры и неполные цели.
У этого инструмента нет округления к лотам, контроля рисков и издержек:
ими занимается консультант. Возвращаемый словарь не является приказом.
Для JSON сериализуйте Decimal в строки без потери точности.

## compute_optimization(OptimizationRequest)

Оценка альтернативных весов по истории цен. Не использует LLM, Tauric API,
брокера, рекомендации BUY/SELL или внешние максимальные веса консультанта.

```python
from datetime import date, timedelta
from trader_consultant import OptimizationRequest, compute_optimization

comparison = compute_optimization(OptimizationRequest(
    prices={
        "A": [100, 101, 100, 103, 102, 104],
        "B": [100, 99, 101, 100, 103, 102],
    },
    dates=[date(2026, 1, 5) + timedelta(days=i) for i in range(6)],
    method="min_variance",
    risk_free_annual=0.03,
    periods_per_year=252,
))
assert abs(sum(comparison["weights"].values()) - 1) < 1e-7
```

Пример синтетический; в реальных данных dates должны быть торговыми датами.
Для 2–50 активов передайте положительные конечные цены на **одни и те же даты**.
Не заполняйте пропуски будущими значениями. Разные длины и повторяющиеся либо
неупорядоченные даты отвергаются; автоматического обрезания массивов нет.
Число доходностей должно превышать число активов. Вырожденная ковариация
отвергается. Корректировку корпоративных событий и сопоставление валют
выполняет вызывающее приложение до расчёта.

Простые доходности: r[t] = price[t] / price[t−1] − 1.
Оценки: μ = mean(r) × periods_per_year,
Σ = sample_covariance(r, ddof=1) × periods_per_year.

| method | Целевая функция | Решатель |
| --- | --- | --- |
| min_variance | Минимум wᵀΣw | SciPy SLSQP |
| max_sharpe | Максимум (wᵀμ − rf) / sqrt(wᵀΣw) | SLSQP через преобразование весов |
| mean_variance | Минимум дисперсии при wᵀμ = target_return | SLSQP |
| risk_parity | Равные вклады в дисперсию | L-BFGS-B, логарифмический барьер |

По умолчанию long_only=True; сумма весов — 1. Для risk_parity веса всегда
положительные. risk_free_annual обязателен для max_sharpe; target_return —
для mean_variance. Доходности и ставки передаются долями, не процентами.
Неудача решателя или нарушение результата по численной проверке вызывает
ValueError. Ошибки входной схемы — pydantic.ValidationError.

Результат: weights, gross_exposure, annual_return, annual_volatility,
sharpe (при переданной ставке), risk_contributions, n_periods и параметры метода.
Оценка ожидаемой доходности основана на историческом среднем, не на прогнозе AI.
Нет ограничений по секторам, индивидуальных потолков, оборота, комиссий, ликвидности
и бюджета консультанта. Поэтому эти веса нельзя автоматически превращать в приказ.
Результат предназначен для отдельного сравнения; основные BUY/SELL/KEEP правила
консультанта остаются неизменны.
