@echo off
REM Configura o servico Projeto Sacaria utilizando NSSM.
REM Espera que o arquivo windows_service.ini esteja devidamente ajustado.

SETLOCAL
SET PROJECT_DIR=%~dp0..

IF EXIST "%PROJECT_DIR%\venv\Scripts\python.exe" (
    SET PYTHON="%PROJECT_DIR%\venv\Scripts\python.exe"
) ELSE (
    SET PYTHON=python
)

%PYTHON% "%PROJECT_DIR%\scripts\install_windows_service_nssm.py"
SET EXITCODE=%ERRORLEVEL%

IF %EXITCODE% NEQ 0 (
    echo Falha ao configurar o servico via NSSM. Codigo %EXITCODE%.
) ELSE (
    echo Configuracao concluida. Utilize "nssm start <Servico>" conforme o nome exibido acima.
)

ENDLOCAL
EXIT /B %EXITCODE%
