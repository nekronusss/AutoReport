# dqflow CLI

[README](../README.md)

## Команды

| Команда | Назначение |
|---|---|
| `doctor` | Конфигурация, доступность схемы, доступ reader, запись артефактов, свободный диск |
| `migrate` | Alembic, отдельная роль migrator |
| `demo generate --period D --rows N --seed S [--scenario NAME]` | Детерминированный источник; N кратно 20 или равно 0 |
| `run --period D --source demo` | Загрузка, 20 правил, dbt build/test, публикация, отчёт, outbox |
| `status --period D --json` | Отдельные состояния публикации, отчёта, уведомления и последней попытки |
| `report --period D` | Повтор рендера текущего run_id без загрузки |
| `retry --run-id UUID` | Повтор исходного snapshot; отдельный attempt_id |
| `backfill --from D1 --to D2 --source demo` | Обе даты включены; остановка на первой ошибке |
| `rollback --period D --to-run-id UUID --reason TEXT` | Переключение проверенной версии с аудитом |
| `schedule tick` | После 05:00 догоняет отсутствующие даты последних 7 дней |
| `outbox drain` | Атомарное резервирование событий через FOR UPDATE SKIP LOCKED |
| `acceptance --profile local` | Unit/integration-тесты; JUnit в `data/acceptance.xml` |
| `benchmark --rows 200000 --repeat 20 [--baseline]` | Реальные повторные загрузки в отдельных dataset; JSON с замерами |
| `cleanup` | Удаление просроченного raw неуспешных запусков после подтверждённой внешней копии |
| `backup-confirmed --reference TEXT` | Отметка проверенной внешней копии; вызывается backup.sh после roundtrip |
| `serve [--kind reports|source|mock]` | Локальный HTTP-сервис |

Даты строго `YYYY-MM-DD`. `--source demo` использует настроенный тип CSV/HTTP;
допустимо также имя настроенного типа. Все CLI-команды: `dqflow --help`.

Коды выхода: **0** успех/no-op, **2** конфигурация/параметры, **3** качество,
**4** источник, **5** инфраструктура, **6** публикация есть, отчёт ожидается,
**75** занята блокировка. JSON в stdout; стадии в JSON-логах stderr.

Сценарии генератора: `clean`, `duplicate`, `null`, `decimal`, `status`,
`missing_customer`, `missing_item`, `orphan_payment`, `currency`, `negative`,
`over_refund`, `unpaid`, `wrong_total`, `future`, `empty_id`, `incomplete`,
`corrupt`, `schema`, `warning`, `source_sum`.

```powershell
# Испорченная новая поставка заблокируется, корректная публикация останется доступна.
uv run dqflow demo generate --period 2026-09-30 --rows 200000 --seed 43 --scenario duplicate
uv run dqflow run --period 2026-09-30 --source demo
uv run dqflow status --period 2026-09-30 --json
```

