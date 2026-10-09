# Доступ и секреты

Роли: bootstrap postgres, dqflow_migrator (DDL), dqflow_runner (raw/candidate/meta),
dqflow_publisher (публикация), dqflow_reader (опубликованные версии и необходимая
метаинформация). Reader не читает raw. Блокировка dataset удерживается publisher-соединением;
та же сессия выполняет COMMIT публикации. Переподключение для продолжения COMMIT не выполняется.

Миграция 002 запрещает runner изменять meta.publications. Runner также не может
вставлять версии в mart; эти операции доступны publisher. Doctor проверяет
актуальную Alembic revision и отсутствие у runner права переключения публикации.

Файлы секретов не включены в репозиторий и образ. `.env` содержит только несекретные
параметры. Production runner не получает migrator/admin secret, report-web получает
только reader secret. Не передавайте секреты через URL или аргументы CLI.

Report-web и metrics-exporter монтируют только artifacts в read-only режиме,
без исходных файлов, с read-only root filesystem и временным tmpfs.

HTML autoescape, CSP, CSV formula neutralization, allowlist имён артефактов,
проверка containment пути; raw/candidate не обслуживаются HTTP.
Произвольные архивы не распаковываются. HTTP-коннектор не следует redirect.

Production требует verify-full TLS к БД и HTTPS к источнику, TLS+auth на proxy.
Nginx-шаблон: ops/nginx.conf.example. Без настройки proxy локальный HTTP сам по себе
не реализует вход пользователей: наружу его публиковать нельзя. Compose связывает
порт report-web только с 127.0.0.1; БД не публикуется.

Нативный тестовый кластер, созданный для проверки, слушает только 127.0.0.1 и использует
trust-аутентификацию для локальной демонстрации. Он не является production-конфигурацией.
Docker PostgreSQL использует случайный пароль и стандартную парольную аутентификацию образа.

Backup содержит чувствительные служебные сведения (включая определения ролей),
поэтому архив шифруется age до загрузки во внешнее хранилище. Ключ дешифрования хранится отдельно.
