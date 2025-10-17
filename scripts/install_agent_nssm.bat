@echo off
REM Instala/atualiza o Agente Projeto Sacaria via NSSM.
REM Usa agent.ini por padrao (ou AGENT_INI se definido).

SETLOCAL
SET PROJECT_DIR=%~dp0..

IF EXIST "%PROJECT_DIR%\venv\Scripts\python.exe" (
    SET PYTHON="%PROJECT_DIR%\venv\Scripts\python.exe"
    ) ELSE (
    SET PYTHON=python
)

%PYTHON% "%PROJECT_DIR%\scripts\install_agent_nssm.py"
SET EXITCODE=%ERRORLEVEL%

IF %EXITCODE% NEQ 0 (
    echo Falha ao instalar o agente via NSSM. Codigo %EXITCODE%.
) ELSE (
    echo Agente configurado com sucesso. Use "nssm start ProjetoSacaria_Agent_*".
)

ENDLOCAL
EXIT /B %EXITCODE%
