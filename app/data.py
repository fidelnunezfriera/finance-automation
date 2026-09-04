"""
Data loading layer for the finance dashboard.
All Google Sheets and yfinance fetches live here.
"""

import json
import logging
import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st
import yaml
import gspread
from google.oauth2.service_account import Credentials
import yfinance as yf

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from schema import POSITIONS_COLUMNS, TRANSACTIONS_COLUMNS  # noqa: E402

log = logging.getLogger(__name__)


# ── Google Sheets connection ──────────────────────────────────────────────────

class SheetConfigError(RuntimeError):
    """Configuración incompleta o incorrecta, con un mensaje para el usuario.

    El dashboard la captura y la muestra tal cual, así que el texto debe
    explicar qué está mal y cómo arreglarlo, no dar detalles técnicos.
    """


@st.cache_resource
def _spreadsheet() -> gspread.Spreadsheet:
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        raise SheetConfigError(
            "No existe `config.yaml` en la raíz del proyecto.\n\n"
            "Ejecuta `setup.bat`, o copia `config.example.yaml` a `config.yaml` "
            "y rellena tu `spreadsheet_id`."
        )

    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}

    try:
        sa_rel = cfg["credentials"]["gdrive_sa"]
        sheet_id = cfg["google_sheets"]["spreadsheet_id"]
    except (KeyError, TypeError) as exc:
        raise SheetConfigError(
            f"Falta la clave {exc} en `config.yaml`.\n\n"
            "Compara tu fichero con `config.example.yaml`: hacen falta "
            "`google_sheets.spreadsheet_id` y `credentials.gdrive_sa`."
        ) from exc

    if not sheet_id or "TU_" in str(sheet_id).upper():
        raise SheetConfigError(
            "`google_sheets.spreadsheet_id` en `config.yaml` sigue con el valor "
            "de la plantilla.\n\n"
            "Cópialo de la URL de tu hoja: "
            "`docs.google.com/spreadsheets/d/`**`ESTE_TROZO`**`/edit`"
        )

    sa_path = ROOT / sa_rel
    if not sa_path.exists():
        raise SheetConfigError(
            f"No se encuentra el fichero de credenciales `{sa_rel}`.\n\n"
            "Descarga la clave JSON de tu cuenta de servicio de Google y "
            "guárdala ahí. El apartado 5 de SETUP.md lo explica paso a paso."
        )

    creds = Credentials.from_service_account_file(
        str(sa_path),
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )

    try:
        return gspread.authorize(creds).open_by_key(sheet_id)
    except gspread.SpreadsheetNotFound as exc:
        raise SheetConfigError(
            f"No existe ninguna hoja con el ID `{sheet_id}`, o tu cuenta de "
            "servicio no tiene acceso.\n\n"
            "Comprueba el `spreadsheet_id` y, en tu Google Sheet, pulsa "
            "**Compartir** y da permiso de **Editor** al `client_email` que "
            "aparece en tu JSON de credenciales."
        ) from exc
    except gspread.exceptions.APIError as exc:
        raise SheetConfigError(
            f"Google ha rechazado la petición: {exc}\n\n"
            "Lo habitual es que falte habilitar la **Google Sheets API** en tu "
            "proyecto de Google Cloud, o que la hoja no esté compartida con el "
            "`client_email` de la cuenta de servicio."
        ) from exc


@st.cache_data
def account_holder_name() -> str:
    """Nombre del titular configurado en config.yaml, o "" si no está.

    Solo para mostrarlo en el dashboard -- la lógica que lo consume de
    verdad vive en pipeline/convert_pytr_to_clean.py:infer_type(), donde un
    ingreso cuya nota contiene este nombre se clasifica como `deposit`
    (traspaso entre cuentas propias) en vez de `income`.
    """
    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        return ""
    with open(config_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f) or {}
    return str(cfg.get("pipeline", {}).get("account_holder_name", "")).strip()


def _worksheet(title: str):
    """Devuelve una pestaña, con un error claro si no existe."""
    try:
        return _spreadsheet().worksheet(title)
    except gspread.WorksheetNotFound as exc:
        raise SheetConfigError(
            f"Tu hoja de cálculo no tiene ninguna pestaña llamada `{title}`.\n\n"
            "Los nombres distinguen mayúsculas y espacios. El apartado 4 de "
            "SETUP.md lista las pestañas necesarias y sus cabeceras."
        ) from exc


def _require_columns(df: pd.DataFrame, expected: list[str], tab: str) -> None:
    """Valida la fila de cabecera de una pestaña."""
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise SheetConfigError(
            f"A la pestaña `{tab}` le faltan estas columnas: "
            f"`{'`, `'.join(missing)}`.\n\n"
            "Revisa que la primera fila tenga exactamente las cabeceras que "
            "lista SETUP.md, escritas igual y sin espacios de más."
        )


# ── Sheet loaders ─────────────────────────────────────────────────────────────

_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


def _a_numero(valor) -> float | None:
    """Convierte una celda en número, o None si no lo es.

    Los números llegan ya como tales al pedir los valores sin formatear. El
    resto del cuerpo es para pestañas que el usuario construye a mano y que
    pueden traer texto formateado con separadores de su idioma.

    Con los dos separadores presentes, manda el último: en `1.234,56` la coma
    es el decimal, y en `1,234.56` lo es el punto.
    """
    if isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return float(valor)

    texto = str(valor).strip()
    if not texto:
        return None
    texto = re.sub(r"[^\d,.\-]", "", texto)   # fuera símbolos de moneda y espacios
    if not texto:
        return None

    if "," in texto and "." in texto:
        decimal = max(texto.rfind(","), texto.rfind("."))
        miles = "." if texto[decimal] == "," else ","
        texto = texto.replace(miles, "")
    texto = texto.replace(",", ".")

    try:
        return float(texto)
    except ValueError:
        return None


def _a_bool(valor) -> bool:
    """Convierte una celda en booleano.

    `derive_positions.py` escribe los textos "TRUE"/"FALSE", pero Google los
    interpreta y guarda como casillas booleanas de verdad. Al pedir los valores
    sin formatear vuelven como `True`/`False` de Python, no como texto — y una
    columna donde todas las filas lo son deja de admitir el accesor `.str`.

    Se admiten las dos formas, y cualquier otra cosa cuenta como falso.
    """
    if isinstance(valor, bool):
        return valor
    if isinstance(valor, (int, float)):
        return bool(valor)
    return str(valor).strip().lower() in ("true", "verdadero", "si", "sí", "yes", "1", "x")


def _fix_excel_serial(series: pd.Series) -> pd.Series:
    """Convert Excel serial date numbers to ISO strings where needed.

    gspread returns unformatted numeric cells as integers (e.g. 45849).
    Any value that is purely numeric is treated as an Excel serial date;
    everything else is left unchanged for pd.to_datetime to parse normally.
    """
    def _convert(v):
        if v is None or v == "":
            return v
        try:
            serial = float(v)
        except (ValueError, TypeError):
            return v          # texto: que lo interprete pd.to_datetime

        # Rango plausible de fecha: 1 = 1900-01-01, 80000 = año 2118.
        # Se aceptan seriales con decimales, que es como Sheets guarda una
        # fecha con hora. Si se dejaran pasar, pd.to_datetime los tomaría por
        # nanosegundos desde 1970 y todas las fechas caerían silenciosamente
        # en 1970-01-01.
        if 1 < serial < 80000:
            ts = (_EXCEL_EPOCH + pd.Timedelta(days=serial)).round("s")
            # Sin hora se devuelve sólo la fecha, para no mezclar formatos con
            # las celdas que ya vienen como texto "YYYY-MM-DD".
            if ts.normalize() == ts:
                return str(ts.date())
            return ts.isoformat()
        return v

    return series.map(_convert)


# El esquema se usa sólo cuando la hoja está vacía: gspread devuelve [] si sólo
# hay cabecera, y pd.DataFrame([]) no tiene ninguna columna, así que todo lo de
# abajo reventaba con KeyError en una hoja recién creada.
_TX_COLUMNS = TRANSACTIONS_COLUMNS
_POS_COLUMNS = POSITIONS_COLUMNS


# Se piden los valores SIN FORMATEAR. Con los formateados, Google devuelve lo
# que se ve en pantalla, que depende del idioma de la hoja: en español un
# importe llega como "-2,85". Y gspread, al convertir a número, borra las comas
# dando por hecho que separan miles (`utils.numericise`), así que ese importe se
# volvía -285. Sin formatear llega el número tal cual y no hay nada que
# interpretar. Es el mismo problema de idioma que ya apareció al escribir.
_SIN_FORMATO = gspread.utils.ValueRenderOption.unformatted


@st.cache_data(ttl=300)
def load_transactions() -> pd.DataFrame:
    records = _worksheet("transactions").get_all_records(
        value_render_option=_SIN_FORMATO)
    df = pd.DataFrame(records) if records else pd.DataFrame(columns=_TX_COLUMNS)
    _require_columns(df, _TX_COLUMNS, "transactions")
    # format="mixed" interpreta cada celda por separado. Sin él, pandas deduce
    # un único formato de la primera fila y convierte en NaT todas las que no
    # encajen, que es fácil aquí: la misma columna puede traer texto y fechas
    # con hora según cómo tenga configurada la hoja cada usuario.
    df["date"]           = pd.to_datetime(_fix_excel_serial(df["date"]),
                                          errors="coerce", format="mixed")
    # utc=True normaliza todo a tz-aware UTC, tanto si la celda ya traía zona
    # horaria (texto ISO con "Z", como exporta pytr) como si no (una fecha de
    # Sheets autoconvertida a serial pierde la zona al pasar por
    # _fix_excel_serial). Sin esto, una sola fila sin zona horaria basta para
    # que la comparación con un cutoff tz-aware reviente la página Dashboard
    # con un TypeError en vez de fallar solo esa fila.
    df["datetime"]       = pd.to_datetime(_fix_excel_serial(df["datetime"]),
                                          errors="coerce", format="mixed",
                                          utc=True)
    df["amount"]         = pd.to_numeric(df["amount"], errors="coerce").fillna(0.0)
    df["year_month"]     = df["date"].dt.to_period("M")
    # astype(str) antes del accesor: sin formatear, una columna cuyas celdas
    # sean todas numéricas o booleanas llega con ese dtype y `.str` falla.
    df["event_domain_l"] = df["event_domain"].astype(str).str.strip().str.lower()
    df["tipus_norm"]     = df["tipus"].astype(str).str.strip().str.upper()
    df["rule_confidence"]= pd.to_numeric(df["rule_confidence"], errors="coerce").fillna(0.0)
    return df


@st.cache_data(ttl=300)
def load_positions() -> pd.DataFrame:
    records = _worksheet("positions").get_all_records(
        value_render_option=_SIN_FORMATO)
    df = pd.DataFrame(records) if records else pd.DataFrame(columns=_POS_COLUMNS)
    _require_columns(df, _POS_COLUMNS, "positions")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce").fillna(0.0)
    # Se normalizan a booleanos aquí para que quien las consuma no tenga que
    # saber si la hoja las guardó como texto o como casilla.
    for col in ("anomaly", "adjusted"):
        if col in df.columns:
            df[col] = df[col].map(_a_bool)
    return df


@st.cache_data(ttl=300)
def load_category_month() -> pd.DataFrame:
    """Melt the pivot tab into (category, year_month, amount) long format.

    Esta pestaña es opcional (ver SETUP.md): si no existe, el gráfico de
    categorías sale vacío en vez de tumbar el dashboard.
    """
    try:
        # Sin formatear, igual que las otras pestañas: los importes llegan como
        # números y no hay que adivinar si la coma separa miles o decimales.
        rows = _worksheet("display_category_month").get_values(
            value_render_option=_SIN_FORMATO)
    except SheetConfigError:
        log.info("Pestaña display_category_month ausente; se omite el gráfico.")
        rows = []

    if not rows:
        return pd.DataFrame(columns=["category", "year_month", "amount"])

    # Las cabeceras son los meses. Si la tabla dinámica agrupa por una columna
    # de fecha, sin formatear llegan como seriales de Excel; si agrupa por
    # `year_month`, llegan como texto "2026-08". Se admiten las dos.
    headers = _fix_excel_serial(pd.Series(
        [str(h).strip() for h in rows[0]])).tolist()

    records = []
    for row in rows[1:]:
        padded = list(row) + [""] * (len(headers) - len(row))
        cat = str(padded[0]).strip()
        if not cat:
            continue
        for i, col in enumerate(headers):
            if col in ("category", "null", ""):
                continue
            val = _a_numero(padded[i])
            if val is None:
                continue
            try:
                dt = pd.to_datetime(col)
            except (ValueError, TypeError):
                continue
            records.append({"category": cat, "year_month": dt.to_period("M"),
                            "amount": val})

    return pd.DataFrame(records)


# ── Price fetching ────────────────────────────────────────────────────────────

@st.cache_data(ttl=60)
def fx_rate(from_ccy: str) -> float:
    """Return rate: 1 from_ccy = ? EUR. Returns 1.0 on failure."""
    if from_ccy == "EUR":
        return 1.0
    try:
        t = yf.Ticker(f"{from_ccy}EUR=X")
        p = t.fast_info.last_price
        if p:
            return float(p)
    except Exception:
        pass
    # Fallback: try inverse
    try:
        t = yf.Ticker(f"EUR{from_ccy}=X")
        p = t.fast_info.last_price
        if p:
            return 1.0 / float(p)
    except Exception:
        pass
    log.warning("Could not fetch FX rate %s/EUR", from_ccy)
    return 1.0


def _ticker_price_eur(symbol: str) -> float | None:
    try:
        t     = yf.Ticker(symbol)
        fi    = t.fast_info
        price = fi.last_price
        if not price:
            hist = t.history(period="2d")
            if hist.empty:
                return None
            price = float(hist["Close"].iloc[-1])
        ccy = getattr(fi, "currency", "EUR") or "EUR"
        # GBp = British pence (1/100 of GBP) — normalise before converting
        if ccy == "GBp":
            return float(price) / 100.0 * fx_rate("GBP")
        return float(price) * fx_rate(ccy)
    except Exception:
        return None


def _crypto_ticker_from_name(name: str) -> str | None:
    """
    TradeRepublic crypto ISINs (XF000*) are internal and unknown to Yahoo Finance.
    Derive the yfinance ticker from the asset name: 'Bitcoin' → 'BTC-EUR'.
    Uses yfinance Search with the name + 'EUR' to find the crypto pair.
    """
    try:
        for q in (yf.Search(f"{name} EUR", max_results=10).quotes or []):
            sym = q.get("symbol", "")
            # Accept only crypto pairs ending in -EUR
            if sym.endswith("-EUR") and q.get("quoteType", "") in ("CRYPTOCURRENCY", ""):
                return sym
        # Second pass: less strict — any result whose symbol ends in -EUR
        for q in (yf.Search(name, max_results=10).quotes or []):
            sym = q.get("symbol", "")
            if sym.endswith("-EUR"):
                return sym
    except Exception:
        pass
    return None


_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"

# securityType / securityType2 substrings that indicate fixed income
_BOND_KEYWORDS = {
    "bond", "fixed income", "govt", "muni", "agcy", "sovereign",
    "agency", "note", "tbill", "treasury", "structured", "corp",
}


@st.cache_data(ttl=86400)
def _openfigi_lookup(isin: str) -> dict | None:
    """Raw OpenFIGI mapping record for an ISIN (cached 24 h), or None."""
    try:
        resp = requests.post(
            _OPENFIGI_URL,
            json=[{"idType": "ID_ISIN", "idValue": isin}],
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        result = resp.json()[0]

        if "error" in result or not result.get("data"):
            log.info("OpenFIGI: no data for %s", isin)
            return None

        return result["data"][0]

    except Exception as exc:
        log.warning("OpenFIGI error for %s: %s", isin, exc)
        return None


def classify_isin(isin: str) -> str:
    """Return instrument class for an ISIN via OpenFIGI (cached 24 h).

    Returns one of: 'bond', 'etf', 'equity', 'crypto', 'unknown'.
    """
    rec = _openfigi_lookup(isin)
    if not rec:
        return "unknown"

    raw_type = (rec.get("securityType2") or rec.get("securityType") or "").strip().lower()
    log.info("OpenFIGI: %s → %r", isin, raw_type)

    if any(kw in raw_type for kw in _BOND_KEYWORDS):
        return "bond"
    if raw_type in {"etf", "etp", "exchange traded fund", "fund",
                    "open-end fund", "mutual fund"}:
        return "etf"
    if raw_type in {"common stock", "equity", "ordinary shares",
                    "stock", "reit", "preferred stock"}:
        return "equity"
    if "crypto" in raw_type or "digital" in raw_type:
        return "crypto"

    log.warning("OpenFIGI: unrecognised type %r for %s — treating as unknown", raw_type, isin)
    return "unknown"


@st.cache_data(ttl=3600)
def _resolve_symbol(isin: str, name: str) -> str | None:
    """Best-effort yfinance ticker symbol for an ISIN, or None.

    Strategy depends on OpenFIGI classification:
    - bond       → not applicable, caller uses quantity as nominal EUR value
    - crypto     → derive ticker from name (XF000* are TR-internal ISINs)
    - etf/equity/unknown → search by ISIN, then by broker name, then by
      OpenFIGI's official ticker/name (broker labels like "Physical Gold USD
      (Acc)" often don't match Yahoo's own naming — OpenFIGI's record does).
    """
    instrument = classify_isin(isin)
    if instrument == "bond":
        return None

    if instrument == "crypto" or isin.startswith("XF000"):
        sym = _crypto_ticker_from_name(name)
        if sym and _ticker_price_eur(sym) is not None:
            return sym

    rec = _openfigi_lookup(isin)
    queries = [isin, name]
    if rec:
        if rec.get("ticker"):
            queries.append(rec["ticker"])
        if rec.get("name"):
            queries.append(rec["name"])

    for query in queries:
        try:
            for q in (yf.Search(query, max_results=5).quotes or []):
                sym = q.get("symbol", "")
                if sym and _ticker_price_eur(sym) is not None:
                    return sym
        except Exception:
            pass

    return None


@st.cache_data(ttl=60)
def get_price_eur(isin: str, name: str) -> float | None:
    """Current price in EUR for one asset. Returns None if unavailable."""
    instrument = classify_isin(isin)
    if instrument == "bond":
        return None

    sym = _resolve_symbol(isin, name)
    if sym:
        p = _ticker_price_eur(sym)
        if p:
            return p

    log.warning("No price found: %s (%s) [%s]", isin, name, instrument)
    return None


@st.cache_data(ttl=60)
def get_all_prices(positions_key: tuple) -> dict[str, float | None]:
    """Fetch prices for a set of (isin, name) pairs."""
    return {isin: get_price_eur(isin, name) for isin, name in positions_key}


@st.cache_data(ttl=86400)
def historical_monthly_returns(isin: str, name: str) -> pd.Series | None:
    """Monthly % returns from up to 10y of price history for one asset, or
    None if unavailable (e.g. bonds, or an asset yfinance has no history
    for). Index is a monthly PeriodIndex so series from different assets
    can be aligned by calendar month.

    10y is a deliberate middle ground: long enough to include at least one
    full bear/bull cycle (2020 crash, 2022 bear market) so a single recent
    regime doesn't dominate the estimate, but not so long (`period="max"`)
    that it drags in market/rate regimes from decades ago that are less
    representative of near-future returns. Younger assets (crypto, recently
    listed ETFs) are unaffected — yfinance just returns what's available.
    """
    sym = _resolve_symbol(isin, name)
    if not sym:
        return None
    try:
        hist = yf.Ticker(sym).history(period="10y", interval="1mo")["Close"].dropna()
        if len(hist) < 12:
            return None
        returns = hist.pct_change().dropna()
        returns.index = returns.index.tz_localize(None).to_period("M")
        return returns
    except Exception as exc:
        log.warning("Historical returns error for %s (%s) [%s]: %s", isin, name, sym, exc)
        return None


# ── Derived helpers ───────────────────────────────────────────────────────────

def avg_buy_prices(tx: pd.DataFrame) -> dict[str, float]:
    """Average cost per share (EUR) per ISIN from buy transactions."""
    cost: dict[str, list] = {}
    for _, row in tx[tx["type"] == "buy"].iterrows():
        rn = row.get("rule_notes", "")
        if not rn:
            continue
        try:
            d      = json.loads(rn)
            isin   = d.get("isin", "")
            shares = float(d.get("shares", 0))
            amount = abs(float(row["amount"]))
            if isin and shares > 0:
                cost.setdefault(isin, [0.0, 0.0])
                cost[isin][0] += amount
                cost[isin][1] += shares
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    return {
        isin: v[0] / v[1]
        for isin, v in cost.items()
        if v[1] > 0
    }


def monthly_investments(tx: pd.DataFrame) -> pd.Series:
    """Net monthly amount invested (EUR): gross buys minus gross sells.

    Selling one asset to fund a purchase of another isn't new money — only
    the net cash flow into the portfolio counts as a real contribution.
    """
    buys  = tx[tx["type"] == "buy"].groupby("year_month")["amount"].apply(lambda s: s.abs().sum())
    sells = tx[tx["type"] == "sell"].groupby("year_month")["amount"].sum()
    net = buys.subtract(sells, fill_value=0.0).sort_index()
    net.name = "invested"
    return net


def monthly_expenses(tx: pd.DataFrame, categoria: str | None = None,
                     subcategoria: str | None = None) -> pd.Series:
    """Gasto mensual, en positivo, listo para modelar.

    Los meses sin gasto se rellenan con 0 €: si se dejaran fuera, la posición
    en la serie dejaría de significar «mes» y cualquier ajuste mediría otra
    cosa. Se quita el mes en curso, que va a medias.

    Es la misma serie que usa `pipeline/seleccionar_modelos.py` para decidir,
    y tiene que serlo: modelar sobre una serie distinta de la que se evaluó
    invalidaría la elección.
    """
    gastos = tx[(tx["event_domain_l"] == "cashflow") & (tx["amount"] < 0)]
    if gastos.empty:
        return pd.Series(dtype=float)

    # El rango lo marca SIEMPRE el gasto total, también para una categoría.
    # Si cada categoría empezara en su primer mes con gasto, la serie no
    # sería la misma sobre la que se decidió el modelo.
    meses = gastos["year_month"]
    rango = pd.period_range(meses.min(), meses.max(), freq="M")

    if categoria is not None:
        gastos = gastos[gastos["category"] == categoria]
    if subcategoria is not None:
        gastos = gastos[gastos["subcategory"] == subcategoria]
    if gastos.empty:
        return drop_incomplete_month(pd.Series(0.0, index=rango))

    serie = gastos["amount"].abs().groupby(gastos["year_month"]).sum()
    return drop_incomplete_month(serie.reindex(rango, fill_value=0.0))


def drop_incomplete_month(serie: pd.Series, hoy: pd.Timestamp | None = None) -> pd.Series:
    """Quita el mes en curso, que todavía no ha terminado.

    Un mes a medias no es comparable con los cerrados: recoge lo aportado
    hasta hoy, no lo que se aportará. Metido en la tendencia, cuenta como una
    caída y arrastra la pendiente hacia abajo — el día 3 del mes, hacia abajo
    del todo.

    Si al quitarlo no queda nada, se devuelve la serie tal cual: es preferible
    un dato parcial a ninguno.
    """
    if serie.empty:
        return serie

    actual = pd.Period(hoy or pd.Timestamp.now(), freq="M")
    if serie.index[-1] != actual:
        return serie

    recortada = serie.iloc[:-1]
    return recortada if len(recortada) else serie


# Ventana de historia que se mira, y con qué rapidez pierde peso lo antiguo.
#
# Se usan mínimos cuadrados ponderados con pesos que decaen exponencialmente,
# en vez de una ventana corta sin pesos. Una ventana corta obliga a elegir
# entre memoria y sesgo: con 12 meses la media es realista pero la pendiente
# sólo ve la meseta reciente; con 24 sin pesos, la pendiente es mejor pero la
# media se hunde al mezclar dos épocas distintas. Ponderando, la ventana puede
# ser larga y la memoria efectiva la fija la semivida.
#
# SEMIVIDA_MESES es cada cuántos meses el peso se parte por dos: con 4, el mes
# pasado vale 1, hace 4 meses 0,5, hace 8 meses 0,25, hace 2 años 0,03.
MESES_VENTANA = 24
SEMIVIDA_MESES = 4


# No hay tope a la tasa de crecimiento de las aportaciones.
#
# Lo hubo: ±10% mensual primero, ±2% después. Se quitó porque quien acota la
# proyección ahora es la amortiguación, no un recorte: el crecimiento se apaga
# y la aportación converge a una meseta en vez de dispararse.
#
# Queda una consecuencia que conviene tener presente. La amortiguación acota
# la FORMA, no el NIVEL: la meseta escala como (1+g)^(p/(1-p)), así que una
# tasa alta sigue dando una meseta alta —un 10% mensual la deja en 22 veces la
# aportación de partida—. Lo que protege de eso no es un tope, es que la
# meseta se enseña en pantalla y se ve el disparate.


# Cuánto persiste el crecimiento de las aportaciones al proyectar.
#
# Con 1.0 el crecimiento no se apaga nunca y la aportación se dispara: es lo
# que hacía antes. Con 0.97 el efecto se va agotando y la aportación tiende a
# una meseta, que es lo que muestran los datos —se sube de ritmo una vez y
# luego se sostiene— y lo que hace el modelo Holt amortiguado, el que el banco
# de evaluación elige una y otra vez.
PERSISTENCIA_CRECIMIENTO = 0.97


def aportaciones_proyectadas(base: float, tasa_mensual: float, meses: int,
                             persistencia: float = PERSISTENCIA_CRECIMIENTO
                             ) -> np.ndarray:
    """La aportación mes a mes, con el crecimiento amortiguado.

    Aplicar una tasa fija compuesta supone que cada mes subes el ritmo tanto
    como el anterior, para siempre. Nadie hace eso: se sube de nivel y se
    mantiene. Aquí el exponente no es el número de meses sino la suma
    amortiguada `p + p² + … + p^t`, que converge, así que la aportación tiende
    a una meseta en vez de a infinito.

    Con `persistencia = 1` se recupera el comportamiento anterior.
    """
    if meses <= 0:
        return np.array([], dtype=float)
    if not tasa_mensual:
        return np.full(meses, float(base))

    t = np.arange(1, meses + 1, dtype=float)
    if persistencia >= 1.0:
        exponente = t
    else:
        # Suma geométrica p + p^2 + ... + p^t
        exponente = persistencia * (1 - persistencia ** t) / (1 - persistencia)
    return float(base) * (1 + tasa_mensual) ** exponente


def _tasa_log_lineal(y: np.ndarray) -> float:
    """La tasa mensual compuesta que sale de un ajuste log-lineal ponderado."""
    from sklearn.linear_model import LinearRegression

    pos = y > 0
    if pos.sum() < 2:
        return 0.0
    X = np.arange(len(y)).reshape(-1, 1).astype(float)
    w = exponential_weights(len(y))
    modelo = LinearRegression().fit(X[pos], np.log(y[pos]), sample_weight=w[pos])
    return float(np.exp(modelo.coef_[0]) - 1)


def estimar_persistencia(serie: pd.Series,
                         candidatos: tuple[float, ...] = (
                             0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8,
                             0.9, 0.95, 0.97, 0.99, 1.0),
                         min_entrenamiento: int = 6) -> float:
    """Cuánto persiste el crecimiento, medido en vez de elegido.

    Era una constante, 0,97, y esa constante era el problema: fija el exponente
    de la meseta en 32, así que **amplifica cualquier tasa** por mucho que la
    tasa venga bien estimada. Una tasa del 10% con esa persistencia sigue
    dejando la meseta 22 veces por encima de la aportación actual.

    Aquí se prueba cada candidato con el mismo protocolo que el banco: desde
    cada origen se estima la tasa con lo anterior, se proyecta con esa
    persistencia y se compara con lo que pasó de verdad. Gana el que menos se
    equivoca. Si el histórico se ha aplanado —como pasa cuando alguien sube de
    ritmo una vez y se sostiene— sale una persistencia baja y la meseta cae
    cerca del nivel actual, sin que nadie tenga que recortar la tasa.

    La rejilla de candidatos sí está escrita a mano, pero eso es un espacio de
    búsqueda, no un valor elegido: quién gana lo decide la serie.
    """
    y = serie.values.astype(float)
    n = len(y)
    if n < min_entrenamiento + 2:
        return PERSISTENCIA_CRECIMIENTO

    mejor, mejor_error = PERSISTENCIA_CRECIMIENTO, float("inf")
    for p in candidatos:
        errores = []
        for t in range(min_entrenamiento, n):
            entrenamiento = y[:t]
            base = float(entrenamiento[-1])
            tasa = _tasa_log_lineal(entrenamiento)
            pred = aportaciones_proyectadas(base, tasa, 1, persistencia=p)
            if len(pred):
                errores.append(abs(float(pred[-1]) - y[t]))
        if errores:
            error = float(np.mean(errores))
            if error < mejor_error:
                mejor, mejor_error = p, error
    return mejor


def meseta_aportacion(base: float, tasa_mensual: float,
                      persistencia: float = PERSISTENCIA_CRECIMIENTO) -> float:
    """Dónde se estabiliza la aportación si el crecimiento se apaga.

    Es el límite de `aportaciones_proyectadas` cuando los meses tienden a
    infinito. Enseñarlo es lo que permite juzgar la suposición: «crece un 2%
    que se va apagando» no dice nada; «acaba en 1.430 €/mes» sí.
    """
    if persistencia >= 1.0:
        return float("inf")
    return float(base) * (1 + tasa_mensual) ** (persistencia / (1 - persistencia))


def tasa_anual(mensual: float) -> float:
    """La tasa mensual compuesta, en términos anuales.

    Existe porque «+3,858% mensual» no se lee como «+57% anual» en la cabeza
    de nadie, y es la segunda cifra la que deja ver si la suposición se
    sostiene.
    """
    return (1 + mensual) ** 12 - 1


def exponential_weights(n: int, semivida: float = SEMIVIDA_MESES) -> np.ndarray:
    """Pesos que decaen a la mitad cada `semivida` meses.

    El último elemento —el mes más reciente— pesa 1, y hacia atrás va
    decayendo. Sirven tanto para la media ponderada como para `sample_weight`
    de una regresión.
    """
    edad = np.arange(n - 1, -1, -1, dtype=float)
    return 0.5 ** (edad / float(semivida))


def contribution_window(serie: pd.Series, meses: int = MESES_VENTANA,
                        hoy: pd.Timestamp | None = None) -> pd.Series:
    """Los meses sobre los que se calculan la media y la tendencia.

    Tres cosas, en este orden:

    1. **Fuera el mes en curso**, que va a medias y se lee como un desplome.
    2. **Los últimos `meses` naturales**, contados hacia atrás desde el último
       mes cerrado — no desde la última compra. Si llevas tres meses sin
       invertir, eso tiene que notarse.
    3. **Los meses sin inversión valen 0 €**, no «dato que falta». Sin esto,
       el índice sólo contiene meses con movimiento y un hueco de tres años
       ocupa lo mismo que un mes: la regresión trata «la fila siguiente» como
       «el mes siguiente» y un movimiento viejo pesa igual que el del mes
       pasado.

    Los ceros **anteriores a la primera aportación de la ventana** sí se
    quitan. Dejarlos haría que la recta subiera desde cero hasta el ritmo
    actual, que no es que estés aportando más: es que antes no aportabas. Los
    ceros de en medio se quedan, porque ésos sí son información.
    """
    serie = drop_incomplete_month(serie, hoy)
    if serie.empty:
        return serie

    fin = pd.Period(hoy or pd.Timestamp.now(), freq="M") - 1
    if fin < serie.index[0]:
        return serie

    completo = serie.reindex(
        pd.period_range(fin - (meses - 1), fin, freq="M"), fill_value=0.0)

    # El corte se hace por el primer mes que existía en la serie original, no
    # por el primero distinto de cero: un mes en el que compraste y vendiste lo
    # mismo aporta 0 € neto, pero es un mes con actividad y cuenta.
    con_actividad = [p for p in completo.index if p in serie.index]
    if not con_actividad:
        # La ventana entera sin actividad: se cae a lo último que hubo, que
        # dice más que una serie de ceros.
        return serie.tail(meses)

    return completo.loc[con_actividad[0]:]
