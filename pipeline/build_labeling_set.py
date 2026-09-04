#!/usr/bin/env python3
"""
Genera el fichero de etiquetado manual para el clasificador de categorías.

Extrae los comercios más frecuentes dentro de la categoría 'Otros' del tab
'transactions' y produce un CSV con una fila por comercio, listo para que el
usuario rellene a mano las columnas 'category_manual' y 'subcategory_manual'.

Estas etiquetas son la VERDAD DE REFERENCIA para evaluar el clasificador: se
mantienen fuera del Sheet a propósito. Escribirlas en el tab 'transactions'
sería inútil, porque apply_rules_to_sheet.py sobrescribe category y subcategory
en cada ejecución, y además contaminaría la evaluación.

Uso:
    python pipeline/build_labeling_set.py             # 200 comercios (por defecto)
    python pipeline/build_labeling_set.py --top 300
    python pipeline/build_labeling_set.py --category Otros
"""

import argparse
import sys
from pathlib import Path

import gspread
import pandas as pd
import yaml
from google.oauth2.service_account import Credentials

PROJ_DIR = Path(__file__).parent.parent.resolve()
sys.path.insert(0, str(PROJ_DIR))

# Comercios sin información suficiente para etiquetar: transferencias entre
# personas, reintegros en cajero y similares. Se marcan como no etiquetables en
# lugar de excluirse, porque cuantificarlos es un resultado en sí mismo — es el
# techo de lo que cualquier modelo puede alcanzar con estos datos.
NO_ETIQUETABLES = {
    "bizum",
    "transferencia realizada",
    "ret. efectivo a debito con tarj. en cajero. aut.",
}


def _load_cfg() -> dict:
    with open(PROJ_DIR / "config.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _load_transactions(cfg: dict) -> pd.DataFrame:
    creds = Credentials.from_service_account_file(
        PROJ_DIR / cfg["credentials"]["gdrive_sa"],
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    sheet = gspread.authorize(creds).open_by_key(cfg["google_sheets"]["spreadsheet_id"])
    ws = sheet.worksheet(cfg["pipeline"]["sheet_name"])
    return pd.DataFrame(ws.get_all_records())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=200,
                    help="número de comercios a incluir (por frecuencia)")
    ap.add_argument("--category", default="Otros",
                    help="categoría de la que extraer los comercios")
    ap.add_argument("--out", default="data/etiquetado_otros.csv",
                    help="ruta de salida (data/ está en .gitignore)")
    args = ap.parse_args()

    cfg = _load_cfg()
    df = _load_transactions(cfg)

    target = df[df["category"] == args.category]
    if target.empty:
        print(f"No hay filas en la categoría '{args.category}'")
        sys.exit(1)

    counts = target["merchant_norm"].value_counts()
    top = counts.head(args.top)

    # Un importe de ejemplo y el rango ayudan a decidir la categoría: no es lo
    # mismo un cargo recurrente de 9,99 que uno puntual de 400.
    amounts = target.copy()
    amounts["amount"] = pd.to_numeric(amounts["amount"], errors="coerce")
    stats = amounts.groupby("merchant_norm")["amount"].agg(["mean", "min", "max"])

    rows = []
    for merchant, n in top.items():
        s = stats.loc[merchant] if merchant in stats.index else None
        ejemplos = target[target["merchant_norm"] == merchant]["description"].head(1)
        rows.append({
            "merchant_norm":       merchant,
            "n_filas":             n,
            "importe_medio":       round(s["mean"], 2) if s is not None else "",
            "importe_min":         round(s["min"], 2) if s is not None else "",
            "importe_max":         round(s["max"], 2) if s is not None else "",
            "ejemplo_descripcion": ejemplos.iloc[0] if len(ejemplos) else "",
            "no_etiquetable":      "x" if merchant in NO_ETIQUETABLES else "",
            "category_manual":     "",
            "subcategory_manual":  "",
        })

    out = PROJ_DIR / args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8-sig")

    cubiertas = int(top.sum())
    total = len(target)
    sin_etiquetar = sum(r["n_filas"] for r in rows if r["no_etiquetable"] == "x")

    print(f"Escrito: {out}")
    print(f"  {len(rows)} comercios a etiquetar")
    print(f"  cubren {cubiertas} de {total} filas de '{args.category}' "
          f"({cubiertas / total * 100:.1f}%)")
    print(f"  de los cuales {sin_etiquetar} filas están marcadas como no etiquetables")
    print()
    print("Rellena category_manual y subcategory_manual. Deja en blanco las filas")
    print("marcadas con 'x' en no_etiquetable, o quita la marca si sí sabes qué son.")

    # Taxonomía existente, para que el etiquetado sea consistente con las reglas
    print()
    print("Categorías ya en uso:")
    for cat, n in df["category"].value_counts().items():
        subs = sorted(set(df[df["category"] == cat]["subcategory"]) - {""})
        print(f"  {cat} ({n})" + (f" -> {', '.join(subs)}" if subs else ""))


if __name__ == "__main__":
    main()
