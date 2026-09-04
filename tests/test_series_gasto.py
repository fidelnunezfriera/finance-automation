"""La serie de gasto mensual que consumen el banco y el dashboard.

Tiene que ser **la misma** en los dos sitios. Si el dashboard predijera sobre
una serie distinta de la que se evaluó, la elección de modelo dejaría de
significar nada — y sería un fallo invisible: los números saldrían, sólo que
apoyados en una comparación que nunca se hizo.
"""

import pandas as pd
import pytest

from conftest import data_constants

data = data_constants()

HOY = pd.Timestamp("2026-08-03")


COLUMNAS = ["year_month", "category", "amount", "event_domain_l"]


def _tx(filas):
    """Transacciones mínimas: (mes, categoria, importe en positivo).

    Las columnas se declaran siempre, también sin filas: una hoja recién
    creada está vacía pero tiene su cabecera, y es el caso que importa.
    """
    registros = [{
        "year_month":     pd.Period(mes, freq="M"),
        "category":       categoria,
        "amount":         -abs(gasto),
        "event_domain_l": "cashflow",
    } for mes, categoria, gasto in filas]
    return pd.DataFrame(registros, columns=COLUMNAS)


# ── Construcción de la serie ──────────────────────────────────────────────────

def test_agrega_por_mes():
    tx = _tx([("2026-01", "Compra", 100), ("2026-01", "Compra", 50),
              ("2026-02", "Compra", 70)])
    s = data.monthly_expenses(tx)

    assert s.loc[pd.Period("2026-01")] == 150.0
    assert s.loc[pd.Period("2026-02")] == 70.0


def test_los_meses_sin_gasto_valen_cero():
    """Sin rellenar, la posicion en la serie dejaria de significar 'mes'."""
    tx = _tx([("2026-01", "Compra", 100), ("2026-04", "Compra", 80)])
    s = data.monthly_expenses(tx)

    assert list(s.index.astype(str)) == ["2026-01", "2026-02", "2026-03", "2026-04"]
    assert s.loc[pd.Period("2026-02")] == 0.0


def test_los_ingresos_no_cuentan():
    tx = _tx([("2026-01", "Compra", 100)])
    ingreso = pd.DataFrame([{
        "year_month": pd.Period("2026-01", freq="M"), "category": "Ingresos",
        "amount": 2000.0, "event_domain_l": "cashflow",
    }])
    s = data.monthly_expenses(pd.concat([tx, ingreso], ignore_index=True))

    assert s.loc[pd.Period("2026-01")] == 100.0


def test_los_movimientos_de_activos_no_cuentan():
    """Comprar acciones no es gasto: es mover dinero de sitio."""
    tx = _tx([("2026-01", "Compra", 100)])
    activo = pd.DataFrame([{
        "year_month": pd.Period("2026-01", freq="M"), "category": "Inversión",
        "amount": -500.0, "event_domain_l": "asset",
    }])
    s = data.monthly_expenses(pd.concat([tx, activo], ignore_index=True))

    assert s.loc[pd.Period("2026-01")] == 100.0


def test_sin_gastos_devuelve_serie_vacia():
    assert data.monthly_expenses(_tx([])).empty


# ── Por categoría ─────────────────────────────────────────────────────────────

def test_una_categoria_abarca_el_rango_del_total():
    """Es la trampa: si cada categoria empezara en su primer mes con gasto,
    la serie no seria la misma sobre la que se eligio el modelo."""
    tx = _tx([("2026-01", "Compra", 100), ("2026-02", "Compra", 100),
              ("2026-03", "Vacaciones", 400), ("2026-04", "Compra", 100)])

    total = data.monthly_expenses(tx)
    vacaciones = data.monthly_expenses(tx, "Vacaciones")

    assert list(vacaciones.index) == list(total.index)
    assert vacaciones.loc[pd.Period("2026-01")] == 0.0
    assert vacaciones.loc[pd.Period("2026-03")] == 400.0


def test_una_categoria_inexistente_da_ceros_no_un_error():
    tx = _tx([("2026-01", "Compra", 100), ("2026-02", "Compra", 100)])
    s = data.monthly_expenses(tx, "No existe")

    assert len(s) == 2
    assert (s == 0).all()


def test_las_categorias_suman_el_total():
    tx = _tx([("2026-01", "Compra", 100), ("2026-01", "Dispensable", 60),
              ("2026-02", "Compra", 40)])
    total = data.monthly_expenses(tx)
    partes = sum(data.monthly_expenses(tx, c) for c in ("Compra", "Dispensable"))

    assert (total == partes).all()


# ── El mes en curso ───────────────────────────────────────────────────────────

def test_se_quita_el_mes_en_curso(monkeypatch):
    """Agosto a medias tiraria la prevision hacia abajo, igual que hacia con
    la tendencia de aportaciones."""
    monkeypatch.setattr(data.pd, "Timestamp", pd.Timestamp)
    tx = _tx([("2026-06", "Compra", 100), ("2026-07", "Compra", 100),
              ("2026-08", "Compra", 5)])

    s = data.drop_incomplete_month(data.monthly_expenses(tx), hoy=HOY)
    assert list(s.index.astype(str))[-1] == "2026-07"
