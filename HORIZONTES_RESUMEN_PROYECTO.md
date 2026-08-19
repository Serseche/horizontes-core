# HORIZONTES — Estado del Proyecto al 22/04/2026

## Stack Completo (100% operativo)

```
yFinance → DataFetcher.py → ScoringEngine → Supabase (scoring_cache) → horizontes_dashboard_v3.html
```

---

## Archivos del proyecto (C:\Users\Usuario\Documents\Horizontes\)

| Archivo | Estado | Función |
|---|---|---|
| `DataFetcher.py` | ✅ Completo | Fetcher dual yFinance/EODHD, CCL Bluelytics, CEDEAR/VRU |
| `scoring_engine.py` | ✅ Completo | Graham/Murphy/Lynch/InvestingPro, 4 perfiles |
| `warmup_cache.py` | ✅ Con load_dotenv | Pre-carga scoring_cache en Supabase, actualiza CCL |
| `cargar_mercado_completo.bat` | ✅ Listo | Doble click → carga 40+ tickers automáticamente |
| `horizontes_dashboard_v3.html` | ✅ Versión activa | Dashboard con Top 10, Semáforo Dalio, Explainer |
| `.env` | ✅ Configurado | SUPABASE_URL, SUPABASE_ANON_KEY, SUPABASE_SERVICE_KEY |
| `requirements.txt` | ✅ | Dependencias Python |
| `.venv/` | ✅ Activo | Entorno virtual Python 3.12 |

---

## Supabase (Horizontes-SaaS)
- **URL**: https://hfrliueqvwnqmdehonvr.supabase.co
- **Región**: East US (North Virginia) — plan NANO/FREE
- **Tablas**: `scoring_cache`, `macro_semaforo`, `user_profiles`, `kids_saver_goals`, `investment_journal`
- **Políticas RLS**: Activas. `scoring_cache` tiene policy `anon` para lectura pública.

### Estado del scoring_cache
- Tickers cargados: NVDA, META, GOOGL, MSFT, AAPL, AMZN + CEDEARs (.BA)
- TTL: 24 horas. Expiran automáticamente.
- Para recargar: correr `cargar_mercado_completo.bat` (doble click)

### Semáforo macro (valores actuales correctos al 22/04/2026)
| Indicador | Valor | Estado |
|---|---|---|
| Tasa Fed Funds | 3.75% | VERDE |
| Inflación CPI YoY | 2.90% | AMARILLO |
| Riesgo País EMBI (AR) | 526 bps | AMARILLO |
| CCL | 1465 ARS/USD | AMARILLO |
| S&P 500 / SMA200 | 1.11 | VERDE |

---

## Dashboard v3 — Funcionalidades activas

1. **Score de Convicción** — Gauge animado + clasificación STRONG BUY/ACCUMULATE/HOLD/AVOID
2. **Ficha de Salud** — 4 bloques: Graham / Murphy / Lynch / InvestingPro con barras de progreso
3. **Narrativa Housel** — Texto contextual anti-pánico generado por el ScoringEngine
4. **Alertas** — RSI sobrecompra, Current Ratio bajo, PEG alto, etc.
5. **Comparador de Horizontes** — Liquidez / Corto / Mediano / Largo
6. **Top 10** — Ranking dinámico por perfil desde scoring_cache
7. **Panel CEDEAR/VRU** — Aparece solo si es CEDEAR, muestra alerta trampa de devaluación
8. **Explainer** — Descripción de los 4 pilares + clasificaciones + disclaimer legal
9. **Semáforo Macro Dalio** — 5 indicadores con descripción de ciclos económicos
10. **Timestamp** — Fecha de cálculo y expiración del caché

---

## Claves API (dónde encontrarlas)

- **Para el dashboard** (browser): `anon key` → Supabase → Settings → API → Legacy → anon/public
- **Para el warmup** (Python): `service_role key` → Supabase → Settings → API → Legacy → service_role → Reveal

---

## Perfiles del ScoringEngine

| Perfil DB | Label UI | Horizonte | Pesos |
|---|---|---|---|
| `liquidez_plus` | Liquidez | < 1 año | 70% Murphy / 30% InvestingPro |
| `conservador` | Corto | 1–2 años | 40% Graham / 30% InvestingPro / 25% Murphy / 5% Lynch |
| `moderado` | Mediano | 3–7 años | 45% Graham / 25% InvestingPro / 20% Lynch / 10% Murphy |
| `agresivo` | Largo | 10+ años | 60% InvestingPro / 30% Lynch / 10% Graham / 0% Murphy |

---

## Pendientes / Mejoras identificadas

- [ ] Hacer que el semáforo macro se actualice automáticamente desde la DB (no solo en modo demo)
- [ ] Agregar `load_dotenv()` al inicio de DataFetcher.py para entorno de desarrollo
- [ ] Suite de tests pytest para scoring_engine.py (4 perfiles + casos CEDEAR/VRU)
- [ ] FastAPI endpoint `/analyze` para exponer CachedScoringPipeline como REST API
- [ ] Supabase Realtime para push notifications de score changes
- [ ] Actualización automática diaria del semáforo macro (FRED API para CPI, Bluelytics para CCL)
- [ ] Módulo Kids Saver (tabla ya creada en Supabase, falta frontend)

---

## Flujo diario de uso

1. Abrir VS Code → carpeta Horizontes
2. Ctrl+` para abrir terminal
3. `.venv\Scripts\activate`
4. `python warmup_cache.py` o doble click en `cargar_mercado_completo.bat`
5. Abrir `horizontes_dashboard_v3.html` en el browser
6. ⚙ → anon key → buscar ticker
