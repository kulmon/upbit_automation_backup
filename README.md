# upbit_automation_backup

This repository contains a sample Bitcoin swing trading bot (`trade_bot.py`).
The bot fetches market data from Upbit, calculates technical indicators,
asks GPT for trading advice, and uses a trend filter with moving averages
and ADX to ignore buy signals during strong downtrends. Buy conditions also
require multiple positive signals such as volume increase, divergence,
and MACD golden crosses.
