@echo off
REM ============================================================
REM  CoinSwitch Trader - Install dependencies and run tests
REM  Usage: Double-click this file, or run from project root:
REM         install_and_test.bat
REM ============================================================

echo.
echo ============================================================
echo  Installing dependencies into active virtual environment...
echo ============================================================
echo.

REM Core framework
pip install fastapi==0.111.0
pip install "uvicorn[standard]==0.30.1"
pip install jinja2==3.1.4
pip install python-multipart==0.0.9
pip install aiofiles==23.2.1

REM HTTP
pip install httpx==0.27.0
pip install websockets==12.0
pip install tenacity==8.3.0

REM Database
pip install sqlalchemy==2.0.30
pip install aiosqlite==0.20.0
pip install alembic==1.13.1

REM Settings
pip install pydantic==2.7.1
pip install pydantic-settings==2.2.1
pip install python-dotenv==1.0.1

REM Security
pip install "python-jose[cryptography]==3.3.0"
pip install cryptography==42.0.7
pip install "passlib[bcrypt]==1.7.4"

REM Scheduling
pip install apscheduler==3.10.4

REM Data
pip install pandas==2.2.2
pip install numpy==1.26.4
pip install ta==0.11.0

REM Logging
pip install loguru==0.7.2

REM Testing
pip install pytest==8.2.0
pip install pytest-asyncio==0.23.7

REM Optional - alerts (comment out if causing issues)
pip install python-telegram-bot==21.3

echo.
echo ============================================================
echo  Running tests...
echo ============================================================
echo.

pytest tests/ -v

echo.
pause
