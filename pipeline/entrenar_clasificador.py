#!/usr/bin/env python3
"""
Clasificador de categorías para el cajón 'Otros'.

Entrena sobre las filas que el motor de reglas YA categorizó (category !=
'Otros') y mide si es capaz de rescatar el cajón 'Otros' comparando contra
data/etiquetado_otros.csv, la verdad de referencia etiquetada a mano.

Por qué no se entrena ni evalúa sobre las mismas filas rule-labeled: todas
las etiquetas de esas filas las puso una función determinista, así que un
modelo entrenado y evaluado sobre ellas solo puede aprender a imitarla --
acierto altísimo, sin valor, y un fallo metodológico evidente. La pregunta
con valor de negocio es otra: de las filas huérfanas en 'Otros', ¿cuántas
recupera el modelo sin escribir una regla nueva?

Dos evaluaciones, con propósitos distintos:
  1. Split agrupado por COMERCIO (no por fila) sobre las filas rule-labeled:
     mide generalización a comercios nunca vistos dentro del régimen que las
     reglas ya conocen. Es el chequeo de cordura, no el resultado central.
     Agrupado y no aleatorio por lo mismo de antes: una partición por fila
     metería el mismo comercio en train y test y el número no significaría
     nada.
  2. Verdad de referencia manual sobre 'Otros': mide el rescate real. Este es
     el resultado que importa.

Clases con menos de MIN_EJEMPLOS_CLASE ejemplos en el entrenamiento se
agrupan en un cajón "Otras (categoría minoritaria)": no hay ejemplos
suficientes para medir su acierto con honestidad por separado.

Resultado obtenido (15/08/2026), y por qué no se elige un "modelo ganador":
en el chequeo de cordura la regresión logística saca 97% de accuracy, pero
en el rescate real ningún modelo supera la línea base de predecir siempre la
categoría mayoritaria (~54%) -- se comprobó que no es un problema de
desbalanceo (balancear pesos lo empeora) ni de cobertura (un umbral de
confianza alto se estanca en 60-65% de precisión). El techo real es que
'Otros' son, por construcción, los comercios que NO se parecen textualmente a
nada que las reglas ya reconozcan -- así que un modelo entrenado en
similitud de texto tiene un límite estructural ahí. La única categoría que se
midió por separado y que sí compensa mover a una regla en vez de al modelo es
la de coincidencia de identidad exacta (reglas R099-R100 en la pestaña `rules`):
ninguna cantidad de datos del nombre del titular iba a enseñarle al modelo a
reconocer una cuenta propia en otro proveedor como la misma persona.

Uso:
    python pipeline/entrenar_clasificador.py
"""

import json
import sys
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import yaml
from google.oauth2.service_account import Credentials
from scipy.sparse import hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import GroupShuffleSplit
from sklearn.naive_bayes import MultinomialNB
from sklearn.preprocessing import OneHotEncoder
import gspread

_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ROOT))
from pipeline.logger import get_logger

MIN_EJEMPLOS_CLASE = 20
CAJON_MINORITARIO = "Otras (categoría minoritaria)"
RANDOM_STATE = 42
OUT_MODEL = _ROOT / "out" / "clasificador_categoria.joblib"
OUT_METRICS = _ROOT / "logs" / "clasificador_metrics.json"


def _load_cfg() -> dict:
    with open(_ROOT / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_transactions(cfg: dict) -> pd.DataFrame:
    creds = Credentials.from_service_account_file(
        _ROOT / cfg["credentials"]["gdrive_sa"],
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    sheet = gspread.authorize(creds).open_by_key(cfg["google_sheets"]["spreadsheet_id"])
    ws = sheet.worksheet(cfg["pipeline"]["sheet_name"])
    df = pd.DataFrame(ws.get_all_records())
    df["amount"] = pd.to_numeric(df["amount"], errors="coerce")
    return df


def _load_etiquetado(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path, encoding="utf-8-sig", sep=None, engine="python")
    return df[df["category_manual"].notna() & df["category_manual"].str.strip().ne("")]


def _agrupar_minoritarias(y: pd.Series, umbral: int) -> tuple[pd.Series, set[str]]:
    counts = y.value_counts()
    minoritarias = set(counts[counts < umbral].index)
    return y.where(~y.isin(minoritarias), CAJON_MINORITARIO), minoritarias


def _features(df: pd.DataFrame, vectorizer: TfidfVectorizer,
              type_encoder: OneHotEncoder, fit: bool):
    merchant = df["merchant_norm"].fillna("").astype(str)
    text = vectorizer.fit_transform(merchant) if fit else vectorizer.transform(merchant)

    amount = np.log1p(df["amount"].abs().fillna(0)).to_numpy().reshape(-1, 1)

    tipo = df["type"].fillna("").astype(str).to_numpy().reshape(-1, 1)
    tipo_oh = type_encoder.fit_transform(tipo) if fit else type_encoder.transform(tipo)

    return hstack([text, amount, tipo_oh]).tocsr()


def _entrenar_y_evaluar(nombre: str, modelo, X_train, y_train, X_dev, y_dev, log) -> dict:
    modelo.fit(X_train, y_train)
    pred = modelo.predict(X_dev)
    acc = accuracy_score(y_dev, pred)
    f1_macro = f1_score(y_dev, pred, average="macro", zero_division=0)
    f1_weighted = f1_score(y_dev, pred, average="weighted", zero_division=0)
    log.info("[%s] accuracy=%.3f  F1 macro=%.3f  F1 ponderado=%.3f",
              nombre, acc, f1_macro, f1_weighted)
    return {"modelo": nombre, "accuracy": acc, "f1_macro": f1_macro,
            "f1_ponderado": f1_weighted}


def _top_ngramas_por_categoria(modelo: LogisticRegression, vectorizer: TfidfVectorizer,
                                n_extra_cols: int, top_n: int = 8) -> dict[str, list[str]]:
    """N-gramas de caracteres que más empujan hacia cada categoría, según los
    coeficientes de la regresión logística. Es la vía de interpretabilidad
    del modelo."""
    vocab = np.array(vectorizer.get_feature_names_out())
    resultado = {}
    for i, categoria in enumerate(modelo.classes_):
        coefs = modelo.coef_[i][: len(vocab)]  # las columnas extra van al final
        top_idx = np.argsort(coefs)[-top_n:][::-1]
        resultado[categoria] = [vocab[j] for j in top_idx]
    return resultado


def main() -> int:
    cfg = _load_cfg()
    log = get_logger(Path(__file__).stem, cfg)

    try:
        log.info("Cargando transacciones desde Sheets...")
        tx = _load_transactions(cfg)

        entrenamiento = tx[tx["category"] != "Otros"].copy()
        log.info("Filas de entrenamiento (category != Otros): %d", len(entrenamiento))
        log.info("Comercios distintos en entrenamiento: %d",
                  entrenamiento["merchant_norm"].nunique())

        y_completo, minoritarias = _agrupar_minoritarias(
            entrenamiento["category"], MIN_EJEMPLOS_CLASE)
        log.info("Categorías agrupadas en %r (< %d ejemplos): %s",
                  CAJON_MINORITARIO, MIN_EJEMPLOS_CLASE, sorted(minoritarias))

        # --- split agrupado por comercio: el chequeo de cordura -------------
        splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=RANDOM_STATE)
        idx_train, idx_dev = next(splitter.split(
            entrenamiento, groups=entrenamiento["merchant_norm"]))
        train, dev = entrenamiento.iloc[idx_train], entrenamiento.iloc[idx_dev]
        y_train, y_dev = y_completo.iloc[idx_train], y_completo.iloc[idx_dev]

        solapados = set(train["merchant_norm"]) & set(dev["merchant_norm"])
        assert not solapados, f"fuga de comercios entre train y dev: {solapados}"
        log.info("Split agrupado -- train: %d filas / %d comercios | dev: %d filas / %d comercios",
                  len(train), train["merchant_norm"].nunique(),
                  len(dev), dev["merchant_norm"].nunique())

        vectorizer = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 4), min_df=2)
        type_encoder = OneHotEncoder(handle_unknown="ignore")

        X_train = _features(train, vectorizer, type_encoder, fit=True)
        X_dev = _features(dev, vectorizer, type_encoder, fit=False)

        modelos = {
            "naive_bayes":         MultinomialNB(),
            "regresion_logistica": LogisticRegression(max_iter=2000, class_weight="balanced"),
            "lightgbm":            __import__("lightgbm").LGBMClassifier(
                                        random_state=RANDOM_STATE, verbosity=-1),
        }

        log.info("--- Chequeo de cordura: comercios nunca vistos, dentro del régimen de reglas ---")
        resultados_dev = []
        entrenados = {}
        for nombre, modelo in modelos.items():
            resultados_dev.append(
                _entrenar_y_evaluar(nombre, modelo, X_train, y_train, X_dev, y_dev, log))
            entrenados[nombre] = modelo

        # No se elige un "modelo ganador" aquí: ganar el chequeo de cordura no
        # predice quién rescata mejor 'Otros' (ver docstring). Los tres se
        # entrenan y evalúan por igual en las dos pruebas.

        # --- rescate real: verdad de referencia sobre 'Otros' ---------------
        log.info("--- Rescate de 'Otros': verdad de referencia etiquetada a mano ---")
        etiquetado_path = _ROOT / "data" / "etiquetado_otros.csv"
        etiquetas = _load_etiquetado(etiquetado_path)
        log.info("Comercios con etiqueta manual usable: %d", len(etiquetas))

        otros = tx[tx["category"] == "Otros"].copy()
        otros = otros.merge(
            etiquetas[["merchant_norm", "category_manual"]],
            on="merchant_norm", how="inner",
        )
        log.info("Filas de 'Otros' con verdad de referencia (nivel transacción): %d", len(otros))

        y_real, _ = _agrupar_minoritarias(otros["category_manual"], MIN_EJEMPLOS_CLASE)
        # Umbral aplicado sobre el propio conjunto de rescate: una categoría
        # con pocos ejemplos aquí tampoco se puede evaluar con honestidad,
        # aunque tuviera volumen en el entrenamiento.
        X_otros = _features(otros, vectorizer, type_encoder, fit=False)

        clase_mayoritaria = y_real.value_counts().idxmax()
        n_linea_base = int((y_real == clase_mayoritaria).sum())
        acc_linea_base = n_linea_base / len(otros)
        log.info("[linea_base: siempre %r] rescata %d de %d filas de 'Otros' (%.1f%%)",
                  clase_mayoritaria, n_linea_base, len(otros), acc_linea_base * 100)

        rescate = [{"modelo": "linea_base_clase_mayoritaria", "clase": clase_mayoritaria,
                    "filas_rescatadas": n_linea_base, "filas_totales": len(otros),
                    "accuracy": acc_linea_base}]
        for nombre, modelo in entrenados.items():
            pred = modelo.predict(X_otros)
            acc = accuracy_score(y_real, pred)
            n_correctas = int((pred == y_real.to_numpy()).sum())
            supera_linea_base = acc > acc_linea_base
            log.info("[%s] rescata %d de %d filas de 'Otros' (%.1f%%) -- %s la linea base",
                      nombre, n_correctas, len(otros), acc * 100,
                      "SUPERA" if supera_linea_base else "NO supera")
            rescate.append({"modelo": nombre, "filas_rescatadas": n_correctas,
                             "filas_totales": len(otros), "accuracy": acc,
                             "supera_linea_base": supera_linea_base})

        log.info("--- Interpretabilidad: n-gramas que más pesan por categoría (%s) ---",
                  "regresion_logistica")
        ngramas = _top_ngramas_por_categoria(
            entrenados["regresion_logistica"], vectorizer, n_extra_cols=2)
        for categoria, top in ngramas.items():
            log.info("  %-30s %s", categoria, ", ".join(top))

        for nombre, modelo in entrenados.items():
            log.info("--- Informe de clasificación (%s), split de cordura ---", nombre)
            log.info("\n%s", classification_report(y_dev, modelo.predict(X_dev),
                                                     zero_division=0))
            log.info("--- Informe de clasificación (%s), rescate de 'Otros' ---", nombre)
            log.info("\n%s", classification_report(y_real, modelo.predict(X_otros),
                                                     zero_division=0))

        # Se guardan los tres modelos, no uno "ganador": ninguno superó la
        # línea base en el rescate real (ver docstring), así que elegir uno
        # para producción sería fingir una conclusión que los datos no dan.
        OUT_MODEL.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump({"modelos": entrenados, "vectorizer": vectorizer,
                     "type_encoder": type_encoder,
                     "minoritarias": sorted(minoritarias)}, OUT_MODEL)
        log.info("Modelos guardados en %s", OUT_MODEL)

        OUT_METRICS.parent.mkdir(parents=True, exist_ok=True)
        OUT_METRICS.write_text(json.dumps({
            "chequeo_cordura": resultados_dev,
            "rescate_otros": rescate,
            "ningun_modelo_supera_la_linea_base": not any(
                r["accuracy"] > acc_linea_base for r in rescate[1:]),
            "categorias_minoritarias": sorted(minoritarias),
            "n_gramas_por_categoria": ngramas,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        log.info("Métricas guardadas en %s", OUT_METRICS)

        return 0

    except Exception:
        log.exception("Unhandled error in %s", Path(__file__).name)
        sys.exit(1)


if __name__ == "__main__":
    raise SystemExit(main())
