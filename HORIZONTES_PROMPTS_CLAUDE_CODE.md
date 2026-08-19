# HORIZONTES — Prompts para Claude Code en VS Code

Usá estos prompts en el panel CHAT de Claude Code (lado derecho de VS Code).
Abrí el archivo relevante antes de pegar el prompt para que Claude Code tenga contexto.

---

## BLOQUE 1 — Diagnóstico y verificación del stack

### 1.1 — Verificar que todo el stack funciona
```
Tengo un proyecto Python financiero en esta carpeta. Revisá los archivos 
DataFetcher.py, scoring_engine.py y warmup_cache.py.

Verificá:
1. Que warmup_cache.py tiene load_dotenv() al inicio y lo llama correctamente
2. Que CachedScoringPipeline.from_env() lee SUPABASE_URL y SUPABASE_SERVICE_KEY del entorno
3. Que el cliente Supabase en DataFetcher.py está inicializado correctamente
4. Que no haya imports faltantes o versiones incompatibles en requirements.txt

Si encontrás problemas, corregilos directamente en los archivos.
```

### 1.2 — Diagnosticar por qué warmup no guarda en Supabase
```
El warmup_cache.py muestra "✅ Exitosos: 4/4" pero la tabla scoring_cache 
en Supabase queda vacía después de correrlo.

Abrí warmup_cache.py y DataFetcher.py. Encontrá por qué los scores se 
calculan pero no se persisten. Probablemente el problema está en cómo 
CachedScoringPipeline detecta las credenciales de Supabase.

Mostrá el flujo exacto desde get_score() hasta el upsert en Supabase 
y arreglá el bug.
```

---

## BLOQUE 2 — Mejoras en DataFetcher.py

### 2.1 — Agregar async support
```
Abrí DataFetcher.py. La función fetch() es síncrona y usa time.sleep() 
entre retries. 

Refactorizá la clase DataFetcher para soportar asyncio:
- Convertí fetch() en async def fetch()
- Usá aiohttp en lugar de requests para las llamadas HTTP
- Mantené la API pública igual (mismos parámetros y return types)
- El CachedScoringPipeline puede seguir siendo síncrono por ahora

Asegurate de que los tests existentes sigan pasando.
```

### 2.2 — Mejorar el manejo de errores y logging
```
En DataFetcher.py, el manejo de errores es básico. Mejoralo:

1. Agregá tipos de excepción específicos: DataFetchError, SupabaseConnectionError
2. Agregá retry con backoff exponencial para llamadas a yFinance
3. Cuando falla EODHD, que haga fallback a yFinance automáticamente y logguee el fallback
4. Agregá un health_check() method que verifique conectividad con Supabase, yFinance y Bluelytics

No rompas la API existente.
```

### 2.3 — Actualizar semáforo macro automáticamente
```
En warmup_cache.py, la función actualizar_ccl_en_semaforo() solo actualiza el CCL.

Extendela para que también actualice automáticamente:
1. S&P500/SMA200 ratio — calcularlo desde los datos que ya bajamos de yFinance para SPY
2. Riesgo País EMBI — scrapear de https://indicadores.ar o usar el valor de Bluelytics si falla
3. Inflación CPI — consultá FRED API (serie CPIAUCSL) con la misma lógica que ya existe en DataFetcher

Todos los updates deben ser parte del flujo normal del warmup, antes del loop de tickers.
```

---

## BLOQUE 3 — Testing

### 3.1 — Generar suite completa de tests
```
Necesito una suite pytest completa para scoring_engine.py.

Generá el archivo tests/test_scoring_engine.py con:
1. Test de los 4 perfiles (liquidez_plus, conservador, moderado, agresivo) con métricas mock
2. Test que el score esté entre 0 y 100 siempre
3. Test del módulo CEDEAR/VRU con precio ARS simulado
4. Test de la alerta trampa de devaluación (cuando CCL implícito << CCL real)
5. Test de edge cases: métricas todas en None, P/E negativo, RSI extremo

Usá pytest fixtures y mock data realistas basados en NVDA y AAPL.
Asegurate que los tests sean independientes entre sí.
```

### 3.2 — Test de integración Supabase
```
Generá tests/test_supabase_integration.py que verifique:

1. Que CachedScoringPipeline.from_env() se inicializa correctamente
2. Que get_score() devuelve (ScoringResult, bool) 
3. Que el segundo llamado al mismo ticker devuelve was_cached=True
4. Que los datos en scoring_cache tienen todos los campos requeridos (score_final, clasificacion, etc.)

Usá variables de entorno de prueba y una tabla scoring_cache_test separada para no contaminar producción.
```

---

## BLOQUE 4 — FastAPI endpoint

### 4.1 — Crear API REST
```
Creá un archivo api.py en la carpeta del proyecto con FastAPI que exponga:

GET  /health              → status de conexión Supabase + yFinance
GET  /analyze/{ticker}    → score para un ticker, acepta ?perfil=moderado
GET  /top10?perfil=largo  → los 10 mejores tickers del caché para ese perfil
GET  /semaforo            → estado actual del semáforo macro desde Supabase
POST /warmup              → dispara el warmup de una lista de tickers (body: {"tickers": ["NVDA","META"]})

Usá CachedScoringPipeline de DataFetcher.py.
Agregá CORS habilitado para localhost:3000 y *.supabase.co.
Incluí manejo de errores HTTP apropiado (404 si no hay caché, 503 si falla yFinance).

El servidor debe arrancar con: uvicorn api:app --reload
```

---

## BLOQUE 5 — Dashboard

### 5.1 — Hacer que el semáforo siempre lea de Supabase
```
Abrí horizontes_dashboard_v3.html.

El semáforo macro actualmente usa DEMO_SEM (datos hardcodeados) cuando está 
en modo demo. Cuando conecta a Supabase, lee macro_semaforo correctamente.

El problema: si el usuario conecta y luego recarga la página, vuelve a modo demo.

Solucionalo guardando config (url + key) en localStorage del browser para que 
persista entre recargas, y que el semáforo siempre consulte Supabase si hay 
credenciales guardadas.

También agregá un botón "Actualizar semáforo" que fuerce un re-fetch de macro_semaforo.
```

### 5.2 — Agregar búsqueda con autocompletado
```
En horizontes_dashboard_v3.html, el input del ticker es texto libre.

Mejoralo:
1. Al conectar a Supabase, cargá la lista de tickers disponibles en scoring_cache
2. Mostrá un dropdown con sugerencias mientras el usuario escribe
3. Indicá al lado de cada sugerencia el score y clasificación actuales
4. Si el ticker no está en caché, mostrá "⚡ Análisis en tiempo real" como opción

Usá el mismo sbFetch() que ya existe para consultar la lista.
```

### 5.3 — Modo de comparación de tickers
```
Agregá a horizontes_dashboard_v3.html un botón "Comparar" que permita 
agregar hasta 3 tickers a una vista comparativa lado a lado.

Mostrá para cada uno:
- Score total y clasificación
- Los 4 bloques en una mini tabla
- Un radar chart (usando SVG puro, sin librerías externas) con los 4 dimensiones

El modo comparación debe poder activarse/desactivarse sin perder la vista principal.
```

---

## BLOQUE 6 — Seguridad y producción

### 6.1 — Preparar para deployment
```
El proyecto corre localmente. Ayudame a prepararlo para deployment en producción.

Revisá todos los archivos y:
1. Asegurate que no haya credenciales hardcodeadas en ningún .py o .html
2. Generá un .gitignore apropiado para este stack (Python + Node + secrets)
3. Creá un Dockerfile para el backend Python (DataFetcher + warmup)
4. Documentá las variables de entorno requeridas en un .env.example

El frontend (HTML) es estático y puede servirse desde cualquier CDN.
```

### 6.2 — Agregar rate limiting al warmup
```
El warmup_cache.py hace muchas llamadas a yFinance en poco tiempo.
yFinance puede bloquear si hay demasiadas requests.

Mejorá el warmup:
1. Agregá rate limiting configurable (por defecto: max 10 requests/minuto)
2. Detectá cuando yFinance devuelve datos vacíos (síntoma de throttling) y 
   esperá 60 segundos antes de reintentar
3. Logueá claramente cuando hay throttling
4. Agregá --workers N flag para correr N tickers en paralelo con asyncio
   (solo si ya implementaste el async del prompt 2.1)
```
