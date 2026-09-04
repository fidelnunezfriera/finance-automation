"""
Personal Finance Dashboard — Streamlit app.
Run from repo root: streamlit run app/main.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
import streamlit as st
from sklearn.linear_model import LinearRegression

from data import (
    ROOT,
    SheetConfigError,
    account_holder_name,
    avg_buy_prices,
    classify_isin,
    get_all_prices,
    get_price_eur,
    historical_monthly_returns,
    load_category_month,
    load_positions,
    load_transactions,
    monthly_investments,
    monthly_expenses,
    contribution_window,
    exponential_weights,
    SEMIVIDA_MESES,
    tasa_anual,
    aportaciones_proyectadas,
    meseta_aportacion,
    estimar_persistencia,
)

# La decisión de qué modelos usar la toma el pipeline y se lee de disco; aquí
# sólo se aplica. Ver pipeline/seleccionar_modelos.py.
from pipeline.evaluacion import seleccion  # noqa: E402
from pipeline.convert_pytr_to_clean import strip_merchant_prefix  # noqa: E402

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Finance Dashboard",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Hide the running/stop status widget (Deploy button + main menu already
# hidden via .streamlit/config.toml toolbarMode="minimal").
st.markdown(
    "<style>[data-testid='stStatusWidget'] { visibility: hidden; }</style>",
    unsafe_allow_html=True,
)

# ── Theme constants ───────────────────────────────────────────────────────────
BG       = "#0f1117"
CARD     = "#1a1f2e"
GREEN    = "#00d4aa"
BLUE     = "#4f8ef7"
RED      = "#ef4444"
ORANGE   = "#f97316"
TEXT     = "#e8ecf0"
DIM      = "#8892a4"
BORDER   = "#2a3040"

CHART_COLORS = [GREEN, BLUE, "#a78bfa", ORANGE, "#f43f5e", "#06b6d4",
                "#84cc16", "#eab308", "#ec4899", "#14b8a6"]

# strftime("%B") depende del locale del sistema, que no está garantizado en
# español -- esta lista evita que un mes salga en inglés según la máquina.
MESES_ES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio",
            "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# ── Global CSS ────────────────────────────────────────────────────────────────
st.markdown(f"""
<style>
  [data-testid="stAppViewContainer"] {{ background-color: {BG}; }}
  [data-testid="stSidebar"]          {{ background-color: {CARD}; border-right: 1px solid {BORDER}; }}
  [data-testid="stHeader"]           {{ background-color: {BG}; }}
  section[data-testid="stSidebar"] * {{ color: {TEXT}; }}
  div[data-testid="stDataFrame"]     {{ border-radius: 8px; overflow: hidden; }}
  .stDataFrame thead th              {{ background-color: {CARD} !important; color: {DIM} !important; }}
  .stMarkdown p, .stMarkdown li      {{ color: {TEXT}; }}
  h1, h2, h3                         {{ color: {TEXT}; }}

  /* Filas "título + horizonte + exacto" en Proyección y Objetivos (cartera,
     por activo, camino al objetivo): cada widget se encoge a su contenido
     (flex: 0 0 auto) en vez de ocupar una fracción fija de columna -- si
     no, un título corto deja su columna a medio llenar y se ve como hueco
     vacío. space-between lo empeoraba: con contenido estrecho reparte TODO
     el ancho sobrante como dos huecos enormes. Un gap fijo y moderado los
     agrupa en vez de esparcirlos. */
  .st-key-horizonte_row [data-testid="stHorizontalBlock"],
  .st-key-horizonte_activo_row [data-testid="stHorizontalBlock"],
  .st-key-obj_horizonte_row [data-testid="stHorizontalBlock"] {{
      display: flex; justify-content: flex-start; align-items: center;
      flex-wrap: nowrap; gap: 2.5rem;
  }}
  .st-key-horizonte_row [data-testid="stColumn"],
  .st-key-horizonte_activo_row [data-testid="stColumn"],
  .st-key-obj_horizonte_row [data-testid="stColumn"] {{
      flex: 0 0 auto !important; width: auto !important; min-width: 0 !important;
  }}
  /* El título se queda a su ancho natural; slider y exacto, ajustados a su
     contenido, salían demasiado estrechos para manejarlos cómodamente. */
  .st-key-horizonte_row [data-testid="stColumn"]:has([data-testid="stSlider"]),
  .st-key-horizonte_activo_row [data-testid="stColumn"]:has([data-testid="stSlider"]),
  .st-key-obj_horizonte_row [data-testid="stColumn"]:has([data-testid="stSlider"]) {{
      flex: 0 0 260px !important; width: 260px !important;
  }}
  .st-key-horizonte_row [data-testid="stColumn"]:has([data-testid="stNumberInput"]),
  .st-key-horizonte_activo_row [data-testid="stColumn"]:has([data-testid="stNumberInput"]),
  .st-key-obj_horizonte_row [data-testid="stColumn"]:has([data-testid="stNumberInput"]) {{
      flex: 0 0 140px !important; width: 140px !important;
  }}

  /* Icono de ayuda (?) junto a la etiqueta de un widget: en sliders, el div
     que lo envuelve crece para ocupar todo el ancho de la etiqueta y el
     icono acaba pegado al borde derecho, lejos del texto -- en radios no
     pasa. Forzarlo a no crecer lo deja siempre pegado al texto, en
     cualquier tipo de widget. */
  [data-testid="stWidgetLabel"] > div:has([data-testid="stTooltipIcon"]) {{
      flex: 0 0 auto !important; width: auto !important;
      margin-left: 0.35rem !important;
  }}
</style>
""", unsafe_allow_html=True)


# ── Helpers ───────────────────────────────────────────────────────────────────

def dark_fig(fig: go.Figure) -> go.Figure:
    fig.update_layout(
        paper_bgcolor=BG,
        plot_bgcolor=CARD,
        font_color=TEXT,
        font_family="Inter, ui-sans-serif, sans-serif",
        margin=dict(l=16, r=16, t=48, b=16),
        legend=dict(bgcolor="rgba(0,0,0,0)", font_color=DIM),
        title_font_color=TEXT,
        title_font_size=14,
    )
    fig.update_xaxes(gridcolor=BORDER, linecolor=BORDER, tickfont_color=DIM)
    fig.update_yaxes(gridcolor=BORDER, linecolor=BORDER, tickfont_color=DIM)
    return fig


def kpi(label: str, value: str, sub: str = "", color: str = GREEN, help: str = "") -> None:
    sub_html = f'<p style="color:{DIM};margin:6px 0 0;font-size:.82rem">{sub}</p>' if sub else ""
    # Tarjeta hecha con HTML propio, no un widget nativo, así que no hay
    # help= de Streamlit disponible -- se simula con un icono con title
    # (tooltip nativo del navegador al pasar el ratón). Un salto de línea
    # real dentro del atributo (p.ej. una línea en blanco de un texto de
    # varios párrafos) hace que el markdown de Streamlit lo lea como fin de
    # bloque y rompa la etiqueta -- por eso también se sustituyen por la
    # entidad HTML &#10;, que el navegador sigue pintando como salto de
    # línea dentro del tooltip.
    help_attr = help.replace(chr(34), "&quot;").replace("\n", "&#10;")
    help_html = (f' <span title="{help_attr}" '
                 f'style="cursor:help;color:{DIM}">ⓘ</span>') if help else ""
    st.markdown(f"""
    <div style="background:{CARD};border-radius:12px;padding:20px 24px;
                border-left:3px solid {color};margin-bottom:4px">
      <p style="color:{DIM};margin:0;font-size:.72rem;text-transform:uppercase;letter-spacing:.08em">{label}{help_html}</p>
      <p style="color:{TEXT};margin:6px 0 0;font-size:1.55rem;font-weight:700;line-height:1">{value}</p>
      {sub_html}
    </div>""", unsafe_allow_html=True)


def fmt(val: float, decimals: int = 2) -> str:
    return f"{val:,.{decimals}f} €"


def open_positions(pos: pd.DataFrame) -> pd.DataFrame:
    return pos[pos["status"] == "open"].copy()


# ── Page 1: Dashboard ─────────────────────────────────────────────────────────

def page_dashboard(tx: pd.DataFrame, pos: pd.DataFrame) -> None:
    st.title(
        "Dashboard",
        help="Visión general de la cuenta.\n\n"
             "En la barra lateral se pueden ajustar los días que se tienen "
             "en cuenta para el gráfico de rosca, y los meses para la "
             "gráfica de cashflow mensual.\n\n"
             "La gráfica de cashflow mensual es reactiva, al clicar en una "
             "barra se mostrarán las transacciones de ese tipo y mes en el "
             "historial de transacciones más abajo.",
    )

    now     = pd.Timestamp.now()
    cur_per = now.to_period("M")
    # Ventana móvil de 30 días naturales (no el mes de calendario en curso):
    # "este mes" penalizaba el día 1 mostrando casi nada, aunque el mes
    # anterior hubiera sido normal.
    cutoff_30 = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=30)
    tx_30     = tx[tx["datetime"] >= cutoff_30]

    with st.sidebar:
        st.markdown(f"<hr style='border-color:{BORDER}'>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{DIM};font-size:.78rem;text-transform:uppercase'>Dashboard</p>",
                    unsafe_allow_html=True)

        if "dash_donut_days_slider" not in st.session_state:
            st.session_state.dash_donut_days_slider = 30
            st.session_state.dash_donut_days_number = 30

        def _dash_sync_donut_days_from_slider():
            st.session_state.dash_donut_days_number = st.session_state.dash_donut_days_slider

        def _dash_sync_donut_days_from_number():
            st.session_state.dash_donut_days_slider = st.session_state.dash_donut_days_number

        st.slider(
            "Gastos por categoría — últimos X días",
            min_value=1, max_value=365, step=1,
            key="dash_donut_days_slider",
            on_change=_dash_sync_donut_days_from_slider,
        )
        st.number_input(
            "Gastos por categoría — últimos X días (exacto)",
            min_value=1, max_value=365, step=1,
            key="dash_donut_days_number",
            on_change=_dash_sync_donut_days_from_number,
        )
        donut_days = st.session_state.dash_donut_days_slider

        if "dash_cashflow_months_slider" not in st.session_state:
            st.session_state.dash_cashflow_months_slider = 12
            st.session_state.dash_cashflow_months_number = 12

        def _dash_sync_cashflow_months_from_slider():
            st.session_state.dash_cashflow_months_number = st.session_state.dash_cashflow_months_slider

        def _dash_sync_cashflow_months_from_number():
            st.session_state.dash_cashflow_months_slider = st.session_state.dash_cashflow_months_number

        st.slider(
            "Cashflow mensual — últimos X meses",
            min_value=1, max_value=60, step=1,
            key="dash_cashflow_months_slider",
            on_change=_dash_sync_cashflow_months_from_slider,
        )
        st.number_input(
            "Cashflow mensual — últimos X meses (exacto)",
            min_value=1, max_value=60, step=1,
            key="dash_cashflow_months_number",
            on_change=_dash_sync_cashflow_months_from_number,
        )
        cashflow_months = st.session_state.dash_cashflow_months_slider

    # Rolling N-day window (today back N calendar days), independent of the
    # calendar-month KPIs above — used for the spending-by-category donut.
    cutoff_donut = pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=donut_days)
    tx_donut     = tx[tx["datetime"] >= cutoff_donut]

    # KPIs
    income_m  = tx_30[tx_30["type"] == "income"]["amount"].sum()
    expense_m = tx_30[
        (tx_30["event_domain_l"] == "cashflow") & (tx_30["amount"] < 0)
    ]["amount"].sum()
    net_m = income_m + expense_m

    open_pos = open_positions(pos)
    prices   = get_all_prices(tuple(
        (r["isin"], r["name"]) for _, r in open_pos.iterrows()
    ))
    portfolio_val = sum(
        r["quantity"] * prices[r["isin"]] if prices.get(r["isin"]) is not None
        else (r["quantity"] if classify_isin(r["isin"]) == "bond" else 0.0)
        for _, r in open_pos.iterrows()
    )

    holder = account_holder_name()
    ingresos_help = (
        f"No cuenta como ingreso un movimiento cuya nota incluya tu nombre "
        f"configurado ({holder!r}) -- se trata como traspaso entre tus "
        f"propias cuentas, no como dinero nuevo. Si solo usas esta cuenta "
        f"para invertir y quieres que esos traspasos sí cuenten como "
        f"ingreso, borra pipeline.account_holder_name en config.yaml."
    ) if holder else ""

    c1, c2, c3, c4 = st.columns(4)
    with c1:
        kpi("Ingresos últimos 30 días", fmt(income_m), color=GREEN, help=ingresos_help)
    with c2:
        kpi("Gastos últimos 30 días", fmt(abs(expense_m)), color=ORANGE)
    with c3:
        kpi("Cashflow neto últimos 30 días", fmt(net_m), color=GREEN if net_m >= 0 else RED)
    with c4:
        kpi("Valor portfolio", fmt(portfolio_val), color=BLUE)

    st.markdown("<br>", unsafe_allow_html=True)

    # Donut + bar
    col_l, col_r = st.columns([1, 2])

    mes_seleccionado  = None
    tipo_seleccionado = None

    with col_l:
        st.subheader(f"Gastos por categoría — últimos {donut_days} días")
        exp_cat = (
            tx_donut[
                (tx_donut["event_domain_l"] == "cashflow") &
                (tx_donut["amount"] < 0) &
                (tx_donut["category"] != "")
            ]
            .groupby("category")["amount"].sum().abs()
            .reset_index()
            .sort_values("amount", ascending=False)
        )
        if not exp_cat.empty:
            fig = go.Figure(go.Pie(
                labels=exp_cat["category"], values=exp_cat["amount"],
                hole=0.62, marker_colors=CHART_COLORS,
                textinfo="label+percent", textfont_size=11,
                insidetextorientation="radial",
            ))
            fig.update_layout(showlegend=False, title="")
            # Sin on_select: un donut no dispara plotly_selected (esa vía es
            # solo para trazas cartesianas) y Streamlit solo traduce clics a
            # selección para gráficos jerárquicos (treemap/sunburst, que sí
            # llevan id/parent) -- en un pie el clic se pierde siempre, así
            # que aquí se queda puramente visual.
            st.plotly_chart(dark_fig(fig), width="stretch")
        else:
            st.info(f"Sin gastos registrados en los últimos {donut_days} días.")

    with col_r:
        st.subheader(
            f"Cashflow mensual — últimos {cashflow_months} meses",
            help=ingresos_help or None,
        )
        start_per = cur_per - (cashflow_months - 1)
        tx_12 = tx[tx["year_month"] >= start_per]
        inc_m  = tx_12[tx_12["type"] == "income"].groupby("year_month")["amount"].sum()
        exp_m  = tx_12[
            (tx_12["event_domain_l"] == "cashflow") & (tx_12["amount"] < 0)
        ].groupby("year_month")["amount"].sum().abs()

        months     = pd.period_range(start_per, cur_per, freq="M")
        month_strs = [str(m) for m in months]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            name="Ingresos", x=month_strs,
            y=[float(inc_m.get(m, 0)) for m in months],
            marker_color=GREEN, opacity=0.9,
        ))
        fig.add_trace(go.Bar(
            name="Gastos", x=month_strs,
            y=[float(exp_m.get(m, 0)) for m in months],
            marker_color=RED, opacity=0.9,
        ))
        fig.update_layout(
            barmode="group", xaxis_title="", yaxis_title="EUR",
            yaxis_ticksuffix=" €", title="",
        )
        # Eje de categorías, no de fechas: con strings "YYYY-MM", Plotly
        # detecta un eje de fecha por su cuenta y el "x" que devuelve al
        # hacer clic deja de ser el mes exacto (llegaba a reportar un
        # timestamp de otro mes). Forzarlo a categoría hace que el "x" del
        # clic sea siempre uno de los strings de month_strs, tal cual.
        fig.update_xaxes(type="category")
        # A diferencia del donut, las barras SÍ son cartesianas: un clic
        # dispara plotly_selected de verdad y Streamlit lo traduce a
        # selección -- clic de nuevo sobre la misma barra la deselecciona.
        evento = st.plotly_chart(
            dark_fig(fig), width="stretch",
            on_select="rerun", selection_mode="points",
            key="dash_cashflow_chart",
        )
        puntos = evento.selection.points if evento else []
        if puntos:
            mes_seleccionado  = puntos[0].get("x")
            # curve_number: 0 es la traza "Ingresos" (se añadió primero),
            # 1 es "Gastos" -- así se sabe cuál de las dos barras del mes
            # se clicó, no solo el mes.
            tipo_seleccionado = "Ingresos" if puntos[0].get("curve_number") == 0 else "Gastos"
        if mes_seleccionado:
            periodo_sel = pd.Period(mes_seleccionado, freq="M")
            etiqueta_mes = f"{MESES_ES[periodo_sel.month - 1]} {periodo_sel.year}"
            st.caption(f"Filtrando por **{tipo_seleccionado} — {etiqueta_mes}** "
                       "— haz clic de nuevo en la barra para quitarlo.")

    # Historial de transacciones -- ya no solo las últimas 10: la tabla
    # lleva todo el histórico (filtrado por mes y tipo si hay una barra
    # seleccionada en el gráfico de cashflow), con altura fija y scroll
    # interno para verlas todas sin salir de la vista.
    if mes_seleccionado:
        periodo_sel = pd.Period(mes_seleccionado, freq="M")
        etiqueta_mes = f"{MESES_ES[periodo_sel.month - 1]} {periodo_sel.year}"
        st.subheader(f"{tipo_seleccionado} — {etiqueta_mes}")
        tx_mes = tx[(tx["event_domain_l"] == "cashflow") & (tx["year_month"] == periodo_sel)]
        if tipo_seleccionado == "Ingresos":
            tx_tabla = tx_mes[tx_mes["type"] == "income"]
        else:
            tx_tabla = tx_mes[tx_mes["amount"] < 0]
    else:
        st.subheader("Historial de transacciones")
        tx_tabla = tx[tx["event_domain_l"] == "cashflow"]

    tabla = (
        tx_tabla
        .sort_values("date", ascending=False)
        [["date", "merchant_raw", "amount", "category", "subcategory"]]
        .copy()
    )
    tabla["date"] = tabla["date"].dt.strftime("%Y-%m-%d")
    tabla["merchant_raw"] = tabla["merchant_raw"].apply(strip_merchant_prefix)
    tabla.columns = ["Fecha", "Comercio", "Importe", "Categoría", "Subcategoría"]
    # Alto ajustado al número de filas (hasta 10) en vez de fijo -- con
    # menos de 10 transacciones (p.ej. un mes filtrado) un height fijo
    # dejaba huecos vacíos debajo de la última fila real.
    alto_tabla = 38 + min(len(tabla), 10) * 35 + 3
    st.dataframe(
        tabla, width="stretch", hide_index=True, height=alto_tabla,
        column_config={
            # Sin esto, st.dataframe reparte el ancho a partes iguales y a
            # "Fecha" (10 caracteres) le sobra muchísimo hueco.
            "Fecha": st.column_config.TextColumn(width="small"),
            "Comercio": st.column_config.TextColumn(width="large"),
            # Importe se queda numérico -- formatearlo a texto
            # ("+1,234.56 €") antes de pasarlo a st.dataframe hacía que la
            # columna se ordenara alfabéticamente al pulsar la cabecera, no
            # por valor. Con NumberColumn el dato subyacente sigue siendo
            # float y solo cambia cómo se pinta.
            "Importe": st.column_config.NumberColumn(format="%+.2f €", width="small"),
            "Categoría": st.column_config.TextColumn(width="medium"),
            "Subcategoría": st.column_config.TextColumn(width="medium"),
        },
    )


# ── Page 2: Gastos ────────────────────────────────────────────────────────────

@st.cache_data(ttl=600)
def _decision_modelos() -> dict:
    """La decisión que dejó `pipeline/seleccionar_modelos.py`.

    Se lee de disco: elegir modelos cuesta minutos y una página no puede
    gastarlos. Predecir con ellos, en cambio, es instantáneo.
    """
    return seleccion.cargar(ROOT / "logs" / "modelos_elegidos.json")


def _opciones_series(tx: pd.DataFrame) -> dict:
    """Qué se puede predecir: total, categorías y subcategorías.

    Solo se ofrecen las que tienen decisión guardada. Lo demás no es que
    prediga mal, es que nadie ha comprobado con qué.
    """
    decision = _decision_modelos()
    disponibles = set((decision or {}).get("series", {}))

    total_ok = "gasto-total" in disponibles or not disponibles

    gastos = tx[(tx["event_domain_l"] == "cashflow") & (tx["amount"] < 0)]
    categorias = sorted(
        cat for cat in gastos["category"].dropna().unique()
        if f"categoria:{cat}" in disponibles
    )
    subcategorias = sorted(
        sub for sub in gastos["subcategory"].dropna().unique()
        if sub and f"subcategoria:{sub}" in disponibles
    )
    return {"total": total_ok, "categorias": categorias, "subcategorias": subcategorias}


def _bloque_prediccion(tx: pd.DataFrame) -> None:
    """Formulario de predicción: no calcula nada hasta que se pulsa."""
    st.subheader(
        "Predecir gasto",
        help="Pide una previsión del año completo elegido, del gasto "
             "total o de una categoría/subcategoría. Los meses ya "
             "cerrados de ese año se muestran como gasto real; los que "
             "quedan por venir se predicen con modelos de series "
             "temporales, con una banda alta/baja según lo que esos "
             "modelos fallaron al validarse contra el histórico. \n\n"
             "**Por qué no aparecen todas las categorías**: solo se "
             "ofrecen las que tienen al menos ~24 meses de historia. "
             "Con menos, no hay suficientes datos para validar de "
             "verdad ningún modelo -- predecir igualmente daría un "
             "número sin fundamento, así que la categoría simplemente "
             "no aparece en el desplegable hasta que acumule más "
             "historial.",
    )

    opciones = _opciones_series(tx)
    ambitos = []
    if opciones["total"]:
        ambitos.append("Gasto total")
    if opciones["categorias"]:
        ambitos.append("Categoría")
    if opciones["subcategorias"]:
        ambitos.append("Subcategoría")

    if not ambitos:
        st.info("Todavía no hay modelos elegidos. Ejecuta "
                "`scripts\\seleccionar_modelos.bat` para decidirlos con tus datos.")
        return

    hoy = pd.Period(pd.Timestamp.now(), freq="M")
    meses = [hoy + i for i in range(0, 25)]
    anios = sorted({m.year for m in meses})

    # Un contenedor normal, no un st.form: el ámbito tiene que poder
    # mostrar/ocultar el desplegable de categoría/subcategoría al momento,
    # y los widgets de un st.form no fuerzan un rerun hasta que se envía.
    # El cálculo caro (los modelos) lo sigue gateando el botón, no cada
    # cambio de selector.
    with st.container(border=True):
        c1, c2, c3, c4 = st.columns([1.3, 1.3, 0.9, 1.1])
        with c1:
            ambito = st.selectbox("Ámbito de la predicción", ambitos, key="pred_ambito")
        detalle = None
        with c2:
            if ambito == "Categoría":
                detalle = st.selectbox("Categoría", opciones["categorias"], key="pred_categoria")
            elif ambito == "Subcategoría":
                detalle = st.selectbox("Subcategoría", opciones["subcategorias"], key="pred_subcategoria")
        with c3:
            anio_sel = st.selectbox("Año a predecir", anios)
        with c4:
            st.markdown("<div style='height:1.9rem'></div>", unsafe_allow_html=True)
            lanzar = st.button("Predecir gasto", type="primary", width="stretch")

    if not lanzar:
        return

    if ambito == "Categoría":
        etiqueta = f"Categoría · {detalle}"
        nombre_serie, filtros = f"categoria:{detalle}", {"categoria": detalle}
    elif ambito == "Subcategoría":
        etiqueta = f"Subcategoría · {detalle}"
        nombre_serie, filtros = f"subcategoria:{detalle}", {"subcategoria": detalle}
    else:
        etiqueta, nombre_serie, filtros = "Gasto total", "gasto-total", {}

    serie = monthly_expenses(tx, **filtros)
    decision = _decision_modelos()
    ultimo = serie.index[-1] if not serie.empty else None

    # El año se predice mes a mes: los meses ya cerrados son dato real (lo
    # que de verdad se gastó), los que quedan por venir son predicción. Sumar
    # los dos da un total de año coherente, en vez de mezclar "lo gastado"
    # con "lo estimado" como si fueran la misma cosa.
    meses_anio = [pd.Period(f"{anio_sel}-{m:02d}", freq="M") for m in range(1, 13)]
    reales: list[tuple[pd.Period, float]] = []
    predichos: list[tuple[pd.Period, seleccion.Prevision]] = []
    for mes in meses_anio:
        if ultimo is not None and mes <= ultimo:
            reales.append((mes, float(serie.get(mes, 0.0))))
        else:
            h = (mes - ultimo).n if ultimo is not None else (mes - hoy).n + 1
            predichos.append((mes, seleccion.predecir(serie, h, decision, nombre_serie)))

    if not predichos:
        st.warning(f"{anio_sel} ya ha pasado entero: elige un año futuro.")
        return

    total_real = sum(v for _, v in reales)
    partes = [p for _, p in predichos]
    prevision = seleccion.Prevision(
        central=total_real + sum(p.central for p in partes),
        bajo=total_real + sum(p.bajo for p in partes),
        alto=total_real + sum(p.alto for p in partes),
        modelos=partes[0].modelos,
        horizonte_pedido=max(p.horizonte_pedido for p in partes),
        horizonte_validado=max((p.horizonte_validado or 0) for p in partes) or None,
        meses_historia=partes[0].meses_historia,
        intermitente=partes[0].intermitente,
    )

    explicacion_txt = seleccion.explicacion(prevision, decision)
    if reales:
        explicacion_txt += (
            f"\n\nIncluye {len(reales)} mes(es) ya cerrado(s) de "
            f"{anio_sel} como dato real, no estimado.")
    # El icono de ayuda va pegado a un título de verdad (st.markdown con
    # help=), no a un <span title=""> dentro de HTML a medida -- ese tooltip
    # del navegador no se disparaba de forma fiable. help= es el mismo
    # tooltip nativo que ya usan los títulos de página y "Predecir gasto".
    st.markdown(
        f"<span style='color:{TEXT};font-size:1.05rem;font-weight:600'>"
        f"{etiqueta} · {anio_sel}</span>",
        unsafe_allow_html=True, help=explicacion_txt,
    )
    st.markdown(
        f"<div style='background:{CARD};border-radius:12px;padding:16px 24px;"
        f"border-left:3px solid {ORANGE};margin-top:-2px'>"
        f"<p style='color:{TEXT};margin:0;font-size:1.55rem;"
        f"font-weight:700;line-height:1'>{fmt(max(prevision.central, 0))}</p>"
        f"<p style='color:{DIM};margin:6px 0 0;font-size:.82rem'>"
        f"entre {fmt(max(prevision.bajo, 0))} y {fmt(max(prevision.alto, 0))}</p>"
        f"</div>", unsafe_allow_html=True)

    etiquetas_mes = [f"{MESES_ES[m.month - 1][:3]} {m.year}" for m, _ in reales] + \
                    [f"{MESES_ES[m.month - 1][:3]} {m.year}" for m, _ in predichos]
    fig = go.Figure()
    if reales:
        fig.add_trace(go.Bar(
            x=etiquetas_mes[:len(reales)],
            y=[round(v, 2) for _, v in reales],
            name="Real", marker_color=BLUE, opacity=0.85,
            hovertemplate="%{x}: %{y:,.2f} €<extra></extra>",
        ))
    if predichos:
        # Un gasto no puede ser negativo: se recorta central/bajo/alto a 0
        # antes de calcular las barras de error, si no la banda baja de un
        # mes con predicción ~0 se cuela por debajo del eje.
        y_central, y_bajo, y_alto = [], [], []
        for _, p in predichos:
            central_ef = max(p.central, 0)
            bajo_ef = max(p.bajo, 0)
            alto_ef = max(p.alto, central_ef)
            y_central.append(round(central_ef, 2))
            y_bajo.append(round(max(central_ef - bajo_ef, 0), 2))
            y_alto.append(round(max(alto_ef - central_ef, 0), 2))
        fig.add_trace(go.Bar(
            x=etiquetas_mes[len(reales):],
            y=y_central,
            name="Predicho", marker_color=ORANGE, opacity=0.85,
            error_y=dict(type="data", symmetric=False,
                        array=y_alto, arrayminus=y_bajo,
                        color=DIM, thickness=1.3, width=4),
            hovertemplate="%{x}: %{y:,.2f} €<extra></extra>",
        ))
    fig.update_layout(
        xaxis_title="", yaxis_ticksuffix=" €", title="",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(type="category")
    st.plotly_chart(dark_fig(fig), width="stretch")

    st.markdown("<br>", unsafe_allow_html=True)


def page_gastos(tx: pd.DataFrame) -> None:
    st.title(
        "Gastos",
        help="Explora tu histórico de gasto y pide previsiones para el "
             "futuro. Los filtros de la barra lateral (año, mes, "
             "categoría, subcategoría) se combinan entre sí y afectan a "
             "los KPIs, los gráficos y la tabla de esta página. "
             "**\"Predecir gasto\"** muestra una previsión anual del "
             "gasto total o de una categoría/subcategoría concreta: los "
             "meses ya cerrados del año se muestran como dato real y los "
             "que quedan por venir como predicción -- el botón \"ℹ️ Cómo "
             "se calcula\" de cada previsión explica qué modelos se "
             "usaron y por qué.",
    )

    expenses = tx[
        (tx["event_domain_l"] == "cashflow") & (tx["amount"] < 0)
    ].copy()
    expenses["amount_abs"] = expenses["amount"].abs()

    # Si el gráfico "Por categoría" (más abajo) registró un clic nuevo,
    # fuerza el filtro de categoría a esa categoría y vacía el de
    # subcategoría -- antes de crear esos widgets, y solo la primera vez
    # que se procesa ese clic concreto, para no pisar después una edición
    # manual del usuario sobre el filtro. Año y mes no se tocan.
    click_previo  = st.session_state.get("gastos_cat_chart", {}) or {}
    puntos_click  = click_previo.get("selection", {}).get("points") or []
    cat_clicada   = puntos_click[0].get("y") if puntos_click else None
    if cat_clicada and st.session_state.get("_gastos_cat_click_aplicado") != cat_clicada:
        st.session_state["_gastos_cat_click_aplicado"] = cat_clicada
        st.session_state["gastos_cat_filter"] = [cat_clicada]
        st.session_state["gastos_sub_filter"] = []
    elif not cat_clicada:
        st.session_state["_gastos_cat_click_aplicado"] = None

    # Sidebar filters
    with st.sidebar:
        st.markdown(f"<hr style='border-color:{BORDER}'>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{DIM};font-size:.78rem;text-transform:uppercase'>Filtros</p>",
                    unsafe_allow_html=True)

        all_years = sorted(expenses["date"].dt.year.dropna().unique().astype(int).tolist(), reverse=True)
        # El año en curso, no "el más reciente con datos" -- si en enero
        # todavía no hay transacciones importadas de este año, cae al más
        # reciente disponible en vez de dejar el filtro vacío.
        current_year = pd.Timestamp.now().year
        default_year = [current_year] if current_year in all_years else all_years[:1]
        sel_years = st.multiselect("Año", all_years, default=default_year)

        all_months = list(range(1, 13))
        sel_months = st.multiselect("Mes", all_months,
                                    format_func=lambda m: pd.Timestamp(2000, m, 1).strftime("%B"))

        all_cats = sorted(expenses["category"].dropna().unique().tolist())
        sel_cats = st.multiselect("Categoría", all_cats, key="gastos_cat_filter")

        # Subcategoría solo ofrece las que existen dentro de las
        # categorías ya elegidas -- si no hay ninguna categoría elegida,
        # ofrece todas. Si cambiar de categoría deja una subcategoría ya
        # marcada fuera de las opciones nuevas, se quita sola: dejarla
        # sería un valor inválido para el widget y Streamlit lo rechaza.
        subs_source = expenses[expenses["category"].isin(sel_cats)] if sel_cats else expenses
        # No solo dropna(): un gasto sin subcategoría llega como "" (cadena
        # vacía), no como nulo -- si no se filtra, sale una opción en blanco
        # que no representa nada seleccionable.
        all_subs = sorted(
            s for s in subs_source["subcategory"].dropna().unique().tolist() if s
        )
        if "gastos_sub_filter" in st.session_state:
            st.session_state["gastos_sub_filter"] = [
                s for s in st.session_state["gastos_sub_filter"] if s in all_subs
            ]
        sel_subs = st.multiselect("Subcategoría", all_subs, key="gastos_sub_filter")

    df = expenses.copy()
    if sel_years:
        df = df[df["date"].dt.year.isin(sel_years)]
    if sel_months:
        df = df[df["date"].dt.month.isin(sel_months)]
    if sel_cats:
        df = df[df["category"].isin(sel_cats)]
    if sel_subs:
        df = df[df["subcategory"].isin(sel_subs)]

    # KPIs
    total_spend = df["amount_abs"].sum()
    months_repr = df["year_month"].nunique() or 1
    avg_per_month = total_spend / months_repr
    top_cat = df.groupby("category")["amount_abs"].sum().idxmax() if not df.empty else "—"

    c1, c2, c3 = st.columns(3)
    with c1:
        kpi("Gasto total filtrado", fmt(total_spend), color=ORANGE)
    with c2:
        kpi("Media mensual", fmt(avg_per_month), color=BLUE)
    with c3:
        kpi("Categoría principal", top_cat, color=GREEN)

    st.markdown("<br>", unsafe_allow_html=True)

    _bloque_prediccion(tx)

    col_l, col_r = st.columns(2)

    with col_l:
        if sel_cats:
            # Con una categoría ya fijada en el filtro, desglosar otra vez
            # por categoría no dice nada nuevo -- lo que interesa es cómo
            # se reparte dentro de ella, así que se cambia a subcategoría.
            titulo_sub = ("Por subcategoría · " + sel_cats[0]
                          if len(sel_cats) == 1 else "Por subcategoría")
            st.subheader(titulo_sub)
            by_sub = (df[df["subcategory"].fillna("") != ""]
                        .groupby("subcategory")["amount_abs"].sum()
                        .sort_values(ascending=True).reset_index())
            if by_sub.empty:
                st.info("Sin datos para este filtro.")
            else:
                fig = go.Figure(go.Bar(
                    x=by_sub["amount_abs"], y=by_sub["subcategory"],
                    orientation="h", marker_color=BLUE, opacity=0.85,
                    text=by_sub["amount_abs"].apply(lambda v: f"{v:,.2f} €"),
                    textposition="outside",
                ))
                fig.update_layout(xaxis_ticksuffix=" €", yaxis_title="", title="",
                                  xaxis_title="Gasto (€)")
                fig.update_yaxes(type="category")
                st.plotly_chart(dark_fig(fig), width="stretch")
        else:
            st.subheader("Por categoría")
            by_cat = (df.groupby("category")["amount_abs"].sum()
                        .sort_values(ascending=True).reset_index())
            if not by_cat.empty:
                fig = go.Figure(go.Bar(
                    x=by_cat["amount_abs"], y=by_cat["category"],
                    orientation="h", marker_color=BLUE, opacity=0.85,
                    text=by_cat["amount_abs"].apply(lambda v: f"{v:,.2f} €"),
                    textposition="outside",
                ))
                fig.update_layout(xaxis_ticksuffix=" €", yaxis_title="", title="",
                                  xaxis_title="Gasto (€)")
                # Categórico, no numérico/fecha: así el "y" que devuelve el
                # clic es siempre el nombre exacto de la categoría.
                fig.update_yaxes(type="category")
                # Un clic en una barra pone esa categoría en el filtro de la
                # barra lateral (y vacía subcategoría) -- la lectura real del
                # clic pasa por session_state, arriba, antes de crear esos
                # widgets; aquí solo hace falta la key para que quede guardado.
                st.plotly_chart(
                    dark_fig(fig), width="stretch",
                    on_select="rerun", selection_mode="points",
                    key="gastos_cat_chart",
                )

    with col_r:
        st.subheader("Tendencia mensual")
        # df ya lleva aplicados Año/Mes/Categoría/Subcategoría -- una
        # ventana fija de últimos 12 meses (como se probó antes) los
        # ignoraba, así que el gráfico dejaba de reflejar lo que decía la
        # barra lateral. Vuelve a salir de df, sin ventana propia.
        if not df.empty:
            if sel_months:
                # El filtro Mes ya deja la serie "lineal" por construcción
                # (un punto por año para ese mes exacto) -- reindexar a
                # todos los meses de calendario metería de vuelta los meses
                # que el usuario pidió excluir explícitamente.
                serie_mes = df.groupby("year_month")["amount_abs"].sum().sort_index()
                etiquetas_x = [str(m) for m in serie_mes.index]
            else:
                # Reindexado a TODOS los meses entre el primero y el último
                # con dato, con 0€ donde no hubo gasto -- si no, agrupar
                # salta directamente de un mes con gasto al siguiente que lo
                # tenga (p.ej. enero 2021 seguido de julio 2026 con una
                # categoría poco usada), y el eje X deja de ser una línea de
                # tiempo real.
                rango = pd.period_range(df["year_month"].min(), df["year_month"].max(), freq="M")
                serie_mes = df.groupby("year_month")["amount_abs"].sum().reindex(rango, fill_value=0.0)
                etiquetas_x = [str(m) for m in rango]
            fig = go.Figure(go.Scatter(
                x=etiquetas_x, y=serie_mes.values,
                mode="lines+markers", line_color=ORANGE, line_width=2,
                marker=dict(color=ORANGE, size=6),
                fill="tozeroy",
                fillcolor=f"rgba(249,115,22,0.12)",
            ))
            fig.update_layout(xaxis_title="", yaxis_ticksuffix=" €", title="")
            # Categórico, no de fecha: si no, Plotly se salta meses en el eje.
            fig.update_xaxes(type="category")
            st.plotly_chart(dark_fig(fig), width="stretch")

    # Transactions table
    st.subheader("Detalle de transacciones")
    show = df[["date", "merchant_raw", "amount_abs", "category", "subcategory"]].copy()
    show["date"] = show["date"].dt.strftime("%Y-%m-%d")
    show["merchant_raw"] = show["merchant_raw"].apply(strip_merchant_prefix)
    show.columns = ["Fecha", "Comercio", "Importe", "Categoría", "Subcategoría"]
    st.dataframe(
        show.sort_values("Fecha", ascending=False), width="stretch", hide_index=True,
        # Importe numérico, no texto -- mismo motivo que en el historial
        # del Dashboard: si no, ordenar por esta columna era alfabético.
        column_config={"Importe": st.column_config.NumberColumn(format="%.2f €")},
    )


# ── Page 3: Activos ───────────────────────────────────────────────────────────

def page_activos(pos: pd.DataFrame, tx: pd.DataFrame) -> None:
    st.title(
        "Activos",
        help="El desglose de tu cartera, activo por activo. La rosca "
             "muestra cómo se reparte el valor entre activos (la renta "
             "fija se agrupa como \"Renta fija\"). Cada tarjeta trae el "
             "precio actual, el valor de la posición y la ganancia o "
             "pérdida frente a tu coste medio de compra. Los bonos no "
             "cotizan en el mercado, así que se muestran a su valor "
             "nominal, no a un precio.",
    )

    open_pos = open_positions(pos)
    if open_pos.empty:
        st.info("No hay posiciones abiertas.")
        return

    with st.spinner("Cargando precios..."):
        prices = get_all_prices(tuple(
            (r["isin"], r["name"]) for _, r in open_pos.iterrows()
        ))

    avg_costs = avg_buy_prices(tx)

    rows = []
    for _, r in open_pos.iterrows():
        price      = prices.get(r["isin"])
        qty        = r["quantity"]
        no_price   = price is None
        is_bond    = classify_isin(r["isin"]) == "bond"

        if is_bond:
            value = qty          # nominal EUR
        elif no_price:
            value = None         # unknown — exclude from totals
        else:
            value = qty * price

        avg_c = avg_costs.get(r["isin"])
        if avg_c and not no_price and not is_bond:
            gain_pct = (price - avg_c) / avg_c * 100
            gain_eur = (price - avg_c) * qty
        else:
            gain_pct = gain_eur = None
        rows.append({
            "isin":      r["isin"],
            "name":      r["name"],
            "qty":       qty,
            "price":     price,
            "value":     value,
            "is_bond":   is_bond,
            "no_price":  no_price,
            "avg_cost":  avg_c,
            "gain_pct":  gain_pct,
            "gain_eur":  gain_eur,
        })

    df_rows   = pd.DataFrame(rows)
    total_val = df_rows["value"].dropna().sum()

    st.subheader(f"Valor total del portfolio: {fmt(total_val)}")

    # Donut: portfolio allocation — only rows with a known value
    donut_df = df_rows[df_rows["value"].notna()].copy()
    donut_df["donut_label"] = donut_df.apply(
        lambda x: "Renta fija" if x["is_bond"] else x["name"], axis=1
    )
    donut_agg = donut_df.groupby("donut_label")["value"].sum().reset_index()
    donut_agg = donut_agg.sort_values("value", ascending=False)

    fig = go.Figure(go.Pie(
        labels=donut_agg["donut_label"], values=donut_agg["value"],
        hole=0.62, marker_colors=CHART_COLORS,
        textinfo="label+percent", textfont_size=11,
    ))
    fig.update_layout(title="Distribución del portfolio", showlegend=True,
                      legend=dict(orientation="v", x=1.0, y=0.5))
    st.plotly_chart(dark_fig(fig), width="stretch")

    # Per-position cards
    st.subheader("Posiciones")
    cols_per_row = 3
    for i in range(0, len(rows), cols_per_row):
        batch = rows[i: i + cols_per_row]
        cols  = st.columns(len(batch))
        for col, r in zip(cols, batch):
            if r["is_bond"]:
                price_str   = "Renta fija"
                value_str   = fmt(r["value"])
                gain_clr    = DIM
                gain_str    = "Valor nominal"
                units_label = "Nominal"
                units_val   = fmt(r["value"])
            elif r["no_price"]:
                price_str   = "—"
                value_str   = "—"
                gain_clr    = DIM
                gain_str    = "Sin precio de mercado"
                units_label = "Unidades"
                units_val   = f"{r['qty']:.6f}"
            else:
                price_str   = fmt(r["price"])
                value_str   = fmt(r["value"])
                units_label = "Unidades"
                units_val   = f"{r['qty']:.6f}"
                if r["gain_pct"] is not None:
                    sign     = "+" if r["gain_pct"] >= 0 else ""
                    gain_clr = GREEN if r["gain_pct"] >= 0 else RED
                    gain_str = f"{sign}{r['gain_pct']:.2f}% ({sign}{r['gain_eur']:,.2f} €)"
                else:
                    gain_clr = DIM
                    gain_str = "Coste medio no disponible"

            with col:
                st.markdown(f"""
                <div style="background:{CARD};border-radius:12px;padding:18px 20px;
                            margin-bottom:12px;border:1px solid {BORDER}">
                  <p style="color:{DIM};font-size:.7rem;margin:0;font-family:monospace">{r['isin']}</p>
                  <p style="color:{TEXT};font-weight:700;font-size:1rem;margin:4px 0 0">{r['name']}</p>
                  <hr style="border-color:{BORDER};margin:10px 0">
                  <p style="color:{DIM};margin:2px 0;font-size:.82rem">{units_label}: <span style="color:{TEXT}">{units_val}</span></p>
                  <p style="color:{DIM};margin:2px 0;font-size:.82rem">Precio actual: <span style="color:{TEXT}">{price_str}</span></p>
                  <p style="color:{DIM};margin:2px 0;font-size:.82rem">Valor total: <span style="color:{TEXT};font-weight:600">{value_str}</span></p>
                  <p style="color:{gain_clr};margin:6px 0 0;font-size:.82rem">{gain_str}</p>
                </div>""", unsafe_allow_html=True)


# ── Page 4/5 shared helpers (Proyección / Objetivos) ──────────────────────────

def _portfolio_return_stats(open_pos: pd.DataFrame, prices: dict):
    """Value-weighted CAGR and true covariance-based volatility across held
    non-bond assets with resolvable price history (10y monthly).

    Volatility uses the real covariance between assets' monthly returns —
    not a naive weighted average — so diversification actually lowers the
    portfolio's risk the way it does in reality (assets rarely move in
    lockstep, so combined volatility is normally below the average of the
    parts).

    Returns (weighted_cagr, weighted_vol, asset_returns, asset_value,
    asset_cagr) — the last three dicts (keyed by ISIN) are kept for
    per-asset use (see "Proyección por activo").
    """
    asset_returns = {}   # isin -> monthly returns Series
    asset_value   = {}   # isin -> current EUR value
    asset_cagr    = {}   # isin -> annualised CAGR
    for _, r in open_pos.iterrows():
        isin_, name_ = r["isin"], r["name"]
        if classify_isin(isin_) == "bond":
            continue
        price = prices.get(isin_)
        if not price:
            continue
        value = r["quantity"] * price
        if value <= 0:
            continue
        returns = historical_monthly_returns(isin_, name_)
        if returns is None or len(returns) < 12:
            continue
        asset_returns[isin_] = returns
        asset_value[isin_]   = value
        asset_cagr[isin_]    = float((1 + returns).prod() ** (12 / len(returns)) - 1)

    total_weight = sum(asset_value.values())
    if total_weight > 0:
        weights = {k: v / total_weight for k, v in asset_value.items()}
        weighted_cagr = sum(weights[k] * asset_cagr[k] for k in weights)

        returns_df = pd.DataFrame(asset_returns).dropna()
        if returns_df.shape[1] >= 2 and len(returns_df) >= 6:
            w_vec = np.array([weights[k] for k in returns_df.columns])
            cov_monthly = returns_df.cov().values
            port_var_monthly = float(w_vec @ cov_monthly @ w_vec.T)
            weighted_vol = float(np.sqrt(max(port_var_monthly, 0.0)) * np.sqrt(12))
        else:
            # Single asset, or not enough overlapping months to correlate —
            # nothing to diversify against, so fall back to a value-weighted
            # average of individual volatilities.
            weighted_vol = sum(
                weights[k] * float(asset_returns[k].std() * np.sqrt(12)) for k in weights
            )
    else:
        weighted_cagr = None
        weighted_vol = None

    return weighted_cagr, weighted_vol, asset_returns, asset_value, asset_cagr


def _historial_para_tendencia(monthly_inv: pd.Series) -> pd.Series:
    """Los meses que sirven para deducir una tendencia.

    Todo lo que se deriva del histórico de aportaciones pasa por aquí, para
    que la media, la recta del gráfico y la tasa de crecimiento cuenten la
    misma historia. `contribution_window` (en data.py) hace cuatro cosas:

      - quita el mes en curso, que va a medias
      - se queda con los últimos MESES_VENTANA naturales (hoy, 24)
      - cuenta como 0 € los meses sin inversión, que son un dato y no un hueco
      - descarta los vacíos anteriores a la primera aportación de la ventana
    """
    return contribution_window(monthly_inv)


def _trend_growth_rate(monthly_inv: pd.Series, y: np.ndarray) -> float:
    """Tasa mensual compuesta de crecimiento de las aportaciones.

    Ajuste log-lineal ponderado sobre la ventana de `_historial_para_tendencia`
    —no sobre el histórico entero—, sin recortar: la amortiguación de
    `aportaciones_proyectadas` es lo que evita que la proyección se dispare.
    """
    recent_y = _historial_para_tendencia(monthly_inv).values.astype(float)
    recent_X = np.arange(len(recent_y)).reshape(-1, 1).astype(float)
    w = exponential_weights(len(recent_y))

    # Los meses a cero se descartan aquí porque el ajuste es sobre el
    # logaritmo, no porque no cuenten: log(0) no existe.
    pos_mask = recent_y > 0
    if pos_mask.sum() >= 2:
        log_model = LinearRegression().fit(
            recent_X[pos_mask], np.log(recent_y[pos_mask]),
            sample_weight=w[pos_mask])
        # Sin recortar: la tasa es la que dice el histórico. Lo que impide que
        # la proyección se dispare es la amortiguación, no un tope.
        return float(np.exp(log_model.coef_[0]) - 1)
    return 0.0


def _contribution_trend(monthly_inv: pd.Series):
    """Media, pendiente y línea de tendencia de las aportaciones.

    Se calcula sobre el último año cerrado, no sobre el histórico entero: un
    movimiento suelto de hace años ancla la recta en un punto que no
    representa nada, y el mes en curso la hunde porque aún va a medias. La
    media alimenta además el valor por defecto de las proyecciones, así que el
    sesgo no se quedaba en el gráfico.

    Devuelve (tramo, media, pendiente, valores de la recta sobre ese tramo).
    """
    tramo = _historial_para_tendencia(monthly_inv)
    y = tramo.values.astype(float)

    if len(y) == 0:
        return tramo, 0.0, 0.0, []

    # Media ponderada y regresión ponderada con los mismos pesos: lo reciente
    # manda en las dos, y las dos cuentan la misma historia.
    w = exponential_weights(len(y))
    media = float(np.average(y, weights=w))

    if len(y) < 2:
        return tramo, media, 0.0, y.tolist()

    X = np.arange(len(y)).reshape(-1, 1).astype(float)
    modelo = LinearRegression().fit(X, y, sample_weight=w)
    return tramo, media, float(modelo.coef_[0]), modelo.predict(X).tolist()


def _build_scenarios(scenario_mode: str, weighted_cagr, effective_vol):
    """{label: annual_rate} for Conservador/Moderado/Agresivo, from either
    the fixed 5/8/12% defaults or the portfolio's real CAGR ± volatility.
    """
    if scenario_mode == "Basados en histórico de mis activos" and weighted_cagr is not None:
        # Sin recortar. Estaba en [-30%, +50%], y era un recorte silencioso
        # sobre una cifra calculada de los activos del usuario: con una
        # cartera de CAGR real del 60% se enseñaba 50% sin decir que se había
        # tocado. Si el número sale raro, el problema está en el histórico de
        # precios, y esconderlo no lo arregla.
        conservative = float(weighted_cagr - effective_vol)
        moderate     = float(weighted_cagr)
        aggressive   = float(weighted_cagr + effective_vol)
        return {
            f"Conservador ({conservative * 100:.1f}%)": conservative,
            f"Moderado ({moderate * 100:.1f}%)":         moderate,
            f"Agresivo ({aggressive * 100:.1f}%)":        aggressive,
        }
    return {"Conservador (5%)": 0.05, "Moderado (8%)": 0.08, "Agresivo (12%)": 0.12}


def _required_contribution(target: float, v0: float, n_months: int,
                            annual_rate: float, variable: bool,
                            growth_rate: float = 0.0) -> float:
    """Monthly contribution needed to go from v0 to target in n_months at
    annual_rate return, using the same month-by-month compounding as the
    projection charts (grow, then add the contribution). If `variable`,
    this is the STARTING contribution, growing by growth_rate compounded
    monthly thereafter — otherwise it's a flat contribution every month.
    Can come out <= 0 if the target is already met by growth alone.
    """
    monthly_rate = annual_rate / 12
    compound     = (1 + monthly_rate) ** n_months
    remaining    = target - v0 * compound

    if variable:
        x = (1 + growth_rate) / (1 + monthly_rate)
        growth_sum = (x * (x ** n_months - 1) / (x - 1)) if abs(x - 1) > 1e-9 else float(n_months)
        denom = compound * growth_sum
    else:
        denom = (compound - 1) / monthly_rate if abs(monthly_rate) > 1e-12 else float(n_months)

    if denom == 0:
        return float("inf")
    return remaining / denom


def _required_growth_rate(target: float, v0: float, n_months: int,
                           annual_rate: float, c0: float) -> float | None:
    """Monthly compound growth rate a starting contribution of c0 must grow
    by (same compounding as _required_contribution's variable mode) so that
    v0 plus those contributions reach target in n_months at annual_rate
    return. Solved by bisection since there's no closed form for the rate.

    Returns None if unsolvable: target already met by growth alone (caller
    shows a different message), or c0 <= 0 with a gap still remaining.
    """
    monthly_rate = annual_rate / 12
    compound     = (1 + monthly_rate) ** n_months
    remaining    = target - v0 * compound

    if remaining <= 0 or c0 <= 0:
        return None

    required_sum = remaining / (c0 * compound)

    def geometric_sum(x: float) -> float:
        return x * (x ** n_months - 1) / (x - 1) if abs(x - 1) > 1e-9 else float(n_months)

    lo, hi = 1e-9, 2.0
    while geometric_sum(hi) < required_sum and hi < 1e6:
        hi *= 2
    for _ in range(100):
        mid = (lo + hi) / 2
        if geometric_sum(mid) < required_sum:
            lo = mid
        else:
            hi = mid
    x = (lo + hi) / 2
    return x * (1 + monthly_rate) - 1


# ── Page 4: Forecasting ───────────────────────────────────────────────────────

def page_forecasting(pos: pd.DataFrame, tx: pd.DataFrame) -> None:
    st.title(
        "Proyección",
        help="Proyecta el valor futuro de tu cartera bajo tres "
             "escenarios de rentabilidad (Conservador/Moderado/"
             "Agresivo). Los controles de la barra lateral fijan cuánto "
             "aportas cada mes (aportación fija o con una tendencia "
             "estimada de tu histórico) y si los escenarios de "
             "rentabilidad usan cifras fijas o el CAGR/volatilidad "
             "reales de tus activos. El gráfico de **Proyección de "
             "cartera** tiene en cuenta tus aportaciones futuras para "
             "el cálculo de la predicción; el de **un activo suelto**, "
             "no -- la aportación a un activo concreto suele ser "
             "puntual (una compra aislada, no un ritmo mensual) y puede "
             "dispararse un mes y caer a cero al siguiente, así que no "
             "hay una cifra representativa que asumir -- solo proyecta "
             "la revalorización de lo que ya tienes en él. El horizonte "
             "(años) se controla junto al título de cada gráfico, no en "
             "la barra lateral, y es independiente entre cartera y "
             "activo individual. Cada control tiene su propio icono ⓘ "
             "con el detalle de cómo se calcula.",
    )

    open_pos  = open_positions(pos)
    prices    = get_all_prices(tuple(
        (r["isin"], r["name"]) for _, r in open_pos.iterrows()
    ))
    portfolio_val = sum(
        r["quantity"] * prices[r["isin"]] if prices.get(r["isin"]) is not None
        else (r["quantity"] if classify_isin(r["isin"]) == "bond" else 0.0)
        for _, r in open_pos.iterrows()
    )

    weighted_cagr, weighted_vol, asset_returns, asset_value, asset_cagr = \
        _portfolio_return_stats(open_pos, prices)

    monthly_inv = monthly_investments(tx)

    if monthly_inv.empty:
        st.info("No hay historial de compras para proyectar.")
        return

    y = monthly_inv.values.astype(float)
    tramo_reciente, avg_contribution, slope, trend_line = _contribution_trend(monthly_inv)

    trend_direction = "creciente" if slope > 1 else ("decreciente" if slope < -1 else "estable")

    computed_growth = _trend_growth_rate(monthly_inv, y)
    # Estimada de la serie, no fijada: es lo que impide que una tasa alta
    # se amplifique por un exponente elegido a dedo.
    persistencia = estimar_persistencia(_historial_para_tendencia(monthly_inv))

    # El horizonte se necesita ya aquí abajo (texto de meseta en el modo
    # variable, más adelante) aunque el control se renderice junto al título
    # del gráfico: el valor vive en session_state desde este punto, el
    # widget solo decide dónde se ve y se puede tocar.
    if "horizonte_slider" not in st.session_state:
        st.session_state.horizonte_slider = 5
        st.session_state.horizonte_number = 5

    def _sync_horizon_from_slider():
        st.session_state.horizonte_number = st.session_state.horizonte_slider

    def _sync_horizon_from_number():
        st.session_state.horizonte_slider = st.session_state.horizonte_number

    horizon_years = st.session_state.horizonte_slider
    n_months = horizon_years * 12

    c1, c2, c3 = st.columns(3)
    with c1:
        kpi("Valor portfolio actual", fmt(portfolio_val), color=BLUE)
    with c2:
        kpi("Aportación media mensual", fmt(avg_contribution), color=GREEN)
    with c3:
        kpi("Tendencia aportaciones", trend_direction.capitalize(),
            color=GREEN if slope > 0 else (RED if slope < -1 else DIM))

    st.markdown("<br>", unsafe_allow_html=True)

    # User override
    with st.sidebar:
        st.markdown(f"<hr style='border-color:{BORDER}'>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{DIM};font-size:.78rem;text-transform:uppercase'>Proyección</p>",
                    unsafe_allow_html=True)

        if "aportacion_slider" not in st.session_state:
            default_val = int(max(avg_contribution, 0))
            st.session_state.aportacion_slider = default_val
            st.session_state.aportacion_number = default_val

        def _sync_from_slider():
            st.session_state.aportacion_number = st.session_state.aportacion_slider

        def _sync_from_number():
            st.session_state.aportacion_slider = st.session_state.aportacion_number

        st.slider(
            "Aportación mensual (€)",
            min_value=0, max_value=5000, step=50,
            key="aportacion_slider",
            on_change=_sync_from_slider,
            help="Lo que inviertes en tu cartera cada mes. Por defecto es la "
                 "media ponderada de tu histórico de aportaciones (más peso "
                 "a lo reciente), pero puedes ajustarla a mano. Se usa de "
                 "forma compuesta, mes a mes, para calcular el valor futuro "
                 "de tu cartera.",
        )
        st.number_input(
            "Aportación mensual (€) — exacta",
            min_value=0, max_value=5000, step=50,
            key="aportacion_number",
            on_change=_sync_from_number,
        )

        def _use_average():
            avg_val = int(max(avg_contribution, 0))
            st.session_state.aportacion_slider = avg_val
            st.session_state.aportacion_number = avg_val

        st.button("Usar aportación media mensual", on_click=_use_average)
        override = st.session_state.aportacion_slider

        contrib_mode = st.radio(
            "Modo de aportación",
            options=["Fijo", "Variable (tendencia)"],
            key="contrib_mode",
            help="Fijo: cada mes se aporta siempre la cantidad de arriba. "
                 "Variable (tendencia): la aportación crece o decrece según "
                 "una tendencia mensual estimada de tu histórico mediante "
                 "una regresión ponderada (más peso a lo reciente), en vez "
                 "de quedarse constante.",
        )

        if contrib_mode == "Variable (tendencia)":
            if "tendencia_slider" not in st.session_state:
                # Empieza en la tasa estimada del histórico, con el aviso de
                # abajo si es descabellada: un ajuste log-lineal sobre un
                # histórico corto puede leer un salto de ritmo puntual como
                # si fuera a componer para siempre.
                default_growth = round(computed_growth * 100, 3)
                st.session_state.tendencia_slider = default_growth
                st.session_state.tendencia_number = default_growth

            def _sync_growth_from_slider():
                st.session_state.tendencia_number = st.session_state.tendencia_slider

            def _sync_growth_from_number():
                st.session_state.tendencia_slider = st.session_state.tendencia_number

            # El deslizador necesita extremos, pero no van fijos: salen de la
            # propia tasa estimada, con un mínimo para que siempre haya rango
            # con el que jugar. El campo exacto va sin límites.
            tope_pct = max(1.0, round(abs(computed_growth) * 100 * 2, 1))
            st.slider(
                "Tendencia mensual (%)",
                min_value=-tope_pct, max_value=tope_pct, step=0.05,
                key="tendencia_slider",
                on_change=_sync_growth_from_slider,
                help="Cuánto crece (o decrece) tu aportación cada mes, en "
                     "modo Variable. Por defecto es la tasa estimada de tu "
                     "histórico mediante una regresión log-lineal "
                     "ponderada -- si el histórico es corto, una subida "
                     "puntual de una vez puede leerse como si fuera a "
                     "repetirse cada mes para siempre, disparando la "
                     "proyección a largo plazo mucho más de lo realista.",
            )
            st.number_input(
                "Tendencia mensual (%) — exacta",
                step=0.001, format="%.3f",
                key="tendencia_number",
                on_change=_sync_growth_from_number,
            )

            def _use_estimated_growth():
                default_growth = round(computed_growth * 100, 3)
                st.session_state.tendencia_slider = default_growth
                st.session_state.tendencia_number = default_growth

            st.button("Usar tendencia estimada", on_click=_use_estimated_growth)

            # Un ajuste log-lineal no distingue "subiste el ritmo una vez y
            # te estabilizaste" de "vas camino de seguir subiendo": mide la
            # pendiente entre el tramo antiguo y el reciente y la extrapola
            # como si compusiera para siempre. Por encima de ±20% anual eso
            # ya es más ruido que señal, así que se avisa en vez de darlo por
            # bueno.
            annual_growth = tasa_anual(computed_growth)
            growth_caption = (f"Estimada del histórico: {computed_growth * 100:+.3f}% mensual "
                               f"({annual_growth * 100:+.1f}% anual).")
            if abs(annual_growth) > 0.20:
                st.caption(f"⚠️ {growth_caption} Es una extrapolación ingenua de un "
                           f"salto de ritmo puntual, no una previsión — mejor ajustar "
                           f"el porcentaje a mano arriba que darlo por bueno.")
            else:
                st.caption(growth_caption)

            monthly_growth = st.session_state.tendencia_slider / 100

            if "persistencia_slider" not in st.session_state:
                st.session_state.persistencia_slider = float(persistencia)

            st.slider(
                "Amortiguación de la tendencia",
                min_value=0.0, max_value=1.0, step=0.01,
                key="persistencia_slider",
                help="Cada mes, el impulso extra que añade la subida se "
                     "multiplica por este valor respecto al mes anterior "
                     "-- con 0 desaparece del todo tras el primer mes y la "
                     "aportación se aplana enseguida; con 1 no se reduce "
                     "nunca y compone sin freno. Por ejemplo, con "
                     "amortiguación 0.9 y un impulso inicial de +100€: el "
                     "mes siguiente ese impulso es 100×0.9 → 90€, el "
                     "siguiente 90×0.9 → 81€, y así sucesivamente -- la "
                     "suma converge a una meseta (asíntota) en vez de "
                     "crecer sin límite. Cuanto más alta salga la "
                     "tendencia mensual de arriba, más importa bajar este "
                     "valor si no te la crees.",
            )
            persistencia_usada = st.session_state.persistencia_slider

            def _use_estimated_persistence():
                st.session_state.persistencia_slider = float(persistencia)

            st.button("Usar la estimada de mi histórico",
                      on_click=_use_estimated_persistence)
            st.caption(f"Amortiguación estimada de tu histórico: {persistencia:.2f}")

            proyectadas = aportaciones_proyectadas(
                override, monthly_growth, n_months, persistencia_usada)
            meseta = meseta_aportacion(override, monthly_growth, persistencia_usada)
            final = float(proyectadas[-1]) if len(proyectadas) else float(override)

            if monthly_growth and persistencia_usada > 0:
                if np.isfinite(meseta):
                    st.caption(
                        f"De {fmt(override)} pasarías a **{fmt(final)}** al mes "
                        f"en {horizon_years} año(s), estabilizándose en torno a "
                        f"{fmt(meseta)}.")
                else:
                    st.caption(
                        f"⚠️ De {fmt(override)} pasarías a **{fmt(final)}** al "
                        f"mes en {horizon_years} año(s), **y sin estabilizarse**.")
            elif not monthly_growth:
                st.caption(
                    f"Con tendencia 0%, la aportación queda plana en "
                    f"{fmt(override)} -- igual que en modo Fijo.")
            else:
                st.caption(
                    f"Con amortiguación 0, el impulso de la tendencia no llega "
                    f"a aplicarse ni un mes: la aportación queda plana en "
                    f"{fmt(override)} -- igual que en modo Fijo. Sube la "
                    f"amortiguación de arriba si crees que la tendencia sí "
                    f"debería notarse.")

        st.markdown(f"<hr style='border-color:{BORDER}'>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{DIM};font-size:.78rem;text-transform:uppercase'>"
                    f"Escenarios de rentabilidad</p>", unsafe_allow_html=True)
        scenario_mode = st.radio(
            "Escenarios",
            options=["Fijos (5/8/12%)", "Basados en histórico de mis activos"],
            key="scenario_mode",
            help="Fijos: el escenario típico de una cartera diversificada "
                 "(5/8/12% anual). Basados en histórico: se calculan a "
                 "partir del CAGR y la volatilidad reales de tus activos. "
                 "Con activos muy volátiles los tres escenarios pueden "
                 "separarse mucho entre sí -- toma las proyecciones con "
                 "cautela.",
        )
        effective_vol = None
        cagr_usado = weighted_cagr
        if scenario_mode == "Basados en histórico de mis activos":
            if weighted_cagr is None:
                st.warning("No hay histórico de precios suficiente para tus activos "
                           "actuales; se usan los escenarios fijos.")
            else:
                if "cagr_slider" not in st.session_state:
                    default_cagr = round(weighted_cagr * 100, 2)
                    st.session_state.cagr_slider = default_cagr
                    st.session_state.cagr_number = default_cagr

                def _sync_cagr_from_slider():
                    st.session_state.cagr_number = st.session_state.cagr_slider

                def _sync_cagr_from_number():
                    st.session_state.cagr_slider = st.session_state.cagr_number

                cagr_tope = max(10.0, round(abs(weighted_cagr) * 100 * 2, 1))
                st.slider(
                    "CAGR anualizado (%)",
                    min_value=-cagr_tope, max_value=cagr_tope, step=0.1,
                    key="cagr_slider",
                    on_change=_sync_cagr_from_slider,
                    help="Tasa de crecimiento anual compuesta -- la "
                         "rentabilidad media anual que, aplicada de forma "
                         "compuesta, explica el paso del valor inicial al "
                         "final. Por defecto es la media ponderada por "
                         "valor del CAGR real de tus activos (10 años de "
                         "histórico), pero puedes ajustarla a mano.",
                )
                st.number_input(
                    "CAGR anualizado (%) — exacto",
                    step=0.01, format="%.2f",
                    key="cagr_number",
                    on_change=_sync_cagr_from_number,
                )

                def _use_computed_cagr():
                    default_cagr = round(weighted_cagr * 100, 2)
                    st.session_state.cagr_slider = default_cagr
                    st.session_state.cagr_number = default_cagr

                st.button("Usar CAGR calculado", on_click=_use_computed_cagr)
                st.caption(f"CAGR ponderado de tu cartera (10 años): {weighted_cagr * 100:+.1f}%")

                cagr_usado = st.session_state.cagr_slider / 100

                if "volatilidad_slider" not in st.session_state:
                    default_vol = round(weighted_vol * 100, 3)
                    st.session_state.volatilidad_slider = default_vol
                    st.session_state.volatilidad_number = default_vol

                def _sync_vol_from_slider():
                    st.session_state.volatilidad_number = st.session_state.volatilidad_slider

                def _sync_vol_from_number():
                    st.session_state.volatilidad_slider = st.session_state.volatilidad_number

                st.slider(
                    "Volatilidad anualizada (%)",
                    min_value=0.0, max_value=150.0, step=0.5,
                    key="volatilidad_slider",
                    on_change=_sync_vol_from_slider,
                    help="Cuánto varía la rentabilidad de un año a otro "
                         "respecto a la media -- a más volatilidad, más se "
                         "separan los escenarios Conservador y Agresivo del "
                         "Moderado (CAGR ± volatilidad). Por defecto se "
                         "calcula con la covarianza real entre tus "
                         "activos, no un promedio ingenuo, así que la "
                         "diversificación ya reduce esta cifra.",
                )
                st.number_input(
                    "Volatilidad anualizada (%) — exacta",
                    min_value=0.0, max_value=150.0, step=0.001, format="%.3f",
                    key="volatilidad_number",
                    on_change=_sync_vol_from_number,
                )

                def _use_computed_vol():
                    default_vol = round(weighted_vol * 100, 3)
                    st.session_state.volatilidad_slider = default_vol
                    st.session_state.volatilidad_number = default_vol

                st.button("Usar volatilidad calculada", on_click=_use_computed_vol)
                st.caption(f"Volatilidad calculada (covarianza real de tu cartera): "
                           f"{weighted_vol * 100:.3f}%")

                effective_vol = st.session_state.volatilidad_slider / 100

    # Project the chosen horizon
    if contrib_mode == "Variable (tendencia)":
        # La del control, no la estimada: el usuario puede haberla movido.
        proj_contribs = aportaciones_proyectadas(
            override, monthly_growth, n_months, persistencia_usada)
    else:
        proj_contribs = np.full(n_months, float(override))

    scenarios = _build_scenarios(scenario_mode, cagr_usado, effective_vol)
    colors_s  = [GREEN, BLUE, ORANGE]

    future_labels = [
        str(pd.Period(monthly_inv.index[-1], "M") + i + 1)
        for i in range(n_months)
    ]

    fig = go.Figure()

    for (label, annual_rate), color in zip(scenarios.items(), colors_s):
        monthly_rate = annual_rate / 12
        values = []
        v = portfolio_val
        for c in proj_contribs:
            v = v * (1 + monthly_rate) + float(c)
            values.append(v)
        fig.add_trace(go.Scatter(
            x=future_labels, y=values,
            mode="lines", name=label, line_color=color, line_width=2,
        ))

    # Historical reference line
    hist_months = [str(p) for p in monthly_inv.index]
    hist_vals   = []
    v = portfolio_val
    for c in monthly_inv.values[::-1]:   # rough back-fill (visual only)
        pass
    # Just show current value as baseline point
    fig.add_trace(go.Scatter(
        x=[future_labels[0]], y=[portfolio_val],
        mode="markers", name="Hoy",
        marker=dict(color="white", size=10, symbol="circle"),
    ))

    fig.update_layout(
        # Vacío, no ausente: dark_fig() fija title_font_* con la notación de
        # subrayado de Plotly, que crea layout.title aunque no se le ponga
        # texto -- y Plotly.js pinta literalmente "undefined" si el objeto
        # title existe pero text no. El título visible ahora es el
        # st.subheader de más abajo.
        title="",
        xaxis_title="Mes", yaxis_title="Valor (€)",
        yaxis_ticksuffix=" €",
        hovermode="x unified",
    )
    fig.update_xaxes(nticks=20)
    fig.update_traces(hovertemplate="%{y:,.3~s} €")

    with st.container(key="horizonte_row"):
        col_title, col_slider, col_number = st.columns(
            [1, 1, 1], vertical_alignment="center")
        with col_title:
            st.subheader(
                f"Proyección de cartera a {horizon_years} "
                f"año{'s' if horizon_years != 1 else ''}",
                help="Incluye las aportaciones futuras según el modo elegido (fijo "
                     "o variable) -- no es solo la revalorización del valor "
                     "actual. Con horizontes largos, el escenario Agresivo puede "
                     "crecer tanto que los otros dos se vean aplastados contra el "
                     "eje; usa el zoom del gráfico para verlos con detalle.",
            )
        with col_slider:
            st.slider(
                "Horizonte (años)",
                min_value=1, max_value=100, step=1,
                key="horizonte_slider",
                on_change=_sync_horizon_from_slider,
            )
        with col_number:
            st.number_input(
                "Exacto",
                min_value=1, max_value=100, step=1,
                key="horizonte_number",
                on_change=_sync_horizon_from_number,
            )
    st.plotly_chart(dark_fig(fig), width="stretch")

    # Contribution history + trend
    st.subheader(
        "Histórico de aportaciones",
        help="Aportación neta por mes, con todo tu histórico -- compras "
             "menos ventas: vender un activo para comprar otro no es "
             "dinero nuevo, así que se resta del total de ese mes. De aquí "
             "salen la media ponderada y la tendencia (línea naranja) "
             "utilizadas para las predicciones del valor de la cartera.",
    )
    hist_strs = [str(p) for p in monthly_inv.index]

    fig2 = go.Figure()
    fig2.add_trace(go.Bar(
        x=hist_strs, y=monthly_inv.values.tolist(),
        name="Aportado", marker_color=BLUE, opacity=0.8,
    ))
    # La recta sólo se dibuja sobre el tramo que la ha producido. Extenderla
    # hacia atrás la haría cruzar años sin un solo movimiento, que es
    # justamente lo que la falseaba.
    fig2.add_trace(go.Scatter(
        x=[str(p) for p in tramo_reciente.index], y=trend_line,
        mode="lines", name="Tendencia", line_color=ORANGE, line_dash="dash",
    ))
    if len(tramo_reciente) < len(monthly_inv):
        sin_inversion = int((tramo_reciente == 0).sum())
        nota = (f"Tendencia y media ponderadas sobre {tramo_reciente.index[0]} – "
                f"{tramo_reciente.index[-1]} (los meses recientes pesan más: "
                f"el peso se reduce a la mitad cada {SEMIVIDA_MESES} meses)")
        if sin_inversion:
            nota += (f", contando {sin_inversion} mes"
                     f"{'es' if sin_inversion > 1 else ''} sin inversión como 0 €")
        if monthly_inv.index[-1] == pd.Period(pd.Timestamp.now(), freq="M"):
            nota += ". El mes en curso queda fuera: aún no ha terminado"
        fig2.add_annotation(
            xref="paper", yref="paper", x=0, y=1.08, showarrow=False,
            font=dict(size=11, color=DIM), text=nota,
        )
    fig2.update_layout(xaxis_title="", yaxis_ticksuffix=" €", title="")
    fig2.update_traces(hovertemplate="%{y:,.3~s} €")
    st.plotly_chart(dark_fig(fig2), width="stretch")

    # Per-asset projection
    st.subheader("Proyección por activo")

    # Horizonte propio, independiente del de la cartera de arriba: puede
    # interesar mirar un activo a corto plazo mientras la cartera se
    # proyecta a 40 años, o al revés.
    if "horizonte_activo_slider" not in st.session_state:
        st.session_state.horizonte_activo_slider = 5
        st.session_state.horizonte_activo_number = 5

    def _sync_horizon_activo_from_slider():
        st.session_state.horizonte_activo_number = st.session_state.horizonte_activo_slider

    def _sync_horizon_activo_from_number():
        st.session_state.horizonte_activo_slider = st.session_state.horizonte_activo_number

    horizon_years_activo = st.session_state.horizonte_activo_slider
    n_months_activo = horizon_years_activo * 12

    open_names = open_pos["name"].tolist()
    sel_asset  = st.selectbox("Selecciona activo", open_names)
    if sel_asset:
        sel_row   = open_pos[open_pos["name"] == sel_asset].iloc[0]
        sel_isin  = sel_row["isin"]
        sel_price = prices.get(sel_isin)
        # Los bonos (ISIN XS*) no cotizan en yfinance -- no hay precio que
        # buscar, su valor es el nominal. Sin este caso especial, cualquier
        # bono caía al 0€ del "sin precio" en vez de al nominal que ya usa
        # el resto del dashboard (portfolio_val, más abajo, sí lo aplica).
        sel_is_bond = classify_isin(sel_isin) == "bond"
        if sel_price is not None:
            sel_val = sel_row["quantity"] * sel_price
        elif sel_is_bond:
            sel_val = sel_row["quantity"]
        else:
            sel_val = 0.0
        sel_val_known = sel_price is not None or sel_is_bond

        # Sin aportación futura asumida: a diferencia de la aportación total
        # de cartera, la de un activo suelto puede moverse muchísimo de un
        # mes a otro (una compra puntual la dispara, el mes siguiente cae a
        # cero) y no hay ventana que la haga representativa. Se proyecta
        # solo la revalorización del valor actual, sin sumar nada cada mes.
        kpi(f"Valor actual — {sel_asset}", fmt(sel_val) if sel_val_known else "—", color=BLUE)

        # Per-asset scenarios use this asset's own CAGR/volatility, not the
        # portfolio-blended figures — a single volatile holding shouldn't be
        # projected with the (diversified) risk of the whole portfolio.
        if scenario_mode == "Basados en histórico de mis activos" and sel_isin in asset_cagr:
            asset_own_cagr = asset_cagr[sel_isin]
            asset_own_vol  = float(asset_returns[sel_isin].std() * np.sqrt(12))
            # Sin recortar, igual que los escenarios de cartera.
            asset_conservative = float(asset_own_cagr - asset_own_vol)
            asset_moderate     = float(asset_own_cagr)
            asset_aggressive   = float(asset_own_cagr + asset_own_vol)
            asset_scenarios = {
                f"Conservador ({asset_conservative * 100:.1f}%)": asset_conservative,
                f"Moderado ({asset_moderate * 100:.1f}%)":         asset_moderate,
                f"Agresivo ({asset_aggressive * 100:.1f}%)":        asset_aggressive,
            }
            st.caption(f"CAGR propio de {sel_asset} (10 años): {asset_own_cagr * 100:+.1f}%  "
                       f"± {asset_own_vol * 100:.1f}% de volatilidad anualizada (propia del activo).")
        elif scenario_mode == "Basados en histórico de mis activos":
            st.caption(f"No hay histórico de precios suficiente para {sel_asset}; "
                       f"se usan los escenarios fijos.")
            asset_scenarios = {"Conservador (5%)": 0.05, "Moderado (8%)": 0.08, "Agresivo (12%)": 0.12}
        else:
            asset_scenarios = scenarios

        future_labels_activo = [
            str(pd.Period(monthly_inv.index[-1], "M") + i + 1)
            for i in range(n_months_activo)
        ]

        fig3 = go.Figure()
        for (label, annual_rate), color in zip(asset_scenarios.items(), colors_s):
            monthly_rate = annual_rate / 12
            values = []
            v = sel_val
            for _ in range(n_months_activo):
                v = v * (1 + monthly_rate)
                values.append(v)
            fig3.add_trace(go.Scatter(
                x=future_labels_activo, y=values,
                mode="lines", name=label, line_color=color, line_width=2,
            ))
        fig3.update_layout(
            title="",  # vacío, no ausente -- ver comentario en fig.update_layout arriba
            xaxis_title="Mes", yaxis_ticksuffix=" €",
            hovermode="x unified",
        )
        fig3.update_xaxes(nticks=20)
        fig3.update_traces(hovertemplate="%{y:,.3~s} €")

        with st.container(key="horizonte_activo_row"):
            col_title, col_slider, col_number = st.columns(
                [1, 1, 1], vertical_alignment="center")
            with col_title:
                st.subheader(
                    f"Proyección — {sel_asset}",
                    help="Solo proyecta la revalorización del valor actual de "
                         "este activo -- NO suma ninguna aportación futura. "
                         "Con horizontes largos, el escenario Agresivo puede "
                         "crecer tanto que los otros dos se vean aplastados "
                         "contra el eje; usa el zoom del gráfico para verlos "
                         "con detalle.",
                )
            with col_slider:
                st.slider(
                    "Horizonte (años)",
                    min_value=1, max_value=100, step=1,
                    key="horizonte_activo_slider",
                    on_change=_sync_horizon_activo_from_slider,
                )
            with col_number:
                st.number_input(
                    "Exacto",
                    min_value=1, max_value=100, step=1,
                    key="horizonte_activo_number",
                    on_change=_sync_horizon_activo_from_number,
                )
        st.plotly_chart(dark_fig(fig3), width="stretch")


# ── Page 5: Objetivos ─────────────────────────────────────────────────────────

def page_objetivos(pos: pd.DataFrame, tx: pd.DataFrame) -> None:
    st.title(
        "Objetivos",
        help="La pregunta inversa a Proyección: en vez de proyectar tu "
             "cartera y ver a dónde llega, fijas un **objetivo en "
             "euros** y un plazo, y se calcula qué hace falta para "
             "llegar. Modo **Fijo**: cuánto tendrías que aportar cada "
             "mes, constante, para llegarlo. Modo **Variable**: "
             "partiendo de una aportación inicial, qué tendencia de "
             "crecimiento mensual haría falta. Los escenarios de "
             "rentabilidad (Conservador/Moderado/Agresivo) pueden usar "
             "cifras fijas o el CAGR/volatilidad reales de tus activos "
             "-- se eligen en la barra lateral. El horizonte (años) se "
             "controla junto al título del gráfico, no en la barra "
             "lateral.",
    )

    open_pos  = open_positions(pos)
    prices    = get_all_prices(tuple(
        (r["isin"], r["name"]) for _, r in open_pos.iterrows()
    ))
    portfolio_val = sum(
        r["quantity"] * prices[r["isin"]] if prices.get(r["isin"]) is not None
        else (r["quantity"] if classify_isin(r["isin"]) == "bond" else 0.0)
        for _, r in open_pos.iterrows()
    )

    weighted_cagr, weighted_vol, *_ = _portfolio_return_stats(open_pos, prices)

    monthly_inv = monthly_investments(tx)
    if monthly_inv.empty:
        st.info("No hay historial de compras para proyectar.")
        return

    y = monthly_inv.values.astype(float)
    # Misma media que en Proyección: sobre el tramo final sin huecos, no sobre
    # el histórico entero. Alimenta el valor por defecto de la aportación.
    _tramo, avg_contribution, _slope, _linea = _contribution_trend(monthly_inv)

    # El horizonte se necesita ya aquí abajo aunque el control se renderice
    # junto al título del gráfico, igual que en Proyección: el valor vive en
    # session_state desde este punto, el widget solo decide dónde se ve.
    if "obj_horizonte_slider" not in st.session_state:
        st.session_state.obj_horizonte_slider = 5
        st.session_state.obj_horizonte_number = 5

    def _obj_sync_horizon_from_slider():
        st.session_state.obj_horizonte_number = st.session_state.obj_horizonte_slider

    def _obj_sync_horizon_from_number():
        st.session_state.obj_horizonte_slider = st.session_state.obj_horizonte_number

    horizon_years = st.session_state.obj_horizonte_slider
    n_months = horizon_years * 12

    c1, c2 = st.columns(2)
    with c1:
        kpi("Valor portfolio actual", fmt(portfolio_val), color=BLUE)
    with c2:
        kpi("Aportación media mensual", fmt(avg_contribution), color=GREEN)

    st.markdown("<br>", unsafe_allow_html=True)

    with st.sidebar:
        st.markdown(f"<hr style='border-color:{BORDER}'>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{DIM};font-size:.78rem;text-transform:uppercase'>Objetivo</p>",
                    unsafe_allow_html=True)

        if "obj_target_slider" not in st.session_state:
            st.session_state.obj_target_slider = 100_000
            st.session_state.obj_target_number = 100_000

        def _obj_sync_target_from_slider():
            st.session_state.obj_target_number = st.session_state.obj_target_slider

        def _obj_sync_target_from_number():
            st.session_state.obj_target_slider = st.session_state.obj_target_number

        st.slider(
            "Objetivo (€)",
            min_value=0, max_value=1_000_000, step=1000,
            key="obj_target_slider",
            on_change=_obj_sync_target_from_slider,
        )
        st.number_input(
            "Objetivo (€) — exacto",
            min_value=0, max_value=100_000_000, step=1000,
            key="obj_target_number",
            on_change=_obj_sync_target_from_number,
        )
        target = float(st.session_state.obj_target_slider)

        contrib_mode = st.radio(
            "Modo de aportación",
            options=["Fijo", "Variable (tendencia)"],
            key="obj_contrib_mode",
            help="Fijo: calcula qué cantidad constante tendrías que aportar "
                 "cada mes para llegar al objetivo. Variable (tendencia): "
                 "en vez de una cantidad fija, calcula qué tendencia de "
                 "crecimiento mensual haría falta partiendo de la "
                 "aportación indicada.",
        )

        if contrib_mode == "Variable (tendencia)":
            if "obj_base_aportacion_slider" not in st.session_state:
                default_val = int(max(avg_contribution, 0))
                st.session_state.obj_base_aportacion_slider = default_val
                st.session_state.obj_base_aportacion_number = default_val

            def _obj_sync_base_from_slider():
                st.session_state.obj_base_aportacion_number = st.session_state.obj_base_aportacion_slider

            def _obj_sync_base_from_number():
                st.session_state.obj_base_aportacion_slider = st.session_state.obj_base_aportacion_number

            st.slider(
                "Aportación mensual (€)",
                min_value=0, max_value=5000, step=50,
                key="obj_base_aportacion_slider",
                on_change=_obj_sync_base_from_slider,
                help="Tu aportación de partida (por defecto, la media "
                     "ponderada de tu histórico). A partir de ella se "
                     "calcula qué tendencia de crecimiento mensual sería "
                     "necesaria para llegar al objetivo -- no es la "
                     "tendencia real de tu histórico, sino la que hace "
                     "falta para conseguirlo.",
            )
            st.number_input(
                "Aportación mensual (€) — exacta",
                min_value=0, max_value=5000, step=50,
                key="obj_base_aportacion_number",
                on_change=_obj_sync_base_from_number,
            )

            def _obj_use_average_base():
                avg_val = int(max(avg_contribution, 0))
                st.session_state.obj_base_aportacion_slider = avg_val
                st.session_state.obj_base_aportacion_number = avg_val

            st.button("Usar aportación media mensual", on_click=_obj_use_average_base,
                      key="obj_use_average_base_btn")

            base_contribution = float(st.session_state.obj_base_aportacion_slider)
        else:
            base_contribution = 0.0

        st.markdown(f"<hr style='border-color:{BORDER}'>", unsafe_allow_html=True)
        st.markdown(f"<p style='color:{DIM};font-size:.78rem;text-transform:uppercase'>"
                    f"Escenarios de rentabilidad</p>", unsafe_allow_html=True)
        scenario_mode = st.radio(
            "Escenarios",
            options=["Fijos (5/8/12%)", "Basados en histórico de mis activos"],
            key="obj_scenario_mode",
            help="Fijos: el escenario típico de una cartera diversificada "
                 "(5/8/12% anual). Basados en histórico: se calculan a "
                 "partir del CAGR y la volatilidad reales de tus activos. "
                 "Con activos muy volátiles los tres escenarios pueden "
                 "separarse mucho entre sí -- toma las cifras resultantes "
                 "con cautela.",
        )
        effective_vol = None
        cagr_usado = weighted_cagr
        if scenario_mode == "Basados en histórico de mis activos":
            if weighted_cagr is None:
                st.warning("No hay histórico de precios suficiente para tus activos "
                           "actuales; se usan los escenarios fijos.")
            else:
                if "obj_cagr_slider" not in st.session_state:
                    default_cagr = round(weighted_cagr * 100, 2)
                    st.session_state.obj_cagr_slider = default_cagr
                    st.session_state.obj_cagr_number = default_cagr

                def _obj_sync_cagr_from_slider():
                    st.session_state.obj_cagr_number = st.session_state.obj_cagr_slider

                def _obj_sync_cagr_from_number():
                    st.session_state.obj_cagr_slider = st.session_state.obj_cagr_number

                cagr_tope = max(10.0, round(abs(weighted_cagr) * 100 * 2, 1))
                st.slider(
                    "CAGR anualizado (%)",
                    min_value=-cagr_tope, max_value=cagr_tope, step=0.1,
                    key="obj_cagr_slider",
                    on_change=_obj_sync_cagr_from_slider,
                    help="Tasa de crecimiento anual compuesta -- la "
                         "rentabilidad media anual que, aplicada de forma "
                         "compuesta, explica el paso del valor inicial al "
                         "final. Por defecto es la media ponderada por "
                         "valor del CAGR real de tus activos (10 años de "
                         "histórico), pero puedes ajustarla a mano.",
                )
                st.number_input(
                    "CAGR anualizado (%) — exacto",
                    step=0.01, format="%.2f",
                    key="obj_cagr_number",
                    on_change=_obj_sync_cagr_from_number,
                )

                def _obj_use_computed_cagr():
                    default_cagr = round(weighted_cagr * 100, 2)
                    st.session_state.obj_cagr_slider = default_cagr
                    st.session_state.obj_cagr_number = default_cagr

                st.button("Usar CAGR calculado", on_click=_obj_use_computed_cagr,
                          key="obj_use_computed_cagr_btn")
                st.caption(f"CAGR ponderado de tu cartera (10 años): {weighted_cagr * 100:+.1f}%")

                cagr_usado = st.session_state.obj_cagr_slider / 100

                if "obj_volatilidad_slider" not in st.session_state:
                    default_vol = round(weighted_vol * 100, 3)
                    st.session_state.obj_volatilidad_slider = default_vol
                    st.session_state.obj_volatilidad_number = default_vol

                def _obj_sync_vol_from_slider():
                    st.session_state.obj_volatilidad_number = st.session_state.obj_volatilidad_slider

                def _obj_sync_vol_from_number():
                    st.session_state.obj_volatilidad_slider = st.session_state.obj_volatilidad_number

                st.slider(
                    "Volatilidad anualizada (%)",
                    min_value=0.0, max_value=150.0, step=0.5,
                    key="obj_volatilidad_slider",
                    on_change=_obj_sync_vol_from_slider,
                    help="Cuánto varía la rentabilidad de un año a otro "
                         "respecto a la media -- a más volatilidad, más se "
                         "separan los escenarios Conservador y Agresivo del "
                         "Moderado (CAGR ± volatilidad). Por defecto se "
                         "calcula con la covarianza real entre tus "
                         "activos, no un promedio ingenuo, así que la "
                         "diversificación ya reduce esta cifra.",
                )
                st.number_input(
                    "Volatilidad anualizada (%) — exacta",
                    min_value=0.0, max_value=150.0, step=0.001, format="%.3f",
                    key="obj_volatilidad_number",
                    on_change=_obj_sync_vol_from_number,
                )

                def _obj_use_computed_vol():
                    default_vol = round(weighted_vol * 100, 3)
                    st.session_state.obj_volatilidad_slider = default_vol
                    st.session_state.obj_volatilidad_number = default_vol

                st.button("Usar volatilidad calculada", on_click=_obj_use_computed_vol,
                          key="obj_use_computed_vol_btn")
                st.caption(f"Volatilidad calculada (covarianza real de tu cartera): "
                           f"{weighted_vol * 100:.3f}%")

                effective_vol = st.session_state.obj_volatilidad_slider / 100

    scenarios = _build_scenarios(scenario_mode, cagr_usado, effective_vol)
    colors_s  = [GREEN, BLUE, ORANGE]
    is_variable = contrib_mode == "Variable (tendencia)"

    future_labels = [
        str(pd.Period(monthly_inv.index[-1], "M") + i + 1)
        for i in range(n_months)
    ]

    st.subheader(f"Aportación necesaria para llegar a {fmt(target)} en "
                 f"{horizon_years} año{'s' if horizon_years != 1 else ''}")

    fig = go.Figure()
    results: dict[str, dict] = {}

    for (label, annual_rate), color in zip(scenarios.items(), colors_s):
        monthly_rate = annual_rate / 12
        compound     = (1 + monthly_rate) ** n_months
        already_met  = (target - portfolio_val * compound) <= 0

        if is_variable:
            if already_met:
                growth_needed = None
            else:
                growth_needed = _required_growth_rate(
                    target, portfolio_val, n_months, annual_rate, base_contribution,
                )
            g = growth_needed or 0.0
            proj_contribs = [base_contribution * (1 + g) ** (i + 1) for i in range(n_months)]
            results[label] = {
                "already_met": already_met,
                "unreachable": (not already_met) and base_contribution <= 0,
                "growth_needed": growth_needed,
            }
        else:
            required = max(_required_contribution(
                target, portfolio_val, n_months, annual_rate, variable=False,
            ), 0.0)
            proj_contribs = [required] * n_months
            results[label] = {"already_met": already_met, "required": required}

        values = []
        v = portfolio_val
        for c in proj_contribs:
            v = v * (1 + monthly_rate) + c
            values.append(v)
        fig.add_trace(go.Scatter(
            x=future_labels, y=values,
            mode="lines", name=label, line_color=color, line_width=2,
        ))

    fig.add_hline(y=target, line_dash="dash", line_color=TEXT,
                  annotation_text="Objetivo", annotation_position="top left")
    fig.add_trace(go.Scatter(
        x=[future_labels[0]], y=[portfolio_val],
        mode="markers", name="Hoy",
        marker=dict(color="white", size=10, symbol="circle"),
    ))

    fig.update_layout(
        # Vacío, no ausente -- ver comentario en page_forecasting sobre el
        # bug de Plotly con dark_fig()/title_font_*.
        title="",
        xaxis_title="Mes", yaxis_title="Valor (€)",
        yaxis_ticksuffix=" €",
        hovermode="x unified",
    )
    fig.update_xaxes(nticks=20)
    fig.update_traces(hovertemplate="%{y:,.3~s} €")

    with st.container(key="obj_horizonte_row"):
        col_title, col_slider, col_number = st.columns(
            [1, 1, 1], vertical_alignment="center")
        with col_title:
            st.subheader(
                f"Camino al objetivo — {horizon_years} año"
                f"{'s' if horizon_years != 1 else ''} ({n_months} meses)"
            )
        with col_slider:
            st.slider(
                "Horizonte (años)",
                min_value=1, max_value=100, step=1,
                key="obj_horizonte_slider",
                on_change=_obj_sync_horizon_from_slider,
            )
        with col_number:
            st.number_input(
                "Exacto",
                min_value=1, max_value=100, step=1,
                key="obj_horizonte_number",
                on_change=_obj_sync_horizon_from_number,
            )
    st.plotly_chart(dark_fig(fig), width="stretch")

    cols = st.columns(3)
    for (label, _), color, col in zip(scenarios.items(), colors_s, cols):
        r = results[label]
        with col:
            if r["already_met"]:
                st.markdown(
                    f"<div style='border-left:4px solid {color};padding:8px 12px;"
                    f"background:{CARD};border-radius:6px'>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:0'>{label}</p>"
                    f"<p style='color:{color};font-weight:700;margin:4px 0 0'>"
                    f"Ya lo alcanzas solo con el crecimiento</p>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:2px 0 0'>"
                    f"No necesitas aportar nada más</p></div>",
                    unsafe_allow_html=True,
                )
            elif is_variable and r.get("unreachable"):
                st.markdown(
                    f"<div style='border-left:4px solid {color};padding:8px 12px;"
                    f"background:{CARD};border-radius:6px'>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:0'>{label}</p>"
                    f"<p style='color:{color};font-weight:700;margin:4px 0 0'>No alcanzable con 0€</p>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:2px 0 0'>"
                    f"Sube la aportación mensual base</p></div>",
                    unsafe_allow_html=True,
                )
            elif is_variable:
                g = r["growth_needed"]
                semestral = (1 + g) ** 6 - 1
                anual     = (1 + g) ** 12 - 1
                # El € es lo que de verdad se entiende: un +0,42% mensual no
                # dice nada por sí solo, pero "+14,54 €/mes" sí. Es el extra
                # sobre la aportación de hoy que tocaría *en ese punto* (mes
                # 1, 6 o 12), no el nuevo total ni un extra acumulado mes a
                # mes: el signo sale solo si la propia tasa es negativa.
                eur_mensual   = base_contribution * (1 + g) - base_contribution
                eur_semestral = base_contribution * (1 + semestral) - base_contribution
                eur_anual     = base_contribution * (1 + anual) - base_contribution
                st.markdown(
                    f"<div style='border-left:4px solid {color};padding:8px 12px;"
                    f"background:{CARD};border-radius:6px'>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:0'>{label}</p>"
                    f"<p style='color:{color};font-weight:700;margin:4px 0 0'>"
                    f"Desde {fmt(base_contribution)}/mes</p>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:6px 0 0'>Incremento necesario:</p>"
                    f"<p style='color:{TEXT};font-size:.82rem;margin:2px 0 0'>"
                    f"{g * 100:+.3f}% mensual ({eur_mensual:+,.2f} €/mes)</p>"
                    f"<p style='color:{TEXT};font-size:.82rem;margin:2px 0 0'>"
                    f"{semestral * 100:+.2f}% semestral ({eur_semestral:+,.2f} €/semestre)</p>"
                    f"<p style='color:{TEXT};font-size:.82rem;margin:2px 0 0'>"
                    f"{anual * 100:+.2f}% anual ({eur_anual:+,.2f} €/año)</p></div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    f"<div style='border-left:4px solid {color};padding:8px 12px;"
                    f"background:{CARD};border-radius:6px'>"
                    f"<p style='color:{DIM};font-size:.78rem;margin:0'>{label}</p>"
                    f"<p style='color:{color};font-weight:700;margin:4px 0 0'>"
                    f"{fmt(r['required'])}/mes fijos</p></div>",
                    unsafe_allow_html=True,
                )


# ── Navigation & entry point ──────────────────────────────────────────────────

def main() -> None:
    with st.sidebar:
        st.markdown(f"""
        <div style="padding:16px 0 8px">
          <p style="color:{GREEN};font-size:1.1rem;font-weight:700;margin:0">📊 Finance</p>
          <p style="color:{DIM};font-size:.75rem;margin:2px 0 0">Personal Dashboard</p>
        </div>""", unsafe_allow_html=True)

        page = st.radio(
            "Navegación",
            ["Dashboard", "Gastos", "Activos", "Proyección", "Objetivos"],
            label_visibility="collapsed",
        )

    try:
        with st.spinner("Cargando datos..."):
            tx  = load_transactions()
            pos = load_positions()
    except SheetConfigError as exc:
        st.title("📊 Finance Dashboard")
        st.error(f"**No se ha podido leer tu Google Sheet**\n\n{exc}")
        st.caption(
            "Corrige el problema y pulsa **R** para recargar. "
            "La guía completa está en SETUP.md."
        )
        return

    # Hoja recién creada: sin transacciones no hay nada que representar, y
    # cada página fallaría por su cuenta al operar sobre series vacías.
    if tx.empty:
        st.title("📊 Finance Dashboard")
        st.info(
            "**Todavía no hay transacciones en tu Google Sheet.**\n\n"
            "Ejecuta `scripts/run_full_pipeline.bat` para importar tus movimientos de "
            "Trade Republic. Si ya lo has hecho, comprueba que "
            "`google_sheets.spreadsheet_id` en `config.yaml` apunta a la hoja "
            "correcta y que la pestaña `transactions` tiene la fila de "
            "cabecera descrita en SETUP.md."
        )
        return

    if page == "Dashboard":
        page_dashboard(tx, pos)
    elif page == "Gastos":
        page_gastos(tx)
    elif page == "Activos":
        page_activos(pos, tx)
    elif page == "Proyección":
        page_forecasting(pos, tx)
    elif page == "Objetivos":
        page_objetivos(pos, tx)


if __name__ == "__main__" or True:
    main()
