"""
╔══════════════════════════════════════════════════════╗
║   ALERTAS BOT — GitHub Actions                       ║
║   Datos: data912.com                                 ║
╚══════════════════════════════════════════════════════╝

Comandos disponibles desde Telegram:
    /alerta GGAL menor 1500   → avisa cuando GGAL baja de $1500
    /alerta AAPL mayor 210    → avisa cuando AAPL sube de $210
    /precio GGAL              → precio actual con bid y ask
    /lista                    → alertas numeradas
    /borrar #2                → borra la alerta número 2
    /borrar GGAL              → borra todas las alertas de GGAL
    /borrar all               → borra todas las alertas
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

BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CHAT_ID     = os.environ.get("CHAT_ID", "")
GH_TOKEN    = os.environ.get("GH_TOKEN", "")
REPO        = "javi-funes/alertasbot"
ALERTS_FILE = Path("alerts.json")
OFFSET_FILE = Path("tg_offset.txt")

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


def next_id(alerts: list) -> int:
    if not alerts:
        return 1
    return max(a.get("id", 0) for a in alerts) + 1


# ══════════════════════════════════════════════════════
#  📋  FORMATO DE LISTA
# ══════════════════════════════════════════════════════

def format_lista(alerts: list) -> str:
    activas    = [a for a in alerts if not a.get("triggered")]
    disparadas = [a for a in alerts if a.get("triggered")]

    if not alerts:
        return (
            "📋 No tenés alertas configuradas.\n\n"
            "Usá:\n/alerta GGAL menor 1500\n/alerta AAPL mayor 210"
        )

    msg = "📋 Tus alertas:\n\n"

    if activas:
        msg += "🟢 Activas:\n"
        for a in activas:
            cur   = "ARS" if a.get("market") == "arg" else "USD"
            emoji = "📈" if a["condition"] == "mayor" else "📉"
            mkt   = market_emoji(a.get("market", ""))
            msg  += f"  #{a['id']} {emoji} {a['ticker']} {a['condition']} {fmt_price(a['target'], cur)} {mkt}\n"

    if disparadas:
        msg += "\n✅ Ya disparadas:\n"
        for a in disparadas:
            cur   = "ARS" if a.get("market") == "arg" else "USD"
            emoji = "📈" if a["condition"] == "mayor" else "📉"
            mkt   = market_emoji(a.get("market", ""))
            msg  += f"  #{a['id']} {emoji} {a['ticker']} {a['condition']} {fmt_price(a['target'], cur)} {mkt}\n"

    msg += "\nPara borrar: /borrar #2"
    return msg


# ══════════════════════════════════════════════════════
#  🚨  MENSAJE DE ALERTA DISPARADA
# ══════════════════════════════════════════════════════

def format_alerta_disparada(alert: dict, data: dict) -> str:
    condition = alert["condition"]
    ticker    = alert["ticker"]
    target    = float(alert["target"])
    cur       = data["currency"]
    pct       = data["pct_change"]
    precio    = data["price"]
    sign      = "+" if pct >= 0 else ""
    arrow     = "🟢 ▲" if pct >= 0 else "🔴 ▼"
    aid       = alert.get("id", "?")

    # Texto descriptivo según condición
    if condition == "mayor":
        accion = f"{ticker} superó tu objetivo"
        emoji  = "📈"
    else:
        accion = f"{ticker} bajó a tu objetivo"
        emoji  = "📉"

    return (
        f"🚨 ALERTA #{aid} DISPARADA!\n"
        f"{'─' * 24}\n\n"
        f"{emoji} {accion}\n\n"
        f"🏛 Mercado:       {market_label(data['market'])}\n"
        f"🎯 Tu objetivo:   {condition} a {fmt_price(target, cur)}\n"
        f"💰 Precio actual: {fmt_price(precio, cur)}\n"
        f"📊 Cambio hoy:    {arrow} {sign}{pct:.2f}%\n"
        f"📋 Bid: {fmt_price(data['bid'], cur)} · Ask: {fmt_price(data['ask'], cur)}\n\n"
        f"🕐 {datetime.now().strftime('%d/%m/%Y %H:%M')}"
    )


# ══════════════════════════════════════════════════════
#  📲  PROCESAR MENSAJES
# ══════════════════════════════════════════════════════

AYUDA = (
    "📈 Bot de Alertas de Acciones\n\n"
    "Comandos:\n\n"
    "🔔 /alerta GGAL menor 1500\n"
    "   avisa cuando GGAL baja de $1500\n\n"
    "🔔 /alerta AAPL mayor 210\n"
    "   avisa cuando AAPL sube de $210\n\n"
    "💰 /precio GGAL\n"
    "   precio actual con bid y ask\n\n"
    "📋 /lista\n"
    "   ver alertas con su número\n\n"
    "🗑 /borrar #2\n"
    "   borrar la alerta número 2\n\n"
    "🗑 /borrar GGAL\n"
    "   borrar todas las alertas de GGAL\n\n"
    "🗑 /borrar all\n"
    "   borrar todas las alertas\n\n"
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

            if cb_data.startswith("market:"):
                parts     = cb_data.split(":")
                market    = parts[1]
                ticker    = parts[2]
                target    = float(parts[3])
                condition = parts[4]

                price_data = get_price_in_market(ticker, market, all_data)
                if not price_data:
                    answer_callback(cb_id, "❌ No se encontró el ticker en ese mercado")
                    continue

                aid = next_id(alerts)
                alerts.append({
                    "id":        aid,
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
                answer_callback(cb_id, f"✅ Alerta #{aid} guardada!")
                edit_message(chat_id, msg_id,
                    f"✅ Alerta #{aid} guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Avisame cuando sea {condition} a {fmt_price(target, cur)}\n\n"
                    f"Precio actual: {fmt_price(price_data['price'], cur)}\n"
                    f"Bid: {fmt_price(price_data['bid'], cur)} · "
                    f"Ask: {fmt_price(price_data['ask'], cur)}\n\n"
                    f"Te aviso en máximo 5 min si se dispara 🔔"
                )
                print(f"   ✅ Alerta #{aid}: {ticker} {condition} {target} en {market}")
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
                market     = markets_found[0]
                price_data = get_price_in_market(ticker, market, all_data)
                cur        = price_data["currency"]
                emoji      = "📈" if condition == "mayor" else "📉"
                aid        = next_id(alerts)

                alerts.append({
                    "id":        aid,
                    "ticker":    ticker,
                    "condition": condition,
                    "target":    target,
                    "market":    market,
                    "triggered": False,
                    "created":   datetime.now().strftime("%d/%m %H:%M"),
                    "chat_id":   chat_id,
                })

                send_telegram(
                    f"✅ Alerta #{aid} guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Avisame cuando sea {condition} a {fmt_price(target, cur)}\n\n"
                    f"Precio actual: {fmt_price(price_data['price'], cur)}\n"
                    f"Bid: {fmt_price(price_data['bid'], cur)} · "
                    f"Ask: {fmt_price(price_data['ask'], cur)}\n\n"
                    f"Te aviso en máximo 5 min si se dispara 🔔",
                    chat_id
                )
                print(f"   ✅ Alerta #{aid}: {ticker} {condition} {target} en {market}")

            else:
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

            msg_text = f"💰 {ticker}\n" + "─" * 22 + "\n"
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
            send_telegram(format_lista(alerts), chat_id)

        # ── /borrar ──
        elif cmd == "/borrar":
            if len(parts) != 2:
                send_telegram(
                    "❌ Uso:\n"
                    "/borrar #2     → borra la alerta número 2\n"
                    "/borrar GGAL   → borra todas las de GGAL\n"
                    "/borrar all    → borra todas",
                    chat_id
                )
                continue

            arg = parts[1]

            if arg.startswith("#"):
                try:
                    aid = int(arg[1:])
                except:
                    send_telegram("❌ Número de alerta inválido. Ej: /borrar #2", chat_id)
                    continue

                match = next((a for a in alerts if a.get("id") == aid), None)
                if not match:
                    send_telegram(
                        f"❌ No encontré la alerta #{aid}.\n\n"
                        f"Usá /lista para ver tus alertas.",
                        chat_id
                    )
                    continue

                alerts.remove(match)
                cur   = "ARS" if match.get("market") == "arg" else "USD"
                emoji = "📈" if match["condition"] == "mayor" else "📉"
                send_telegram(
                    f"🗑 Alerta #{aid} eliminada:\n"
                    f"{emoji} {match['ticker']} {match['condition']} {fmt_price(match['target'], cur)} {market_emoji(match.get('market',''))}",
                    chat_id
                )

            elif arg.upper() == "ALL":
                n = len(alerts)
                alerts.clear()
                send_telegram(f"🗑 {n} alerta(s) eliminadas.", chat_id)

            else:
                ticker = arg.upper()
                antes  = len(alerts)
                alerts = [a for a in alerts if a["ticker"] != ticker]
                elim   = antes - len(alerts)
                if elim:
                    send_telegram(f"🗑 {elim} alerta(s) de '{ticker}' eliminadas.", chat_id)
                else:
                    send_telegram(
                        f"❌ No encontré alertas para '{ticker}'.\n\n"
                        f"Usá /lista para ver tus alertas.",
                        chat_id
                    )

        elif cmd in ("/ayuda", "/start"):
            send_telegram(AYUDA, chat_id)

        else:
            send_telegram(
                "❓ No entendí ese comando.\n\n"
                "Escribí /ayuda para ver los comandos disponibles.",
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

        print(f"   #{alert.get('id','?')} {ticker}: {fmt_price(precio, data['currency'])} — objetivo {condition} {fmt_price(target, data['currency'])}")

        fired = (condition == "mayor" and precio >= target) or \
                (condition == "menor" and precio <= target)

        if fired:
            alert["triggered"]    = True
            alert["triggered_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            dest = alert.get("chat_id", CHAT_ID)

            msg = format_alerta_disparada(alert, data)
            ok  = send_telegram(msg, dest)
            if ok:
                print(f"   🚨 DISPARADA #{alert.get('id')}: {ticker} @ {fmt_price(precio, data['currency'])}")
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
