# ⚡ CoinSwitch Automated Trading Platform

A production-ready, modular automated crypto trading platform for the CoinSwitch Trading API.

## Features

- **Spot & Futures Trading** — Full order management with market/limit/stop orders
- **5 Built-in Strategies** — MA Crossover, RSI, Grid Trading, DCA Bot, Breakout
- **Backtesting** — Test strategies against historical data with metrics
- **Risk Management** — Max loss, position limits, leverage caps, kill switch
- **Real-time Data** — WebSocket feed with auto-reconnect
- **Web Dashboard** — Dark-mode UI for monitoring and control
- **Simulation Mode** — Test everything without real money
- **Alerts** — Telegram and email notifications
- **Deploy anywhere** — Docker, Railway, Render, Fly.io, Replit

## Quick Start

```bash
git clone <repo>
cd coinswitch-trader
pip install -r requirements.txt
cp .env.example .env
python main.py
```

Open **http://localhost:8000**

## Project Structure

```
coinswitch-trader/
├── main.py                   # FastAPI application entry point
├── config/settings.py        # Configuration management
├── app/
│   ├── api/
│   │   ├── client.py         # CoinSwitch REST client + HMAC auth
│   │   └── routes.py         # All API endpoints
│   ├── trading/
│   │   ├── spot.py           # Spot trading service
│   │   └── futures.py        # Futures trading service
│   ├── strategies/
│   │   ├── base.py           # Abstract base strategy
│   │   ├── engine.py         # Strategy orchestrator
│   │   ├── ma_crossover.py   # Moving average crossover
│   │   ├── rsi_strategy.py   # RSI overbought/oversold
│   │   ├── grid_strategy.py  # Grid trading
│   │   ├── dca_strategy.py   # Dollar cost averaging
│   │   └── breakout_strategy.py  # Price breakout
│   ├── services/
│   │   ├── market_data.py    # Ticker, candles, orderbook
│   │   ├── backtesting.py    # Historical backtesting engine
│   │   ├── alerts.py         # Telegram + email alerts
│   │   └── scheduler.py      # APScheduler task runner
│   ├── risk/manager.py       # Risk controls & kill switch
│   ├── websocket/listener.py # HFT WebSocket with reconnect
│   ├── models/db_models.py   # SQLAlchemy ORM models
│   └── database/session.py   # Async DB session management
├── frontend/templates/       # Jinja2 HTML templates
├── tests/test_core.py        # Unit tests
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── DEPLOYMENT.md             # Full deployment guide
```

## Configuration

All settings are in `.env`. Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `SIMULATION_MODE` | `true` | Test without real orders |
| `COINSWITCH_API_KEY` | — | Your API key |
| `DATABASE_URL` | SQLite | Database connection |
| `TELEGRAM_BOT_TOKEN` | — | For alerts |

## Authentication

CoinSwitch API uses HMAC-SHA256 signatures:
```
Signature = HMAC_SHA256(secret, nonce + METHOD + path + body)
```

Headers: `CS-API-KEY`, `CS-SIGNATURE`, `CS-NONCE`, `CS-PASSPHRASE`

## Adding Strategies

Extend `BaseStrategy`, implement `run()`, register in `STRATEGY_REGISTRY`. See `DEPLOYMENT.md`.

## Tests

```bash
pytest tests/ -v
```

## License

MIT — See LICENSE file.

## ⚠️ Disclaimer

Trading cryptocurrencies involves substantial risk of loss. This software is provided for educational purposes. Always test in simulation mode first.
