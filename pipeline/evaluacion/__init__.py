"""Banco de evaluación de modelos de serie temporal.

Separa dos cosas que conviene no mezclar:

  `protocolo`  cómo se compara: partición temporal, métricas, sin fuga.
  `modelos`    qué se compara: el catálogo de candidatos.

Añadir un modelo nuevo es añadir una función al catálogo; el protocolo no se
toca. Y si el protocolo estuviera mal, estaría mal en un solo sitio.
"""

from .protocolo import (escala_mase, errores_por_origen, evaluar,
                        horizontes_factibles, tabla_resumen)
from . import modelos, seleccion

__all__ = ["evaluar", "tabla_resumen", "escala_mase", "errores_por_origen",
           "horizontes_factibles", "modelos", "seleccion"]
