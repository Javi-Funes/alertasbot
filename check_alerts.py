"""
╔══════════════════════════════════════════════════════╗
║   ALERTAS BOT — GitHub Actions                       ║
║   Modo 1: chequear precios (cada 5 min)              ║
║   Modo 2: recibir comandos de Telegram               ║
╚══════════════════════════════════════════════════════╝
"""

import json
import os
import sys
import requests
from datetime import datetime
from pathlib import Path

# ══════════════════════════════════════════════════════
#  ⚙️  CONFIGURACIÓN
# ══════════════════════════════════════════════════════

BOT_TOKEN  = os.environ.get("BOT_TOKEN", "")
CHAT_ID    = os.environ.get("CHAT_ID", "")
GH_TOKEN   = os.environ.get("GH_TOKEN", "")
REPO       = "javi-funes/alertasbot"
ALERTS_FILE = Path("alerts.json")

# ══════════════════════════════════════════════════════
#  🌐  DATA912.COM
# ══════════════════════════════════════════════════════

ENDPOINTS = {
    "arg":  "https://data912.com/live/arg_stocks",
    "usa":  "https://data912.com/live/usa_stocks",
    "adrs": "https://data912.com/live/usa_adrs",
}

def fetch_all_markets() -> dict:
    all_data = {}
    for market, url in ENDPOINTS.items():
        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            all_data[market] = {
                item["symbol"].upper(): item
                for item in data if "symbol" in item
            }
            print(f"✅ {market.upper()}: {len(all_data[market])} símbolos")
        except Exception as e:
            print(f"⚠️  Error {market}: {e}")
            all_data[market] = {}
    return all_data


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

def send_telegram(message: str, chat_id: str = None) -> bool:
    cid = chat_id or CHAT_ID
    try:
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json={
            "chat_id": cid,
            "text":    message,
        }, timeout=10)
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"❌ Error Telegram: {e}")
        return False


def get_telegram_updates(offset: int = 0) -> list:
    try:
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        return resp.json().get("result", [])
    except:
        return []


def set_telegram_offset(offset: int):
    """Marca los mensajes como leídos."""
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        requests.get(url, params={"offset": offset}, timeout=10)
    except:
        pass


# ══════════════════════════════════════════════════════
#  💾  ALERTAS
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
#  📲  MODO 1: LEER COMANDOS DE TELEGRAM
# ══════════════════════════════════════════════════════

def process_telegram_commands():
    """Lee los últimos mensajes de Telegram y procesa comandos."""
    print("📲 Leyendo comandos de Telegram...")

    # Cargar offset guardado
    offset_file = Path("tg_offset.txt")
    offset = int(offset_file.read_text()) if offset_file.exists() else 0

    updates = get_telegram_updates(offset)
    if not updates:
        print("   Sin mensajes nuevos.")
        return

    alerts = load_alerts()
    all_data = None  # lazy load
    new_offset = offset

    for update in updates:
        new_offset = update["update_id"] + 1
        msg = update.get("message", {})
        text = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if not text or not chat_id:
            continue

        print(f"   Mensaje recibido: {text}")

        # ── /alerta TICKER PRECIO sube|baja ──
        if text.lower().startswith("/alerta"):
            parts = text.split()
            if len(parts) != 4:
                send_telegram(
                    "❌ Formato correcto:\n"
                    "/alerta GGAL 1500 baja\n"
                    "/alerta AAPL 210 sube",
                    chat_id
                )
                continue

            ticker = parts[1].upper()
            try:
                target = float(parts[2].replace(",", "."))
            except:
                send_telegram("❌ El precio debe ser un número. Ej: 1500 o 210.50", chat_id)
                continue

            cond = parts[3].lower()
            if cond not in ("sube", "baja"):
                send_telegram("❌ La condición debe ser 'sube' o 'baja'", chat_id)
                continue

            # Verificar ticker en la API
            if all_data is None:
                all_data = fetch_all_markets()

            price_data = get_price(ticker, all_data)
            if not price_data:
                send_telegram(
                    f"⚠️ No encontré '{ticker}' en ningún mercado.\n"
                    f"Ejemplos: GGAL, YPFD, AAPL, MSFT, YPF",
                    chat_id
                )
                continue

            alerts.append({
                "ticker":    ticker,
                "condition": cond,
                "target":    target,
                "triggered": False,
                "created":   datetime.now().strftime("%d/%m %H:%M"),
                "chat_id":   chat_id,
            })

            cur = price_data["currency"]
            emoji = "🚀" if cond == "sube" else "🔻"
            send_telegram(
                f"✅ Alerta guardada!\n\n"
                f"{emoji} {ticker} — {market_label(price_data['market'])}\n"
                f"Cuando {cond} a {fmt_price(target, cur)}\n"
                f"Precio actual: {fmt_price(price_data['price'], cur)}\n\n"
                f"Te aviso en máximo 5 minutos si se dispara 🔔",
                chat_id
            )
            print(f"   ✅ Alerta agregada: {ticker} {cond} {target}")

        # ── /lista ──
        elif text.lower().startswith("/lista"):
            activas    = [a for a in alerts if not a.get("triggered")]
            disparadas = [a for a in alerts if a.get("triggered")]

            if not alerts:
                send_telegram("📋 No tenés alertas.\n\nUsá: /alerta GGAL 1500 baja", chat_id)
                continue

            msg = "📋 Tus alertas:\n\n"
            if activas:
                msg += "🟢 Activas:\n"
                for a in activas:
                    e = "🚀" if a["condition"] == "sube" else "🔻"
                    msg += f"  {a['ticker']} {e} ${a['target']:,.2f}\n"
            if disparadas:
                msg += "\n✅ Disparadas:\n"
                for a in disparadas:
                    e = "🚀" if a["condition"] == "sube" else "🔻"
                    msg += f"  {a['ticker']} {e} ${a['target']:,.2f}\n"
            send_telegram(msg, chat_id)

        # ── /precio TICKER ──
        elif text.lower().startswith("/precio"):
            parts = text.split()
            if len(parts) != 2:
                send_telegram("❌ Uso: /precio GGAL", chat_id)
                continue

            ticker = parts[1].upper()
            if all_data is None:
                all_data = fetch_all_markets()

            data = get_price(ticker, all_data)
            if not data or data["price"] == 0:
                send_telegram(f"❌ No encontré '{ticker}'.", chat_id)
                continue

            pct  = data["pct_change"]
            sign = "+" if pct >= 0 else ""
            arrow = "🟢 ▲" if pct >= 0 else "🔴 ▼"
            cur  = data["currency"]
            send_telegram(
                f"💰 {ticker} — {market_label(data['market'])}\n\n"
                f"Precio:  {fmt_price(data['price'], cur)}\n"
                f"Cambio:  {arrow} {sign}{pct:.2f}%\n"
                f"Bid:     {fmt_price(data['bid'], cur)}\n"
                f"Ask:     {fmt_price(data['ask'], cur)}\n\n"
                f"🕐 {datetime.now().strftime('%H:%M:%S')}",
                chat_id
            )

        # ── /borrar TICKER ──
        elif text.lower().startswith("/borrar"):
            parts = text.split()
            if len(parts) != 2:
                send_telegram("❌ Uso: /borrar GGAL  o  /borrar all", chat_id)
                continue

            arg = parts[1].upper()
            if arg == "ALL":
                n = len(alerts)
                alerts.clear()
                send_telegram(f"🗑 {n} alerta(s) eliminadas.", chat_id)
            else:
                antes = len(alerts)
                alerts = [a for a in alerts if a["ticker"] != arg]
                eliminadas = antes - len(alerts)
                if eliminadas:
                    send_telegram(f"🗑 {eliminadas} alerta(s) de '{arg}' eliminadas.", chat_id)
                else:
                    send_telegram(f"❌ No encontré alertas para '{arg}'", chat_id)

        # ── /ayuda ──
        elif text.lower().startswith("/ayuda") or text.lower().startswith("/start"):
            send_telegram(
                "📈 Bot de Alertas de Acciones\n\n"
                "🔔 /alerta GGAL 1500 baja\n"
                "🔔 /alerta AAPL 210 sube\n"
                "💰 /precio GGAL\n"
                "📋 /lista\n"
                "🗑 /borrar GGAL\n"
                "🗑 /borrar all\n\n"
                "Mercados: BYMA · NYSE/NASDAQ · ADRs\n"
                "Se chequea cada 5 minutos ⏱",
                chat_id
            )

    # Guardar nuevo offset y alertas
    offset_file.write_text(str(new_offset))
    save_alerts(alerts)
    print(f"✅ {len(updates)} mensaje(s) procesado(s).")


# ══════════════════════════════════════════════════════
#  🔔  MODO 2: CHEQUEAR PRECIOS Y DISPARAR ALERTAS
# ══════════════════════════════════════════════════════

def check_prices():
    """Chequea precios y dispara alertas si corresponde."""
    print(f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} — Chequeando precios...")

    alerts = load_alerts()
    pendientes = [a for a in alerts if not a.get("triggered", False)]

    if not pendientes:
        print("✅ No hay alertas pendientes.")
        return

    print(f"📋 {len(pendientes)} alerta(s) activa(s)")
    all_data = fetch_all_markets()
    fired_count = 0

    for alert in alerts:
        if alert.get("triggered"):
            continue

        data = get_price(alert["ticker"], all_data)
        if not data or data["price"] == 0:
            print(f"⚠️  Sin precio para {alert['ticker']}")
            continue

        precio = data["price"]
        cond   = alert["condition"]
        target = float(alert["target"])
        cur    = data["currency"]

        print(f"   {alert['ticker']}: {fmt_price(precio, cur)} → objetivo {cond} {fmt_price(target, cur)}")

        fired = (cond == "sube" and precio >= target) or \
                (cond == "baja" and precio <= target)

        if fired:
            alert["triggered"]    = True
            alert["triggered_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")

            pct   = data["pct_change"]
            sign  = "+" if pct >= 0 else ""
            emoji = "🚀" if cond == "sube" else "🔻"

            msg = (
                f"🚨 ALERTA DISPARADA!\n\n"
                f"{emoji} {alert['ticker']} {cond} al objetivo\n\n"
                f"💰 Precio actual: {fmt_price(precio, cur)}\n"
                f"🎯 Objetivo:      {fmt_price(target, cur)}\n"
                f"📊 Cambio hoy:    {sign}{pct:.2f}%\n"
                f"🏛 Mercado:       {market_label(data['market'])}\n\n"
                f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )

            # Mandar al chat_id de quien creó la alerta, o al CHAT_ID por defecto
            dest = alert.get("chat_id", CHAT_ID)
            ok = send_telegram(msg, dest)
            if ok:
                print(f"   🚨 DISPARADA y enviada: {alert['ticker']} @ {precio}")
                fired_count += 1

    save_alerts(alerts)
    print(f"\n✅ Listo. {fired_count} alerta(s) disparada(s).")


# ══════════════════════════════════════════════════════
#  🚀  MAIN
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"

    if mode == "commands":
        process_telegram_commands()
    else:
        process_telegram_commands()  # siempre leer comandos primero
        check_prices()               # luego chequear precios
