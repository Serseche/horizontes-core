# 📋 Qué cambió en esta tanda (post revisión de archivos Fable 5)

## Diagnóstico de los archivos que compartiste
La mayoría de la sesión de Fable 5 (jul-2026) **ya está viva en tu producción**:
`balanz_parser.py`, `run_daily_scoring.py`, el workflow, los tests — confirmado
cruzando contra lo que corrió hoy mismo en tu repo real. Nada de eso hacía falta
reconstruir.

**Gap real encontrado:** el punto 4 del propio `RESUMEN_EJECUTIVO.md` de Fable 5
decía "aplicar `fred_cpi_patch.py` a tu `DataFetcher.py` de producción" — quedó
pendiente y nunca se hizo. Tu `fetch_us_inflation_cpi()` seguía con el parsing
frágil original (`split(",")[1]` a mano, sin banda de sanidad, sin manejo de
valores faltantes de FRED). Lo apliqué ahora.

## Archivos en este entregable

| Archivo | Cambio |
|---|---|
| `DataFetcher.py` | `fetch_us_inflation_cpi()` ahora delega en `fred_cpi_patch.py` (no duplica lógica) — más P0/P1 de la tanda anterior |
| `fred_cpi_patch.py` | Sin cambios — es el archivo original de Fable 5, ahora sí conectado a producción |
| `api_main.py` | + endpoint `POST /portfolio/import` (P3): el usuario sube su CSV de Balanz, autenticado, y se inserta bajo SU `user_id` real — reemplaza que vos corras `balanz_parser.py --supabase` a mano |
| `tests/test_api_score.py` | 14 tests (8 de `/score` + 6 nuevos de `/portfolio/import`), incluyendo el test que prueba que dos usuarios distintos con el mismo archivo se guardan cada uno bajo su propio user_id |

**No incluido acá** (sin cambios, ya los tenías): `balanz_parser.py`, `run_daily_scoring.py`,
`test_qa_horizontes.py`, `scoring_engine.py` (de la tanda anterior), workflow de Actions.

## Validado
7/7 canarios QA · 52/52 pytest (suite completa: Balanz + FRED + runner + API) · 6/6 archivos core compilan.

## Instrucciones de aplicación
1. Reemplazá `DataFetcher.py` en tu carpeta real (mismo proceso de siempre: backup, reemplazar, correr tests)
2. Agregá `fred_cpi_patch.py` a tu carpeta si no está ya (aunque lo subiste, confirmá que está en la raíz junto a `DataFetcher.py`)
3. Agregá el nuevo `api_main.py` (pisa el de la tanda anterior — ya incluye `/score` + `/portfolio/import`)
4. `tests/test_api_score.py` también se pisa completo

## Estado del plan self-service
- ✅ P0 — disclaimer embebido
- ✅ P1 — estado sin_datos (más el bug real de FRED, encontrado en esta revisión)
- ✅ P2 — API self-service (`/score`, `/me/quota`)
- ✅ P3 — import de cartera self-service (`/portfolio/import`)

**El producto está funcionalmente completo del lado del código.** Lo que falta es
100% infraestructura/configuración de tu lado: `SUPABASE_JWT_SECRET`, migración
de `usage_quota`, deploy de la API, completar el frontend — todo documentado en
`PUESTA_EN_MARCHA_SELF_SERVICE.md` de la tanda anterior.
