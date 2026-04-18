---
name: duckdb-analyst
description: Use for any analytical question over ./data/*.parquet. Prefer DuckDB, always show the SQL.
---

# DuckDB Analyst

## Conventions

- Data lives in `./data/*.parquet` (a read-only mount). Never write there.
- Use DuckDB — either the `duckdb` CLI or `python -c "import duckdb; …"`.
- **Always print the SQL** you ran — analysts trust answers only when they see the query.
- Cap queries to `LIMIT 1000` by default.
- Save intermediate results as `./cache-<slug>.parquet` in the workdir.

## Workflow

1. `ls ./data` to discover files
2. `duckdb -c "DESCRIBE SELECT * FROM read_parquet('./data/X.parquet') LIMIT 0"` to learn schema
3. Write the query and run it via `duckdb -c "..."` or a short Python script
4. If the user wants a chart, follow the `matplotlib-charts` skill next

## Guardrails

- If the underlying file is larger than ~10 GB, warn the user before scanning it
- Never suggest or run `DELETE` / `UPDATE` / `DROP` — DuckDB on parquet can't anyway, but the LLM must still not suggest it

## Example shell

```bash
duckdb -c "SELECT product, SUM(revenue) AS total
           FROM read_parquet('./data/sales.parquet')
           GROUP BY product ORDER BY total DESC LIMIT 1000"
```
