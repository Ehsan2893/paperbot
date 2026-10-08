# Paper-trading simulator (NO real money) - one run per execution (GitHub Actions)
import requests, time, json, os, csv
from datetime import datetime

API = "https://api.dexscreener.com"
STATE = "state.json"
LOG = "trades.csv"

CFG = dict(
    start_balance=40.0,
    size=8.0,
    max_open=5,
    tp=7.0,
    sl=-5.0,
    max_minutes=60,
    cost_pct=1.5,
    min_liq=50000,
    min_vol_h1=20000,
    min_txns_h1=100,
    min_age_h=6,
    min_h1_change=0.0,
    max_h1_change=30.0,
    spike=2.0,
    cooldown_h=6,
)


def get(path):
    try:
        r = requests.get(API + path, timeout=15)
        if r.status_code == 200:
            return r.json()
        print("HTTP", r.status_code, path[:60])
    except Exception as e:
        print("net error:", str(e)[:80])
    return None


def load():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"balance": CFG["start_balance"], "open": [], "recent": {}}


def save(s):
    with open(STATE, "w") as f:
        json.dump(s, f)


def log_trade(row):
    new = not os.path.exists(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["time", "chain", "symbol", "entry", "exit",
                        "pnl_pct", "profit_usd", "reason", "minutes"])
        w.writerow(row)


def candidates():
    seen, out = set(), []
    for path in ("/token-boosts/latest/v1", "/token-boosts/top/v1",
                 "/token-profiles/latest/v1"):
        data = get(path)
        if not isinstance(data, list):
            continue
        for t in data:
            c, a = t.get("chainId"), t.get("tokenAddress")
            if c and a and (c, a) not in seen:
                seen.add((c, a))
                out.append((c, a))
    return out


def fetch_pairs(chain, addrs):
    pairs = []
    for i in range(0, len(addrs), 30):
        chunk = addrs[i:i + 30]
        data = get("/tokens/v1/%s/%s" % (chain, ",".join(chunk)))
        if isinstance(data, list):
            pairs += data
        time.sleep(0.3)
    return pairs


def best_pairs(pairs):
    best = {}
    for p in pairs:
        try:
            k = p["chainId"] + ":" + p["baseToken"]["address"]
            liq = (p.get("liquidity") or {}).get("usd") or 0
            if k not in best or liq > ((best[k].get("liquidity") or {}).get("usd") or 0):
                best[k] = p
        except Exception:
            pass
    return best


def passes(p):
    c = CFG
    liq = (p.get("liquidity") or {}).get("usd") or 0
    vol = p.get("volume") or {}
    tx = p.get("txns") or {}
    pc = p.get("priceChange") or {}
    price = float(p.get("priceUsd") or 0)
    created = p.get("pairCreatedAt")
    if not created or price <= 0:
        return False
    age_h = (time.time() * 1000 - created) / 3.6e6
    m5 = tx.get("m5") or {}
    h1 = tx.get("h1") or {}
    v5, v1 = vol.get("m5") or 0, vol.get("h1") or 0
    ch1 = pc.get("h1") or 0
    return (liq >= c["min_liq"] and v1 >= c["min_vol_h1"]
            and (h1.get("buys", 0) + h1.get("sells", 0)) >= c["min_txns_h1"]
            and age_h >= c["min_age_h"]
            and c["min_h1_change"] <= ch1 <= c["max_h1_change"]
            and v5 * 12 >= c["spike"] * v1
            and m5.get("buys", 0) > m5.get("sells", 0))


def manage_open(s):
    if not s["open"]:
        return
    by_chain = {}
    for pos in s["open"]:
        by_chain.setdefault(pos["chain"], []).append(pos["addr"])
    prices = {}
    for chain, addrs in by_chain.items():
        for k, p in best_pairs(fetch_pairs(chain, addrs)).items():
            prices[k] = float(p.get("priceUsd") or 0)
    still = []
    now = time.time()
    for pos in s["open"]:
        key = pos["chain"] + ":" + pos["addr"]
        price = prices.get(key)
        mins = (now - pos["t"]) / 60
        if not price:
            if mins > CFG["max_minutes"] * 3:
                price = pos["entry"] * 0.0
                reason = "NO_DATA_LOSS"
            else:
                still.append(pos)
                continue
        else:
            raw = (price / pos["entry"] - 1) * 100
            if raw >= CFG["tp"]:
                reason = "TP"
            elif raw <= CFG["sl"]:
                reason = "SL"
            elif mins >= CFG["max_minutes"]:
                reason = "TIME"
            else:
                still.append(pos)
                continue
        raw = (price / pos["entry"] - 1) * 100
        pnl = raw - CFG["cost_pct"]
        profit = CFG["size"] * pnl / 100
        s["balance"] += CFG["size"] + profit
        log_trade([datetime.now().strftime("%m-%d %H:%M"), pos["chain"],
                   pos["sym"], pos["entry"], price, round(pnl, 2),
                   round(profit, 3), reason, round(mins)])
        print("CLOSE %s %s %+.1f%% (%s)" % (pos["chain"], pos["sym"], pnl, reason))
    s["open"] = still


def find_entries(s):
    if len(s["open"]) >= CFG["max_open"]:
        return
    now = time.time()
    s["recent"] = {k: t for k, t in s["recent"].items()
                   if now - t < CFG["cooldown_h"] * 3600}
    by_chain = {}
    for c, a in candidates():
        by_chain.setdefault(c, []).append(a)
    for chain, addrs in by_chain.items():
        for k, p in best_pairs(fetch_pairs(chain, addrs)).items():
            if len(s["open"]) >= CFG["max_open"] or s["balance"] < CFG["size"]:
                return
            if k in s["recent"] or any(
                    o["chain"] + ":" + o["addr"] == k for o in s["open"]):
                continue
            if passes(p):
                s["balance"] -= CFG["size"]
                s["recent"][k] = now
                s["open"].append({
                    "chain": p["chainId"], "addr": p["baseToken"]["address"],
                    "sym": p["baseToken"].get("symbol", "?"),
                    "entry": float(p["priceUsd"]), "t": now})
                print("OPEN  %s %s @ %s | %s" % (
                    p["chainId"], p["baseToken"].get("symbol"), p["priceUsd"],
                    p.get("url", "")))


def stats():
    if not os.path.exists(LOG):
        return "no closed trades yet"
    rows = list(csv.reader(open(LOG)))[1:]
    n = len(rows)
    wins = sum(1 for r in rows if float(r[5]) > 0)
    total = sum(float(r[6]) for r in rows)
    return "trades=%d wins=%d (%.0f%%) total P/L=$%.2f" % (
        n, wins, 100 * wins / max(n, 1), total)


def main():
    s = load()
    manage_open(s)
    find_entries(s)
    save(s)
    print("cash=$%.2f open=%d | %s" % (s["balance"], len(s["open"]), stats()))


main()
