"""
Cronometer -> SQLite sync
-------------------------
Loads a Cronometer "Daily Nutrition" CSV export and upserts it into the
local SQLite warehouse (data/powerlifting.db), keyed on date.

Usage:
    python scripts/sync_cronometer.py
    python scripts/sync_cronometer.py --csv /path/to/other_export.csv

Safe to re-run: re-importing the same CSV (or one with overlapping dates)
updates existing rows instead of duplicating them. This is also the
function a future cron job calls after pulling a fresh export automatically
(see PROJECT_LOG.md backlog) -- the CSV source is just swapped out underneath
it, nothing else here changes.
"""

import argparse
import sqlite3
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"
DEFAULT_CSV_PATH = DATA_DIR / "cronometer_daily_nutrition.csv"
DB_PATH = DATA_DIR / "powerlifting.db"

TABLE_NAME = "cronometer_daily_nutrition"

# Maps Cronometer's raw export headers (units baked in) to clean snake_case
# SQL column names. Units live in the suffix instead of the column name:
#   *_g = grams, *_mg = milligrams, *_ug = micrograms, *_iu = international units.
# If Cronometer changes its export headers, update this dict -- load_csv()
# will raise a clear error rather than silently dropping columns.
COLUMN_MAP = {
    "Date": "date",
    "Energy (kcal)": "energy_kcal",
    "Alcohol (g)": "alcohol_g",
    "Caffeine (mg)": "caffeine_mg",
    "Oxalate (mg)": "oxalate_mg",
    "Phytate (mg)": "phytate_mg",
    "Water (g)": "water_g",
    "B1 (Thiamine) (mg)": "b1_thiamine_mg",
    "B2 (Riboflavin) (mg)": "b2_riboflavin_mg",
    "B3 (Niacin) (mg)": "b3_niacin_mg",
    "B5 (Pantothenic Acid) (mg)": "b5_pantothenic_acid_mg",
    "B6 (Pyridoxine) (mg)": "b6_pyridoxine_mg",
    "B12 (Cobalamin) (\u00b5g)": "b12_cobalamin_ug",
    "Folate (\u00b5g)": "folate_ug",
    "Vitamin C (mg)": "vitamin_c_mg",
    "Vitamin D (IU)": "vitamin_d_iu",
    "Vitamin E (mg)": "vitamin_e_mg",
    "Vitamin K (\u00b5g)": "vitamin_k_ug",
    "Calcium (mg)": "calcium_mg",
    "Copper (mg)": "copper_mg",
    "Iron (mg)": "iron_mg",
    "Magnesium (mg)": "magnesium_mg",
    "Manganese (mg)": "manganese_mg",
    "Phosphorus (mg)": "phosphorus_mg",
    "Potassium (mg)": "potassium_mg",
    "Selenium (\u00b5g)": "selenium_ug",
    "Sodium (mg)": "sodium_mg",
    "Zinc (mg)": "zinc_mg",
    "Net Carbs (g)": "net_carbs_g",
    "Carbs (g)": "carbs_g",
    "Fiber (g)": "fiber_g",
    "Insoluble Fiber (g)": "insoluble_fiber_g",
    "Soluble Fiber (g)": "soluble_fiber_g",
    "Starch (g)": "starch_g",
    "Sugars (g)": "sugars_g",
    "Fat (g)": "fat_g",
    "Cholesterol (mg)": "cholesterol_mg",
    "Monounsaturated (g)": "monounsaturated_g",
    "Polyunsaturated (g)": "polyunsaturated_g",
    "Saturated (g)": "saturated_g",
    "Trans-Fats (g)": "trans_fats_g",
    "Omega-3 (g)": "omega_3_g",
    "ALA (g)": "ala_g",
    "DHA (g)": "dha_g",
    "EPA (g)": "epa_g",
    "Omega-6 (g)": "omega_6_g",
    "AA (g)": "aa_g",
    "LA (g)": "la_g",
    "Cystine (g)": "cystine_g",
    "Histidine (g)": "histidine_g",
    "Isoleucine (g)": "isoleucine_g",
    "Leucine (g)": "leucine_g",
    "Lysine (g)": "lysine_g",
    "Methionine (g)": "methionine_g",
    "Phenylalanine (g)": "phenylalanine_g",
    "Protein (g)": "protein_g",
    "Threonine (g)": "threonine_g",
    "Tryptophan (g)": "tryptophan_g",
    "Tyrosine (g)": "tyrosine_g",
    "Valine (g)": "valine_g",
    "Completed": "completed",
}

# Every mapped column except date/completed is a nutrient amount -> REAL.
NUTRIENT_COLUMNS = [c for c in COLUMN_MAP.values() if c not in ("date", "completed")]
ALL_COLUMNS = list(COLUMN_MAP.values())  # date first, completed last, matches dict insertion order


def build_schema_sql() -> str:
    """Generate the CREATE TABLE statement from COLUMN_MAP so the schema can't drift from the parser."""
    cols_sql = ",\n        ".join(f"{col} REAL" for col in NUTRIENT_COLUMNS)
    return f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        date TEXT PRIMARY KEY,
        {cols_sql},
        completed INTEGER
    )
    """


def load_csv(csv_path: Path) -> pd.DataFrame:
    """Read a Cronometer daily-nutrition export and return it with clean column names, ready to upsert."""
    df = pd.read_csv(csv_path)

    missing = set(COLUMN_MAP) - set(df.columns)
    if missing:
        raise ValueError(
            f"CSV is missing expected columns: {sorted(missing)}. "
            "Cronometer may have changed its export format -- update COLUMN_MAP in this script."
        )

    df = df.rename(columns=COLUMN_MAP)[ALL_COLUMNS]
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    df = df.dropna(subset=["date"])

    # "Completed" comes through as the literal strings "true"/"false" (or bool, depending
    # on pandas' type sniffing) -- normalise both cases to 0/1 explicitly rather than relying
    # on pandas to guess right.
    df["completed"] = (
        df["completed"].astype(str).str.strip().str.lower().map({"true": 1, "false": 0}).fillna(0).astype(int)
    )

    return df


def upsert(df: pd.DataFrame, db_path: Path) -> tuple[int, int]:
    """Insert/update rows keyed on date. Returns (rows in this CSV, total rows in the table after)."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(build_schema_sql())

        update_clause = ", ".join(f"{c}=excluded.{c}" for c in ALL_COLUMNS if c != "date")
        sql = f"""
            INSERT INTO {TABLE_NAME} ({", ".join(ALL_COLUMNS)})
            VALUES ({", ".join("?" for _ in ALL_COLUMNS)})
            ON CONFLICT(date) DO UPDATE SET {update_clause}
        """

        # NaNs (missing nutrients on a given day, e.g. oxalate not tracked back in 2022)
        # must become SQL NULL, not the float NaN -- sqlite3 doesn't have a clean NaN concept.
        rows = df[ALL_COLUMNS].astype(object).where(pd.notnull(df[ALL_COLUMNS]), None)

        conn.executemany(sql, rows.itertuples(index=False, name=None))
        conn.commit()

        total_rows = conn.execute(f"SELECT COUNT(*) FROM {TABLE_NAME}").fetchone()[0]
        return len(df), total_rows
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="Sync a Cronometer CSV export into the SQLite warehouse.")
    parser.add_argument("--csv", type=Path, default=DEFAULT_CSV_PATH, help="Path to the Cronometer export CSV.")
    parser.add_argument("--db", type=Path, default=DB_PATH, help="Path to the SQLite database file.")
    args = parser.parse_args()

    if not args.csv.exists():
        raise SystemExit(f"CSV not found: {args.csv}")

    df = load_csv(args.csv)
    n_csv, n_total = upsert(df, args.db)
    print(f"Synced {n_csv} rows from {args.csv.name} -> {args.db.name} ({n_total} total rows in {TABLE_NAME}).")


if __name__ == "__main__":
    main()
