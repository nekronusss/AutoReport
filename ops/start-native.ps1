# Local development alternative when Docker Desktop is unavailable.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
$uvTool = if (Test-Path '.tools/uv/uv.exe') { (Resolve-Path '.tools/uv/uv.exe').Path } else { (Get-Command uv -ErrorAction Stop).Source }
& $uvTool sync --frozen
if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed' }
$pgBin = @('C:\Program Files\PostgreSQL\17\bin', 'C:\Program Files\PostgreSQL\18\bin') | Where-Object { Test-Path (Join-Path $_ 'pg_ctl.exe') } | Select-Object -First 1
if (-not $pgBin) { throw 'Install PostgreSQL 17 or specify pgBin in this script' }
$env:DQFLOW_CONFIG = 'config/native-http.yaml'
& .\.venv\Scripts\dqflow.exe local-init
if ($LASTEXITCODE -ne 0) { throw 'local-init failed' }
New-Item -ItemType Directory -Force .tools | Out-Null
if (-not (Test-Path '.tools/pgdata/PG_VERSION')) {
    & (Join-Path $pgBin 'initdb.exe') -D '.tools/pgdata' -U postgres -A scram-sha-256 --pwfile='secrets/db_admin_password' --encoding=UTF8 --locale=C
    if ($LASTEXITCODE -ne 0) { throw 'initdb failed' }
}
$savedPreference = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& (Join-Path $pgBin 'pg_ctl.exe') -D '.tools/pgdata' status *> $null
$pgRunning = $LASTEXITCODE -eq 0
$ErrorActionPreference = $savedPreference
if (-not $pgRunning) {
    & (Join-Path $pgBin 'pg_ctl.exe') -D '.tools/pgdata' -l '.tools/postgres.log' -o '-p 55432 -h 127.0.0.1' -t 180 start
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL startup failed' }
}
$password = (Get-Content secrets/db_admin_password -Raw).Trim()
$pgPassPath = Join-Path (Get-Location) '.tools/pgpass'
[IO.File]::WriteAllText($pgPassPath, "127.0.0.1:55432:*:postgres:$password`n")
$env:PGPASSFILE = $pgPassPath
$exists = & (Join-Path $pgBin 'psql.exe') -h 127.0.0.1 -p 55432 -U postgres -d postgres -At -c "SELECT 1 FROM pg_database WHERE datname='dqflow_demo'"
if ($LASTEXITCODE -ne 0) { throw 'Cannot connect to local PostgreSQL' }
if ($exists -ne '1') {
    & (Join-Path $pgBin 'createdb.exe') -h 127.0.0.1 -p 55432 -U postgres dqflow_demo
    if ($LASTEXITCODE -ne 0) { throw 'createdb failed' }
}
& .\.venv\Scripts\dqflow.exe bootstrap-roles
if ($LASTEXITCODE -ne 0) { throw 'Role bootstrap failed' }
& .\.venv\Scripts\dqflow.exe migrate
if ($LASTEXITCODE -ne 0) { throw 'Migration failed' }
foreach ($entry in @(@('source', '18000', 'dqflow.source_api:app'), @('mock', '18001', 'dqflow.webhook:app'), @('web', '8080', 'dqflow.web:app'))) {
    $port = [int]$entry[1]
    if (-not (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue)) {
        Start-Process -FilePath '.\.venv\Scripts\python.exe' -ArgumentList @('-m', 'uvicorn', $entry[2], '--host', '127.0.0.1', '--port', $entry[1]) -WindowStyle Hidden -RedirectStandardOutput ".tools/$($entry[0])-out.log" -RedirectStandardError ".tools/$($entry[0])-error.log"
    }
}
& .\.venv\Scripts\dqflow.exe demo generate --period 2026-09-30 --rows 200000 --seed 42
if ($LASTEXITCODE -ne 0) { throw 'Demo generation failed' }
& .\.venv\Scripts\dqflow.exe run --period 2026-09-30 --source demo
if ($LASTEXITCODE -ne 0) { throw 'Pipeline failed; inspect JSON and logs' }
Write-Host 'Open http://127.0.0.1:8080'
