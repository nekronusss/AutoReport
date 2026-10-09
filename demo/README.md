# Demo scenarios

Генератор находится в `src/dqflow/demo.py`; CLI: `dqflow demo generate`.
API: `src/dqflow/source_api.py`; CLI: `dqflow serve --kind source --port 8000`.

API читает неизменяемые каталоги `<snapshot_id>` и атомарный `<period>.json` pointer.
На стороне источника можно создать `fault.json` в каталоге demo:

```json
{"id":"throttle-1","table":"orders","status":429,"times":1,"retry_after":3}
```

Для новой fault-серии используйте новый id. Файл применяется только к synthetic
source; с production URL этот механизм не связан. Удаление fault.json возвращает
нормальную работу. 401 не повторяется; 429/503 повторяются с ограничением.

Сценарии качества доступны `--scenario duplicate|decimal|...`; полный список в README.
Изменение seed или scenario создаёт другой snapshot_id. Для проверки пустого дня — `--rows 0`.
