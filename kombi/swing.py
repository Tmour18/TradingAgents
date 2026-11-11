#!/usr/bin/env python3
"""Swing-Ablauf fuer kleine Budgets (Trade Republic, 1 EUR pro Order). Nur Standardbibliothek.

Verbindet die day-trader-skill-Werkzeuge in .claude/skills/ zu einem Ablauf fuer
Trades ueber Tage bis Wochen. Es wird nichts gekauft oder verkauft: das Skript
schreibt Vorschlaege, ein Papier-Journal und Auswertungen.

    python3 kombi/swing.py plan              # taeglicher Lauf vor US-Boersenstart -> trader/berichte/DATUM.md
    python3 kombi/swing.py plan --symbols KO,NVDA   # nur diese Aktien statt des ganzen Markts
    python3 kombi/swing.py journal add KO    # einen Vorschlag aus dem letzten Bericht ins Papier-Journal
    python3 kombi/swing.py journal update    # offene Papier-Positionen gegen die Tageskurse pruefen
    python3 kombi/swing.py journal auswertung  # Rendite nach Gebuehren, Drawdown, Trefferquote vs. MSCI World
    python3 kombi/swing.py backtest          # dieselbe Regel auf 2 Jahren Historie, mit Gebuehren, vs. ETF

Was woher kommt:
  - Kandidaten: trader-scanner (scan.py daily) -- Setups auf Tagesschlusskursen
  - News: trader-news (news.py), Quartalszahlen: trader-analyst (facts.py)
  - Regelpruefung: trader-risk (check.py) mit einer Dollar-Order samt Stop im Plan
  - ERGAENZT (nicht in day-trader-skill, klar gekennzeichnet): die Swing-Regel fuer Stop und
    Kursziel auf Tageskerzen (ATR), die Gebuehrenpruefung in Euro, die Positionsgroesse in Euro,
    die historische Pruefung auf Tageskerzen, das Journal und der Backtest. trader-entry,
    -exit und -timemachine sind nur fuer Intraday-Trades gebaut und werden hier nicht benutzt.

Regel (Datei trader/swing.json):
  Einstieg   naechste Eroeffnung nach dem Signal (im Plan: letzter Schlusskurs als Referenz)
  Stop       Einstieg - stop_atr x ATR(14, Tageskerzen)
  Ziel       Einstieg + target_r x (Einstieg - Stop)
  Zeit-Stop  spaetestens nach max_hold_days Handelstagen raus
  Groesse    so dass Verlust am Stop + 2 Gebuehren <= max_loss_eur, hoechstens max_position_pct des Budgets
  Gebuehren  BLOCK, wenn Gewinn am Ziel nach 2 Gebuehren < min_net_gain_fee_multiple x 2 Gebuehren
             oder Netto-Gewinn/Netto-Verlust < min_net_reward_risk
Bei einer Kerze, die Stop und Ziel beruehrt, zaehlt der Stop (vorsichtig). Oeffnet der Kurs
schon jenseits des Stops, wird zum Eroeffnungskurs verkauft (Kursluecke).
"""

import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")
TRADER = os.path.join(ROOT, "trader")
CFG_PATH = os.path.join(TRADER, "swing.json")
JOURNAL = os.path.join(TRADER, "journal.json")
BERICHTE = os.path.join(TRADER, "berichte")
LETZTER = os.path.join(TRADER, "state", "swing-letzter-plan.json")

DEFAULT_CFG = {
    "budget_eur": 100.0, "fee_eur": 1.0, "max_loss_eur": 5.0, "max_position_pct": 50,
    "min_position_eur": 10.0, "max_open_positions": 2,
    "stop_atr": 2.0, "target_r": 3.0, "max_hold_days": 20,
    "min_net_gain_fee_multiple": 2.0, "min_net_reward_risk": 1.0,
    "setups": ["breakout_20d", "bull_flag", "reclaim_ma20", "oversold_bounce"],
    "min_price_usd": 5.0, "min_volume": 1000000, "max_candidates": 25, "min_history_trades": 3,
    "benchmark": "EUNL.DE", "fx": "EURUSD=X",
}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SCAN = _load("trader_scan", os.path.join(SKILLS, "trader-scanner", "scan.py"))


# ------------------------------------------------------------------ Hilfen

def read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def write_json(path, doc):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def config():
    cfg = dict(DEFAULT_CFG)
    cfg.update(read_json(CFG_PATH, {}) or {})
    return cfg


def eur(x):
    return ("%.2f EUR" % x).replace(".", ",") if x is not None else "-"


def usd(x):
    return ("%.2f $" % x).replace(".", ",") if x is not None else "-"


def pct(x):
    return ("%+.1f %%" % (x * 100)).replace(".", ",") if x is not None else "-"


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None, microsecond=0)


def ny_today(now=None):
    return SCAN.to_eastern(now or now_utc()).date()


# ------------------------------------------------------------------ Daten (Yahoo ueber scan.py)

_CACHE = {}


def daily_bars(sym, rng="2y", now=None):
    """[{d, o, h, l, c}] abgeschlossene Tageskerzen, aelteste zuerst."""
    key = (sym, rng)
    if key in _CACHE:
        return _CACHE[key]
    foreign = sym.endswith(".DE") or sym.endswith("=X")  # Xetra-ETF, Devisen: Ticker unveraendert lassen
    path = ("/v8/finance/chart/%s?range=%s&interval=1d" % (sym, rng)) if foreign else SCAN.chart_path(sym, rng, "1d")
    series = SCAN.parse_chart(SCAN.yahoo_get(path), sym)
    now = now or now_utc()
    today = ny_today(now)
    sess = SCAN.session(today)
    today_done = sess is None or SCAN.to_eastern(now).time() >= sess[1]
    out = {}
    for i, ts in enumerate(series["t"]):
        vals = [series[k][i] if series.get(k) else None for k in ("o", "h", "l", "c")]
        if any(v is None for v in vals):
            continue
        d = SCAN.ts_to_eastern(ts).date()
        if sym.endswith(".DE") or sym.endswith("=X"):
            d = (datetime.datetime(1970, 1, 1) + datetime.timedelta(seconds=int(ts))).date()
        elif d > today or (d == today and not today_done):
            continue
        out[d] = {"d": d, "o": float(vals[0]), "h": float(vals[1]), "l": float(vals[2]), "c": float(vals[3])}
    bars = [out[d] for d in sorted(out)]
    _CACHE[key] = bars
    time.sleep(0.3)  # hoeflich zu den freien Endpunkten
    return bars


class Series(object):
    """Tagesschlusskurse mit 'letzter bekannter Wert bis Datum'."""

    def __init__(self, bars):
        self.dates = [b["d"] for b in bars]
        self.vals = [b["c"] for b in bars]

    def at(self, d):
        lo, hi = 0, len(self.dates) - 1
        if hi < 0 or d < self.dates[0]:
            return self.vals[0] if self.vals else None
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if self.dates[mid] <= d:
                lo = mid
            else:
                hi = mid - 1
        return self.vals[lo]

    def last(self):
        return (self.dates[-1], self.vals[-1]) if self.dates else (None, None)


def atr(bars, i, n=14):
    """ATR(n) bis einschliesslich Kerze i (einfacher Durchschnitt der True Range)."""
    if i < n:
        return None
    trs = []
    for k in range(i - n + 1, i + 1):
        b, p = bars[k], bars[k - 1]
        trs.append(max(b["h"] - b["l"], abs(b["h"] - p["c"]), abs(b["l"] - p["c"])))
    return sum(trs) / n


def levels(cfg, entry, a):
    stop = round(entry - cfg["stop_atr"] * a, 2)
    target = round(entry + cfg["target_r"] * (entry - stop), 2)
    return stop, target


def size(cfg, entry, stop, target, cash_eur):
    """Positionsgroesse in EUR und Gebuehrenpruefung. Kurse in USD, Prozent-Abstaende in EUR gleich."""
    fee2 = 2 * cfg["fee_eur"]
    stop_pct = (entry - stop) / entry
    tgt_pct = (target - entry) / entry
    reasons = []
    if stop_pct <= 0:
        return None, ["Stop liegt nicht unter dem Einstieg"]
    by_loss = (cfg["max_loss_eur"] - fee2) / stop_pct
    by_cap = cfg["budget_eur"] * cfg["max_position_pct"] / 100.0
    pos = max(0.0, min(by_loss, by_cap, cash_eur))
    pos = float(int(pos))  # ganze Euro
    decided = "Max-Verlust" if pos == int(by_loss) else ("Positionsgrenze" if pos == int(by_cap) else "Bargeld")
    loss = pos * stop_pct + fee2
    gain = pos * tgt_pct - fee2
    if pos < cfg["min_position_eur"]:
        reasons.append("Betrag %s unter dem Minimum %s (Stop zu weit oder zu wenig Bargeld)"
                       % (eur(pos), eur(cfg["min_position_eur"])))
    if gain < cfg["min_net_gain_fee_multiple"] * fee2:
        reasons.append("Gebuehren: Gewinn am Ziel nach Gebuehren %s ist nicht mindestens %gx die Gebuehren (%s)"
                       % (eur(gain), cfg["min_net_gain_fee_multiple"], eur(fee2)))
    if loss > 0 and gain / loss < cfg["min_net_reward_risk"]:
        reasons.append("Gebuehren: Chance/Risiko nach Gebuehren %.2f unter %.2f" % (gain / loss if loss else 0,
                                                                              cfg["min_net_reward_risk"]))
    return {"betrag_eur": pos, "begrenzt_durch": decided, "stop_pct": stop_pct, "ziel_pct": tgt_pct,
            "verlust_am_stop_eur": round(loss, 2), "gewinn_am_ziel_eur": round(gain, 2),
            "gebuehren_eur": fee2}, reasons


def simulate_exit(bars, start, entry, stop, target, max_hold):
    """Ab Kerze start (Einstiegstag, Einstieg zur Eroeffnung). -> (index, kurs, grund) oder None (offen)."""
    for j in range(start, len(bars)):
        b = bars[j]
        if j > start and b["o"] <= stop:
            return j, b["o"], "Stop (Kursluecke)"
        if b["l"] <= stop:
            return j, stop, "Stop"
        if j > start and b["o"] >= target:
            return j, b["o"], "Ziel (Kursluecke)"
        if b["h"] >= target:
            return j, target, "Ziel"
        if j - start + 1 >= max_hold:
            return j, b["c"], "Zeit-Stop"
    return None


def pnl_eur(betrag, entry, exit_px, fx_in, fx_out, fee_eur):
    shares = betrag * fx_in / entry
    return shares * exit_px / fx_out - betrag - 2 * fee_eur


def history_check(cfg, bars, setup, fx):
    """Dieselbe Regel auf allen frueheren Signalen derselben Art (Tageskerzen, ohne Ueberlappung)."""
    closes = [b["c"] for b in bars]
    trades, i = [], 40
    while i < len(bars) - 2:
        tags, _ = SCAN.find_setups(closes[:i + 1])
        a = atr(bars, i)
        if setup in tags and a:
            e_i = i + 1
            entry = bars[e_i]["o"]
            stop, target = levels(cfg, entry, a)
            plan, why = size(cfg, entry, stop, target, cfg["budget_eur"])
            ex = simulate_exit(bars, e_i, entry, stop, target, cfg["max_hold_days"])
            if ex is None:
                break
            j, px, grund = ex
            if plan["betrag_eur"] >= cfg["min_position_eur"]:
                net = pnl_eur(plan["betrag_eur"], entry, px, fx.at(bars[e_i]["d"]), fx.at(bars[j]["d"]),
                              cfg["fee_eur"])
                trades.append({"d": bars[e_i]["d"].isoformat(), "grund": grund, "netto_eur": round(net, 2),
                               "tage": j - e_i + 1, "gebuehren_ok": not why})
            i = j + 1
            continue
        i += 1
    n = len(trades)
    if not n:
        return {"n": 0}
    wins = [t for t in trades if t["netto_eur"] > 0]
    return {"n": n, "gewinner": len(wins), "trefferquote": len(wins) / n,
            "ziel": sum(1 for t in trades if t["grund"].startswith("Ziel")),
            "stop": sum(1 for t in trades if t["grund"].startswith("Stop")),
            "zeit": sum(1 for t in trades if t["grund"] == "Zeit-Stop"),
            "summe_eur": round(sum(t["netto_eur"] for t in trades), 2),
            "schnitt_eur": round(sum(t["netto_eur"] for t in trades) / n, 2),
            "von": bars[0]["d"].isoformat(), "bis": bars[-1]["d"].isoformat()}


# ------------------------------------------------------------------ Repo-B-Werkzeuge aufrufen

def run_tool(args, timeout=900, env_extra=None):
    env = dict(os.environ)
    env.update(env_extra or {})
    p = subprocess.run([sys.executable] + args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env)
    return p.returncode, p.stdout, p.stderr


def risk_check(cand, fx_now):
    """trader-risk check.py auf eine Dollar-Order (notional) mit Stop und Ziel im Plan."""
    order = {"symbol": cand["symbol"], "side": "buy", "notional": "%.2f" % (cand["betrag_eur"] * fx_now),
             "type": "market", "time_in_force": "day",
             "plan": {"entry": cand["einstieg"], "stop": cand["stop"], "target": cand["ziel"], "setup": cand["setup"]}}
    path = os.path.join(TRADER, "state", "swing-orders.json")
    write_json(path, [order])
    code, out, err = run_tool([os.path.join(SKILLS, "trader-risk", "check.py"), "--orders", path,
                               "--portfolio", os.path.join(TRADER, "state", "swing-portfolio.json"),
                               "--prices", os.path.join(TRADER, "state", "keine-preise.json"),
                               "--no-write", "--json"])
    try:
        doc = json.loads(out)
        res = (doc.get("results") or [{}])[0]
        return res.get("verdict", "PASS" if code == 0 else "BLOCK"), res.get("reasons") or [], res.get("warnings") or []
    except ValueError:
        return "FEHLER", [(err or out).strip()[:300]], []


# ------------------------------------------------------------------ Journal

def journal():
    return read_json(JOURNAL, {"start": None, "budget_eur": DEFAULT_CFG["budget_eur"], "trades": []})


def open_trades(j):
    return [t for t in j["trades"] if t["status"] in ("geplant", "offen")]


def cash_left(cfg, j):
    used = sum(t["plan"]["betrag_eur"] for t in open_trades(j))
    realized = sum(t.get("ergebnis_eur") or 0 for t in j["trades"] if t["status"] == "geschlossen")
    return cfg["budget_eur"] + realized - used


def journal_update(cfg, j, fx, now=None):
    msgs = []
    for t in open_trades(j):
        try:
            bars = daily_bars(t["symbol"], "6mo", now)
        except Exception as e:  # Daten fehlen: offen lassen und sagen
            msgs.append("%s: keine Kursdaten (%s), Status unveraendert" % (t["symbol"], e))
            continue
        rec = datetime.date.fromisoformat(t["empfohlen_am"])
        if t["status"] == "geplant":
            idx = next((k for k, b in enumerate(bars) if b["d"] > rec), None)
            if idx is None:
                continue
            b = bars[idx]
            t["einstieg"] = {"datum": b["d"].isoformat(), "kurs_usd": round(b["o"], 2),
                             "fx": round(fx.at(b["d"]), 4)}
            t["status"] = "offen"
            msgs.append("%s: Papier-Kauf zur Eroeffnung am %s zu %s" % (t["symbol"], b["d"], usd(b["o"])))
        start = next(k for k, b in enumerate(bars) if b["d"].isoformat() == t["einstieg"]["datum"])
        entry = t["einstieg"]["kurs_usd"]
        ex = simulate_exit(bars, start, entry, t["plan"]["stop"], t["plan"]["ziel"], cfg["max_hold_days"])
        if ex:
            k, px, grund = ex
            fx_out = fx.at(bars[k]["d"])
            t["ausstieg"] = {"datum": bars[k]["d"].isoformat(), "kurs_usd": round(px, 2), "fx": round(fx_out, 4),
                             "grund": grund}
            t["ergebnis_eur"] = round(pnl_eur(t["plan"]["betrag_eur"], entry, px, t["einstieg"]["fx"], fx_out,
                                              cfg["fee_eur"]), 2)
            t["status"] = "geschlossen"
            msgs.append("%s: Papier-Verkauf am %s zu %s (%s), Ergebnis nach Gebuehren %s"
                        % (t["symbol"], bars[k]["d"], usd(px), grund, eur(t["ergebnis_eur"])))
        else:
            last = bars[-1]
            t["zuletzt"] = {"datum": last["d"].isoformat(), "kurs_usd": round(last["c"], 2),
                            "tage": len(bars) - start}
            t["wert_eur_jetzt"] = round(t["plan"]["betrag_eur"] * t["einstieg"]["fx"] / entry * last["c"]
                                        / fx.at(last["d"]), 2)
    return msgs


def equity_curve(cfg, j, fx, now=None):
    """Taeglicher Depotwert in EUR (Bargeld + offene Positionen zum Schlusskurs) seit Journal-Start."""
    if not j.get("start"):
        return []
    start = datetime.date.fromisoformat(j["start"])
    bench = Series(daily_bars(cfg["benchmark"], "1y", now))
    days = [d for d in bench.dates if d >= start]
    px = {}
    for t in j["trades"]:
        if t.get("einstieg"):
            px[t["symbol"]] = Series(daily_bars(t["symbol"], "1y", now))
    curve = []
    for d in days:
        val = cfg["budget_eur"]
        for t in j["trades"]:
            if not t.get("einstieg"):
                continue
            ein = datetime.date.fromisoformat(t["einstieg"]["datum"])
            aus = datetime.date.fromisoformat(t["ausstieg"]["datum"]) if t.get("ausstieg") else None
            if d < ein:
                continue
            if aus and d >= aus:
                val += t["ergebnis_eur"]
            else:
                betrag = t["plan"]["betrag_eur"]
                shares = betrag * t["einstieg"]["fx"] / t["einstieg"]["kurs_usd"]
                val += shares * px[t["symbol"]].at(d) / fx.at(d) - betrag - cfg["fee_eur"]
        curve.append((d, val))
    return curve


def max_drawdown(values):
    peak, dd = None, 0.0
    for v in values:
        peak = v if peak is None or v > peak else peak
        if peak:
            dd = min(dd, v / peak - 1)
    return dd


# ------------------------------------------------------------------ Befehle

def cmd_plan(a):
    cfg = config()
    now = now_utc()
    today = ny_today(now)
    ok, why = SCAN.trading_day(today)
    lines = ["# Swing-Bericht %s" % today.isoformat(), ""]
    if not ok:
        lines.append("US-Boerse heute geschlossen (%s). Keine neuen Vorschlaege." % why)
    fx = Series(daily_bars(cfg["fx"], "2y", now))
    fx_d, fx_now = fx.last()
    lines.append("Wechselkurs: 1 EUR = %s USD (Yahoo %s, Schlusskurs). Alle Kurse in USD sind verzoegerte "
                 "Gratis-Daten von Yahoo: **vor jeder Order den Kurs in der Trade-Republic-App pruefen.**"
                 % (("%.4f" % fx_now).replace(".", ","), fx_d))
    lines.append("")

    j = journal()
    msgs = journal_update(cfg, j, fx, now)
    write_json(JOURNAL, j)
    lines.append("## Papier-Journal")
    lines += ["- " + m for m in msgs] or ["- keine Aenderungen"]
    for t in open_trades(j):
        z = t.get("zuletzt") or {}
        lines.append("- offen: %s, %s, Stop %s, Ziel %s, zuletzt %s (%s), Wert jetzt ca. %s, Zeit-Stop nach %d Handelstagen"
                     % (t["symbol"], eur(t["plan"]["betrag_eur"]), usd(t["plan"]["stop"]), usd(t["plan"]["ziel"]),
                        usd(z.get("kurs_usd")), z.get("datum", "-"), eur(t.get("wert_eur_jetzt")), cfg["max_hold_days"]))
    cash = cash_left(cfg, j)
    lines.append("- freies Papier-Bargeld: %s, offene Positionen: %d von %d"
                 % (eur(cash), len(open_trades(j)), cfg["max_open_positions"]))
    lines.append("")

    cands, blocked = [], []
    if ok:
        scan_args = [os.path.join(SKILLS, "trader-scanner", "scan.py"), "daily", "--quiet",
                     "--min-price", str(cfg["min_price_usd"]), "--min-volume", str(cfg["min_volume"])]
        if a.symbols:
            scan_args += ["--symbols", a.symbols]
        elif not os.path.exists(os.path.join(TRADER, "universe.txt")) or a.universe:
            run_tool([os.path.join(SKILLS, "trader-scanner", "scan.py"), "universe"])
        code, out, err = run_tool(scan_args, timeout=1200)
        wl = read_json(os.path.join(TRADER, "watchlist.json"), {}) or {}
        head = [l for l in out.splitlines() if l.strip()][:3]
        lines.append("## Scanner (trader-scanner, Tagesschlusskurse)")
        lines += ["    " + l for l in head] or ["    (keine Ausgabe: %s)" % err.strip()[:200]]
        hits = [h for h in wl.get("hits", []) if set(h["setups"]) & set(cfg["setups"])]
        lines.append("Treffer mit Swing-Setups (%s): %d, gepruefte: %d (die meistgehandelten)."
                     % (", ".join(cfg["setups"]), len(hits), min(len(hits), cfg["max_candidates"])))
        lines.append("")
        names = {}
        try:
            with open(os.path.join(TRADER, "universe.txt"), encoding="utf-8") as fh:
                for l in fh:
                    s, _, n = l.strip().partition("|")
                    names[s] = n
        except OSError:
            pass
        slots = cfg["max_open_positions"] - len(open_trades(j))
        for h in hits[:cfg["max_candidates"]]:
            sym = h["symbol"]
            setup = next(s for s in cfg["setups"] if s in h["setups"])
            try:
                bars = daily_bars(sym, "2y", now)
            except Exception as e:
                blocked.append({"symbol": sym, "setup": setup, "gruende": ["keine Tagesdaten: %s" % e]})
                continue
            i = len(bars) - 1
            a14 = atr(bars, i)
            if not a14:
                blocked.append({"symbol": sym, "setup": setup, "gruende": ["zu wenig Historie"]})
                continue
            entry = bars[i]["c"]
            stop, target = levels(cfg, entry, a14)
            # Ist das Positionslimit voll, mit vollem Budget rechnen: sonst meldet die Gebuehrenpruefung
            # nur, dass das Restgeld zu klein ist. Abgelehnt wird dann ueber das Limit.
            plan, reasons = size(cfg, entry, stop, target, cash if slots > 0 else cfg["budget_eur"])
            hist = history_check(cfg, bars, setup, fx)
            c = {"symbol": sym, "name": names.get(sym, ""), "setup": setup, "alle_setups": h["setups"],
                 "datum": bars[i]["d"].isoformat(), "einstieg": round(entry, 2), "stop": stop, "ziel": target,
                 "atr": round(a14, 2), "historie": hist, "rsi": h.get("rsi")}
            c.update(plan or {})
            if hist.get("n", 0) >= cfg["min_history_trades"] and hist["schnitt_eur"] <= 0:
                reasons.append("Historie: dieselbe Regel brachte bei %s im Schnitt %s pro Trade (%d Trades)"
                               % (sym, eur(hist["schnitt_eur"]), hist["n"]))
            if not reasons and slots <= 0:
                reasons.append("schon %d offene Positionen (Limit %d)" % (len(open_trades(j)), cfg["max_open_positions"]))
            if any(t["symbol"] == sym for t in open_trades(j)):
                reasons.append("schon im Journal offen")
            if not reasons:
                verdict, rr, ww = risk_check(c, fx_now)
                c["risk"] = verdict
                c["risk_warnungen"] = ww
                if verdict != "PASS":
                    reasons += ["trader-risk: " + r for r in rr]
            if reasons:
                c["gruende"] = reasons
                blocked.append(c)
            else:
                cands.append(c)
        cands.sort(key=lambda c: -(c["historie"].get("schnitt_eur") or 0))

        # News und Quartalszahlen fuer die besten Kandidaten (trader-news, trader-analyst)
        top = cands[:5]
        if top:
            tick = [c["symbol"] for c in top]
            code, out, err = run_tool([os.path.join(SKILLS, "trader-news", "news.py"), "--tickers"] + tick +
                                      ["--hours", "72", "--json", "--dry-run", "--all"], timeout=300)
            try:
                news = json.loads(out)
            except ValueError:
                news = {}
            for c in top:
                items = [it for it in (news.get("items") or [])
                         if c["symbol"] in (it.get("touches") or [it.get("ticker")])]
                c["news"] = [{"zeit": (it.get("time") or "")[:16].replace("T", " "), "titel": it.get("title"),
                              "tags": it.get("tags"), "link": it.get("link")} for it in items[:5]]
                code, out, err = run_tool([os.path.join(SKILLS, "trader-analyst", "facts.py"), c["symbol"],
                                           "--quarters", "4", "--json"], timeout=180)
                try:
                    comp = json.loads(out)[0]
                    fil = comp.get("filings") or {}
                    c["quartal"] = {"aenderungen": comp.get("changes"), "hinweis": comp.get("note"),
                                    "earnings_8k": (fil.get("earnings_8k") or {}).get("exhibit"),
                                    "bericht": (fil.get("report") or {}).get("url")}
                    filed = (fil.get("earnings_8k") or {}).get("filed")
                    if filed:
                        nxt = datetime.date.fromisoformat(filed[:10]) + datetime.timedelta(days=91)
                        c["naechste_zahlen_schaetzung"] = nxt.isoformat()
                        if nxt <= today + datetime.timedelta(days=int(cfg["max_hold_days"] * 1.45)):
                            c.setdefault("warnungen", []).append(
                                "Naechste Quartalszahlen voraussichtlich um %s (Schaetzung: letzte am %s + 91 Tage) "
                                "-- liegt in der Haltedauer, Kursluecke moeglich" % (nxt, filed[:10]))
                except (ValueError, IndexError, AttributeError, KeyError, TypeError):
                    c["quartal"] = {"fehler": (err or out).strip()[:200] or "keine SEC-Daten (z. B. ausl. Firma)"}

    # Bericht
    lines.append("## Kandidaten, die alle Regeln bestehen")
    if not cands:
        lines.append("Keine. Heute nichts kaufen.")
    for n, c in enumerate(cands, 1):
        h = c["historie"]
        lines += ["", "### %d. %s (%s) -- %s" % (n, c["symbol"], c["name"] or "Name unbekannt", c["setup"]),
                  "- Betrag: **%s** (begrenzt durch %s)" % (eur(c["betrag_eur"]), c["begrenzt_durch"]),
                  "- Referenz-Einstieg (Schlusskurs %s): %s = ca. %s" % (c["datum"], usd(c["einstieg"]),
                                                                       eur(c["einstieg"] / fx_now)),
                  "- Stop-Loss: %s = ca. %s (%s)  |  Ziel: %s = ca. %s (%s)" % (
                      usd(c["stop"]), eur(c["stop"] / fx_now), pct(-c["stop_pct"]),
                      usd(c["ziel"]), eur(c["ziel"] / fx_now), pct(c["ziel_pct"])),
                  "- Spaetestens raus: nach %d Handelstagen (Zeit-Stop)" % config()["max_hold_days"],
                  "- Nach Gebuehren: Gewinn am Ziel %s, Verlust am Stop %s (Gebuehren %s)" % (
                      eur(c["gewinn_am_ziel_eur"]), eur(-c["verlust_am_stop_eur"]), eur(c["gebuehren_eur"])),
                  "- Historie (gleiche Regel, %s bis %s): %d Trades, %d Gewinner (%s), Ziel %d / Stop %d / Zeit %d, "
                  "im Schnitt %s, Summe %s" % (h.get("von"), h.get("bis"), h.get("n", 0), h.get("gewinner", 0),
                                               pct(h.get("trefferquote")).replace("+", ""), h.get("ziel", 0),
                                               h.get("stop", 0), h.get("zeit", 0), eur(h.get("schnitt_eur")),
                                               eur(h.get("summe_eur"))) if h.get("n") else
                  "- Historie: keine frueheren Signale dieser Art in 2 Jahren"]
        for w in c.get("warnungen", []):
            lines.append("- WARNUNG: " + w)
        q = c.get("quartal") or {}
        for ch in (q.get("aenderungen") or [])[:3]:
            lines.append("- Quartal (SEC): %s" % ch)
        if q.get("earnings_8k"):
            lines.append("- Letzte Quartalsmeldung: %s" % q["earnings_8k"])
        for nw in c.get("news", []):
            lines.append("- News %s: %s %s" % (nw["zeit"], nw["titel"], nw["link"] or ""))
    lines += ["", "## Abgelehnt (Auszug)"]
    for b in blocked[:12]:
        lines.append("- %s (%s): %s" % (b["symbol"], b.get("setup"), "; ".join(b["gruende"])))
    lines += ["", "Ergaenzte Swing-Regel: Stop = Einstieg - %g x ATR(14), Ziel = %g R, Zeit-Stop %d Handelstage. "
              "Historie und Backtest sind Rueckblicke, keine Vorhersagen." % (cfg["stop_atr"], cfg["target_r"],
                                                                               cfg["max_hold_days"])]
    text = "\n".join(lines) + "\n"
    os.makedirs(BERICHTE, exist_ok=True)
    path = os.path.join(BERICHTE, today.isoformat() + ".md")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    write_json(LETZTER, {"datum": today.isoformat(), "fx": fx_now, "kandidaten": cands, "abgelehnt": blocked},)
    print(text)
    print("Bericht: %s" % os.path.relpath(path, ROOT))
    return 0


def cmd_journal(a):
    cfg = config()
    j = journal()
    fx = Series(daily_bars(cfg["fx"], "2y"))
    if a.aktion == "add":
        last = read_json(LETZTER, {}) or {}
        c = next((c for c in last.get("kandidaten", []) if c["symbol"] == a.symbol.upper()), None)
        if not c:
            print("%s ist nicht unter den Kandidaten des letzten Berichts (%s)." % (a.symbol, last.get("datum")))
            return 1
        if len(open_trades(j)) >= cfg["max_open_positions"]:
            print("Schon %d offene Positionen." % len(open_trades(j)))
            return 1
        j["start"] = j.get("start") or last["datum"]
        j["trades"].append({"id": "%s-%s" % (last["datum"], c["symbol"]), "symbol": c["symbol"], "name": c["name"],
                            "setup": c["setup"], "empfohlen_am": last["datum"], "status": "geplant",
                            "plan": {"einstieg_ref": c["einstieg"], "stop": c["stop"], "ziel": c["ziel"],
                                     "betrag_eur": c["betrag_eur"], "fx": round(last["fx"], 4)},
                            "notiz": a.notiz or ""})
        write_json(JOURNAL, j)
        print("Ins Papier-Journal: %s, %s, Stop %s, Ziel %s. Papier-Kauf zur naechsten Eroeffnung."
              % (c["symbol"], eur(c["betrag_eur"]), usd(c["stop"]), usd(c["ziel"])))
        return 0
    if a.aktion == "update":
        for m in journal_update(cfg, j, fx) or ["keine Aenderungen"]:
            print(m)
        write_json(JOURNAL, j)
        return 0
    # auswertung
    journal_update(cfg, j, fx)
    write_json(JOURNAL, j)
    closed = [t for t in j["trades"] if t["status"] == "geschlossen"]
    curve = equity_curve(cfg, j, fx)
    print("Papier-Journal seit %s: %d Trades abgeschlossen, %d offen." % (j.get("start"), len(closed),
                                                                          len(open_trades(j))))
    if closed:
        wins = [t for t in closed if t["ergebnis_eur"] > 0]
        print("Trefferquote: %d von %d (%.0f %%), Summe nach Gebuehren: %s, Gebuehren gesamt: %s"
              % (len(wins), len(closed), 100.0 * len(wins) / len(closed), eur(sum(t["ergebnis_eur"] for t in closed)),
                 eur(2 * cfg["fee_eur"] * len(closed))))
    if curve:
        v0, v1 = cfg["budget_eur"], curve[-1][1]
        bench = Series(daily_bars(cfg["benchmark"], "1y"))
        b0 = bench.at(datetime.date.fromisoformat(j["start"]))
        b1 = bench.last()[1]
        bvals = [cfg["budget_eur"] * bench.at(d) / b0 for d, _ in curve]
        print("Depotwert: %s (%s), max. Drawdown %s" % (eur(v1), pct(v1 / v0 - 1), pct(max_drawdown([v for _, v in curve]))))
        print("MSCI World (%s, kaufen und halten, 100 EUR, ohne Gebuehr): %s (%s), max. Drawdown %s"
              % (cfg["benchmark"], eur(cfg["budget_eur"] * b1 / b0), pct(b1 / b0 - 1), pct(max_drawdown(bvals))))
        print("=> Das System %s den ETF bisher." % ("SCHLAEGT" if v1 > cfg["budget_eur"] * b1 / b0 else "schlaegt NICHT"))
    return 0


DEFAULT_BACKTEST = ("AAPL MSFT NVDA AMZN GOOGL META TSLA AVGO JPM V MA UNH XOM JNJ PG HD COST ABBV KO PEP "
                    "MRK WMT BAC CVX ADBE CRM NFLX AMD INTC CSCO ORCL QCOM TXN IBM CAT GE BA DIS NKE MCD "
                    "PFE T VZ F GM UBER PYPL SHOP PLTR SOFI HOOD RKLB COIN SNOW CRWD NET DDOG ROKU AFRM "
                    "DKNG RIVN U ENPH").split()


def cmd_backtest(a):
    """Portfolio-Backtest: ein Budget, max_open_positions gleichzeitig, Signale wie im Live-Lauf."""
    cfg = config()
    for kv in a.set or []:
        k, _, v = kv.partition("=")
        cfg[k] = type(DEFAULT_CFG.get(k, 0.0))(v) if k in DEFAULT_CFG else float(v)
    syms = a.symbols.split(",") if a.symbols else DEFAULT_BACKTEST
    fx = Series(daily_bars(cfg["fx"], "5y"))
    data, failed = {}, []
    for s in syms:
        try:
            data[s] = daily_bars(s, a.range)
        except Exception as e:
            failed.append("%s (%s)" % (s, e))
    print("Daten: %d von %d Aktien geladen%s." % (len(data), len(syms), (", fehlen: " + ", ".join(failed)) if failed else ""))
    idx = {s: {b["d"]: k for k, b in enumerate(bars)} for s, bars in data.items()}
    days = sorted({b["d"] for bars in data.values() for b in bars})
    days = days[60:]  # Vorlauf fuer ATR/RSI/20-Tage-Werte
    cash, open_pos, trades, curve = cfg["budget_eur"], [], [], []
    closes = {s: [b["c"] for b in bars] for s, bars in data.items()}
    for d in days:
        # 1. Ausstiege offener Positionen an diesem Tag
        for p in list(open_pos):
            k = idx[p["s"]].get(d)
            if k is None or k < p["k0"]:
                continue
            bars = data[p["s"]]
            ex = simulate_exit(bars[:k + 1], p["k0"], p["entry"], p["stop"], p["target"], cfg["max_hold_days"])
            if ex and ex[0] == k:
                net = pnl_eur(p["betrag"], p["entry"], ex[1], p["fx"], fx.at(d), cfg["fee_eur"])
                cash += p["betrag"] + net
                trades.append({"s": p["s"], "ein": p["d0"], "aus": d, "grund": ex[2], "netto": net})
                open_pos.remove(p)
        # 2. Wert am Tagesende
        val = cash
        for p in open_pos:
            k = idx[p["s"]].get(d)
            c = data[p["s"]][k]["c"] if k is not None else p["last"]
            p["last"] = c
            val += p["betrag"] * p["fx"] / p["entry"] * c / fx.at(d) - cfg["fee_eur"]
        curve.append((d, val))
        # 3. Signale am Schluss -> Kauf zur naechsten Eroeffnung
        if len(open_pos) >= cfg["max_open_positions"]:
            continue
        sigs = []
        for s, bars in data.items():
            k = idx[s].get(d)
            if k is None or k < 40 or k + 1 >= len(bars) or any(p["s"] == s for p in open_pos):
                continue
            if bars[k]["c"] < cfg["min_price_usd"]:
                continue
            tags, _ = SCAN.find_setups(closes[s][:k + 1])
            if set(tags) & set(cfg["setups"]):
                sigs.append((s, k))
        for s, k in sigs:
            if len(open_pos) >= cfg["max_open_positions"]:
                break
            bars = data[s]
            a14 = atr(bars, k)
            if not a14:
                continue
            entry = bars[k + 1]["o"]
            stop, target = levels(cfg, entry, a14)
            plan, why = size(cfg, entry, stop, target, cash)
            if why or plan["betrag_eur"] < cfg["min_position_eur"]:
                continue
            cash -= plan["betrag_eur"]
            open_pos.append({"s": s, "k0": k + 1, "d0": bars[k + 1]["d"], "entry": entry, "stop": stop,
                             "target": target, "betrag": plan["betrag_eur"], "fx": fx.at(bars[k + 1]["d"]),
                             "last": entry})
    v0, v1 = cfg["budget_eur"], curve[-1][1]
    wins = [t for t in trades if t["netto"] > 0]
    print("Zeitraum %s bis %s, Regel: Setups %s, Stop %g x ATR, Ziel %g R, Zeit-Stop %d Tage, max %d Positionen."
          % (curve[0][0], curve[-1][0], ",".join(cfg["setups"]), cfg["stop_atr"], cfg["target_r"],
             cfg["max_hold_days"], cfg["max_open_positions"]))
    print("Trades: %d (%d noch offen), Trefferquote %.0f %%, Ziel %d / Stop %d / Zeit %d"
          % (len(trades), len(open_pos), 100.0 * len(wins) / len(trades) if trades else 0,
             sum(1 for t in trades if t["grund"].startswith("Ziel")), sum(1 for t in trades if t["grund"].startswith("Stop")),
             sum(1 for t in trades if t["grund"] == "Zeit-Stop")))
    print("System:  100 EUR -> %s (%s nach Gebuehren, Gebuehren gesamt %s), max. Drawdown %s"
          % (eur(v1), pct(v1 / v0 - 1), eur(2 * cfg["fee_eur"] * len(trades)), pct(max_drawdown([v for _, v in curve]))))
    bench = Series(daily_bars(cfg["benchmark"], a.range))
    b0, b1 = bench.at(curve[0][0]), bench.at(curve[-1][0])
    bvals = [v0 * bench.at(d) / b0 for d, _ in curve]
    print("ETF:     100 EUR in %s gekauft und gehalten -> %s (%s, 1 EUR Kaufgebuehr bzw. 0 EUR im Sparplan), "
          "max. Drawdown %s" % (cfg["benchmark"], eur(v0 * b1 / b0), pct(b1 / b0 - 1), pct(max_drawdown(bvals))))
    print("=> Das System %s den MSCI-World-ETF in diesem Zeitraum."
          % ("SCHLAEGT" if v1 > v0 * b1 / b0 else "schlaegt NICHT"))
    print("Vorsicht: feste Aktienliste von heute (Ueberlebende), Tageskerzen statt echter Ausfuehrung, "
          "Spreads bei Trade Republic nicht eingerechnet. Ein Rueckblick, keine Vorhersage.")
    if a.json:
        print(json.dumps({"trades": [{**t, "ein": str(t["ein"]), "aus": str(t["aus"])} for t in trades]}, default=str))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("plan")
    p.add_argument("--symbols", help="nur diese Ticker (Komma-getrennt) statt des ganzen Markts")
    p.add_argument("--universe", action="store_true", help="Aktienliste von Nasdaq Trader neu laden")
    p = sub.add_parser("journal")
    p.add_argument("aktion", choices=["add", "update", "auswertung"])
    p.add_argument("symbol", nargs="?")
    p.add_argument("--notiz")
    p = sub.add_parser("backtest")
    p.add_argument("--symbols")
    p.add_argument("--range", default="2y")
    p.add_argument("--set", action="append", help="Regel-Parameter ueberschreiben, z. B. --set target_r=2")
    p.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    os.chdir(ROOT)
    return {"plan": cmd_plan, "journal": cmd_journal, "backtest": cmd_backtest}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
