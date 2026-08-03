"""
╔══════════════════════════════════════════════════════════════════╗
║   HORIZONTES — Cache Warm-Up Script                              ║
║   Pre-carga el scoring_cache de Supabase con un watchlist        ║
║   completo en los 4 perfiles del inversor.                       ║
╠══════════════════════════════════════════════════════════════════╣
║   Uso:                                                           ║
║     python warmup_cache.py              # watchlist completo     ║
║     python warmup_cache.py NVDA MSFT   # tickers específicos     ║
║     python warmup_cache.py --cedears   # solo CEDEARs            ║
║     python warmup_cache.py --usa       # solo acciones USA       ║
╚══════════════════════════════════════════════════════════════════╝
"""

import asyncio
import sys
import time
import logging
from datetime import datetime

# ── Carga automática del archivo .env ─────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # Si no está instalado, usa las variables del sistema

import os
import requests
from DataFetcher import CachedScoringPipeline, FetchConfig, DataSource, fetch_ccl_bluelytics
from scoring_engine import PerfilInversor


# ─── Actualizar CCL en macro_semaforo ─────────────────────────────────────────
def actualizar_ccl_en_semaforo():
    """Obtiene el CCL live de Bluelytics y actualiza macro_semaforo en Supabase."""
    url = os.environ.get("SUPABASE_URL", "")
    key = os.environ.get("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        log.warning("Sin credenciales Supabase — CCL no actualizado en semáforo.")
        return

    try:
        ccl_valor, ccl_fuente = asyncio.run(fetch_ccl_bluelytics())
        from datetime import date
        payload = {
            "valor_actual": round(ccl_valor, 2),
            "notas": f"Fuente: {ccl_fuente} · actualizado por warmup",
            "fecha_dato": date.today().isoformat(),
        }
        resp = requests.patch(
            f"{url}/rest/v1/macro_semaforo?indicador=eq.Tipo%20Cambio%20CCL",
            json=payload,
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "return=minimal",
            },
            timeout=10,
        )
        if resp.ok:
            log.info(f"  💱 CCL actualizado en semáforo: ${ccl_valor:,.2f} ARS/USD [{ccl_fuente}]")
        else:
            log.warning(f"  ⚠️  Error actualizando CCL: {resp.status_code} {resp.text[:80]}")
    except Exception as e:
        log.warning(f"  ⚠️  No se pudo actualizar CCL: {e}")

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,           # Silencia logs internos del DataFetcher
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("warmup")
log.setLevel(logging.INFO)

# ─── Watchlist ────────────────────────────────────────────────────────────────
# Editá estas listas libremente — son tu universo de análisis inicial.

WATCHLIST_USA = [
    # Mega-cap tecnología
    "NVDA", "MSFT", "AAPL", "GOOGL", "AMZN", "META",
    # Financieras
    "BRK-B", "JPM", "V",
    # Energía & consumo
    "XOM", "COST",
    # ETFs de referencia
    "SPY", "QQQ", "VTI",
]

WATCHLIST_CEDEARS = [
    "AAPL.BA", "MSFT.BA", "GOOGL.BA", "AMZN.BA",
    "NVDA.BA", "META.BA", "TSLA.BA",
]

# Todos los perfiles del ScoringEngine
PERFILES = list(PerfilInversor)

# Delay entre requests para no saturar yFinance (segundos)
DELAY_ENTRE_TICKERS = 3.0

# ─── Resultado ────────────────────────────────────────────────────────────────
class WarmupResult:
    def __init__(self):
        self.ok      = []   # (ticker, perfil, score, clasificacion, was_cached)
        self.errores = []   # (ticker, perfil, error_msg)

    @property
    def total(self):       return len(self.ok) + len(self.errores)
    @property
    def tasa_exito(self):  return len(self.ok) / self.total * 100 if self.total else 0


# ─── Función principal ────────────────────────────────────────────────────────
def warmup(tickers: list[str]) -> WarmupResult:
    result = WarmupResult()

    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║  HORIZONTES — Cache Warm-Up                              ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.info(f"  Tickers : {len(tickers)} ({', '.join(tickers)})")
    log.info(f"  Perfiles: {len(PERFILES)} ({', '.join(p.value for p in PERFILES)})")
    log.info(f"  Total ops: {len(tickers) * len(PERFILES)}")
    log.info("")

    pipeline = CachedScoringPipeline.from_env()

    if pipeline.cache_manager is None:
        log.error("╔══════════════════════════════════════════════════════════╗")
        log.error("║  ✗  Supabase no disponible — abortando warm-up.          ║")
        log.error("║     Los scores NO se guardarían en scoring_cache.        ║")
        log.error("║                                                          ║")
        log.error("║  Revisá el archivo .env en este directorio:              ║")
        log.error("║    SUPABASE_URL=https://xxxx.supabase.co                 ║")
        log.error("║    SUPABASE_SERVICE_KEY=eyJhbGci...                      ║")
        log.error("║                                                          ║")
        log.error("║  También verificá: pip install python-dotenv supabase    ║")
        log.error("╚══════════════════════════════════════════════════════════╝")
        sys.exit(1)

    inicio   = datetime.now()

    # Actualizar CCL live en macro_semaforo
    log.info("── Actualizando CCL en Semáforo Macro...")
    actualizar_ccl_en_semaforo()

    for i, ticker in enumerate(tickers):
        es_ultimo = i == len(tickers) - 1
        log.info(f"── [{i+1}/{len(tickers)}] {ticker} ──────────────────────────────")

        for perfil in PERFILES:
            try:
                score, was_cached = pipeline.get_score(ticker, perfil)
                c = score.clasificacion
                s = score.score_final

                emoji = "✅" if s >= 75 else "🔵" if s >= 55 else "🟡" if s >= 35 else "🔴"
                cached_tag = " [CACHE]" if was_cached else " [nuevo]"
                log.info(f"  {emoji} {perfil.value:<15} → {s:5.1f}/100  {c:<12}{cached_tag}")

                result.ok.append((ticker, perfil.value, s, c, was_cached))

            except Exception as e:
                msg = str(e)[:80]
                log.warning(f"  ⚠️  {perfil.value:<15} → ERROR: {msg}")
                result.errores.append((ticker, perfil.value, msg))

        # Delay solo entre tickers distintos (no al final)
        if not es_ultimo:
            log.info(f"  ⏳ Esperando {DELAY_ENTRE_TICKERS}s antes del siguiente ticker…")
            time.sleep(DELAY_ENTRE_TICKERS)

    # ─── Resumen ──────────────────────────────────────────────────────────────
    duracion = (datetime.now() - inicio).seconds
    log.info("")
    log.info("╔══════════════════════════════════════════════════════════╗")
    log.info("║  RESUMEN DEL WARM-UP                                     ║")
    log.info("╚══════════════════════════════════════════════════════════╝")
    log.info(f"  ✅ Exitosos : {len(result.ok)}/{result.total} ({result.tasa_exito:.0f}%)")
    log.info(f"  ⚠️  Errores  : {len(result.errores)}")
    log.info(f"  ⏱  Duración : {duracion // 60}m {duracion % 60}s")

    if result.errores:
        log.info("")
        log.info("  Tickers con error:")
        for ticker, perfil, msg in result.errores:
            log.info(f"    • {ticker}/{perfil}: {msg}")

    log.info("")
    log.info("  Abrí el dashboard y buscá cualquier ticker del watchlist")
    log.info("  — el score aparecerá al instante desde el caché. 🚀")
    log.info("═" * 58)

    # Top 5 mejores scores (perfil moderado)
    moderados = [(t, s, c) for t, p, s, c, _ in result.ok if p == "moderado"]
    if moderados:
        top = sorted(moderados, key=lambda x: x[1], reverse=True)[:5]
        log.info("")
        log.info("  🏆 TOP 5 — Perfil MODERADO:")
        for rank, (t, s, c) in enumerate(top, 1):
            log.info(f"    {rank}. {t:<12} {s:5.1f}/100  [{c}]")

    return result


# ─── Entry point ──────────────────────────────────────────────────────────────
if __name__ == "__main__":
    args = sys.argv[1:]

    if "--cedears" in args:
        tickers = WATCHLIST_CEDEARS
    elif "--usa" in args:
        tickers = WATCHLIST_USA
    elif args and not args[0].startswith("--"):
        # Tickers explícitos desde la línea de comandos
        tickers = [t.upper() for t in args]
    else:
        # Sin argumentos → watchlist completo
        tickers = WATCHLIST_USA + WATCHLIST_CEDEARS

    warmup(tickers)
