# CoinSwitch Automated Trading Platform — Deployment Guide

## Quick Start (Local)

```bash
# 1. Clone and install
git clone <your-repo>
cd coinswitch-trader
pip install -r requirements.txt

# 2. Configure environment
cp .env.example .env
# Edit .env with your settings

# 3. Run
python main.py
# Open http://localhost:8000
```

---

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `SIMULATION_MODE` | Run without real orders | `true` |
| `COINSWITCH_API_KEY` | Your API key | — |
| `COINSWITCH_API_SECRET` | Your API secret | — |
| `COINSWITCH_PASSPHRASE` | API passphrase | — |
| `DATABASE_URL` | SQLite or PostgreSQL | `sqlite+aiosqlite:///./trading.db` |
| `TELEGRAM_BOT_TOKEN` | For trade alerts | — |
| `TELEGRAM_CHAT_ID` | Telegram chat ID | — |
| `SECRET_KEY` | App secret key | auto-generated |
| `ENCRYPTION_KEY` | For encrypting secrets | auto-generated |

**Important**: Always start with `SIMULATION_MODE=true` until you've verified the bot works correctly.

---

## Docker Deployment

```bash
# Build and run
docker build -t coinswitch-trader .
docker run -p 8000:8000 --env-file .env coinswitch-trader

# Or with docker-compose
docker-compose up -d
```

---

## Deployment on Railway

1. Push code to GitHub
2. Create new project on [railway.app](https://railway.app)
3. Connect your GitHub repository
4. Add environment variables in Railway dashboard:
   - `SIMULATION_MODE=false` (when ready)
   - All `COINSWITCH_*` variables
   - `SECRET_KEY` (use a strong random value)
5. Railway auto-detects the Dockerfile and deploys

```bash
# Or use Railway CLI
npm install -g @railway/cli
railway login
railway init
railway up
```

---

## Deployment on Render

1. Create account at [render.com](https://render.com)
2. New Web Service → Connect GitHub repo
3. Settings:
   - **Runtime**: Docker
   - **Build Command**: (auto from Dockerfile)
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables in Render dashboard
5. Deploy

**Free tier note**: Render free tier sleeps after 15 min inactivity. Use a paid tier for 24/7 trading.

---

## Deployment on Fly.io

```bash
# Install flyctl
curl -L https://fly.io/install.sh | sh

# Login and deploy
fly auth login
fly launch  # Creates fly.toml automatically
fly secrets set COINSWITCH_API_KEY=xxx COINSWITCH_API_SECRET=yyy
fly deploy
```

`fly.toml` (auto-generated, adjust as needed):
```toml
app = "coinswitch-trader"
primary_region = "sin"  # Singapore - close to CoinSwitch

[build]

[http_service]
  internal_port = 8000
  force_https = true

[[vm]]
  memory = "512mb"
  cpu_kind = "shared"
  cpus = 1
```

---

## Deployment on PythonAnywhere

1. Upload code via Files tab or git clone
2. Create a virtual environment:
   ```bash
   mkvirtualenv --python=python3.11 trader
   pip install -r requirements.txt
   ```
3. Configure WSGI file to point to your FastAPI app
4. Set environment variables in `.env` file

**Note**: PythonAnywhere free tier has restricted outbound connections. You may need a paid plan for WebSocket connections.

---

## Deployment on Replit

1. Create a new Repl → Import from GitHub
2. Add Secrets in the Replit UI (equivalent to .env)
3. In `.replit` file:
   ```toml
   run = "python main.py"
   ```
4. Click Run

---

## PostgreSQL Setup (Optional, Recommended for Production)

```bash
# Install PostgreSQL driver
pip install asyncpg psycopg2-binary

# Set in .env
DATABASE_URL=postgresql+asyncpg://user:password@host:5432/trading_db
```

On Railway/Render/Fly, you can add a PostgreSQL addon and they'll provide the connection string.

---

## Getting CoinSwitch API Credentials

1. Visit the CoinSwitch HFT portal
2. Navigate to API Management
3. Create a new API key with trading permissions
4. Copy API Key, Secret, and Passphrase
5. Enter them in the Settings page of the dashboard, OR set them in `.env`

**Security best practices:**
- Never commit `.env` to git
- Use IP whitelisting if available
- Start with read-only permissions to test, then add trading
- Always test in SIMULATION_MODE first

---

## Telegram Alert Setup

1. Message `@BotFather` on Telegram
2. Create new bot: `/newbot`
3. Copy the bot token to `TELEGRAM_BOT_TOKEN`
4. Start a chat with your bot, then visit:
   `https://api.telegram.org/bot<TOKEN>/getUpdates`
5. Copy the `chat.id` to `TELEGRAM_CHAT_ID`

---

## Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## Architecture Overview

```
main.py (FastAPI app)
├── app/api/routes.py          # All REST API endpoints
├── app/trading/
│   ├── spot.py               # Spot order management
│   └── futures.py            # Futures order management
├── app/strategies/
│   ├── engine.py             # Strategy orchestrator
│   ├── ma_crossover.py       # MA strategy
│   ├── rsi_strategy.py       # RSI strategy
│   ├── grid_strategy.py      # Grid trading
│   ├── dca_strategy.py       # DCA bot
│   └── breakout_strategy.py  # Breakout strategy
├── app/services/
│   ├── market_data.py        # Price/candle fetching
│   ├── backtesting.py        # Historical testing
│   ├── alerts.py             # Telegram/email alerts
│   └── scheduler.py          # APScheduler tasks
├── app/risk/manager.py        # Risk controls
├── app/websocket/listener.py  # Real-time WS feed
└── app/api/client.py          # CoinSwitch REST client
```

---

## Adding Custom Strategies

1. Create `app/strategies/my_strategy.py`:
```python
from app.strategies.base import BaseStrategy

class MyStrategy(BaseStrategy):
    name = "my_strategy"
    description = "My custom strategy"

    async def run(self):
        df = await self.get_candles_df("1h")
        # Your logic here
        if some_condition:
            await self.buy()
            return "BUY"
        return None
```

2. Register in `app/strategies/engine.py`:
```python
from app.strategies.my_strategy import MyStrategy
STRATEGY_REGISTRY["my_strategy"] = MyStrategy
```

3. Re-run the app — it will appear in the Settings page automatically.

---

## Monitoring

- Dashboard: `http://localhost:8000/`
- API docs: `http://localhost:8000/api/docs`
- Logs: `./logs/trading.log`
- Database: `./trading.db` (SQLite, viewable with DB Browser for SQLite)

---

## Disclaimer

This software is for educational and informational purposes. Cryptocurrency trading involves significant financial risk. Always test thoroughly in simulation mode before using real funds. The authors are not responsible for any financial losses.
