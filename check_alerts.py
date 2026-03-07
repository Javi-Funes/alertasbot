"""
╔══════════════════════════════════════════════════════╗
║   CHEQUEADOR DE ALERTAS — GitHub Actions             ║
║   Se ejecuta cada 5 minutos automáticamente          ║
║   Datos: data912.com                                 ║
╚══════════════════════════════════════════════════════╝

Este script NO corre de forma continua.
GitHub Actions lo ejecuta cada 5 minutos, chequea
los precios, manda alertas si corresponde, y termina.

Las alertas se guardan en alerts.json en el repositorio.
"""

import json
import os
import requests
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════
#  ⚙️  CONFIGURACIÓN — se leen desde GitHub Secrets
# ══════════════════════════════════════════════════════

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
CHAT_ID   = os.environ.get("CHAT_ID", "")

ALERTS_FILE = Path("alerts.json")

# ══════════════════════════════════════════════════════
#  🌐  DATA912.COM
# ══════════════════════════════════════════════════════

ENDPOINTS = {
    "arg":  "https://data912.com/live/arg_stocks",
    "usa":  "https://data912.com/live/usa_stocks",
    "adrs": "https://data912.com/live/usa_adrs",
}

def fetch_market(market: str) -> dict:
    try:
        resp = requests.get(ENDPOINTS[market], timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return {item["symbol"].upper(): item for item in data if "symbol" in item}
    except Exception as e:
        print(f"⚠️  Error fetching {market}: {e}")
        return {}


def get_price(ticker: str, all_data: dict) -> dict | None:
    ticker = ticker.upper().strip()
    for market in ("arg", "adrs", "usa"):
        if ticker in all_data.get(market, {}):
            item = all_data[market][ticker]
            price = item.get("c") or item.get("px_bid") or 0
            return {
                "ticker":     ticker,
                "price":      float(price),
                "pct_change": float(item.get("pct_change") or 0),
                "bid":        float(item.get("px_bid") or 0),
                "ask":        float(item.get("px_ask") or 0),
                "market":     market,
                "currency":   "ARS" if market == "arg" else "USD",
            }
    return None


def fmt_price(price: float, currency: str) -> str:
    if currency == "ARS":
        return f"${price:,.2f}".replace(",","X").replace(".",",").replace("X",".")
    return f"${price:,.2f}"


def market_label(market: str) -> str:
    return {"arg": "🇦🇷 BYMA", "usa": "🇺🇸 NYSE/NASDAQ", "adrs": "🔵 ADR"}.get(market, market)


# ══════════════════════════════════════════════════════
#  💬  TELEGRAM
# ══════════════════════════════════════════════════════

def send_telegram(message: str) -> bool:
    try:
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id":    CHAT_ID,
            "text":       message,
            "parse_mode": "HTML"
        }, timeout=10)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"❌ Error enviando Telegram: {e}")
        return False


# ══════════════════════════════════════════════════════
#  💾  ALERTAS EN alerts.json
# ══════════════════════════════════════════════════════

def load_alerts() -> list:
    if ALERTS_FILE.exists():
        try:
            return json.loads(ALERTS_FILE.read_text(encoding="utf-8"))
        except:
            return []
    return []


def save_alerts(alerts: list):
    ALERTS_FILE.write_text(
        json.dumps(alerts, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# ══════════════════════════════════════════════════════
#  🚀  MAIN — chequea precios y dispara alertas
# ══════════════════════════════════════════════════════

def main():
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN o CHAT_ID no configurados en GitHub Secrets")
        return

    print(f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} — Chequeando alertas...")

    alerts = load_alerts()
    pendientes = [a for a in alerts if not a.get("triggered", False)]

    if not pendientes:
        print("✅ No hay alertas pendientes.")
        return

    print(f"📋 {len(pendientes)} alerta(s) activa(s) — descargando precios...")

    # Descargar los 3 mercados de una vez
    all_data = {
        "arg":  fetch_market("arg"),
        "usa":  fetch_market("usa"),
        "adrs": fetch_market("adrs"),
    }

    fired_count = 0
    for alert in alerts:
        if alert.get("triggered"):
            continue

        data = get_price(alert["ticker"], all_data)
        if not data or data["price"] == 0:
            print(f"⚠️  No se encontró precio para {alert['ticker']}")
            continue

        precio = data["price"]
        cond   = alert["condition"]
        target = float(alert["target"])
        cur    = data["currency"]

        print(f"   {alert['ticker']}: {fmt_price(precio, cur)} (objetivo: {cond} {fmt_price(target, cur)})")

        fired = (cond == "sube" and precio >= target) or \
                (cond == "baja" and precio <= target)

        if fired:
            alert["triggered"]    = True
            alert["triggered_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            alert["fired_price"]  = precio

            pct  = data["pct_change"]
            sign = "+" if pct >= 0 else ""
            emoji = "🚀" if cond == "sube" else "🔻"

            msg = (
                f"🚨 <b>ALERTA DISPARADA!</b>\n\n"
                f"{emoji} <b>{alert['ticker']}</b> {cond} al objetivo\n\n"
                f"💰 Precio actual: <b>{fmt_price(precio, cur)}</b>\n"
                f"🎯 Objetivo:      {fmt_price(target, cur)}\n"
                f"📊 Cambio hoy:    {sign}{pct:.2f}%\n"
                f"🏛 Mercado:       {market_label(data['market'])}\n\n"
                f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )

            ok = send_telegram(msg)
            if ok:
                print(f"   🚨 ALERTA DISPARADA y enviada: {alert['ticker']} @ {precio}")
                fired_count += 1
            else:
                print(f"   ❌ Alerta disparada pero error al enviar Telegram")

    save_alerts(alerts)
    print(f"\n✅ Listo. {fired_count} alerta(s) disparada(s).")


if __name__ == "__main__":
    main()
