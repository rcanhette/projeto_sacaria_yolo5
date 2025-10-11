@echo off
REM Para e remove o servico do Projeto Sacaria no Windows.

SETLOCAL
SET PROJECT_DIR=%~dp0..

IF EXIST "%PROJECT_DIR%\venv\Scripts\python.exe" (
    SET PYTHON="%PROJECT_DIR%\venv\Scripts\python.exe"
) ELSE (
    SET PYTHON=python
)

echo Parando servico ProjetoSacaria...
%PYTHON% "%PROJECT_DIR%\windows_service.py" stop

echo Removendo servico ProjetoSacaria...
%PYTHON% "%PROJECT_DIR%\windows_service.py" remove

echo Concluido.
ENDLOCAL
