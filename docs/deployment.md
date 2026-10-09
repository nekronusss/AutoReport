# Развёртывание

## Локальный стенд

`ops/start-local.ps1` выполняет полный порядок. Требуется доступный Linux Docker
Engine и сеть для скачивания образов. Образы закреплены тегами конкретных версий,
Python-пакеты — uv.lock. Не используйте `docker compose down -v` для обновления.

## Linux VPS

Production-шаблон требует адаптации к предоставленному серверу. Рекомендуемый
испытательный стенд из ТЗ: 4 vCPU, 8 ГБ RAM, SSD. Это ещё не измеренная ёмкость production.

1. Подготовить `/opt/dqflow/releases/<release_id>`, `/etc/dqflow`, `/srv/dqflow/{raw,artifacts,postgres}`,
   владельца UID 10001 для файлов приложения, отдельные секреты с ограниченными правами.
2. Собрать, проверить и отправить образ в свой registry. Установить
   `DQFLOW_IMAGE=registry/path@sha256:<64 hex>` и проверить digest.
3. Заполнить config на основе `config/prod.example.yaml`. Пример использует внешнюю
   PostgreSQL с TLS verify-full. Если БД в Compose, настроить её TLS-сертификаты,
   ключ и SAN hostname отдельно; базовый локальный postgres-сервис этого не делает.
   Backup/deploy-скрипты рассчитаны на локальный postgres-сервис; для внешней БД заменить
   pg_dump-вызов на аутентифицированное соединение с ней и проверить restore.
   Backup preflight блокирует внешний host, чтобы не создать ошибочную копию другой БД.
4. Создать роли bootstrap-admin, применить миграции migrator. Перед обновлением — backup.
5. Настроить Nginx TLS+auth по примеру, проверить внешний доступ и закрытость DB/metrics.
6. Настроить Alertmanager receiver и независимую внешнюю HTTPS-проверку
   доступности хоста, health и возраста публикации. На одном VPS самонаблюдение
   не обнаруживает отключение всей машины.
7. Выполнить staging smoke + сверку контрольных сумм, backup/restore drill, пилотный период.
8. Установить units из ops/systemd, EnvironmentFile `/etc/dqflow/ops.env` и пользователя
   dqflow-ops. Доступ к Docker считается привилегированным. Создать доступный ему
   `/run/lock/dqflow-maintenance.lock` через systemd-tmpfiles.
9. Проверить `systemd-analyze calendar '*-*-* *:0/15:00'` на целевой ОС; включить timers.

Для обновления существующего стенда: `ops/deploy.sh --environment production --release ID`.
Скрипт проверяет digest/config, делает backup, выполняет миграцию и smoke, затем
переключает current. Первый deploy выполняется по шагам выше: backup требует существующую БД.

## Backup / restore

`ops/backup.sh` требует age, rclone, внешний remote и public recipient file.
Все systemd write jobs используют shared flock, backup — exclusive flock.
На время копирования запрещены ручные записи в обход ops/job.sh; перед первым
backup убедиться, что таких runner/outbox/cleanup процессов нет.

Состав: custom pg_dump, роли, config без секретных значений, uv.lock, image digest,
raw и artifacts. SHA-256 перечень внутри зашифрованного архива; внешняя копия
скачивается обратно и сравнивается побайтно. Только после этого подтверждается backup.
Rclone credentials и age identity размещаются вне репозитория.

Restore всегда в **новую** БД с префиксом dqflow_restore_ и новый каталог. На чистом
кластере сначала проверить/восстановить роли из roles.sql. Скрипт не перезаписывает
рабочую БД. После восстановления пересопоставить пути source_batches/report_artifacts
с изолированным `/data`, отключить notifications и timers, выполнить doctor,
проверить хеши всех артефактов и raw, контрольные суммы, открыть отчёт и replay.

Секреты и private key не включаются в незашифрованный config. RPO/RTO — цели,
подтверждаются конкретным restore drill; наличие скриптов недостаточно.

## CI / выпуск

Workflow проверяет lint, типизацию, unit/integration, dbt build/test через pipeline,
собирает образ. Release workflow публикует в GHCR только при явном workflow_dispatch,
создаёт SBOM/provenance и записывает digest. Никакой образ из этой сессии автоматически
в чужой registry не публиковался. Настройте защищённую production environment отдельно.
