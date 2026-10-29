@echo off
cd /d "%~dp0"
echo Installation de LiveDeck...
where py >nul 2>nul && (py -3 -m venv .venv) || (python -m venv .venv)
.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\python -m pip install -r requirements.txt
.venv\Scripts\python -m livedeck.setup_hidapi
echo.
echo Installation terminee. Double-cliquez sur LiveDeck.bat pour lancer l'application.
pause
