# HORIZONTES — Setup de automatización y herramientas nuevas
**Tiempo total estimado: ~30 minutos. Una sola vez.**

## 1. Scoring diario automático (reemplaza la Edge Function Deno)

El scoring de tu cartera corre solo, todos los días a las **00:30 hora Argentina**, en GitHub Actions — usando tu Python canónico, sin reescribir nada en TypeScript.

**Pasos:**
1. Crear un **repo privado** en GitHub (ej. `horizontes-core`).
2. Copiar dentro: tus archivos canónicos post-junio (`DataFetcher.py`, `scoring_engine.py`, `warmup_cache.py`) + los entregables de esta sesión (`run_daily_scoring.py`, `requirements.txt`, carpeta `.github/`, `tests/`, `balanz_parser.py`, `fred_cpi_patch.py`).
3. `git init && git add . && git commit -m "Horizontes core + automatización" && git push` (GitHub te da los comandos exactos al crear el repo).
4. En GitHub: **Settings → Secrets and variables → Actions → New repository secret**, cargar:
   - `SUPABASE_URL` → `https://hfrliueqvwnqmdehonvr.supabase.co`
   - `SUPABASE_SERVICE_ROLE_KEY` → tu clave **legacy JWT** (`eyJhbGci...`). ⚠️ La `sb_secret_` NO funciona con supabase-py.
   - `EODHD_API_KEY` → opcional.
5. Pestaña **Actions** → habilitar workflows → abrir "Horizontes — Scoring diario" → **Run workflow** (disparo manual de prueba).
6. Verificar: el job termina verde y en Supabase `scoring_cache` tiene `calculado_at` de hoy. El JSON de resumen queda como artifact descargable en cada corrida.

**Notas:**
- Cartera y perfiles se cambian por variable de entorno en el YAML (`HORIZONTES_TICKERS`, `HORIZONTES_PERFILES`) — sin tocar código.
- Si tu `CachedScoringPipeline.from_env()` lee nombres de env distintos a `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`, ajustá el bloque `env:` del workflow para que coincidan.
- Consumo: ~3 min/día ≈ 90 min/mes, contra 2.000 minutos gratis de GitHub. Sobra.
- Bonus real: el proyecto queda **bajo control de versiones**, que hoy no tiene (dashboards v1/v2/v3 dispersos son el síntoma).

## 2. Importar operaciones de Balanz

```powershell
# Vista previa + reporte (no escribe nada):
python balanz_parser.py mi_export_balanz.csv

# Generar SQL idempotente para pegar en el SQL Editor de Supabase:
python balanz_parser.py mi_export_balanz.csv --sql --user-id ea6ef068-223a-4e59-b6bc-68d4cfb8cdc0 > import_balanz.sql

# O insertar directo (requiere SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en el entorno):
python balanz_parser.py mi_export_balanz.csv --supabase --user-id ea6ef068-223a-4e59-b6bc-68d4cfb8cdc0
```

- Destino: **`investment_journal`** (tu tabla existente) — cada operación entra con `narrativa_housel` placeholder para que después completes tu tesis (eso es feature, no bug: el diario te obliga a escribir el "por qué").
- **Dedup automático**: cada fila lleva un tag `balanz:<hash>`; re-importar el mismo export no duplica nada.
- ⚠️ Los tests usan fixtures sintéticos. **Primera corrida con tu export real**: si el reporte dice que no reconoce una columna, el fix es agregar el alias en `COLUMN_ALIASES` (una línea) — pegale el reporte a Sonnet y lo resuelve en un mensaje.

## 3. Aplicar el patch de FRED (5 min, con Claude Code)

En tu `DataFetcher.py` de producción, reemplazar el cuerpo de `fetch_us_inflation_cpi()` por el de `fetch_us_inflation_cpi_v2()` de `fred_cpi_patch.py` (firma compatible; podés simplemente importar y delegar). Con esto la "migración a MCP" para FRED queda **descartada** — problema resuelto en 120 líneas con 8 tests.

## 4. QA

`test_qa_horizontes.py` corregido incluido (tenía un `SyntaxError` en la línea 410 que impedía ejecutarlo). Verificá si tu copia local tiene el mismo bug (`python test_qa_horizontes.py` — si no arranca, usá el archivo entregado). Con el fix: **5/5 casos canarios pasan**.
