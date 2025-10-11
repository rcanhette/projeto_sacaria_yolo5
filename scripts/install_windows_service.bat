@echo off
REM Instala e inicia o servico do Projeto Sacaria no Windows.
REM Editar previamente windows_service.ini para ajustar host/porta/logs.

SETLOCAL
SET PROJECT_DIR=%~dp0..

IF EXIST "%PROJECT_DIR%\venv\Scripts\python.exe" (
    SET PYTHON="%PROJECT_DIR%\venv\Scripts\python.exe"
) ELSE (
    SET PYTHON=python
)

echo Instalando servico ProjetoSacaria...
%PYTHON% "%PROJECT_DIR%\windows_service.py" install
IF %ERRORLEVEL% NEQ 0 (
    echo Falha ao instalar o servico.
    exit /b %ERRORLEVEL%
)

echo Iniciando servico ProjetoSacaria...
%PYTHON% "%PROJECT_DIR%\windows_service.py" start

echo Concluido.
ENDLOCAL
