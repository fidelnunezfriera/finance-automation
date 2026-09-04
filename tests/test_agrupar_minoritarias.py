"""Agrupación de clases minoritarias para el clasificador de categorías.

Una categoría con pocos ejemplos no se puede evaluar con honestidad por
separado, así que se funde en un cajón único antes de entrenar. La prueba
de fondo es que el umbral se aplica sobre el
CONTEO de cada clase, no sobre las propias filas: agrupar no debe alterar el
número total de filas ni inventar categorías nuevas.
"""

import pandas as pd

from pipeline.entrenar_clasificador import CAJON_MINORITARIO, _agrupar_minoritarias


def test_una_clase_por_debajo_del_umbral_se_agrupa():
    y = pd.Series(["A"] * 25 + ["B"] * 3)

    agrupado, minoritarias = _agrupar_minoritarias(y, umbral=20)

    assert minoritarias == {"B"}
    assert set(agrupado) == {"A", CAJON_MINORITARIO}


def test_una_clase_por_encima_del_umbral_no_se_toca():
    y = pd.Series(["A"] * 25 + ["B"] * 25)

    agrupado, minoritarias = _agrupar_minoritarias(y, umbral=20)

    assert minoritarias == set()
    assert list(agrupado) == list(y)


def test_agrupar_no_cambia_el_numero_de_filas():
    y = pd.Series(["A"] * 25 + ["B"] * 3 + ["C"] * 1)

    agrupado, _ = _agrupar_minoritarias(y, umbral=20)

    assert len(agrupado) == len(y)


def test_el_umbral_es_estricto_no_inclusivo():
    """Justo en el umbral no cuenta como minoritaria."""
    y = pd.Series(["A"] * 20 + ["B"] * 5)

    _, minoritarias = _agrupar_minoritarias(y, umbral=20)

    assert "A" not in minoritarias
    assert "B" in minoritarias
