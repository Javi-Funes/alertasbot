"""
╔══════════════════════════════════════════════════════╗
║   ALERTAS BOT — GitHub Actions                       ║
║   Datos: data912.com                                 ║
╚══════════════════════════════════════════════════════╝

Comandos disponibles desde Telegram:
    /alerta GGAL menor 1500   → avisa cuando el precio baja de $1500
    /alerta AAPL mayor 210    → avisa cuando el precio sube de $210
    /precio GGAL              → muestra precio actual (bid, ask, último)
    /lista                    → alertas activas
    /borrar GGAL              → borra alertas de ese ticker
    /borrar all               → borra todas
    /ayuda                    → menú
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

BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
CHAT_ID      = os.environ.get("CHAT_ID", "")
GH_TOKEN     = os.environ.get("GH_TOKEN", "")
REPO         = "javi-funes/alertasbot"
ALERTS_FILE  = Path("alerts.json")
OFFSET_FILE  = Path("tg_offset.txt")

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


def get_price_in_market(ticker: str, market: str, all_data: dict) -> dict | None:
    ticker = ticker.upper().strip()
    if ticker in all_data.get(market, {}):
        item  = all_data[market][ticker]
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


def ticker_in_markets(ticker: str, all_data: dict) -> list:
    return [m for m in ("arg", "adrs", "usa") if ticker.upper() in all_data.get(m, {})]


def fmt_price(price: float, currency: str) -> str:
    if currency == "ARS":
        return f"${price:,.2f}".replace(",","X").replace(".",",").replace("X",".")
    return f"${price:,.2f}"


def market_label(market: str) -> str:
    return {
        "arg":  "🇦🇷 BYMA (pesos)",
        "usa":  "🇺🇸 NYSE/NASDAQ (dólares)",
        "adrs": "🇺🇸 ADR (dólares)"
    }.get(market, market)


def market_emoji(market: str) -> str:
    return {"arg": "🇦🇷", "usa": "🇺🇸", "adrs": "🔵"}.get(market, "")


# ══════════════════════════════════════════════════════
#  💬  TELEGRAM
# ══════════════════════════════════════════════════════

def send_telegram(message: str, chat_id: str = None, reply_markup: dict = None) -> bool:
    cid     = chat_id or CHAT_ID
    payload = {"chat_id": cid, "text": message}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json=payload, timeout=10
        )
        return resp.json().get("ok", False)
    except Exception as e:
        print(f"❌ Error Telegram: {e}")
        return False


def answer_callback(callback_query_id: str, text: str = ""):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10
        )
    except:
        pass


def edit_message(chat_id: str, message_id: int, text: str):
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id":      chat_id,
                "message_id":   message_id,
                "text":         text,
                "reply_markup": json.dumps({"inline_keyboard": []})
            },
            timeout=10
        )
    except:
        pass


def get_updates(offset: int = 0) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 5},
            timeout=10
        )
        return resp.json().get("result", [])
    except:
        return []


# ══════════════════════════════════════════════════════
#  💾  PERSISTENCIA
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
#  📲  PROCESAR MENSAJES
# ══════════════════════════════════════════════════════

AYUDA = (
    "📈 Bot de Alertas de Acciones\n\n"
    "Comandos:\n\n"
    "🔔 /alerta GGAL menor 1500\n"
    "   → avisa cuando GGAL baja de $1500\n\n"
    "🔔 /alerta AAPL mayor 210\n"
    "   → avisa cuando AAPL sube de $210\n\n"
    "💰 /precio GGAL\n"
    "   → precio actual con bid y ask\n\n"
    "📋 /lista\n"
    "   → ver todas tus alertas activas\n\n"
    "🗑 /borrar GGAL\n"
    "   → borrar alertas de ese ticker\n\n"
    "🗑 /borrar all\n"
    "   → borrar todas las alertas\n\n"
    "Mercados: 🇦🇷 BYMA · 🇺🇸 NYSE/NASDAQ · 🔵 ADRs\n"
    "Chequeo automático cada 5 minutos ⏱"
)


def process_updates(all_data: dict):
    offset  = int(OFFSET_FILE.read_text()) if OFFSET_FILE.exists() else 0
    updates = get_updates(offset)

    if not updates:
        print("   Sin mensajes nuevos.")
        return

    alerts     = load_alerts()
    new_offset = offset

    for update in updates:
        new_offset = update["update_id"] + 1

        # ── Callback de botón (elección de mercado) ──
        if "callback_query" in update:
            cb      = update["callback_query"]
            cb_id   = cb["id"]
            cb_data = cb.get("data", "")
            chat_id = str(cb["message"]["chat"]["id"])
            msg_id  = cb["message"]["message_id"]

            # formato: "market:arg:GGAL:1500:menor"
            if cb_data.startswith("market:"):
                parts      = cb_data.split(":")
                market     = parts[1]
                ticker     = parts[2]
                target     = float(parts[3])
                condition  = parts[4]  # "mayor" o "menor"

                price_data = get_price_in_market(ticker, market, all_data)
                if not price_data:
                    answer_callback(cb_id, "❌ No se encontró el ticker en ese mercado")
                    continue

                alerts.append({
                    "ticker":    ticker,
                    "condition": condition,
                    "target":    target,
                    "market":    market,
                    "triggered": False,
                    "created":   datetime.now().strftime("%d/%m %H:%M"),
                    "chat_id":   chat_id,
                })

                cur   = price_data["currency"]
                emoji = "📈" if condition == "mayor" else "📉"
                answer_callback(cb_id, "✅ Alerta guardada!")
                edit_message(chat_id, msg_id,
                    f"✅ Alerta guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Avisame cuando sea {condition} a {fmt_price(target, cur)}\n\n"
                    f"Precio actual: {fmt_price(price_data['price'], cur)}\n"
                    f"Bid: {fmt_price(price_data['bid'], cur)} · "
                    f"Ask: {fmt_price(price_data['ask'], cur)}\n\n"
                    f"Te aviso en máximo 5 min si se dispara 🔔"
                )
                print(f"   ✅ Alerta confirmada: {ticker} {condition} {target} en {market}")

            continue

        # ── Mensaje de texto ──
        msg     = update.get("message", {})
        text    = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if not text or not chat_id:
            continue

        print(f"   Mensaje: {text}")
        parts = text.split()
        cmd   = parts[0].lower()

        # ── /alerta TICKER mayor|menor PRECIO ──
        if cmd == "/alerta":
            # Formato: /alerta GGAL menor 1500
            if len(parts) != 4:
                send_telegram(
                    "❌ Formato correcto:\n\n"
                    "/alerta GGAL menor 1500\n"
                    "/alerta AAPL mayor 210",
                    chat_id
                )
                continue

            ticker    = parts[1].upper()
            condition = parts[2].lower()
            try:
                target = float(parts[3].replace(",", "."))
            except:
                send_telegram("❌ El precio debe ser un número. Ej: 1500 o 210.50", chat_id)
                continue

            if condition not in ("mayor", "menor"):
                send_telegram(
                    "❌ La condición debe ser 'mayor' o 'menor'\n\n"
                    "Ejemplos:\n"
                    "/alerta GGAL menor 1500\n"
                    "/alerta AAPL mayor 210",
                    chat_id
                )
                continue

            markets_found = ticker_in_markets(ticker, all_data)

            if not markets_found:
                send_telegram(
                    f"⚠️ No encontré '{ticker}' en ningún mercado.\n\n"
                    f"Ejemplos de tickers:\n"
                    f"🇦🇷 GGAL, YPFD, PAMP, BMA\n"
                    f"🇺🇸 AAPL, MSFT, TSLA, NVDA\n"
                    f"🔵 YPF, GGAL, PAM (ADRs)",
                    chat_id
                )
                continue

            if len(markets_found) == 1:
                # Solo un mercado → guardar directo
                market     = markets_found[0]
                price_data = get_price_in_market(ticker, market, all_data)
                cur        = price_data["currency"]
                emoji      = "📈" if condition == "mayor" else "📉"

                alerts.append({
                    "ticker":    ticker,
                    "condition": condition,
                    "target":    target,
                    "market":    market,
                    "triggered": False,
                    "created":   datetime.now().strftime("%d/%m %H:%M"),
                    "chat_id":   chat_id,
                })

                send_telegram(
                    f"✅ Alerta guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Avisame cuando sea {condition} a {fmt_price(target, cur)}\n\n"
                    f"Precio actual: {fmt_price(price_data['price'], cur)}\n"
                    f"Bid: {fmt_price(price_data['bid'], cur)} · "
                    f"Ask: {fmt_price(price_data['ask'], cur)}\n\n"
                    f"Te aviso en máximo 5 min si se dispara 🔔",
                    chat_id
                )

            else:
                # Varios mercados → preguntar con botones
                buttons = []
                for market in markets_found:
                    price_data = get_price_in_market(ticker, market, all_data)
                    cur        = price_data["currency"]
                    price_str  = fmt_price(price_data["price"], cur)
                    label      = f"{market_emoji(market)} {market.upper()}  {price_str}"
                    callback   = f"market:{market}:{ticker}:{target}:{condition}"
                    buttons.append([{"text": label, "callback_data": callback}])

                send_telegram(
                    f"📊 '{ticker}' cotiza en varios mercados.\n"
                    f"¿En cuál querés monitorear el precio?",
                    chat_id,
                    reply_markup={"inline_keyboard": buttons}
                )

        # ── /precio TICKER ──
        elif cmd == "/precio":
            if len(parts) != 2:
                send_telegram("❌ Uso: /precio GGAL", chat_id)
                continue

            ticker        = parts[1].upper()
            markets_found = ticker_in_markets(ticker, all_data)

            if not markets_found:
                send_telegram(f"❌ No encontré '{ticker}' en ningún mercado.", chat_id)
                continue

            msg_text = f"💰 {ticker}\n"
            msg_text += "─" * 20 + "\n"
            for market in markets_found:
                d     = get_price_in_market(ticker, market, all_data)
                pct   = d["pct_change"]
                sign  = "+" if pct >= 0 else ""
                arrow = "🟢 ▲" if pct >= 0 else "🔴 ▼"
                cur   = d["currency"]
                msg_text += (
                    f"\n{market_label(market)}\n"
                    f"Último:  {fmt_price(d['price'], cur)}\n"
                    f"Bid:     {fmt_price(d['bid'], cur)}\n"
                    f"Ask:     {fmt_price(d['ask'], cur)}\n"
                    f"Cambio:  {arrow} {sign}{pct:.2f}%\n"
                )
            msg_text += f"\n🕐 {datetime.now().strftime('%H:%M:%S')}"
            send_telegram(msg_text, chat_id)

        # ── /lista ──
        elif cmd == "/lista":
            activas    = [a for a in alerts if not a.get("triggered")]
            disparadas = [a for a in alerts if a.get("triggered")]

            if not alerts:
                send_telegram(
                    "📋 No tenés alertas configuradas.\n\n"
                    "Usá:\n/alerta GGAL menor 1500\n/alerta AAPL mayor 210",
                    chat_id
                )
                continue

            msg_text = "📋 Tus alertas:\n\n"
            if activas:
                msg_text += "🟢 Activas:\n"
                for a in activas:
                    cur      = "ARS" if a.get("market") == "arg" else "USD"
                    emoji    = "📈" if a["condition"] == "mayor" else "📉"
                    msg_text += f"  {emoji} {a['ticker']} {a['condition']} {fmt_price(a['target'], cur)} {market_emoji(a.get('market',''))}\n"
            if disparadas:
                msg_text += "\n✅ Disparadas:\n"
                for a in disparadas:
                    cur      = "ARS" if a.get("market") == "arg" else "USD"
                    emoji    = "📈" if a["condition"] == "mayor" else "📉"
                    msg_text += f"  {emoji} {a['ticker']} {a['condition']} {fmt_price(a['target'], cur)} {market_emoji(a.get('market',''))}\n"
            send_telegram(msg_text, chat_id)

        # ── /borrar ──
        elif cmd == "/borrar":
            if len(parts) != 2:
                send_telegram("❌ Uso: /borrar GGAL  o  /borrar all", chat_id)
                continue

            arg = parts[1].upper()
            if arg == "ALL":
                n = len(alerts)
                alerts.clear()
                send_telegram(f"🗑 {n} alerta(s) eliminadas.", chat_id)
            else:
                antes  = len(alerts)
                alerts = [a for a in alerts if a["ticker"] != arg]
                elim   = antes - len(alerts)
                if elim:
                    send_telegram(f"🗑 {elim} alerta(s) de '{arg}' eliminadas.", chat_id)
                else:
                    send_telegram(f"❌ No encontré alertas para '{arg}'", chat_id)

        # ── /ayuda o /start ──
        elif cmd in ("/ayuda", "/start"):
            send_telegram(AYUDA, chat_id)

        else:
            send_telegram(
                "❓ No entendí ese comando.\n\nEscribí /ayuda para ver los comandos disponibles.",
                chat_id
            )

    OFFSET_FILE.write_text(str(new_offset))
    save_alerts(alerts)
    print(f"✅ {len(updates)} update(s) procesado(s).")


# ══════════════════════════════════════════════════════
#  🔔  CHEQUEAR PRECIOS
# ══════════════════════════════════════════════════════

def check_prices(all_data: dict):
    print(f"\n🕐 {datetime.now().strftime('%d/%m/%Y %H:%M:%S')} — Chequeando precios...")

    alerts     = load_alerts()
    pendientes = [a for a in alerts if not a.get("triggered", False)]

    if not pendientes:
        print("✅ No hay alertas pendientes.")
        return

    print(f"📋 {len(pendientes)} alerta(s) activa(s)")
    fired_count = 0

    for alert in alerts:
        if alert.get("triggered"):
            continue

        market = alert.get("market")
        ticker = alert["ticker"]
        data   = get_price_in_market(ticker, market, all_data) if market else None

        if not data or data["price"] == 0:
            print(f"⚠️  Sin precio para {ticker}")
            continue

        precio    = data["price"]
        condition = alert["condition"]
        target    = float(alert["target"])
        cur       = data["currency"]

        print(f"   {ticker}: {fmt_price(precio, cur)} — objetivo {condition} {fmt_price(target, cur)}")

        fired = (condition == "mayor" and precio >= target) or \
                (condition == "menor" and precio <= target)

        if fired:
            alert["triggered"]    = True
            alert["triggered_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            pct   = data["pct_change"]
            sign  = "+" if pct >= 0 else ""
            emoji = "📈" if condition == "mayor" else "📉"
            dest  = alert.get("chat_id", CHAT_ID)

            msg = (
                f"🚨 ALERTA DISPARADA!\n\n"
                f"{emoji} {ticker} es {condition} al objetivo\n\n"
                f"💰 Precio actual: {fmt_price(precio, cur)}\n"
                f"🎯 Objetivo:      {condition} {fmt_price(target, cur)}\n"
                f"📊 Cambio hoy:    {sign}{pct:.2f}%\n"
                f"🏛 Mercado:       {market_label(data['market'])}\n\n"
                f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
            )
            ok = send_telegram(msg, dest)
            if ok:
                print(f"   🚨 DISPARADA: {ticker} @ {fmt_price(precio, cur)}")
                fired_count += 1

    save_alerts(alerts)
    print(f"\n✅ Listo. {fired_count} alerta(s) disparada(s).")


# ══════════════════════════════════════════════════════
#  🚀  MAIN
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN o CHAT_ID no configurados")
        sys.exit(1)

    print("📡 Descargando precios de data912.com...")
    all_data = fetch_all_markets()

    print("\n📲 Procesando mensajes de Telegram...")
    process_updates(all_data)

    print("\n🔔 Chequeando alertas...")
    check_prices(all_data)
