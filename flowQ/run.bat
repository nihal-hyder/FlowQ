@echo off
rem Starts flowQ on http://localhost:8000 (API docs: http://localhost:8000/docs)
cd /d "%~dp0backend"
if not exist "..\.venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv ..\.venv
  ..\.venv\Scripts\python -m pip install -r requirements.txt
)
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
