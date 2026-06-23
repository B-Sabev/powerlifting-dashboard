"""
Add historical data from FeelFit app to the database. 
weight_data.xlsx is not needed any more unless there is a database failure 
if database is lost and can't be recovered - 
weight_data.xlsx can be restored from the FeelFit app and this script can be run again to repopulate the database with historical data.
"""
import pandas as pd
import sqlite3

# File paths
EXCEL_FILE = "data/weight_data.xlsx"
DB_FILE = "data/powerlifting.db"
TABLE_NAME = "daily_measurements"

def main():
    df = pd.read_excel(EXCEL_FILE, sheet_name="Sheet1")
    df['Measure Time'] = pd.to_datetime(df['Measure Time'], dayfirst=True, errors='coerce')
    df = df.dropna(subset=['Measure Time']) # Drop rows with invalid datetime
    # Keep only rows with valid weight
    df['Weight(kg)'] = pd.to_numeric(df['Weight(kg)'], errors='coerce')
    df = df.dropna(subset=['Weight(kg)'])

    if df.empty:
        print("No valid data rows found. Exiting.")
        return

    # Sort by time (ascending) so the earliest measurement per day comes first
    df = df.sort_values('Measure Time')
    # Extract date part and keep the first row per day
    df['Date'] = df['Measure Time'].dt.date
    df_first = df.groupby('Date').first().reset_index()

    # Select only needed columns
    df_final = df_first[['Date', 'Weight(kg)', 'Body Fat(%)']].copy()
    df_final['Date'] = df_final['Date'].astype(str)  # YYYY-MM-DD format

    print(f"Found {len(df_final)} unique daily records.")

    #Connect to SQLite database and create table if not exists
    conn = sqlite3.connect(DB_FILE)
    cursor = conn.cursor()
    create_table_sql = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_NAME} (
        date TEXT PRIMARY KEY,
        weight_kg REAL,
        body_fat_percent REAL
    );
    """
    cursor.execute(create_table_sql)

    insert_sql = f"""
    INSERT OR REPLACE INTO {TABLE_NAME} (date, weight_kg, body_fat_percent)
    VALUES (?, ?, ?)
    """
    records = df_final.to_records(index=False)
    cursor.executemany(insert_sql, records)

    conn.commit()
    print(f"Successfully inserted {len(records)} records into table '{TABLE_NAME}'.")

    # 8. Show a few sample entries
    cursor.execute(f"SELECT * FROM {TABLE_NAME} ORDER BY date LIMIT 5")
    sample = cursor.fetchall()
    print("\nFirst 5 entries:")
    for row in sample:
        print(row)

    conn.close()

if __name__ == "__main__":
    main()