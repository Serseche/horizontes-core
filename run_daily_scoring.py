#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   HORIZONTES — Runner de scoring diario (CI / GitHub Actions)    ║
╠══════════════════════════════════════════════════════════════════╣
║   Reemplaza a la Edge Function Deno planificada en el roadmap.   ║
║   Decisión CTO (jul-2026): reutilizar el pipeline Python         ║
║   canónico (CachedScoringPipeline) en vez de duplicar el motor   ║
║   de scoring en TypeScript. Un solo cerebro, un solo lenguaje.   ║
╠══════════════════════════════════════════════════════════════════╣
║   Uso:                                                           ║
║     python run_daily_scoring.py               # cartera viva     ║
║     python run_daily_scoring.py --dry-run     # valida config    ║
║     python run_daily_scoring.py NVDA MELI.BA  # tickers ad-hoc   ║
║                                                                  ║
║   Variables de entorno:                                          ║
║     HORIZONTES_TICKERS    lista separada por comas (opcional)    ║
║     HORIZONTES_PERFILES   idem; default: los 4 perfiles          ║
║     HORIZONTES_MIN_EXITO  % de éxito mínimo p/ exit 0 (def: 80)  ║
║     SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY                      ║
║         → las consume CachedScoringPipeline.from_env().          ║
║           En GitHub Actions llegan como env del workflow;        ║
║           NO hace falta load_dotenv() en CI.                     ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("daily_scoring")
log.setLevel(logging.INFO)

# ─── Configuración por defecto ────────────────────────────────────────────────
# Cartera viva en Supabase (jul-2026): 8 CEDEARs + 2 acciones argentinas.
# GGAL y PAMP se guardan sin sufijo (convención Horizontes: bare = ADR/local
# fuera del circuito CEDEAR). Editable vía env HORIZONTES_TICKERS sin tocar código.
DEFAULT_PORTFOLIO: list[str] = [
    "GOOGL.BA", "MELI.BA", "NVDA.BA", "UNH.BA",
    "BRKB.BA", "BABA.BA", "QQQ.BA", "TSLA.BA",
    "GGAL", "PAMP",
]

DEFAULT_PERFILES: list[str] = ["liquidez_plus", "conservador", "moderado", "agresivo"]

DELAY_ENTRE_TICKERS = 3.0          # segundos — respeta rate limit de yfinance
SUMMARY_PATH = Path("scoring_run_summary.json")


# ─── Helpers de configuración ─────────────────────────────────────────────────
def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return list(default)
    return [item.strip().upper() if name == "HORIZONTES_TICKERS" else item.strip().lower()
            for item in raw.split(",") if item.strip()]


def _min_exito() -> float:
    try:
        return float(os.environ.get("HORIZONTES_MIN_EXITO", "80"))
    except ValueError:
        return 80.0


# ─── Núcleo ───────────────────────────────────────────────────────────────────
def run(tickers: list[str], perfiles: list[str], pipeline=None,
        delay: float = DELAY_ENTRE_TICKERS) -> dict:
    """
    Ejecuta el scoring de todos los (ticker, perfil) contra el pipeline cacheado.

    `pipeline` inyectable para tests. Si es None, se importa el canónico.
    El import es LAZY a propósito: permite --dry-run y tests sin que el
    entorno tenga Supabase/red configurados.
    """
    if pipeline is None:
        # Import canónico del proyecto. Requiere el DataFetcher.py de
        # producción (post-auditoría jun-2026) que incluye CachedScoringPipeline.
        from DataFetcher import CachedScoringPipeline  # noqa: WPS433
        pipeline = CachedScoringPipeline.from_env()

    from scoring_engine import PerfilInversor  # noqa: WPS433
    perfil_map = {p.value: p for p in PerfilInversor}

    perfiles_obj = []
    for p in perfiles:
        if p not in perfil_map:
            log.warning("Perfil desconocido '%s' — ignorado.", p)
            continue
        perfiles_obj.append(perfil_map[p])
    if not perfiles_obj:
        raise SystemExit("Sin perfiles válidos. Revisá HORIZONTES_PERFILES.")

    inicio = datetime.now(timezone.utc)
    ok: list[dict] = []
    errores: list[dict] = []

    log.info("═══ HORIZONTES · Scoring diario ═══")
    log.info("Tickers : %d (%s)", len(tickers), ", ".join(tickers))
    log.info("Perfiles: %s", ", ".join(p.value for p in perfiles_obj))

    for i, ticker in enumerate(tickers):
        log.info("── [%d/%d] %s ──", i + 1, len(tickers), ticker)
        for perfil in perfiles_obj:
            try:
                score, was_cached = pipeline.get_score(ticker, perfil)
                ok.append({
                    "ticker": ticker,
                    "perfil": perfil.value,
                    "score": round(float(score.score_final), 2),
                    "clasificacion": score.clasificacion,
                    "cached": bool(was_cached),
                })
                log.info("   %s %-13s → %5.1f  %s%s",
                         "✅", perfil.value, score.score_final,
                         score.clasificacion, " [cache]" if was_cached else "")
            except Exception as exc:  # noqa: BLE001 — un ticker no tumba el batch
                msg = str(exc)[:160]
                errores.append({"ticker": ticker, "perfil": perfil.value, "error": msg})
                log.warning("   ⚠️  %-13s → ERROR: %s", perfil.value, msg)
        if i < len(tickers) - 1:
            time.sleep(delay)

    total = len(ok) + len(errores)
    tasa = (len(ok) / total * 100) if total else 0.0
    resumen = {
        "timestamp_utc": inicio.isoformat(),
        "duracion_seg": int((datetime.now(timezone.utc) - inicio).total_seconds()),
        "tickers": tickers,
        "perfiles": [p.value for p in perfiles_obj],
        "total_ops": total,
        "exitosas": len(ok),
        "fallidas": len(errores),
        "tasa_exito_pct": round(tasa, 1),
        "resultados": ok,
        "errores": errores,
    }
    return resumen


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="HORIZONTES — scoring diario para CI")
    parser.add_argument("tickers", nargs="*", help="Tickers explícitos (opcional)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Valida configuración e imports sin ejecutar red/Supabase")
    parser.add_argument("--summary", default=str(SUMMARY_PATH),
                        help="Ruta del JSON de resumen (default: %(default)s)")
    args = parser.parse_args(argv)

    tickers = ([t.upper() for t in args.tickers]
               if args.tickers else _env_list("HORIZONTES_TICKERS", DEFAULT_PORTFOLIO))
    perfiles = _env_list("HORIZONTES_PERFILES", DEFAULT_PERFILES)
    umbral = _min_exito()

    if args.dry_run:
        log.info("DRY-RUN — configuración validada.")
        log.info("  Tickers  (%d): %s", len(tickers), ", ".join(tickers))
        log.info("  Perfiles (%d): %s", len(perfiles), ", ".join(perfiles))
        log.info("  Umbral éxito : %.0f%%", umbral)
        faltan = [v for v in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY")
                  if not os.environ.get(v)]
        if faltan:
            log.warning("  Env faltantes (necesarias en ejecución real): %s",
                        ", ".join(faltan))
        return 0

    resumen = run(tickers, perfiles)

    Path(args.summary).write_text(json.dumps(resumen, indent=2, ensure_ascii=False),
                                  encoding="utf-8")
    log.info("═══ RESUMEN ═══  %d/%d ok (%.1f%%) — resumen en %s",
             resumen["exitosas"], resumen["total_ops"],
             resumen["tasa_exito_pct"], args.summary)

    if resumen["tasa_exito_pct"] < umbral:
        log.error("Tasa de éxito %.1f%% < umbral %.0f%% → exit 1 (Actions en rojo).",
                  resumen["tasa_exito_pct"], umbral)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
