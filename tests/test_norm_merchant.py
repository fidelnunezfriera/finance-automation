"""Normalización del nombre de comercio.

Trade Republic antepone "Transacción con tarjeta - " (o "Reembolso de
tarjeta - ") a la nota de cada movimiento con tarjeta; otras fuentes de la
hoja no anteponen nada. Sin recortar ese prefijo, el mismo comercio generaba
dos merchant_norm distintos según la fuente, lo que rompía el motor de
reglas para las filas de pytr y fragmentaba la agrupación por comercio del
etiquetado manual.
"""

from pipeline.convert_pytr_to_clean import norm_merchant


def test_recorta_el_prefijo_de_compra_con_tarjeta():
    assert norm_merchant("Transacción con tarjeta - Tienda Omega") == "tienda omega"


def test_recorta_el_prefijo_de_reembolso():
    assert norm_merchant("Reembolso de tarjeta - Juegos Online") == "juegos online"


def test_recorta_la_variante_mal_codificada_del_prefijo():
    assert norm_merchant("TransacciÃ³n con tarjeta - Dia") == "dia"


def test_coincide_con_el_mismo_comercio_sin_prefijo_de_otra_fuente():
    assert norm_merchant("Transacción con tarjeta - Bar Central") == norm_merchant("BAR CENTRAL")


def test_una_nota_sin_prefijo_no_se_toca():
    assert norm_merchant("Mercadona") == "mercadona"
