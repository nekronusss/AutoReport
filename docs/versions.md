# Проверенные версии

Пакеты установлены `uv sync --frozen`; транзитивные зависимости и хеши — `uv.lock`.

| Компонент | Версия / статус |
|---|---|
| Python | 3.12.12, Windows x64 — фактически запущен |
| uv | 0.9.8 |
| PostgreSQL native | 18.1, Windows x64 — фактические интеграционные испытания |
| PostgreSQL Docker / CI | 17.9-bookworm — закреплённый образ; локальный Docker Engine не запустился |
| dbt Core | 1.12.5 |
| dbt-postgres | 1.11.0 |
| psycopg | 3.2.10, binary |
| Pydantic | 2.12.3 |
| HTTPX | 0.28.1 |
| Alembic | 1.17.1 |
| Typer | 0.20.0 |
| Jinja2 | 3.1.6 |
| FastAPI / Starlette / Uvicorn | 0.143.0 / 1.7.0 / 0.38.0 |
| sqlparse / pytest | 0.6.0 / 9.1.1 |
| Docker client | 29.1.3 |
| Python base image | python:3.12.12-slim-bookworm |
| Prometheus / Alertmanager | v3.7.3 / v0.28.1; конфигурация подготовлена |

Docker backend сообщает `wslUpdateRequired: true`; перед Docker-испытаниями
обновить WSL поддерживаемым способом и перезапустить Docker Desktop.

Образ приложения в registry не опубликован, release digest отсутствует. Release
workflow создаёт SBOM/provenance и digest после явного запуска в вашем GitHub-репозитории.
На целевом стенде требуется Linux/PostgreSQL 17 integration + image build до приёмки Docker-релиза.

Использованная первичная документация:

* [dbt PostgreSQL profile](https://docs.getdbt.com/docs/local/connect-data-platform/postgres-setup)
* [psycopg COPY](https://www.psycopg.org/psycopg3/docs/basic/copy.html)
* [PostgreSQL version policy](https://www.postgresql.org/support/versioning/)

Фактический тест совместимости имеет приоритет над предполагаемой совместимостью
по номерам версий. Перед production обновить и проверить поддерживаемые patch-релизы,
dependency audit и образы на целевой архитектуре.

При финальной проверке PyPI advisory metadata выявлены уязвимости старых pytest,
sqlparse и Starlette. Lock обновлён до исправленных версий; для sqlparse 0.6.0
потребовалось совместимое обновление dbt Core/adapter. Итоговая проверка всех пакетов
lock: **0 affected packages, 0 ошибок получения данных**. Подробности и время проверки:
`docs/evidence/pypi-audit.json`. Это проверка известных advisory, не гарантия отсутствия
неизвестных уязвимостей. Длительный pip-audit resolver был остановлен; CI сохраняет
отдельный полный шаг pip-audit.
