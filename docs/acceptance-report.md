# Фактическая приёмка · 08.10.2026

Проверено на Windows 11, Python 3.12.12, PostgreSQL **18.1**, отдельном локальном
кластере. Docker client 29.1.3 установлен, но Linux Engine возвращает HTTP 500.
В журнале Docker backend указано `wslUpdateRequired: true`.
Поэтому Docker build/up и PostgreSQL 17 **не объявляются проверенными**.
Оба Compose-профиля прошли `docker compose config --quiet`.

## Автоматические результаты

* 40 тестов полного набора: `docs/evidence/acceptance.xml`.
* Ещё 4 recovery-теста: `docs/evidence/recovery.xml`.
* После финальных исправлений контракта/outbox — 18 затронутых unit/recovery-тестов:
  `docs/evidence/final-focused.xml`.
* Ruff: passed; mypy: passed, 17 исходных модулей.
* Shell syntax: `bash -n` для backup/restore/deploy/job/compose.
* Есть предупреждение Starlette TestClient о будущем переходе тестового клиента
  с httpx на httpx2; тесты проходят, runtime-коннектор HTTPX работает штатно.

После обновления зависимостей полный набор: **45 passed** (acceptance-final.xml).
Дополнительно добавлена проверка изменения версии при обновлении lock-файла/шаблона;
финальная сфокусированная серия: **18 passed** (identity-final.xml).

Дополнительно проверены допустимые символы CSV snapshot_id и запрет записи
публикации ролью runner (миграция 002).
Заключительная серия после миграции 002: **20 passed**, `docs/evidence/handoff-tests.xml`.

Всего **47 различных автоматических тестов**. Это число не равняется числу
полностью принятых production-сценариев A01–A24.

HTTP-демонстрация 200 000 строк: 10 000 customers, 50 000 orders,
90 000 order_items, 50 000 payments. Данные опубликованы, HTML/CSV/JSON сформированы,
mock webhook подтверждён. Проверка HTTP: index 200, report 200, 20 правил на странице,
metrics 200, /raw/records 404. Автоматическая визуальная проверка в браузере не
выполнена: управляемый браузер в окружении недоступен.

Контрольные итоги seed=42, период 2026-09-30:

| Метрика | Значение |
|---|---:|
| orders_count | 50 000 |
| customers_count | 10 000 |
| ordered_amount | 24 747 750.00 KZT |
| captured_amount | 23 373 075.00 KZT |
| refunded_amount | 0.00 KZT |
| net_collected_amount | 23 373 075.00 KZT |

## Матрица ТЗ

| ID | Результат и границы |
|---|---|
| A01 | PASS локально: реальная HTTP-поставка 200k, PostgreSQL/dbt, отчёт, mock webhook |
| A02 | PASS: тот же run_id/no-op, отдельный attempt_id, суммы не удваиваются |
| A03 | PASS: duplicate business key → DQ05 |
| A04 | PASS: decimal, ID, status, timezone/types; полная поставка не очищается частично |
| A05 | PASS: потеря customer/item и orphan payment |
| A06 | PARTIAL: повреждённый файл блокируется, точные байты всех API-страниц проверены; отдельный сетевой proxy с пропуском страницы не развёрнут |
| A07 | PASS: mock transport 429→200 с Retry-After=3, 401 ровно одна попытка; redirect не следует |
| A08 | PASS с injection: недоступный source не меняет текущую версию; retry той же попытки логически безопасен |
| A09 | PASS: отдельный процесс принудительно завершён os._exit(137) сразу после committed COPY; следующая попытка без дублей |
| A10 | PARTIAL: исключение после COMMIT корректно разрешается чтением mart/publication; реальная потеря TCP-пакета COMMIT не воспроизведена |
| A11 | PASS: вторая session advisory lock получает exit 75 |
| A12 | PASS: неизвестная версия/поле/тип блокируются |
| A13 | PASS: исправленный snapshot переключает период; старый as_of блокируется |
| A14 | PASS: подтверждённый пустой день публикуется с 0, отсутствующий → DQ18/FAILED_SOURCE |
| A15 | PASS: UTC-границы Qyzylorda, явные offsets, заказы вне периода/после as_of блокируются |
| A16 | PASS: фиксированный независимый oracle, 2 позиции + 2 оплаты: 197.00, без размножения |
| A17 | PASS: сбой рендера → код 6, отчёт восстанавливается через outbox; webhook timeout не отменяет публикацию |
| A18 | NOT RUN: реальный reboot VPS и systemd catchup требуют Linux-стенда |
| A19 | PARTIAL: недоступный каталог raw → FAILED_TECH без публикации; физическое заполнение диска и выключение PostgreSQL не выполнялись |
| A20 | LOCAL PASS: pg_dump/restore в новую БД, хеши всех ссылочных файлов, doctor, replay, совпадающие суммы; внешний encrypted remote не предоставлен |
| A21 | PASS: rollback переключает данные и текущий отчёт, причина в audit |
| A22 | Замеры и методика в benchmark.md; не являются приёмкой hardware-профиля Linux 4 vCPU/8 GiB |
| A23 | NOT RUN production: Nginx TLS/auth конфигурация подготовлена; публичный домен/сервер отсутствуют |
| A24 | NOT RUN внешняя проверка отказа VPS: Prometheus rules/metrics подготовлены, внешнего монитора нет |

Restore evidence: `docs/evidence/restore.json`. Локальный dump/restore с проверкой
артефактов и replay на окончательной схеме 002 занял **38.27 с**, результаты совпали.
Предыдущий локальный drill также прошёл (85.16 с). Это не измерение времени
полного восстановления VPS из внешнего хранилища и не доказательство production RPO.

## Открытые пункты до production-приёмки

Реальный контракт source/auth/snapshot; бизнес-владелец формул; PostgreSQL 17/Linux
integration; работоспособный Docker Engine, build и опубликованный digest;
registry/VPS/TLS/auth; Alertmanager receiver и внешний heartbeat; backup remote,
encryption keys и внешний restore drill; reboot/catchup и сетевые fault-тесты.

Дополнительные ограничения реализации: cleanup сохраняет все проверенные версии
и аудит (полная автоматическая TTL-политика не активирована); metrics не содержат
полный OS/CPU/WAL набор и 90-дневную бизнес-SLO агрегацию; для внешней PostgreSQL
нужно адаптировать backup/deploy-шаблоны. Эти пункты не обозначены как выполненные.
