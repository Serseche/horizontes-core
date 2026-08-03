# HORIZONTES — Resumen Ejecutivo de Sesión CTO
**Fecha:** 5 de julio de 2026 · **Modelo:** Claude Fable 5 · **Handoff a:** Claude Sonnet
**Estado global:** el proyecto NO está estancado — está más sano de lo que se siente. El motor pasa 5/5 canarios, la doctrina de junio está implementada en producción, y lo que faltaba eran piezas de operación (automatizar, importar, endurecer), no de arquitectura.

---

## 1. Qué se hizo (todo testeado offline: 38/38 tests + 5/5 canarios QA)

1. **Diagnóstico completo** de código y documentos. Hallazgo central: las copias del Project de Claude son un **snapshot pre-auditoría de junio** (sin QMJ Override, sin Bluelytics, sin `CachedScoringPipeline`). Tu producción local es más nueva que lo que ve cualquier modelo en este Project.
2. **Bug nuevo encontrado y corregido** (no cubierto por la auditoría de junio): `SyntaxError` en `test_qa_horizontes.py:410` (`global` a nivel de módulo tras asignación) — el harness de QA no podía ni ejecutarse. Corregido; 5/5 casos canarios verdes. → `test_qa_horizontes.py`
3. **Scoring diario automatizado** vía GitHub Actions (cron 00:30 ART) con runner CI robusto: cartera viva por defecto, exit code rojo si el éxito cae bajo el 80%, resumen JSON por corrida. → `run_daily_scoring.py`, `.github/workflows/horizontes_daily_scoring.yml`, `requirements.txt`
4. **Parser de Balanz Capital** tolerante (encoding, `;`/`,`, coma decimal AR, alias de encabezados, fechas mixtas) que emite filas para tu tabla existente `investment_journal`, con **dedup por hash** (re-importar no duplica) y tres modos: reporte/JSON/CSV, SQL idempotente, inserción directa a Supabase. → `balanz_parser.py` + `tests/` con 3 fixtures
5. **Hardening del CPI de FRED**: parsing defensivo (valores '.', validación de estructura, banda de sanidad −2%…15%, chequeo de frescura, retry) como drop-in de `fetch_us_inflation_cpi`. → `fred_cpi_patch.py` + 8 tests

## 2. Decisiones tomadas (y por qué)

- **Edge Function Deno → GitHub Actions.** Reimplementar el motor en TypeScript era mantener dos cerebros sincronizados para un dev solo. Actions corre tu Python canónico gratis, con secrets, logs, retry y disparo manual — y de paso mete el proyecto en git, que le falta. La opción Deno queda solo como *trigger* futuro si algún día querés scheduling nativo de Supabase; hoy no aporta.
- **RLS en `scoring_cache`/`macro_semaforo`: MANTENER (es intencional y correcto).** En Freemium, el score *es* el producto: exponerlo a `anon` regala el core y habilita scraping. Tu dashboard requiere login, así que nada queda bloqueado. Si algún día querés un teaser público en la landing, la solución es una VIEW acotada o un endpoint agregado — no abrir las tablas. Decisión cerrada.
- **Migración FRED→MCP: DESCARTADA.** Era infraestructura para un único CSV público; el patch de 120 líneas con tests lo resuelve mejor y sin dependencias nuevas.
- **Import Balanz → `investment_journal` (sin tabla nueva).** Evita una migración y convierte el import en feature: cada compra queda esperando su narrativa Housel.
- **Runner diario sobre la cartera viva (10 tickers), no el watchlist de 20.** Menos llamadas a yfinance, menos superficie de fallo; el watchlist amplio queda para corridas manuales.

## 3. Descartado para este ciclo (breve, como pediste)

- **ASL21**: nada de lo visto cambia el conflicto filosófico; además tu propio doc de sesión lo cerró ("no reabrir"). Ciclo 2.
- **Anti-Pánico Fase B**: tu criterio documentado era "solo con 15+ usuarios activos" — no se cumple. Dejo el esqueleto para no partir de cero cuando toque: *trigger* = señal SELL/pánico + semáforo rojo + drawdown; **capa de fricción temporal** (Thaler): la intención de venta se registra en una tabla `panic_intents` y se re-confirma a las 24 h; al re-confirmar, se muestra la narrativa Housel original de la compra + el contrafactual "si hubieras vendido ayer: Δ%". Una tabla, dos endpoints, un modal — sin ML.
- **Fase 2.5 (SEC EDGAR / Insider Conviction)**: agrega una dependencia (edgartools) y una capa de scoring cuando el cuello de botella actual es distribución y usuarios, no calidad de señal. Ciclo 2.

## 4. Qué quedó abierto

- El **parser Balanz no vio un export real** todavía (fixtures sintéticos). Riesgo bajo y acotado: si un encabezado no matchea, el fix es 1 línea en `COLUMN_ALIASES`.
- Los **nombres de env** que lee tu `CachedScoringPipeline.from_env()` (asumí `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`; si difieren, se ajusta el bloque `env:` del workflow).
- Los **casos canarios de QA no cubren la doctrina QMJ** de junio (el snapshot es anterior a ella).

## 5. Próximos pasos concretos (en orden)

1. **[Vos, ~30 min]** Seguir `SETUP_AUTOMATIZACION.md`: repo privado en GitHub, subir canónicos post-junio + entregables, cargar los 3 secrets, correr el workflow manual una vez. Resultado: scoring diario sin tocar nada nunca más.
2. **[Vos, 10 min]** Exportar tus operaciones reales de Balanz y correr `python balanz_parser.py export.csv`. Si el reporte marca columnas no reconocidas, pegárselo a Sonnet tal cual.
3. **[Sonnet / Claude Code]** Aplicar `fred_cpi_patch.py` a tu `DataFetcher.py` de producción y verificar/aplicar el fix de `test_qa_horizontes.py` en tu copia local.
4. **[Sonnet]** Agregar 2–3 casos canarios de **QMJ Hard Override** al QA (validar: QMJ proxy < 40 ⇒ score_final ≤ 65) usando tu `scoring_engine.py` de producción.
5. **[Vos, 10 min]** Actualizar los archivos del **Project de Claude** con los canónicos post-junio (`DataFetcher.py`, `scoring_engine.py`, `horizontes_migration_v1_1.sql`, `semaforo_macro_v2.js`, `horizontes_cockpit_cartera.html`) — evita que cualquier modelo vuelva a diagnosticar sobre código viejo, como me pasó a mí al inicio de esta sesión.

*Prompt sugerido para abrir con Sonnet:* «Leé RESUMEN_EJECUTIVO.md y SETUP_AUTOMATIZACION.md. Estoy en el paso N. [pegar output/error si hay]. Ejecutá el siguiente paso.»
