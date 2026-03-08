"""
╔══════════════════════════════════════════════════════╗
║   ALERTAS BOT — GitHub Actions                       ║
║   Con botones para elegir mercado                    ║
║   Datos: data912.com                                 ║
╚══════════════════════════════════════════════════════╝

Comandos disponibles desde Telegram:
    /alerta GGAL 1500 baja   → pregunta el mercado con botones
    /precio GGAL             → muestra precio en ambos mercados
    /lista                   → alertas activas
    /borrar GGAL             → borra alertas de ese ticker
    /borrar all              → borra todas
    /ayuda                   → menú
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
PENDING_FILE = Path("pending_alerts.json")  # alertas esperando confirmación de mercado

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
    """Busca el precio de un ticker en un mercado específico."""
    ticker = ticker.upper().strip()
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


def get_price_any(ticker: str, all_data: dict) -> dict | None:
    """Busca el precio en cualquier mercado (orden: arg → adrs → usa)."""
    for market in ("arg", "adrs", "usa"):
        data = get_price_in_market(ticker, market, all_data)
        if data:
            return data
    return None


def ticker_in_markets(ticker: str, all_data: dict) -> list:
    """Retorna en qué mercados existe un ticker."""
    markets = []
    for market in ("arg", "adrs", "usa"):
        if ticker.upper() in all_data.get(market, {}):
            markets.append(market)
    return markets


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

def send_telegram(message: str, chat_id: str = None, reply_markup: dict = None) -> dict:
    cid = chat_id or CHAT_ID
    payload = {"chat_id": cid, "text": message}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        resp = requests.post(url, json=payload, timeout=10)
        return resp.json()
    except Exception as e:
        print(f"❌ Error Telegram: {e}")
        return {}


def answer_callback(callback_query_id: str, text: str = ""):
    """Responde al callback de un botón para quitar el 'reloj' de carga."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10
        )
    except:
        pass


def edit_message(chat_id: str, message_id: int, text: str):
    """Edita un mensaje ya enviado (para reemplazar los botones)."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/editMessageText",
            json={
                "chat_id":    chat_id,
                "message_id": message_id,
                "text":       text,
                "reply_markup": json.dumps({"inline_keyboard": []})
            },
            timeout=10
        )
    except:
        pass


def get_updates(offset: int = 0) -> list:
    try:
        url  = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates"
        resp = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
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


def load_pending() -> dict:
    """Alertas que esperan que el usuario elija el mercado."""
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text(encoding="utf-8"))
        except:
            return {}
    return {}


def save_pending(pending: dict):
    PENDING_FILE.write_text(
        json.dumps(pending, indent=2, ensure_ascii=False),
        encoding="utf-8"
    )


# ══════════════════════════════════════════════════════
#  📲  PROCESAR MENSAJES Y CALLBACKS
# ══════════════════════════════════════════════════════

def process_updates(all_data: dict):
    offset = int(OFFSET_FILE.read_text()) if OFFSET_FILE.exists() else 0
    updates = get_updates(offset)

    if not updates:
        print("   Sin mensajes nuevos.")
        return

    alerts  = load_alerts()
    pending = load_pending()
    new_offset = offset

    for update in updates:
        new_offset = update["update_id"] + 1

        # ── Callback de botón (elección de mercado) ──
        if "callback_query" in update:
            cb       = update["callback_query"]
            cb_id    = cb["id"]
            cb_data  = cb.get("data", "")
            chat_id  = str(cb["message"]["chat"]["id"])
            msg_id   = cb["message"]["message_id"]

            # callback_data formato: "market:arg:GGAL:1500:baja"
            if cb_data.startswith("market:"):
                parts   = cb_data.split(":")
                market  = parts[1]
                ticker  = parts[2]
                target  = float(parts[3])
                cond    = parts[4]

                # Verificar que el ticker existe en ese mercado
                price_data = get_price_in_market(ticker, market, all_data)
                if not price_data:
                    answer_callback(cb_id, "❌ No se encontró el ticker en ese mercado")
                    continue

                # Guardar la alerta
                alerts.append({
                    "ticker":    ticker,
                    "condition": cond,
                    "target":    target,
                    "market":    market,
                    "triggered": False,
                    "created":   datetime.now().strftime("%d/%m %H:%M"),
                    "chat_id":   chat_id,
                })

                cur   = price_data["currency"]
                emoji = "🚀" if cond == "sube" else "🔻"

                answer_callback(cb_id, "✅ Alerta guardada!")
                edit_message(chat_id, msg_id,
                    f"✅ Alerta guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Cuando {cond} a {fmt_price(target, cur)}\n"
                    f"Precio actual: {fmt_price(price_data['price'], cur)}\n\n"
                    f"Te aviso en máximo 5 min si se dispara 🔔"
                )
                print(f"   ✅ Alerta confirmada: {ticker} {cond} {target} en {market}")

            continue  # fin del callback

        # ── Mensaje de texto ──
        msg     = update.get("message", {})
        text    = msg.get("text", "").strip()
        chat_id = str(msg.get("chat", {}).get("id", ""))

        if not text or not chat_id:
            continue

        print(f"   Mensaje: {text}")
        parts = text.split()
        cmd   = parts[0].lower()

        # ── /alerta TICKER PRECIO sube|baja ──
        if cmd == "/alerta":
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

            # Ver en cuántos mercados existe el ticker
            markets_found = ticker_in_markets(ticker, all_data)

            if not markets_found:
                send_telegram(
                    f"⚠️ No encontré '{ticker}' en ningún mercado.\n"
                    f"Ejemplos: GGAL, YPFD, AAPL, MSFT, YPF",
                    chat_id
                )
                continue

            if len(markets_found) == 1:
                # Solo existe en un mercado → guardar directo sin preguntar
                market     = markets_found[0]
                price_data = get_price_in_market(ticker, market, all_data)
                cur        = price_data["currency"]
                emoji      = "🚀" if cond == "sube" else "🔻"

                alerts.append({
                    "ticker":    ticker,
                    "condition": cond,
                    "target":    target,
                    "market":    market,
                    "triggered": False,
                    "created":   datetime.now().strftime("%d/%m %H:%M"),
                    "chat_id":   chat_id,
                })

                send_telegram(
                    f"✅ Alerta guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Cuando {cond} a {fmt_price(target, cur)}\n"
                    f"Precio actual: {fmt_price(price_data['price'], cur)}\n\n"
                    f"Te aviso en máximo 5 min si se dispara 🔔",
                    chat_id
                )

            else:
                # Existe en varios mercados → preguntar con botones
                buttons = []
                for market in markets_found:
                    price_data = get_price_in_market(ticker, market, all_data)
                    cur        = price_data["currency"]
                    price_str  = fmt_price(price_data["price"], cur)
                    label      = f"{market_emoji(market)} {market.upper()}  {price_str}"
                    callback   = f"market:{market}:{ticker}:{target}:{cond}"
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

            ticker = parts[1].upper()
            markets_found = ticker_in_markets(ticker, all_data)

            if not markets_found:
                send_telegram(f"❌ No encontré '{ticker}' en ningún mercado.", chat_id)
                continue

            msg_text = f"💰 {ticker}\n\n"
            for market in markets_found:
                d    = get_price_in_market(ticker, market, all_data)
                pct  = d["pct_change"]
                sign = "+" if pct >= 0 else ""
                arrow = "🟢 ▲" if pct >= 0 else "🔴 ▼"
                cur  = d["currency"]
                msg_text += (
                    f"{market_label(market)}\n"
                    f"Precio: {fmt_price(d['price'], cur)}\n"
                    f"Cambio: {arrow} {sign}{pct:.2f}%\n\n"
                )
            msg_text += f"🕐 {datetime.now().strftime('%H:%M:%S')}"
            send_telegram(msg_text, chat_id)

        # ── /lista ──
        elif cmd == "/lista":
            activas    = [a for a in alerts if not a.get("triggered")]
            disparadas = [a for a in alerts if a.get("triggered")]

            if not alerts:
                send_telegram("📋 No tenés alertas.\n\nUsá: /alerta GGAL 1500 baja", chat_id)
                continue

            msg_text = "📋 Tus alertas:\n\n"
            if activas:
                msg_text += "🟢 Activas:\n"
                for a in activas:
                    e   = "🚀" if a["condition"] == "sube" else "🔻"
                    cur = "ARS" if a.get("market") == "arg" else "USD"
                    msg_text += f"  {a['ticker']} {e} {fmt_price(a['target'], cur)} — {market_emoji(a.get('market',''))}\n"
            if disparadas:
                msg_text += "\n✅ Disparadas:\n"
                for a in disparadas:
                    e   = "🚀" if a["condition"] == "sube" else "🔻"
                    cur = "ARS" if a.get("market") == "arg" else "USD"
                    msg_text += f"  {a['ticker']} {e} {fmt_price(a['target'], cur)}\n"
            send_telegram(msg_text, chat_id)

        # ── /borrar TICKER ──
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
            send_telegram(
                "📈 Bot de Alertas de Acciones\n\n"
                "🔔 /alerta GGAL 1500 baja\n"
                "🔔 /alerta AAPL 210 sube\n"
                "💰 /precio GGAL\n"
                "📋 /lista\n"
                "🗑 /borrar GGAL\n"
                "🗑 /borrar all\n\n"
                "Si el ticker cotiza en varios mercados\n"
                "el bot te pregunta cuál querés 📊\n\n"
                "Mercados: BYMA · NYSE/NASDAQ · ADRs\n"
                "Chequeo cada 5 minutos ⏱",
                chat_id
            )

    # Guardar estado
    OFFSET_FILE.write_text(str(new_offset))
    save_alerts(alerts)
    save_pending(pending)
    print(f"✅ {len(updates)} update(s) procesado(s).")


# ══════════════════════════════════════════════════════
#  🔔  CHEQUEAR PRECIOS Y DISPARAR ALERTAS
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

        # Buscar en el mercado específico si está guardado, sino en todos
        if market:
            data = get_price_in_market(ticker, market, all_data)
        else:
            data = get_price_any(ticker, all_data)

        if not data or data["price"] == 0:
            print(f"⚠️  Sin precio para {ticker}")
            continue

        precio = data["price"]
        cond   = alert["condition"]
        target = float(alert["target"])
        cur    = data["currency"]

        print(f"   {ticker} ({market_label(data['market'])}): {fmt_price(precio, cur)} → objetivo {cond} {fmt_price(target, cur)}")

        fired = (cond == "sube" and precio >= target) or \
                (cond == "baja" and precio <= target)

        if fired:
            alert["triggered"]    = True
            alert["triggered_at"] = datetime.now().strftime("%d/%m/%Y %H:%M")
            pct   = data["pct_change"]
            sign  = "+" if pct >= 0 else ""
            emoji = "🚀" if cond == "sube" else "🔻"
            dest  = alert.get("chat_id", CHAT_ID)

            msg = (
                f"🚨 ALERTA DISPARADA!\n\n"
                f"{emoji} {ticker} {cond} al objetivo\n\n"
                f"💰 Precio actual: {fmt_price(precio, cur)}\n"
                f"🎯 Objetivo:      {fmt_price(target, cur)}\n"
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
