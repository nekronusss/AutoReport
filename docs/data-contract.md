# Контракт 1.0

Полный снимок одного календарного дня в конфигурируемой IANA timezone. UTC-границы
имеют вид [period_start, period_end). as_of не раньше конца периода. Заказы принадлежат
этому периоду; платежи могут быть позже периода, но не позже as_of.

Точные заголовки CSV заданы в `src/dqflow/contracts.py:TABLES`:

* customers: customer_id, created_at, updated_at;
* orders: order_id, customer_id, created_at, updated_at, status, currency, total_amount;
* order_items: order_id, line_id, sku, quantity, unit_price, updated_at;
* payments: payment_id, order_id, kind, status, amount, currency, occurred_at, updated_at.

CSV — UTF-8, запятая, двойная кавычка, удвоение кавычки при escape, заголовок обязателен.
Пустая ячейка не является допустимым ID; даты и числа требуют непустое значение.
NULL у числовых/временных полей вызывает DQ03; пустые ключи — DQ04/DQ06/DQ07.
Лишние столбцы запрещены. CSV-архивы не принимаются и не распаковываются.

ID и денежные значения приходят строками. Decimal: до 16 целых и 2 дробных знаков,
без scientific notation, NaN и Infinity; PostgreSQL numeric(18,2). quantity — целое.
Временная отметка должна содержать offset; в БД используется timestamptz.

Manifest содержит source, schema_version='1.0', snapshot_id, period_start,
period_end, as_of, complete и таблицы с row_count, sha256, money_totals по валютам.
Для order_items контролируется сумма unit_price в валюте dataset, для orders —
всех total_amount (в том числе отменённых), для payments — всех amount (любые статусы).
Генератор вычисляет эти итоги на стороне источника; загрузчик сравнивает их с типизированным слоем.

HTTP snapshot API принимает opaque cursor и возвращает snapshot_id, row_count,
csv_base64, sha256 страницы и next_cursor=null на последней странице.
Байты страниц последовательно дают исходный CSV с хешем manifest; заголовок только
в первой странице. Это конкретная реализация API demo; для другого wire-формата нужен адаптер.
HTTP redirects запрещены. Пять попыток для timeout/408/429/500/502/503/504,
Retry-After, backoff/jitter, общий deadline. Ошибки авторизации не повторяются.

Формулы сводки:

* orders_count — все заказы; customers_count — уникальные customer_id заказов;
* ordered_amount — total_amount заказов кроме cancelled;
* captured_amount — succeeded/payment; refunded_amount — succeeded/refund;
* net_collected_amount = captured_amount - refunded_amount.

Оплаты отменённых заказов входят в денежные показатели и вызывают DQ20.
Completed требует net = total с допуском 0.01. Pending допускает частичную оплату.
Возвраты не могут превышать успешные оплаты. Сначала агрегируются две дочерние
таблицы, затем соединяются с orders — декартово размножение сумм исключено.

Правила DQ01–DQ20 версионируются в config/rules.yaml. Снижение серьёзности без
изменения и проверки кода запрещено. Никакого ignore-all-errors нет.
