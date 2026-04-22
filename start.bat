@echo off
chcp 65001 > nul
title DeepSeek Coder Agent

echo ============================================
echo   DeepSeek Coder Agent - Setup ^& Start
echo ============================================
echo.

:: Suche Python
set PYTHON=
for %%P in (python python3) do (
    if not defined PYTHON (
        %%P --version >nul 2>&1 && set PYTHON=%%P
    )
)

if not defined PYTHON (
    echo [FEHLER] Python wurde nicht gefunden!
    echo Bitte installiere Python 3.10+ von https://www.python.org/downloads/
    echo Stelle sicher, dass "Add Python to PATH" angehaekt ist.
    pause
    exit /b 1
)

echo [OK] Python gefunden: %PYTHON%
echo.

:: .env anlegen falls noetig
if not exist ".env" (
    if exist ".env.example" (
        copy ".env.example" ".env" > nul
        echo [INFO] .env wurde aus .env.example erstellt.
        echo        Bitte trage deinen DeepSeek API-Schluessel ein:
        echo        Datei: %~dp0.env
        echo.
    )
)

:: Pakete installieren / aktualisieren
echo [1/2] Installiere Abhaengigkeiten...
%PYTHON% -m pip install -q --upgrade pip
%PYTHON% -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo [FEHLER] pip install fehlgeschlagen.
    pause
    exit /b 1
)
echo [OK] Pakete installiert.
echo.

:: Starten
echo [2/2] Starte DeepSeek Coder Agent...
echo.
%PYTHON% main.py

if errorlevel 1 (
    echo.
    echo [FEHLER] Programm mit Fehler beendet.
    pause
)
