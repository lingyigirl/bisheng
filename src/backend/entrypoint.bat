@echo off
setlocal

REM ============================================================================
REM BiSheng backend launcher - Windows local development.
REM
REM Counterpart of entrypoint.sh (the Linux/Docker entrypoint): same start modes,
REM adapted for a local Windows checkout.
REM
REM Differences from entrypoint.sh, and why:
REM   * Calls the venv executables explicitly (.venv\Scripts\*.exe) rather than
REM     relying on PATH. A system-wide celery on PATH shadows the venv one -
REM     observed here as celery 5.6.3 instead of the pinned 5.5.3.
REM   * Starts from this file's own directory, so it works from any cwd
REM     (including a double-click); entrypoint.sh assumes cwd = src/backend.
REM   * --workers 1 instead of 2: for local debugging you want a single process,
REM     otherwise the breakpoint lands in a different process than the one
REM     you attached the debugger to.
REM
REM Usage:  entrypoint.bat [mode]
REM Modes:  api | worker | knowledge | knowledge_ocr | workflow | beat
REM         | default | linsight | check
REM Default mode is `api`.
REM
REM Note: %%h in the -n flags is deliberate. A single %h inside a .bat is eaten
REM by cmd, silently collapsing the node name to "knowledge@h".
REM ============================================================================

cd /d "%~dp0"

set "VENV=%~dp0.venv\Scripts"
set "PYTHONPATH=."
set "config=config.yaml"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=api"

REM ---------------------------------------------------------------------------
REM Preflight - fail with an actionable message instead of an obscure traceback
REM ---------------------------------------------------------------------------
if not exist "%VENV%\python.exe" (
    echo [ERROR] No virtualenv at "%VENV%"
    echo         Create it with:  cd src\backend  ^&^&  uv sync --frozen
    exit /b 1
)

if not exist "%~dp0bisheng\config.yaml" (
    echo [ERROR] Missing "%~dp0bisheng\config.yaml"
    echo.
    echo         It is gitignored, so a fresh clone has none. Start from
    echo         docker\bisheng\config\config.yaml and point the hosts at your
    echo         own MySQL / Redis / MinIO / ES / Milvus / OpenFGA.
    echo.
    echo         Passwords in database_url / redis_url MUST stay Fernet-encrypted:
    echo         they are decrypted unconditionally at load time, so plaintext
    echo         aborts startup with InvalidToken.
    exit /b 1
)

if /i "%MODE%"=="check" goto :check
if /i "%MODE%"=="api"            goto :api
if /i "%MODE%"=="worker"         goto :worker
if /i "%MODE%"=="knowledge"      goto :knowledge
if /i "%MODE%"=="knowledge_ocr"  goto :knowledge_ocr
if /i "%MODE%"=="workflow"       goto :workflow
if /i "%MODE%"=="beat"           goto :beat
if /i "%MODE%"=="default"        goto :default
if /i "%MODE%"=="linsight"       goto :linsight

echo Invalid start mode: "%MODE%"
echo Valid modes: api ^| worker ^| knowledge ^| knowledge_ocr ^| workflow ^| beat ^| default ^| linsight ^| check
exit /b 1


:api
REM Mirrors entrypoint.sh: refuse to serve on a stale / half-migrated schema.
REM The usual cause is multiple alembic heads (a new revision whose down_revision
REM was pinned to an already-applied revision instead of the current head).
REM
REM SKIP_MIGRATE=1 bypasses this. Useful when the target DB is shared with others,
REM so a local start is not silently mutating a database you do not own.
if not defined SKIP_MIGRATE (
    echo Running database migrations...
    "%VENV%\alembic.exe" upgrade head
    if errorlevel 1 (
        echo FATAL: 'alembic upgrade head' failed; refusing to start the API on a stale schema. 1>&2
        echo        If the cause is multiple heads: 'alembic heads' then 'alembic merge heads'. 1>&2
        echo        To start anyway:  set SKIP_MIGRATE=1  then re-run. 1>&2
        exit /b 1
    )
) else (
    echo Skipping migrations ^(SKIP_MIGRATE is set^).
)

echo Starting API server on http://localhost:7860 ...
"%VENV%\uvicorn.exe" bisheng.main:app --host 0.0.0.0 --port 7860 --workers 1 --no-access-log
exit /b %errorlevel%


:knowledge
echo Starting Knowledge Celery worker...
"%VENV%\celery.exe" -A bisheng.worker.main worker -l info -c 20 -P threads -Q knowledge_celery -n knowledge@%%h
exit /b %errorlevel%


:knowledge_ocr
REM Only needed when config.yaml sets knowledge_file_worker.ocr_queue_enabled=true;
REM otherwise ocr_celery accumulates tasks that nothing consumes.
echo Starting Knowledge OCR Celery worker...
"%VENV%\celery.exe" -A bisheng.worker.main worker -l info -c 5 -P threads -Q ocr_celery -n knowledge_ocr@%%h
exit /b %errorlevel%


:workflow
REM Multi-node is supported, but each node's queue name must be distinct and
REM prefixed with workflow_celery.
echo Starting Workflow Celery worker...
"%VENV%\celery.exe" -A bisheng.worker.main worker -l info -c 100 -P threads -Q workflow_celery -n workflow@%%h
exit /b %errorlevel%


:beat
echo Starting Celery beat...
"%VENV%\celery.exe" -A bisheng.worker.main beat -l info
exit /b %errorlevel%


:default
echo Starting default celery worker...
"%VENV%\celery.exe" -A bisheng.worker.main worker -l info -c 100 -P threads -Q celery -n celery@%%h
exit /b %errorlevel%


:linsight
echo Starting LinSight worker...
"%VENV%\python.exe" bisheng\linsight\worker.py --worker_num 1 --max_concurrency 5
exit /b %errorlevel%


:worker
REM entrypoint.sh backgrounds these with `&` in one shell. cmd has no job control,
REM so each gets its own window via `start`; closing the window stops that worker.
echo Starting minimal celery worker + beat, each in its own window...
start "BiSheng celery worker" cmd /k ""%VENV%\celery.exe" -A bisheng.worker.main worker -l info -c 100 -P threads -Q knowledge_celery,ocr_celery,workflow_celery,celery -n min_worker@%%h"
start "BiSheng celery beat"   cmd /k ""%VENV%\celery.exe" -A bisheng.worker.main beat -l info"
echo.
echo Both started in separate windows. Close a window (or Ctrl+C in it) to stop that worker.
exit /b 0


:check
REM Smoke-test the config without starting any service. Catches what otherwise
REM shows up as a confusing traceback ~40s into startup.
echo [1/3] Python
"%VENV%\python.exe" --version
if errorlevel 1 exit /b 1
echo.
echo [2/3] config.yaml parses, Fernet passwords decrypt
"%VENV%\python.exe" -c "from bisheng.common.services.config_service import settings as s; m = s.get_minio_conf(); print('  database :', s.database_url.split('@')[-1]); print('  redis    :', s.redis_url); print('  celery   :', s.celery_redis_url); print('  minio    :', m.endpoint); print('  openfga  :', s.openfga.enabled, s.openfga.api_url); print('  es       :', s.get_search_conf().elasticsearch_url)"
if errorlevel 1 (
    echo.
    echo [FAIL] Config did not load - see the traceback above.
    exit /b 1
)
echo.
echo [3/3] Alembic heads ^(must be exactly one^)
"%VENV%\alembic.exe" heads
echo.
echo Check complete. Backends are NOT contacted here; run a start mode for that.
exit /b 0
