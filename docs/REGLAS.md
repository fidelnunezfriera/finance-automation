# Cómo escribir reglas de categorización

Las reglas viven en la pestaña `rules` de tu Google Sheet y se leen **enteras
en cada ejecución**. No hay que tocar código ni reiniciar nada: editas la hoja,
lanzas el pipeline y ya están aplicadas.

Para recategorizar movimientos que ya están cargados, sin volver a importar
nada y **sin conectarte a Trade Republic**:

```
scripts\apply_rules.bat
```

Primero simula y te enseña qué cambiaría. Después pregunta si aplicarlo:
cualquier respuesta que no sea `s` cancela y no escribe nada.

Recorre el histórico entero y toca solo `category`, `subcategory`, `rule_id` y
`rule_confidence`. El resto de columnas no se tocan.

> **No confundir con `run_pipeline_unattended.bat`.** Ese reaplica las reglas
> únicamente a los movimientos del CSV que tienes en disco, y además vuelve a
> cargarlos y a recalcular posiciones. Si lo que has tocado son las reglas y
> quieres que afecte a todo el histórico —incluido lo que venga de otras
> fuentes—, el que necesitas es `apply_rules.bat`.

---

## Las columnas

| Columna | Qué es |
|---|---|
| `rule_id` | Nombre de la regla. Aparece en la columna `rule_id` de cada movimiento, así que sirve para ver **qué regla** lo categorizó |
| `enabled` | `TRUE` o `FALSE`. En `FALSE` la regla se ignora sin borrarla |
| `priority` | Número. Gana **la primera regla que casa**, de menor a mayor |
| `direction` | `in`, `out`, o vacío para no filtrar por signo |
| `applies_to_type` | Limita a un tipo de movimiento, o vacío para todos |
| `match_field` | Qué columna se mira: normalmente `merchant_norm`, también `type` |
| `match_type` | `contains`, `equals`, `regex` o `exists` |
| `match_value` | Con qué se compara |
| `category` | Categoría que se asigna. **Obligatoria**: sin ella la regla se descarta |
| `subcategory` | Subcategoría. Opcional |

Una regla sin `match_field`, sin `match_type` o sin `category` se ignora en
silencio. Si una regla tuya no hace nada, empieza por ahí.

---

## Los cuatro tipos de comparación

Ninguno distingue mayúsculas.

### `contains` — el caso normal

La palabra aparece en algún sitio del campo.

```
match_field: merchant_norm   match_type: contains   match_value: farmacia
```

Casa con `FARMACIA SAN PABLO`, `farmacias del centro`, `parafarmacia`.

Ese último es el riesgo de `contains`: compara subcadenas, sin fronteras de
palabra. `dia` casa dentro de `media markt` y `bolt` dentro de `boltonería`.
Con palabras cortas, usa `regex`.

### `equals` — el valor exacto

El campo es **exactamente** ese valor, ni más ni menos.

```
match_field: merchant_norm   match_type: equals   match_value: bizum
```

Casa con `bizum`. No casa con `bizum enviado`, que es otro comercio.

Útil cuando el comercio normalizado ya viene limpio y no quieres arrastrar
todo lo que empiece igual.

### `regex` — varios comercios en una regla

Una expresión regular. La barra vertical `|` significa «o», así que agrupas
muchos comercios en una sola fila.

```
match_field: merchant_norm   match_type: regex
match_value: mercadona|carrefour|lidl|alcampo|eroski
```

Para palabras cortas, `\b` marca frontera de palabra:

| Patrón | Casa | No casa |
|---|---|---|
| `dia` | `media markt` ❌ | |
| `\bdia\b` | `supermercados dia sa` | `media markt` |

Una expresión mal formada no rompe el pipeline: esa regla simplemente no casa
nunca. Si una regex tuya no hace nada, sospecha de un paréntesis o un corchete
sin cerrar.

### `exists` — cualquier cosa

Casa con cualquier fila que tenga **algo** en ese campo. Ignora `match_value`.

Solo tiene sentido en dos sitios: en el cajón de sastre del final, o combinado
con `applies_to_type` o `direction` para categorizar un tipo entero de
movimiento.

---

## El orden importa

Gana la primera que casa, por `priority` de menor a mayor. Lo específico va
antes que lo general.

El ejemplo que lo explica todo: `uber eats` contiene `uber`.

```
110  ejemplo-restauracion  regex  glovo|uber ?eats|...     Dispensable/Restauración
130  ejemplo-transporte    regex  \buber\b|cabify|...      Transporte/Transporte público
```

Si les das la vuelta, cada pedido de comida cuenta como un viaje. Y no falla
nada: simplemente los números salen mal.

Deja huecos entre prioridades —100, 110, 120— para poder intercalar reglas
después sin renumerar.

Los tramos que trae la hoja de serie:

| Tramo | Para qué |
|---|---|
| 10–40 | Las `tr-`, estructurales. Ganan a todo lo demás |
| 50–99 | Libre. Aquí van tus reglas que deban ganar a las de comercio |
| 100–9998 | Reglas por comercio, las tuyas y las de ejemplo |
| 99999 | El cajón de sastre |

---

## Acotar a qué movimientos se aplica

Dos columnas filtran **antes** de mirar el texto. Vacías, no filtran nada.

### `direction` — por signo del importe

`in` para lo que entra, `out` para lo que sale.

```
match_value: nomina   direction: in   ->  Ingresos/Nómina
```

Sin el `direction`, una devolución cuyo concepto mencione la nómina contaría
como ingreso de nómina.

### `applies_to_type` — por tipo de movimiento

El pipeline deduce un tipo para cada movimiento:

| Tipo | Qué es |
|---|---|
| `buy` / `sell` | Compra o venta de un activo |
| `dividend` | Dividendo cobrado |
| `interest` | Intereses |
| `card` | Pago con tarjeta |
| `transfer` | Transferencia enviada |
| `deposit` | Ingreso a tu cuenta del bróker |
| `income` | Cualquier otra entrada |

También puedes mirar ese campo directamente con `match_field: type` y
`match_type: equals`, que es lo que hace la regla de dividendos de ejemplo.

La diferencia: `match_field: type` **es** la condición; `applies_to_type`
**acota** una condición que va sobre otro campo.

---

## Recetas

**Un comercio concreto a su categoría**

```
priority: 200   match_field: merchant_norm   match_type: contains
match_value: decathlon   category: Compra   subcategory: Deporte
```

**Varios comercios de la misma categoría, en una sola regla**

```
priority: 210   match_type: regex
match_value: zara|bershka|pull ?and ?bear|mango
category: Compra   subcategory: Ropa
```

**Solo cuando el dinero entra**

```
priority: 220   match_type: contains   match_value: devolucion
direction: in   category: Ingresos   subcategory: Devoluciones
```

**Un tipo de movimiento entero**

```
priority: 230   match_field: type   match_type: equals   match_value: interest
category: Inversión   subcategory: Intereses
```

**Desactivar una regla sin perderla**

Pon `enabled` a `FALSE`. Se queda en la hoja, deja de aplicarse.

---

## Comprobar que funcionan

1. `scripts\apply_rules.bat` — enseña qué cambiaría y pregunta si aplicarlo.
2. En la hoja, filtra por `rule_id` para ver qué regla se llevó cada
   movimiento, y por `category = Otros` para ver qué sigue sin reclamar.

Si solo quieres la simulación, sin que te pregunte nada:
`python pipeline/apply_rules_to_sheet.py --dry-run`.

Esa segunda lista es la mejor guía para saber qué regla escribir a
continuación: ordénala por número de movimientos y ataca lo de arriba.

---

## Las reglas que trae de serie

Una hoja recién inicializada trae trece reglas, de dos clases.

**Las `tr-`** son estructurales: categorizan por el tipo de movimiento que
dedujo el pipeline —`buy`, `sell`, `dividend`, `interest`— y no miran el
nombre del comercio. Valen igual para cualquier usuario, porque no dependen de
los hábitos de nadie. Llevan prioridades 10 a 40 para ganar siempre a las
reglas de texto: comprar acciones de Netflix es una inversión, no una
suscripción. Lo normal es dejarlas.

**Las `ejemplo-`** categorizan por comercio y solo aciertan si compras donde
dicen. Están para enseñar el formato: entre todas usan los cuatro tipos de
comparación, `direction`, `match_field: type` y el cajón de sastre. Bórralas en
cuanto tengas las tuyas.

Ninguna reaparece: `init_sheet` solo las escribe cuando la pestaña no tiene
ninguna regla.

Su definición está en [`schema.py`](../schema.py), en `DEFAULT_RULES`.

---

## ¿Muchos comercios sin regla de golpe?

Si acabas de arrancar el proyecto con tu propio histórico, lo normal es que
la mayoría de tus comercios caigan en `Otros`: las reglas de serie apenas
cubren nada fuera de los movimientos de inversión. Escribir una regla por
comercio a mano funciona, pero es lento para partir de cero. Ver
[GENERAR_REGLAS.md](GENERAR_REGLAS.md) para generar un lote de reglas con
ayuda de un LLM a partir de tus propios comercios, que luego revisas y
aplicas con el mismo `apply_rules.bat` de arriba.
