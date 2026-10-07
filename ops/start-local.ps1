$ErrorActionPreference = 'Stop'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
function Compose {
    & docker compose -f compose.yaml -f compose.local.yaml @args
    if ($LASTEXITCODE -ne 0) { throw "Compose failed: $args" }
}
Compose build
Compose run --rm bootstrap local-init
Compose up -d --wait postgres source-demo mock-webhook
Compose run --rm bootstrap-roles
Compose run --rm runner migrate
Compose up -d --wait report-web
Compose run --rm runner doctor
Compose run --rm runner demo generate --period 2026-09-30 --rows 200000 --seed 42
Compose run --rm runner run --period 2026-09-30 --source demo
Write-Host 'Report: http://127.0.0.1:8080'
