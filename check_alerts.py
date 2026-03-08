"""
╔══════════════════════════════════════════════════════╗
║   ALERTAS BOT — GitHub Actions                       ║
║   Precios: Yahoo Finance (velas de 1 hora)           ║
║   Velas cierran a los :30 de cada hora               ║
║   Mercado: NYSE 9:30-16:00 ET (DST automático)       ║
╚══════════════════════════════════════════════════════╝

CORRECCIONES:
  - Timestamp vela: Yahoo devuelve inicio de vela, se suma 1h para mostrar cierre real
  - next_candle_close_ar: considera mercado cerrado y fines de semana
  - Próximo chequeo muestra día y hora correctos

Cierres de vela NYSE (ET → AR verano / AR invierno):
    10:30 → 11:30 / 12:30   ← primera vela
    11:30 → 12:30 / 13:30
    12:30 → 13:30 / 14:30
    13:30 → 14:30 / 15:30
    14:30 → 15:30 / 16:30
    15:30 → 16:30 / 17:30
    16:30 → 17:30 / 18:30   ← última vela

Comandos Telegram:
    /alerta GGAL menor 1500
    /alerta AAPL mayor 210
    /precio GGAL
    /lista
    /borrar #2
    /borrar GGAL
    /borrar all
    /ayuda
"""

import json
import os
import sys
import requests
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

# ══════════════════════════════════════════════════════
#  ⚙️  CONFIGURACIÓN
# ══════════════════════════════════════════════════════

BOT_TOKEN   = os.environ.get("BOT_TOKEN", "")
CHAT_ID     = os.environ.get("CHAT_ID", "")
ALERTS_FILE = Path("alerts.json")
OFFSET_FILE = Path("tg_offset.txt")

TZ_NY = ZoneInfo("America/New_York")
TZ_AR = ZoneInfo("America/Argentina/Buenos_Aires")

MARKET_OPEN_H  = 9
MARKET_OPEN_M  = 30
MARKET_CLOSE_H = 16
MARKET_CLOSE_M = 30  # última vela cierra a las 16:30 ET

CANDLE_TOLERANCE_MIN = 8

DIAS_ES = {
    "Monday":    "lunes",
    "Tuesday":   "martes",
    "Wednesday": "miércoles",
    "Thursday":  "jueves",
    "Friday":    "viernes",
    "Saturday":  "sábado",
    "Sunday":    "domingo",
}


# ══════════════════════════════════════════════════════
#  🕐  HORARIO DE MERCADO Y VELAS
# ══════════════════════════════════════════════════════

def now_ny() -> datetime:
    """Hora actual en Nueva York con DST automático."""
    return datetime.now(TZ_NY)


def is_market_open() -> bool:
    """True si el mercado NYSE está abierto ahora."""
    now = now_ny()
    if now.weekday() >= 5:
        return False
    open_time  = now.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0, microsecond=0)
    close_time = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)
    return open_time <= now <= close_time


def candle_just_closed() -> tuple[bool, datetime | None]:
    """
    Retorna (True, hora_cierre) si una vela de 1h acaba de cerrar.

    Las velas del NYSE cierran a los :30 de cada hora:
        10:30 → primera vela (abre 9:30)
        11:30, 12:30, 13:30, 14:30, 15:30
        16:30 → última vela

    Detecta el cierre si estamos entre HH:30 y HH:38 ET.
    """
    now = now_ny()

    if now.weekday() >= 5:
        return False, None

    if 30 <= now.minute <= 30 + CANDLE_TOLERANCE_MIN:
        closed_at   = now.replace(minute=30, second=0, microsecond=0)
        first_close = now.replace(hour=10, minute=30, second=0, microsecond=0)
        last_close  = now.replace(hour=16, minute=30, second=0, microsecond=0)
        if first_close <= closed_at <= last_close:
            return True, closed_at

    return False, None


def next_candle_close_ar() -> str:
    """
    Retorna la próxima hora de cierre de vela en hora Argentina.
    Considera correctamente si el mercado está cerrado o es fin de semana.
    """
    now = now_ny()

    market_close_today = now.replace(
        hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0
    )

    # Calcular próximo cierre de vela en ET
    if is_market_open():
        if now.minute < 30:
            # Próximo cierre: esta misma hora a los :30
            next_et = now.replace(minute=30, second=0, microsecond=0)
        else:
            # Ya pasó el :30 de esta hora → próximo es hora siguiente a los :30
            next_et = (now + timedelta(hours=1)).replace(minute=30, second=0, microsecond=0)

        # Si el próximo cierre supera el cierre del mercado de hoy
        if next_et > market_close_today:
            next_et = _next_business_day_open(now)
    else:
        # Mercado cerrado → próximo día hábil a las 10:30 ET (primera vela)
        next_et = _next_business_day_open(now)

    # Convertir a hora Argentina
    next_ar  = next_et.astimezone(TZ_AR)
    dia_en   = next_ar.strftime("%A")
    dia_es   = DIAS_ES.get(dia_en, dia_en)
    return f"{next_ar.strftime('%H:%M')} del {dia_es} {next_ar.strftime('%d/%m')}"


def _next_business_day_open(from_dt: datetime) -> datetime:
    """Retorna el próximo día hábil a las 10:30 ET (primera vela)."""
    candidate = from_dt
    for _ in range(7):
        candidate = candidate + timedelta(days=1)
        if candidate.weekday() < 5:
            return candidate.replace(hour=10, minute=30, second=0, microsecond=0)
    # fallback
    return from_dt.replace(hour=10, minute=30, second=0, microsecond=0)


# ══════════════════════════════════════════════════════
#  📈  YAHOO FINANCE
# ══════════════════════════════════════════════════════

def yahoo_ticker(ticker: str, market: str) -> str:
    """BYMA Argentina usa sufijo .BA en Yahoo Finance."""
    return f"{ticker}.BA" if market == "arg" else ticker


def get_last_closed_candle(ticker: str, market: str) -> dict | None:
    """
    Obtiene la última vela de 1h CERRADA de Yahoo Finance.

    IMPORTANTE: Yahoo Finance devuelve el timestamp del INICIO de la vela.
    Se suma 1 hora para mostrar el timestamp de CIERRE real.

    Ejemplo: vela que abre 15:30 ET y cierra 16:30 ET
             Yahoo devuelve: 15:30 ET
             Mostramos:      16:30 ET  ← cierre real
    """
    yf_ticker = yahoo_ticker(ticker, market)
    try:
        url     = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        params  = {"interval": "1h", "range": "5d"}
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data    = resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        chart      = result[0]
        meta       = chart.get("meta", {})
        timestamps = chart.get("timestamp", [])
        quotes     = chart.get("indicators", {}).get("quote", [{}])[0]

        if not timestamps:
            return None

        closes  = quotes.get("close",  [])
        opens   = quotes.get("open",   [])
        highs   = quotes.get("high",   [])
        lows    = quotes.get("low",    [])
        volumes = quotes.get("volume", [])

        # Buscar la última vela con datos completos
        for i in range(len(timestamps) - 1, -1, -1):
            if closes[i] is not None and opens[i] is not None:
                # Yahoo devuelve inicio de vela → sumar 1h para mostrar cierre
                ts_open  = datetime.fromtimestamp(timestamps[i], tz=TZ_NY)
                ts_close = ts_open + timedelta(hours=1)
                ts_ar    = ts_close.astimezone(TZ_AR)

                return {
                    "ticker":       ticker,
                    "market":       market,
                    "currency":     "ARS" if market == "arg" else meta.get("currency", "USD"),
                    "open":         float(opens[i]),
                    "high":         float(highs[i]),
                    "low":          float(lows[i]),
                    "close":        float(closes[i]),
                    "volume":       int(volumes[i]) if volumes[i] else 0,
                    # Mostrar hora de cierre en ET y AR
                    "timestamp":    ts_close.strftime("%d/%m %H:%M ET"),
                    "timestamp_ar": ts_ar.strftime("%d/%m %H:%M AR"),
                }
        return None

    except Exception as e:
        print(f"⚠️  Yahoo Finance error ({yf_ticker}): {e}")
        return None


def get_current_price(ticker: str, market: str) -> dict | None:
    """Precio actual desde Yahoo Finance."""
    yf_ticker = yahoo_ticker(ticker, market)
    try:
        url     = f"https://query1.finance.yahoo.com/v8/finance/chart/{yf_ticker}"
        params  = {"interval": "1m", "range": "1d"}
        headers = {"User-Agent": "Mozilla/5.0"}
        resp    = requests.get(url, params=params, headers=headers, timeout=15)
        resp.raise_for_status()
        data    = resp.json()

        result = data.get("chart", {}).get("result", [])
        if not result:
            return None

        meta   = result[0].get("meta", {})
        price  = meta.get("regularMarketPrice", 0)
        prev   = meta.get("chartPreviousClose", 0) or meta.get("previousClose", 0)
        change = price - prev if prev else 0
        pct    = (change / prev * 100) if prev else 0

        return {
            "ticker":   ticker,
            "market":   market,
            "currency": "ARS" if market == "arg" else meta.get("currency", "USD"),
            "price":    float(price),
            "change":   float(change),
            "pct":      float(pct),
        }
    except Exception as e:
        print(f"⚠️  Yahoo Finance precio error ({yf_ticker}): {e}")
        return None


def detect_market(ticker: str) -> str:
    """Detecta mercado por ticker conocido."""
    AR_TICKERS = {
        "GGAL","YPFD","PAMP","TXAR","ALUA","BBAR","BMA","BYMA",
        "CEPU","CRES","CVH","EDN","HARG","LOMA","METR","MIRG",
        "MOLI","SUPV","TECO2","TGNO4","TGSU2","TRAN","VALO","VIST",
        "IRSA","CADO","GBAN","BOLT","COME","DGCU2","FRAN","GCLA",
        "INVJ","LONG","PATA","RICH","SEMI"
    }
    return "arg" if ticker.upper().replace(".BA", "") in AR_TICKERS else "usa"


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
#  💬  FORMATOS
# ══════════════════════════════════════════════════════

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


def format_alerta_disparada(alert: dict, candle: dict) -> str:
    condition = alert["condition"]
    ticker    = alert["ticker"]
    target    = float(alert["target"])
    cur       = candle["currency"]
    aid       = alert.get("id", "?")
    close     = candle["close"]
    accion    = f"{ticker} superó tu objetivo" if condition == "mayor" else f"{ticker} bajó a tu objetivo"
    emoji     = "📈" if condition == "mayor" else "📉"

    return (
        f"🚨 ALERTA #{aid} DISPARADA!\n"
        f"{'─' * 24}\n\n"
        f"{emoji} {accion}\n\n"
        f"🏛 Mercado:         {market_label(candle['market'])}\n"
        f"🎯 Tu objetivo:     {condition} a {fmt_price(target, cur)}\n"
        f"🕯 Cierre de vela:  {fmt_price(close, cur)}\n"
        f"📊 High / Low:      {fmt_price(candle['high'], cur)} / {fmt_price(candle['low'], cur)}\n"
        f"📋 Open:            {fmt_price(candle['open'], cur)}\n"
        f"📦 Volumen:         {candle['volume']:,}\n"
        f"🕐 Vela cerrada:    {candle['timestamp']} / {candle['timestamp_ar']}\n\n"
        f"⏰ {datetime.now(TZ_NY).strftime('%d/%m/%Y %H:%M ET')} / "
        f"{datetime.now(TZ_AR).strftime('%H:%M AR')}"
    )


AYUDA = (
    "📈 Bot de Alertas de Acciones\n\n"
    "Las alertas se disparan al CIERRE\n"
    "de cada vela de 1 hora (horario NYSE)\n\n"
    "🕯 Cierres de vela (hora Argentina):\n"
    "   Verano USA:   11:30, 12:30 ... 17:30\n"
    "   Invierno USA: 12:30, 13:30 ... 18:30\n\n"
    "Comandos:\n\n"
    "🔔 /alerta GGAL menor 1500\n"
    "🔔 /alerta AAPL mayor 210\n"
    "💰 /precio GGAL\n"
    "📋 /lista\n"
    "🗑 /borrar #2\n"
    "🗑 /borrar GGAL\n"
    "🗑 /borrar all\n"
    "❓ /ayuda\n\n"
    "Mercados: 🇦🇷 BYMA · 🇺🇸 NYSE/NASDAQ\n"
    "DST automático 🔄"
)


# ══════════════════════════════════════════════════════
#  📲  PROCESAR MENSAJES DE TELEGRAM
# ══════════════════════════════════════════════════════

def process_updates():
    offset  = int(OFFSET_FILE.read_text()) if OFFSET_FILE.exists() else 0
    updates = get_updates(offset)

    if not updates:
        print("   Sin mensajes nuevos.")
        return

    alerts     = load_alerts()
    new_offset = offset

    for update in updates:
        new_offset = update["update_id"] + 1
        # Guardar offset INMEDIATAMENTE antes de procesar
        # Si el workflow falla a mitad, no reprocesa el mismo mensaje
        OFFSET_FILE.write_text(str(new_offset))

        # ── Callback de botón ──
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

                aid = next_id(alerts)
                alerts.append({
                    "id":        aid,
                    "ticker":    ticker,
                    "condition": condition,
                    "target":    target,
                    "market":    market,
                    "triggered": False,
                    "created":   datetime.now(TZ_NY).strftime("%d/%m %H:%M ET"),
                    "chat_id":   chat_id,
                })

                cur     = "ARS" if market == "arg" else "USD"
                emoji   = "📈" if condition == "mayor" else "📉"
                proximo = next_candle_close_ar()
                answer_callback(cb_id, f"✅ Alerta #{aid} guardada!")
                edit_message(chat_id, msg_id,
                    f"✅ Alerta #{aid} guardada!\n\n"
                    f"{emoji} {ticker} — {market_label(market)}\n"
                    f"Avisame cuando vela 1h {condition} a {fmt_price(target, cur)}\n\n"
                    f"⏰ Próximo chequeo: {proximo}\n\n"
                    f"Te aviso si se dispara 🔔"
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

        # ── /alerta ──
        if cmd == "/alerta":
            if len(parts) != 4:
                send_telegram(
                    "❌ Formato correcto:\n\n"
                    "/alerta GGAL menor 1500\n"
                    "/alerta AAPL mayor 210",
                    chat_id
                )
                continue

            ticker    = parts[1].upper().replace(".BA", "")
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

            market = detect_market(ticker)
            candle = get_last_closed_candle(ticker, market)

            if not candle:
                other  = "usa" if market == "arg" else "arg"
                candle2 = get_last_closed_candle(ticker, other)
                if candle2:
                    buttons = [
                        [{"text": "🇦🇷 BYMA (pesos)",       "callback_data": f"market:arg:{ticker}:{target}:{condition}"}],
                        [{"text": "🇺🇸 NYSE/ADR (dólares)", "callback_data": f"market:usa:{ticker}:{target}:{condition}"}],
                    ]
                    send_telegram(
                        f"📊 '{ticker}' puede cotizar en varios mercados.\n"
                        f"¿En cuál querés monitorear el precio?",
                        chat_id,
                        reply_markup={"inline_keyboard": buttons}
                    )
                else:
                    send_telegram(
                        f"⚠️ No encontré '{ticker}' en Yahoo Finance.\n\n"
                        f"Ejemplos:\n"
                        f"🇦🇷 GGAL, YPFD, PAMP, BMA\n"
                        f"🇺🇸 AAPL, MSFT, TSLA, NVDA",
                        chat_id
                    )
                continue

            aid     = next_id(alerts)
            cur     = candle["currency"]
            emoji   = "📈" if condition == "mayor" else "📉"
            proximo = next_candle_close_ar()

            alerts.append({
                "id":        aid,
                "ticker":    ticker,
                "condition": condition,
                "target":    target,
                "market":    market,
                "triggered": False,
                "created":   datetime.now(TZ_NY).strftime("%d/%m %H:%M ET"),
                "chat_id":   chat_id,
            })

            send_telegram(
                f"✅ Alerta #{aid} guardada!\n\n"
                f"{emoji} {ticker} — {market_label(market)}\n"
                f"Avisame cuando vela 1h {condition} a {fmt_price(target, cur)}\n\n"
                f"🕯 Última vela cerrada: {fmt_price(candle['close'], cur)}\n"
                f"   {candle['timestamp']} / {candle['timestamp_ar']}\n\n"
                f"⏰ Próximo chequeo: {proximo}\n\n"
                f"Te aviso si se dispara 🔔",
                chat_id
            )
            print(f"   ✅ Alerta #{aid}: {ticker} {condition} {target} en {market}")

        # ── /precio ──
        elif cmd == "/precio":
            if len(parts) != 2:
                send_telegram("❌ Uso: /precio GGAL", chat_id)
                continue

            ticker = parts[1].upper().replace(".BA", "")
            market = detect_market(ticker)
            price  = get_current_price(ticker, market)
            candle = get_last_closed_candle(ticker, market)

            if not price and not candle:
                send_telegram(f"❌ No encontré '{ticker}' en Yahoo Finance.", chat_id)
                continue

            cur   = "ARS" if market == "arg" else "USD"
            p     = price["price"] if price else 0
            pct   = price["pct"]   if price else 0
            sign  = "+" if pct >= 0 else ""
            arrow = "🟢 ▲" if pct >= 0 else "🔴 ▼"

            msg_text = (
                f"💰 {ticker} — {market_label(market)}\n"
                f"{'─' * 24}\n\n"
                f"Precio actual:  {fmt_price(p, cur)}\n"
                f"Cambio hoy:     {arrow} {sign}{pct:.2f}%\n"
            )

            if candle:
                msg_text += (
                    f"\n🕯 Última vela 1h cerrada\n"
                    f"   {candle['timestamp']} / {candle['timestamp_ar']}\n"
                    f"Cierre: {fmt_price(candle['close'], cur)}\n"
                    f"High:   {fmt_price(candle['high'],  cur)}\n"
                    f"Low:    {fmt_price(candle['low'],   cur)}\n"
                    f"Open:   {fmt_price(candle['open'],  cur)}\n"
                )

            now_et = datetime.now(TZ_NY)
            now_ar = datetime.now(TZ_AR)
            msg_text += f"\n🕐 {now_et.strftime('%H:%M ET')} / {now_ar.strftime('%H:%M AR')}"
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
                    send_telegram("❌ Número inválido. Ej: /borrar #2", chat_id)
                    continue
                match = next((a for a in alerts if a.get("id") == aid), None)
                if not match:
                    send_telegram(f"❌ No encontré la alerta #{aid}.\nUsá /lista para ver tus alertas.", chat_id)
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
                    send_telegram(f"❌ No encontré alertas para '{ticker}'.\nUsá /lista.", chat_id)

        elif cmd in ("/ayuda", "/start"):
            send_telegram(AYUDA, chat_id)

        else:
            send_telegram(
                "❓ No entendí ese comando.\n\n"
                "Escribí /ayuda para ver los comandos disponibles.",
                chat_id
            )

    save_alerts(alerts)
    print(f"✅ {len(updates)} update(s) procesado(s).")


# ══════════════════════════════════════════════════════
#  🔔  CHEQUEAR CIERRES DE VELAS
# ══════════════════════════════════════════════════════

def check_candle_closes():
    now_et        = now_ny()
    now_ar        = datetime.now(TZ_AR)
    closed, closed_at = candle_just_closed()

    print(f"\n🕐 ET: {now_et.strftime('%H:%M')}  |  AR: {now_ar.strftime('%H:%M')}")

    if not closed:
        if now_et.weekday() >= 5:
            print("   💤 Fin de semana. Mercado cerrado.")
        elif not is_market_open():
            print("   💤 Mercado cerrado.")
        else:
            print("   ⏳ Esperando cierre de vela (cierran a los :30 ET)")
        return

    closed_ar = closed_at.astimezone(TZ_AR)
    print(
        f"   🕯 Vela cerrada: {closed_at.strftime('%H:%M ET')} / "
        f"{closed_ar.strftime('%H:%M AR')} — chequeando alertas..."
    )

    alerts     = load_alerts()
    pendientes = [a for a in alerts if not a.get("triggered", False)]

    if not pendientes:
        print("   ✅ No hay alertas pendientes.")
        return

    print(f"   📋 {len(pendientes)} alerta(s) activa(s)")
    fired_count = 0

    for alert in alerts:
        if alert.get("triggered"):
            continue

        ticker = alert["ticker"]
        market = alert.get("market", detect_market(ticker))
        candle = get_last_closed_candle(ticker, market)

        if not candle:
            print(f"   ⚠️  Sin datos de vela para {ticker}")
            continue

        close     = candle["close"]
        condition = alert["condition"]
        target    = float(alert["target"])
        cur       = candle["currency"]

        print(
            f"   #{alert.get('id','?')} {ticker}: "
            f"cierre {fmt_price(close, cur)} — "
            f"objetivo {condition} {fmt_price(target, cur)}"
        )

        fired = (condition == "mayor" and close >= target) or \
                (condition == "menor" and close <= target)

        if fired:
            alert["triggered"]    = True
            alert["triggered_at"] = now_et.strftime("%d/%m/%Y %H:%M ET")
            dest = alert.get("chat_id", CHAT_ID)
            msg  = format_alerta_disparada(alert, candle)
            ok   = send_telegram(msg, dest)
            if ok:
                print(f"   🚨 DISPARADA #{alert.get('id')}: {ticker} cierre {fmt_price(close, cur)}")
                fired_count += 1

    save_alerts(alerts)
    print(f"\n   ✅ Listo. {fired_count} alerta(s) disparada(s).")


# ══════════════════════════════════════════════════════
#  🚀  MAIN
# ══════════════════════════════════════════════════════

if __name__ == "__main__":
    if not BOT_TOKEN or not CHAT_ID:
        print("❌ BOT_TOKEN o CHAT_ID no configurados")
        sys.exit(1)

    now_et = now_ny()
    now_ar = datetime.now(TZ_AR)

    print(f"🕐 ET: {now_et.strftime('%d/%m/%Y %H:%M ET')}")
    print(f"🕐 AR: {now_ar.strftime('%d/%m/%Y %H:%M AR')}")
    print(f"🏛 Mercado NYSE: {'ABIERTO ✅' if is_market_open() else 'CERRADO 💤'}\n")

    print("📲 Procesando mensajes de Telegram...")
    process_updates()

    print("\n🔔 Chequeando cierres de velas...")
    check_candle_closes()
