# Generar reglas nuevas con ayuda de un LLM

Un clon nuevo del repositorio solo trae las trece reglas de serie (ver
[REGLAS.md](REGLAS.md)): las `tr-` estructurales, y unas pocas `ejemplo-`
que solo aciertan si compras exactamente donde dicen. Con tus propias
transacciones eso categoriza casi nada — lo comprobamos con un tercero
probando el proyecto desde cero. Escribir a mano una regla por cada
comercio de tu histórico funciona, pero es lento.

Este flujo usa un LLM (Claude, ChatGPT, el que uses) para proponer un lote
de reglas de golpe, a partir de tus propios comercios. Tú revisas y decides
qué entra en la hoja — el LLM no escribe nada por sí solo, ni en la hoja ni
en el repositorio.

---

## Paso 1 — exportar tus comercios sin regla

```
scripts\exportar_comercios_sin_regla.bat
```

Lee la hoja (`rules` y `transactions`), y calcula qué comercios no coinciden
con ninguna regla real tuya —el cajón de sastre final no cuenta como
regla real, o la lista saldría vacía—. Solo lee: no escribe nada en la hoja.

Tres ficheros en `out/` (gitignored):

| Fichero | Contenido |
|---|---|
| `comercios_sin_regla_TIMESTAMP.csv` | `merchant_norm, apariciones, tipo_dominante, direccion_dominante`, ordenado por frecuencia |
| `taxonomia_actual_TIMESTAMP.csv` | Las categorías y subcategorías que ya usan tus reglas reales |
| `limite_prioridad_TIMESTAMP.txt` | El `priority` por debajo del cual tiene que quedar cualquier regla nueva para que no la tape tu cajón de sastre |

El tercero importa tanto como los otros dos: la convención de que el cajón
de sastre va en `priority=99999` es solo la de una hoja recién creada. Una
hoja con historial propio puede tenerlo en cualquier número —en una prueba
real estaba en `999`—, y una regla nueva por encima de eso se añade sin
error pero **nunca se llega a evaluar**: queda en la hoja como letra
muerta. `importar_reglas.bat` (paso 4) también lo comprueba y salta
cualquier fila que lo incumpla, pero mejor que el LLM ya parta del número
correcto.

Si tu nombre completo (el de `account_holder_name` en `config.yaml`)
aparece en algún comercio —típico en traspasos a cuenta propia— sale
redactado como `[mi nombre]`. Aun así, échale un vistazo al CSV antes de
pegarlo en ningún sitio: es tu histórico de comercios, y el criterio de qué
es razonable compartir con un LLM externo es tuyo, no del script.

---

## Paso 2 — el prompt

Copia esto en tu LLM de preferencia, y adjunta (pega o sube) los tres
ficheros del paso 1. Sustituye `LIMITE` por el número que diga
`limite_prioridad_TIMESTAMP.txt`.

> Tengo un histórico de transacciones bancarias ya categorizado en parte por
> un motor de reglas. Te paso: `comercios_sin_regla` (los comercios que
> ninguna regla mía categoriza todavía, con cuántas veces aparecen),
> `taxonomia_actual` (las categorías y subcategorías que ya uso), y el
> límite de prioridad de mi hoja: **LIMITE**.
>
> Quiero que me propongas reglas nuevas, en formato CSV, con estas columnas
> exactas: `rule_id, enabled, priority, direction, applies_to_type,
> match_field, match_type, match_value, category, subcategory`.
>
> Reglas del juego:
> - **No te inventes nada.** Si el texto del comercio no te da una pista
>   razonable, déjalo fuera de tu propuesta en vez de adivinar. Si pone algo
>   tipo `bar` o `cafeteria`, es razonable inferir que es un bar; si es una
>   cadena de caracteres que no reconoces, no le pongas categoría.
> - **Si tienes búsqueda web, úsala para identificar comercios que parezcan
>   empresa o marca** (llevan "SA", un dominio, un nombre comercial
>   reconocible) y que no sepas categorizar solo por el texto. Pero
>   **no busques nombres que parezcan de una persona física** —lo más
>   probable es que sea la otra parte de una transferencia o un Bizum—, ni
>   compiles información sobre nadie a partir del nombre: para esos, decide
>   solo con lo que ya tienes (importe, tipo de movimiento) o déjalo sin
>   categoría.
> - Reutiliza las categorías y subcategorías de `taxonomia_actual` siempre
>   que encajen. Solo propón una categoría o subcategoría nueva si de verdad
>   no hay ninguna existente razonable, y dilo explícitamente.
> - Las categorías son genéricas; puede haber varias subcategorías por
>   categoría, o ninguna (subcategoría vacía) si no aporta nada dividirla.
> - Prioriza cubrir cuantas más filas de `apariciones` mejor con el menor
>   número de reglas: agrupa variantes del mismo comercio con `match_type:
>   regex` (ej. `zara|bershka|mango`) en vez de una regla por fila.
> - `match_type` es uno de `contains`, `equals`, `regex`, `exists`.
>   Usa `contains` por defecto; `regex` con `\b` para agrupar varios
>   comercios o evitar falsos positivos con palabras cortas (`\bdia\b`, no
>   `dia`, porque si no casa dentro de `media markt`).
> - `enabled` siempre `TRUE`. `direction` y `applies_to_type` vacíos salvo
>   que de verdad haga falta acotar por signo o tipo de movimiento.
> - `priority` siempre **menor que LIMITE** (si no, la regla nunca se
>   evaluaría), dejando huecos de 4 o 10 en 10 para poder intercalar reglas
>   después. `rule_id` corto y descriptivo, en minúsculas, sin espacios
>   (ej. `mercado-central`).
> - **Dentro de ese rango, ordena por especificidad: número más bajo
>   (gana antes) para las reglas exactas o inequívocas** (`equals`, o
>   `regex` con nombres de marca completos); **número más alto (gana
>   después) para las ambiguas** (`contains` sobre una palabra corta o
>   genérica, tipo `bar` o `cafe`, que podría aparecer dentro de un
>   comercio que en realidad es otra cosa). Así, si dos reglas tuyas
>   llegan a casar con el mismo comercio, gana la más segura.
> - Un comercio con muy poca información (una cadena corta o críptica) es
>   mejor dejarlo sin regla que forzar una categoría dudosa.

---

## Paso 3 — revisar antes de pegar nada

El LLM se equivoca, sobre todo con comercios ambiguos o abreviados. Antes de
tocar la hoja:

1. Lee fila a fila lo que propone. Si algo no te convence, bórralo o
   corrígelo tú mismo — es tu categorización, no la del LLM.
2. Comprueba que no haya reutilizado un `rule_id` que ya exista en tu
   pestaña `rules`.
3. Revisa las prioridades: no deben pisar las de tus reglas estructurales
   (las que categorizan por `type` en vez de por comercio) ni ser mayores
   o iguales que el número de `limite_prioridad_TIMESTAMP.txt` — si lo son,
   la regla se añadiría pero nunca se evaluaría.

---

## Paso 4 — aplicar

```
scripts\importar_reglas.bat out\propuesta_reglas.csv
```

Simula primero y te enseña qué filas añadiría, cuáles se saltaría (por
`rule_id` repetido, o por faltarle `match_field`/`match_type`/`category`) y
te pregunta antes de escribir. No borra ni pisa ninguna regla que ya
tuvieras: solo añade las nuevas y reordena toda la pestaña `rules` por
`priority`, junto con las que ya había.

Después, para que el histórico se recategorice con las reglas nuevas:

```
scripts\apply_rules.bat
```

> **Alternativa manual**, si prefieres no usar el script: pegar el CSV tal
> cual en Sheets **no separa el texto en columnas** —cae todo en una sola
> celda—. Hace falta importar el CSV como hoja nueva (Archivo → Importar →
> Subir → "Insertar nueva hoja") y copiar el rango ya separado, o pegarlo y
> usar Datos → Dividir texto en columnas con coma como separador.

Simula primero y te enseña qué movimientos cambiarían de categoría antes de
escribir nada — cancela si algo no cuadra. Más detalle de todo el mecanismo
en [REGLAS.md](REGLAS.md).

Si después de aplicar sigue quedando un grupo grande de comercios sin regla
—`category = Otros` en la hoja, o una nueva pasada de
`exportar_comercios_sin_regla.bat`—, repite el ciclo: son justo los que se
quedaron sin cubrir la primera vez.
