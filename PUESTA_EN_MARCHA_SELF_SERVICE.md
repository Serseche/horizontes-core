# 🚀 Horizontes Self-Service — Puesta en Marcha

Esta tanda de trabajo cierra P0, P1 y el esqueleto de P2 del plan que definimos:
producto 100% automático, sin tu intervención manual en el camino de ningún usuario.

## Qué se construyó

| Archivo | Qué hace |
|---|---|
| `scoring_engine.py` | + `DISCLAIMER_HORIZONTES` embebido por default en **todo** `InvestmentScore` (P0) |
| `DataFetcher.py` | + `TickerSinDatosError`: un ticker sin datos ahora lanza un error claro en vez de fabricar un score 0.0/AVOID falso (P1) |
| `api_main.py` | **Nuevo.** API FastAPI: `POST /score`, `GET /me/quota`, `GET /health`. Reutiliza `CachedScoringPipeline` tal cual — no hay motor duplicado |
| `migration_usage_quota.sql` | **Nueva.** Tabla + RLS para el gate Free/Pro |
| `horizontes_self_service.html` | **Nuevo.** Frontend mínimo: login por email (magic link) + formulario de ticker + resultado con disclaimer siempre visible |
| `tests/test_api_score.py` | 8 tests de la API, 100% mockeados (auth, sin_datos, errores, cuota) |

Validado: **7/7 canarios QA + 8/8 tests de API**, cero regresión sobre lo que ya funcionaba.

## Lo que falta que hagas vos (no puedo hacerlo desde acá)

### 1. Conseguir el `SUPABASE_JWT_SECRET`
Es distinto de tu `SUPABASE_SERVICE_ROLE_KEY`. Andá a:
**Supabase → Settings → API → JWT Settings → JWT Secret**
Copialo y agregalo a tu `.env`:
```
SUPABASE_JWT_SECRET=el-secret-que-copiaste
```

### 2. Correr la migración
En **Supabase → SQL Editor**, pegá y ejecutá el contenido de `migration_usage_quota.sql`.

### 3. Habilitar login por email en Supabase
**Supabase → Authentication → Providers → Email** → activado (viene por default, solo confirmar).
**Supabase → Authentication → URL Configuration** → agregar la URL donde vayas a alojar `horizontes_self_service.html` (para que el magic link redirija bien).

### 4. Probar la API en local antes de deployar
```powershell
cd Horizontes
.venv\Scripts\python.exe -m pip install fastapi uvicorn[standard] pyjwt
.venv\Scripts\python.exe -m uvicorn api_main:app --reload --port 8000
```
Abrí `http://localhost:8000/health` en el navegador — debería responder `{"status":"ok",...}`.

### 5. Deploy de la API (recomendado: Render, capa gratuita alcanza para arrancar)
1. Nuevo repo o reusar `horizontes-core` (agregar `api_main.py`, `migration_usage_quota.sql`, `requirements.txt` actualizado)
2. En Render: **New → Web Service** → conectar el repo → Start command: `uvicorn api_main:app --host 0.0.0.0 --port $PORT`
3. Variables de entorno en Render: `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `SUPABASE_JWT_SECRET`

### 6. Completar y publicar el frontend
En `horizontes_self_service.html`, completar:
```js
const SUPABASE_ANON_KEY = "..."; // Supabase → Settings → API → anon/public key (legacy)
const API_BASE_URL      = "https://tu-api.onrender.com"; // la URL que te da Render
```
Después subilo a donde vayas a alojar el frontend (Vercel, Netlify, GitHub Pages — cualquiera sirve para un HTML estático).

## Qué NO cambió (a propósito)
- El cron nocturno (`run_daily_scoring.py` en GitHub Actions) sigue igual — sigue precalentando tu cartera de referencia. Este nuevo camino es **adicional**, para la cola larga de tickers que pida cualquier usuario.
- `warmup_cache.py` no se tocó — no usa `get_score()` en un path que rompa con este cambio (`TickerSinDatosError` se propaga igual que cualquier excepción, y ya tenías manejo de excepciones ahí).

## Lo que sigue (P3, cuando quieras continuar)
Import de cartera self-service (reemplazar que corras `balanz_parser.py` a mano): mismo patrón de auth que ya construimos acá (JWT → `user_id` real), aplicado a un endpoint `POST /portfolio/import` que reciba el CSV directamente del navegador del usuario.
