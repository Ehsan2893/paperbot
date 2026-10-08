# Paper-trading simulator (NO real money) - one run per execution (GitHub Actions)
import requests, time, json, os, csv
from datetime import datetime

API = "https://api.dexscreener.com"
STATE = "paper_state.json"
LOG = "paper_trades.csv"

CFG = dict(
    start_balance=40.0,   # fake dollars
    size=8.0,             # fake dollars per trade
    max_open=5,
    tp=7.0,               # take profit %
    sl=-5.0,              # stop loss %
    max_minutes=60,       # time exit
    cost_pct=1.5,         # simulated fees+slippage per round trip
    min_liq=50000,
    min_vol_h1=20000,
    min_txns_h1=100,
    min_age_h=6,
    min_h1_change=0.0,
    max_h1_change=30.0,
    spike=2.0,            # 5m volume pace vs 1h volume
    cooldown_h=6,
    loop_sec=60,
)


def get(path):
    try:
        r = requests.get(API + path, timeout=15)
