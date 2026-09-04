# Arquitectura del sistema

Diagramas de arquitectura del sistema de automatización financiera personal,
en cuatro niveles de detalle: contexto, flujo de datos, secuencia de ejecución
y modelo de datos.

---

## 1. Diagrama de contexto

Visión general del sistema y sus fronteras: qué es propio, qué es externo y
quién lo opera.

```mermaid
graph TB
    subgraph externo["Fuentes de datos"]
        TR["Trade Republic<br/>(bróker)"]
    end

    subgraph sistema["Sistema de automatización financiera"]
        PIPE["Pipeline ETL<br/>Python"]
        SHEETS[("Google Sheets<br/>capa de persistencia")]
        DASH["Dashboard<br/>Streamlit"]
    end

    subgraph apis["Servicios externos"]
        YF["yfinance<br/>precios de mercado"]
        FIGI["OpenFIGI<br/>clasificación de instrumentos"]
    end

    USER(["Usuario"])

    TR -->|"pytr CLI<br/>(export manual, OTP)"| PIPE
    PIPE -->|"upsert por tx_id"| SHEETS
    SHEETS -->|"lectura"| DASH
    YF --> DASH
    FIGI --> DASH
    USER -->|"ejecuta"| PIPE
    USER -->|"consulta"| DASH
    USER -->|"edita reglas"| SHEETS

    style sistema fill:#1a2332,stroke:#4a90d9,color:#fff
    style externo fill:#2a2118,stroke:#d9a04a,color:#fff
    style apis fill:#1d2a1d,stroke:#5fb35f,color:#fff
```

**Decisión de diseño:** Google Sheets actúa como
base de datos. No es la opción técnicamente óptima, pero resuelve tres
problemas a la vez sin infraestructura: persistencia, interfaz de edición de
reglas para el usuario final, y acceso concurrente desde el pipeline y el
dashboard.

---

## 2. Flujo de datos del pipeline ETL

```mermaid
graph LR
    A["Trade Republic"] -->|pytr export_transactions| B["account_transactions.csv<br/><i>cabeceras en español</i>"]

    B --> C["convert_pytr_to_clean.py"]

    subgraph transform["Transformación"]
        C --> C1["Mapeo de columnas<br/>ES → esquema normalizado"]
        C1 --> C2["Inferencia de tipo<br/><i>árbol de decisión</i>"]
        C2 --> C3["Motor de reglas<br/><i>categorización</i>"]
        C3 --> C4["Deduplicación<br/><i>tx_id = SHA256</i>"]
    end

    R[("tab <b>rules</b>")] -.->|"fetch en cada ejecución"| C3

    C4 --> D["transactions_clean_<br/>TIMESTAMP.csv"]
    D --> E["push_to_sheets.py<br/><i>upsert por tx_id</i>"]
    E --> F[("tab <b>transactions</b>")]
    F --> G["derive_positions.py<br/><i>agregación por ISIN</i>"]
    G --> H[("tab <b>positions</b>")]

    F --> I["Dashboard"]
    H --> I

    style transform fill:#1a2332,stroke:#4a90d9,color:#fff
```

### El motor de reglas

El componente diferencial del sistema. Las reglas viven en una hoja de cálculo,
no en el código: el usuario añade o modifica criterios de categorización sin
tocar Python ni desplegar nada.

```mermaid
graph TD
    TX["Transacción<br/>sin categorizar"] --> LOAD["Cargar reglas activas<br/><i>enabled = TRUE</i>"]
    LOAD --> SORT["Ordenar por priority ↑"]
    SORT --> LOOP{"¿Coincide la regla?"}

    LOOP -->|"evalúa"| M1["direction<br/><i>in / out / ambas</i>"]
    M1 --> M2["applies_to_type<br/><i>card / transfer / buy…</i>"]
    M2 --> M3["match_field + match_type<br/><i>contains / equals / exists / regex</i>"]

    M3 -->|"sí"| WIN["Asignar category,<br/>subcategory, rule_id<br/>rule_confidence = 1"]
    M3 -->|"no"| NEXT{"¿Quedan reglas?"}
    NEXT -->|"sí"| LOOP
    NEXT -->|"no"| UNMATCHED["Sin categorizar<br/>rule_confidence = 0"]

    style WIN fill:#1d2a1d,stroke:#5fb35f,color:#fff
    style UNMATCHED fill:#2a1d1d,stroke:#b35f5f,color:#fff
```

Gana la primera regla que coincide por prioridad ascendente. El script
`apply_rules_to_sheet.py` permite reaplicar el conjunto de reglas
retroactivamente sobre todo el histórico, tocando únicamente las cuatro
columnas de categorización.

---

## 3. Secuencia de una ejecución completa

`run_pipeline.py` orquesta cuatro pasos secuenciales con un `run_id` compartido
que se propaga a los procesos hijos vía variable de entorno, de modo que todos
escriben en el mismo fichero de log.

```mermaid
sequenceDiagram
    autonumber
    actor U as Usuario
    participant O as run_pipeline.py
    participant P as pytr
    participant C as convert_pytr_to_clean
    participant S as push_to_sheets
    participant D as derive_positions
    participant GS as Google Sheets

    U->>O: python pipeline/run_pipeline.py
    O->>O: genera run_id (UTC)

    O->>P: export_transactions
    P-->>O: account_transactions.csv
    Note over O,P: Requiere OTP → no automatizable

    O->>C: 2/4 limpiar transacciones
    C->>GS: leer tab rules
    GS-->>C: reglas activas
    C-->>O: transactions_clean_*.csv

    O->>S: 3/4 push a Sheets
    S->>GS: upsert por tx_id (batch)
    S->>GS: ordenar por datetime desc

    O->>D: 4/4 derivar posiciones
    D->>GS: leer tab transactions
    GS-->>D: histórico completo
    D->>GS: escribir tab positions

    O-->>U: pipeline completado
```

**Nota sobre el orden de los pasos:** `push_to_sheets` se ejecuta *antes* que
`derive_positions`. Es deliberado — `derive_positions` lee el histórico desde
Google Sheets, no desde el CSV local, así que si se invirtiera el orden las
posiciones reflejarían el estado anterior a la importación.

**Limitación conocida:** la autenticación de Trade Republic exige un código OTP
enviado al móvil, lo que impide la ejecución desatendida. Es la razón por la que
el pipeline es de disparo manual y no está programado. Se documenta como
restricción del proveedor, no del diseño.

---

## 4. Modelo de datos

```mermaid
erDiagram
    TRANSACTIONS {
        string tx_id PK "SHA256(fecha,tipo,valor,nota,isin)"
        string source "trade_republic, u otro origen añadido a mano"
        string import_batch_id
        date date
        datetime datetime
        float amount
        string currency
        string merchant_raw
        string merchant_norm "clave de matching"
        string category
        string subcategory
        string rule_id FK
        float rule_confidence
        string type "buy/sell/card/transfer/…"
        string event_domain "asset / cash"
        json rule_notes "cantidad e ISIN del evento"
        string year_month
    }

    RULES {
        string rule_id PK
        bool enabled
        int priority "orden de evaluación"
        string direction "in / out / vacío"
        string applies_to_type
        string match_field "merchant_norm / type"
        string match_type "contains/equals/exists/regex"
        string match_value
        string category
        string subcategory
    }

    POSITIONS {
        string isin PK
        string name
        float quantity "neto compras - ventas"
        string status "open / closed"
        datetime snapshot_at
    }

    RULES ||--o{ TRANSACTIONS : "categoriza"
    TRANSACTIONS ||--o{ POSITIONS : "agrega por ISIN"
```

El esquema de salida de `convert_pytr_to_clean.py` tiene 25 columnas. `tx_id`
es un hash SHA256 sobre los cinco campos que identifican unívocamente un
movimiento, lo que hace la importación idempotente: reimportar el mismo export
no duplica filas.

### Lógica de posiciones y umbrales epsilon

`derive_positions.py` acumula el recuento firmado de participaciones a partir
del campo `rule_notes` de cada transacción con `event_domain = asset`. Los
umbrales epsilon evitan posiciones abiertas fantasma por redondeo fraccionario:

| Prefijo ISIN | Instrumento | Epsilon |
|---|---|---|
| `XS*` | Bonos | Comparación exacta (nominal entero) |
| `XF000*` | Cripto | `1e-6` |
| Resto | Acciones y ETFs | `0.1` |

---

## 5. Resolución de precios en el dashboard

Cascada de resolución de precio por posición. No hay ningún mapa de ISIN a
ticker escrito a mano: el sistema resuelve cada instrumento dinámicamente.

```mermaid
graph TD
    POS["Posición abierta<br/>(ISIN + nombre)"] --> FIGI["OpenFIGI /v3/mapping<br/><i>caché 24 h</i>"]
    FIGI --> CLASS{"Clasificación"}

    CLASS -->|"bond / XS*"| BOND["Cantidad = valor<br/>nominal en EUR<br/><i>sin yfinance</i>"]
    CLASS -->|"crypto / XF000*"| CRYPTO["yf.Search nombre + EUR<br/>→ par *-EUR"]
    CLASS -->|"etf / equity / unknown"| SEARCH["Búsqueda en cascada"]

    SEARCH --> S1["1· por ISIN"]
    S1 -->|"falla"| S2["2· por nombre del bróker"]
    S2 -->|"falla"| S3["3· por ticker oficial<br/>de OpenFIGI"]
    S3 -->|"falla"| NONE["Muestra —<br/>excluida de totales"]

    S1 --> PRICE["Precio"]
    S2 --> PRICE
    S3 --> PRICE
    CRYPTO --> PRICE
    PRICE --> GBP{"¿Cotiza en GBp?"}
    GBP -->|"sí"| CONV["÷100 → GBP → EUR"]
    GBP -->|"no"| EUR["→ EUR"]

    style BOND fill:#1a2332,stroke:#4a90d9,color:#fff
    style NONE fill:#2a1d1d,stroke:#b35f5f,color:#fff
```

El motivo de la cascada: la etiqueta comercial que da el bróker (p. ej.
"Physical Gold USD (Acc)") a menudo no coincide con la nomenclatura de Yahoo
Finance, mientras que el registro oficial de OpenFIGI sí suele hacerlo.

---

## 6. Componentes y responsabilidades

| Componente | LoC | Responsabilidad |
|---|---:|---|
| `pipeline/run_pipeline.py` | 99 | Orquestación, `run_id` compartido, control de errores |
| `pipeline/convert_pytr_to_clean.py` | 349 | Normalización, inferencia de tipo, motor de reglas, dedup |
| `pipeline/derive_positions.py` | 227 | Agregación de eventos de activo → posiciones |
| `pipeline/apply_rules_to_sheet.py` | 269 | Reaplicación retroactiva de reglas al histórico |
| `pipeline/logger.py` | 61 | Logging estructurado, rotación (10 últimos runs) |
| `sheets/push_to_sheets.py` | 276 | Upsert por lotes, ordenación, borrado por batch |
| `app/data.py` | 371 | Capa de acceso a datos, precios, clasificación, caché |
| `app/main.py` | 1368 | Dashboard de 5 páginas |

Total: ~3.000 líneas de Python.

---

## 7. Evolución de la arquitectura

El sistema tuvo una versión previa basada en n8n desplegado sobre AWS
Lightsail, migrada después al pipeline en Python puro que describe el resto
de este documento. No se conserva material de esa etapa en el repositorio.

```mermaid
graph LR
    subgraph v1["v1 — n8n sobre Lightsail"]
        N1["Workflows visuales"] --> N2["Instancia siempre encendida"]
        N2 --> N3["Coste mensual fijo"]
    end

    subgraph v2["v2 — Python local (actual)"]
        P1["Scripts versionados"] --> P2["Ejecución bajo demanda"]
        P2 --> P3["Coste cero"]
    end

    subgraph v3["v3 — AWS Lambda (previsto)"]
        L1["Funciones serverless"] --> L2["S3 + disparo móvil"]
    end

    v1 -.->|"migración"| v2
    v2 -.->|"trabajo futuro"| v3

    style v2 fill:#1d2a1d,stroke:#5fb35f,color:#fff
```
