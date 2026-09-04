# Guía de instalación (para un usuario nuevo)

Esta guía monta el proyecto en tu ordenador usando **tu propia cuenta de Trade
Republic y tu propia hoja de Google Sheets** — nada de datos ni credenciales
de quien te ha pasado el repo llegan a tu máquina.

## 1. Requisitos

- Python 3.11+ — al instalarlo, marca **«Add python.exe to PATH»**
- Git — en Windows: `winget install --id Git.Git -e`, y luego **abre una
  consola nueva** para que aparezca en el PATH
- Una cuenta de Trade Republic (con acceso a la app/OTP por SMS)
- Una cuenta de Google

## 2. Clonar el repositorio

Clónalo en una carpeta que **no** sincronice OneDrive. El Escritorio y
Documentos lo están por defecto, y ahí acabarías subiendo a la nube tus
credenciales de Google y los miles de ficheros del entorno virtual. Por
ejemplo `C:\dev`:

```
cd C:\dev
git clone https://github.com/fidelnunezfriera/finance-automation
cd finance-automation
```

`setup.bat` comprueba esto y avisa si la ubicación no es buena.

Si prefieres no instalar Git, puedes bajar el
[zip del proyecto](https://github.com/fidelnunezfriera/finance-automation/archive/refs/heads/main.zip)
y descomprimirlo. Funciona igual, pero para actualizarte tendrás que volver a
descargarlo entero en vez de hacer `git pull`.

## 3. Entorno virtual y dependencias

**En Windows, haz doble clic en `setup.bat`** (o ejecútalo desde la consola).
Se encarga de todo:

- comprueba que el repositorio está clonado entero y en un sitio válido
- busca un Python 3.11+ y avisa con instrucciones si no lo encuentra
- crea el entorno virtual `.venv`
- instala todas las dependencias y verifica que importan
- crea tu `config.yaml` a partir de la plantilla (nunca sobrescribe uno existente)

Puedes volver a ejecutarlo cuantas veces quieras: reutiliza el entorno si ya es
válido y no toca tu configuración.

Si algo falla o quieres las versiones exactas verificadas, `setup.bat lock`
instala desde `requirements.lock` (requiere Python 3.12+).

<details>
<summary>Instalación manual (Linux/Mac, o si prefieres hacerlo a mano)</summary>

```
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # Linux/Mac
pip install -r requirements.txt
```

</details>

## 4. Crear tu Google Sheet

Google Sheets es la app de hojas de cálculo de Google Drive -- la hoja que
crees se guarda como un fichero más en tu Drive, igual que un documento o una
carpeta. Puedes crearla desde [drive.google.com](https://drive.google.com)
(**Nuevo → Hoja de cálculo de Google**) o directamente en
[sheets.google.com](https://sheets.google.com).

Crea una hoja de cálculo **nueva y vacía**. No hace falta que añadas
pestañas ni cabeceras a mano: de eso se encarga el paso 7.

Copia el ID de la hoja de la URL:
`https://docs.google.com/spreadsheets/d/`**`ESTE_TROZO_ES_EL_ID`**`/edit`

> El fichero tienes que crearlo tú. Una cuenta de servicio puede crear hojas,
> pero nacerían en su propio Drive y Google no le permite transferirte la
> propiedad, así que acabarías sin ser dueño de tus propios datos.

## 5. Crear la Service Account de Google

1. Ve a [Google Cloud Console](https://console.cloud.google.com/) → crea un proyecto (o usa uno existente).
2. Habilita la **Google Sheets API** para ese proyecto: en el buscador de
   arriba (la barra con la lupa, en la parte superior de la consola)
   escribe **"Google Sheets API"**, entra en el primer resultado y pulsa el
   botón **Habilitar**.
3. IAM y administración → Cuentas de servicio → Crear cuenta de servicio.
4. Una vez creada, entra en ella → Claves → Añadir clave → JSON. Se descarga un archivo `.json`.
5. Guarda ese archivo como `credentials/gdrive-sa.json` dentro del repo. La carpeta ya viene creada; todo lo que dejes dentro está en `.gitignore` y nunca se sube.
6. Abre el JSON y copia el valor de `"client_email"` (algo como `xxx@xxx.iam.gserviceaccount.com`).
7. En tu Google Sheet (paso 4), botón **Compartir** → pega ese email → dale permiso de **Editor**.

## 6. Configurar `config.yaml`

Ya lo creó `setup.bat` en el paso 2, copiándolo de `config.example.yaml`. Solo
tienes que editarlo.

(Si estás instalando a mano en Linux o Mac, cópialo tú:
`cp config.example.yaml config.yaml`.)

Edita `config.yaml` y sustituye:

```yaml
google_sheets:
  spreadsheet_id: YOUR_GOOGLE_SHEET_ID_HERE   # <- pon aquí el ID del paso 4
```

Este fichero es tuyo y no se toca nunca: ni `git pull` ni volver a ejecutar
`setup.bat` sobrescriben lo que hayas puesto aquí.

Hay un campo más, `pipeline.account_holder_name`, opcional y con una
implicación que conviene entender antes de rellenarlo:

```yaml
pipeline:
  account_holder_name: "TU NOMBRE COMPLETO TAL CUAL SALE EN LAS TRANSFERENCIAS"
```

- **Si rastreas más de una cuenta** (por ejemplo un banco además de Trade
  Republic), rellénalo: un traspaso cuya nota incluya tu nombre se
  clasifica como movimiento entre tus propias cuentas, no como ingreso
  nuevo, y así el dashboard no cuenta dos veces el mismo dinero.
- **Si solo rastreas una cuenta** y la usas para meter dinero de fuera
  (por ejemplo, Trade Republic como única fuente, ingresando desde tu
  banco para invertir), **déjalo en blanco**. Si lo rellenas, cada
  ingreso se detecta como "tu propio nombre enviándote dinero" y
  desaparece de "Ingresos" en el dashboard, aunque sea dinero real
  entrando en el sistema.

El dashboard avisa de esto con un icono ⓘ junto al KPI de Ingresos y al
gráfico de Cashflow cuando el campo está relleno, por si hace falta
recordarlo más adelante.

## 7. Preparar las pestañas de la hoja

Los lanzadores están en la carpeta `scripts\`. Puedes ejecutarlos con doble
clic desde ahí, o desde una consola en la raíz del proyecto como se indica
aquí.

```
scripts\init_sheet.bat
```

Crea las pestañas `transactions`, `rules`, `positions` y
`display_category_month` con sus cabeceras, y las deja fijadas y en negrita.

Es seguro repetirlo: nunca sobrescribe una celda con contenido. Si una pestaña
ya existe con otra cabecera, te avisa y no la toca. Con `scripts\init_sheet.bat
--dry-run` ves lo que haría sin escribir nada.

### Las reglas de ejemplo

En la pestaña `rules` te deja **trece reglas ya puestas**, de dos clases muy
distintas. Merece la pena saber cuál es cuál antes de ponerte a borrar.

#### Las `tr-` valen para todo el mundo — déjalas

Categorizan los movimientos de Trade Republic por **el tipo que dedujo el
pipeline**, no por el nombre del comercio. No dependen de dónde compres tú: una
venta es una venta.

| Prioridad | Regla | Casa con | Categoría |
|---:|---|---|---|
| 10 | `tr-compra` | movimientos de tipo `buy` | Inversión / Compra Activos |
| 20 | `tr-venta` | tipo `sell` | Inversión / Venta Activos |
| 30 | `tr-dividendo` | tipo `dividend` | Inversión / Dividendo |
| 40 | `tr-intereses` | tipo `interest` | Inversión / Intereses |

Van con prioridad de dos cifras para que ganen a cualquier regla de texto. Si
compras acciones de Netflix, eso es una inversión, no una suscripción.

#### Las `ejemplo-` son para copiarlas

Categorizan por comercio, así que solo aciertan si compras donde dicen. Están
sobre todo para enseñar el formato: entre todas usan los cuatro tipos de
comparación que existen.

| Prioridad | Regla | Comparación | Casa con | Categoría |
|---:|---|---|---|---|
| 85 | `ejemplo-nomina` | `contains` + `direction: in` | contiene `nomina`, solo si entra dinero | Ingresos / Nómina |
| 90 | `ejemplo-bizum` | `equals` | `bizum` exacto | Transferencia/Bizum |
| 100 | `ejemplo-supermercado` | `regex` | Mercadona, Carrefour, Lidl, Alcampo, Eroski, Consum, Dia | Compra / Supermercado |
| 110 | `ejemplo-restauracion` | `regex` | Glovo, Just Eat, Uber Eats, Telepizza, Domino's, McDonald's, Burger King, Starbucks | Dispensable / Restauración |
| 120 | `ejemplo-combustible` | `regex` | Repsol, Cepsa, Galp, Petroprix, Shell, BP | Transporte / Combustible |
| 130 | `ejemplo-transporte` | `regex` | Uber, Cabify, Bolt, Renfe, BlaBlaCar, EMT, Metro | Transporte / Transporte público |
| 140 | `ejemplo-suscripciones` | `regex` | Netflix, Spotify, HBO, Disney, Filmin, Movistar, Prime Video | Dispensable / Suscripciones |
| 150 | `ejemplo-farmacia` | `contains` | contiene `farmacia` | Salud / Farmacia |
| 99999 | `ejemplo-otros` | `exists` | cualquier comercio | Otros |

**Cómo se aplican.** Gana **la primera regla que casa**, por orden de
`priority` de menor a mayor. Las más específicas van con número más bajo — por
eso restauración (110) va antes que transporte (130): si no, un pedido de Uber
Eats contaría como un viaje.

**La última es un cajón de sastre.** Con `exists` y prioridad 99999 recoge todo
lo que ninguna regla anterior haya reclamado y lo manda a `Otros`. Sin ella,
esos movimientos se quedarían con la categoría en blanco: agrupados en `Otros`
los revisas de un vistazo en la hoja y ves cuáles merecen una regla propia. El
99999 deja sitio de sobra por debajo para intercalar las tuyas.

**Para desactivar una** tienes dos formas, y las dos valen igual:

- **Borrar la fila** en la hoja.
- **Poner `enabled` a `FALSE`**, que la deja ahí por si quieres recuperarla.

Las `ejemplo-` bórralas sin miedo en cuanto tengas las tuyas. Las `tr-` y el
cajón de sastre conviene dejarlos.

Solo se escriben **la primera vez**, cuando la pestaña está vacía. En cuanto
haya una regla —tuya o de ejemplo— volver a ejecutar `init_sheet.bat` no añade
nada: si las borras, no reaparecen.

> **Para escribir las tuyas**, la guía completa está en
> [docs/REGLAS.md](docs/REGLAS.md): qué hace cada columna, cuándo usar cada
> tipo de comparación, cómo acotar por signo o por tipo de movimiento, y
> recetas listas para copiar.
>
> **Si tras la primera ejecución casi todo cae en `Otros`**, es normal: las
> `ejemplo-` apenas cubren nada fuera de esos ocho comercios concretos. En
> vez de escribir una regla por comercio a mano, ver
> [docs/GENERAR_REGLAS.md](docs/GENERAR_REGLAS.md) para generar un lote de
> reglas con un LLM a partir de tus propios comercios, que revisas antes de
> aplicar.

### El resto de pestañas

`display_category_month` alimenta el gráfico de categorías del
Dashboard: es una tabla dinámica que construyes tú encima de `transactions`
(categoría en filas, mes en columnas, importe en celdas). Puedes dejarla vacía;
ese gráfico simplemente saldrá sin datos.

## 8. Primera ejecución del pipeline

```
scripts\run_full_pipeline.bat
```

La primera vez te pedirá el login de Trade Republic (teléfono + PIN, luego
un código que te llega por SMS/app) — es el mismo proceso interactivo de
siempre, con tu propia cuenta.

## 9. Lanzar el dashboard

```
scripts\launch_dashboard.bat
```

Si algo falla, el propio dashboard te dice qué revisar. Para errores del
pipeline, mira el log más reciente en `logs/`.

### Predecir gasto

La página de Gastos tiene un botón **Predecir gasto**: eliges qué —el total,
una categoría o una subcategoría— y cuándo —un mes concreto o un año entero— y
te da una estimación con su rango.

Para que funcione hay que decidir antes qué modelos usar, y eso se hace con
tus propios datos: cuál acierta más depende de cuánta historia tengas.

```
scripts\seleccionar_modelos.bat
```

Compara nueve modelos sobre cada serie, a 1, 3, 6, 12 y 24 meses vista,
entrenando siempre con el pasado y midiendo contra el futuro. Se queda con los
tres mejores de cada serie y horizonte, y lo guarda en
`logs/modelos_elegidos.json`. La predicción es la media de esos tres.

Tarda unos minutos la primera vez. Si las series no han cambiado desde la
última —ni meses nuevos ni recategorizaciones— sale sin reevaluar.

Conviene relanzarlo cuando se hayan acumulado meses o cuando cambies las
reglas de categorización, porque eso reescribe el histórico.

Para ver la comparación completa por tu cuenta:

```
python pipeline/evaluar_series.py --serie gasto-total
```

## 10. Opcional: dejarlo ejecutándose solo

El paso 1 (exportar de Trade Republic) necesita el código OTP del móvil, así
que siempre lo lanzas tú. Los pasos 2 a 4 —limpiar, cargar en la hoja y
recalcular posiciones— no necesitan a nadie delante y sí se pueden programar.

Eso mismo lo puedes lanzar a mano cuando quieras, sin login ni OTP:

```
scripts\run_pipeline_unattended.bat
```

Configura la cadencia en `config.yaml`:

```yaml
schedule:
  enabled: true
  frequency: daily        # hourly | daily | weekly
  time: "07:30"
  day_of_week: monday     # solo si frequency es weekly
```

Y registra la tarea:

```
scripts\schedule_pipeline.bat --install
```

Otros usos: `--status` para ver qué hay registrado, `--remove` para quitarlo y
`--install --dry-run` para ver qué haría sin tocar nada. Doble clic sin
argumentos equivale a `--status`.

En Windows crea una entrada en el Programador de tareas; en Linux y macOS, una
línea de crontab. Puede que Windows te pida una consola de administrador.

Cada ejecución añade una fila a `logs/runs.csv` con la duración de cada paso,
las filas leídas y escritas, el estado y, si algo falla, el paso que falló. Es
el sitio donde mirar para saber si las ejecuciones automáticas van bien.
