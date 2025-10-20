from pathlib import Path
import sqlite3


def dump_sqlite_schema(db_path: Path) -> str:
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
        rows = cur.fetchall()
        return "\n\n".join(sql for (sql,) in rows if sql)
    finally:
        con.close()


if __name__ == "__main__":
    root = Path(__file__).resolve().parents[1]
    db_path = root / "ssmo.db"
    out_path = root / "docs" / "schema_main.sql"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if not db_path.exists():
        raise SystemExit(f"No se encontró la base en {db_path}. Ejecuta primero: flask --app app seed-db")

    sql = dump_sqlite_schema(db_path)
    out_path.write_text(sql, encoding="utf-8")
    print(f"Esquema exportado a {out_path}")

