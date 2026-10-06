# Python_Coinswitch — Crypto Trading Context

## Purpose
Python cryptocurrency trading project integrating CoinSwitch-related trading functionality.

## Architecture
Inspect the coinswitch-trader directory first. Trace configuration -> market data -> strategy/decision -> order/API call -> logging/state.

## AI Rules
Never expose API keys. Treat order execution as high impact: do not change live/paper mode, position sizing, stop-loss logic or exchange endpoints implicitly.

## Validation
Use mocked/sandbox API responses for strategy and order-flow tests. Validate rounding, precision, retries and rate limits.