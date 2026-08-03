"""
╔══════════════════════════════════════════════════════════════════╗
║   HORIZONTES — Patch: parsing robusto de CPI desde FRED          ║
╠══════════════════════════════════════════════════════════════════╣
║   Decisión CTO (jul-2026): la "migración a MCP" para FRED queda  ║
║   DESCARTADA. Es un único endpoint CSV público; la fragilidad    ║
║   se resuelve con parsing defensivo, no con infraestructura.     ║
║                                                                  ║
║   Cómo aplicar: en DataFetcher.py, reemplazar el cuerpo de       ║
║   fetch_us_inflation_cpi() por fetch_us_inflation_cpi_v2()       ║
║   (o importar esta función y delegar). Firma compatible.         ║
║                                                                  ║
║   Endurecimientos vs versión original:                           ║
║     · Parsing con csv.reader (no split(',') a mano)              ║
║     · Ignora valores faltantes de FRED ('.')                     ║
║     · Valida encabezado y estructura antes de indexar            ║
║     · YoY sobre las últimas 13 observaciones VÁLIDAS             ║
║     · Chequeo de frescura: última obs > 120 días ⇒ warning       ║
║     · Banda de sanidad: −2% ≤ CPI YoY ≤ 15% ⇒ si no, default     ║
║     · Un reintento con backoff; timeout explícito                ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import csv
import io
import logging
import time
from datetime import date, datetime
from typing import Callable, Optional

import requests

log = logging.getLogger("DataFetcher")

FRED_CPI_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=CPIAUCSL"
INFLACION_US_DEFAULT = 0.031          # mantener sincronizado con DataFetcher.py
_SANITY_MIN, _SANITY_MAX = -0.02, 0.15
_STALE_DIAS = 120                     # CPI publica con ~2 semanas de lag; 120d = feed muerto


def _parse_fred_csv(texto: str) -> list[tuple[date, float]]:
    """Devuelve [(fecha, valor)] solo con observaciones válidas, orden original."""
    filas = list(csv.reader(io.StringIO(texto)))
    if len(filas) < 2:
        raise ValueError("CSV de FRED sin datos.")
    # Encabezado esperado: DATE/observation_date + serie
    header = [c.strip().lower() for c in filas[0]]
    if not header or header[0] not in ("date", "observation_date"):
        raise ValueError(f"Encabezado FRED inesperado: {filas[0]!r}")

    out: list[tuple[date, float]] = []
    for fila in filas[1:]:
        if len(fila) < 2:
            continue
        raw_fecha, raw_val = fila[0].strip(), fila[1].strip()
        if raw_val in ("", "."):          # FRED marca faltantes con '.'
            continue
        try:
            f = datetime.strptime(raw_fecha, "%Y-%m-%d").date()
            v = float(raw_val)
        except ValueError:
            continue
        out.append((f, v))
    return out


def fetch_us_inflation_cpi_v2(
    override: Optional[float] = None,
    http_get: Callable = requests.get,      # inyectable para tests
    timeout: float = 8.0,
    attempts: int = 2,
) -> float:
    """
    Inflación USA anualizada (CPI YoY, decimal). Drop-in de la versión original.
    Nunca lanza: ante cualquier problema devuelve INFLACION_US_DEFAULT con warning.
    """
    if override is not None:
        return override

    texto = None
    for intento in range(1, attempts + 1):
        try:
            resp = http_get(FRED_CPI_URL, timeout=timeout)
            if getattr(resp, "status_code", 0) == 200 and resp.text:
                texto = resp.text
                break
            log.warning("[FRED] HTTP %s en intento %d.",
                        getattr(resp, "status_code", "?"), intento)
        except Exception as exc:  # noqa: BLE001
            log.warning("[FRED] Error de red intento %d: %s", intento, exc)
        if intento < attempts:
            time.sleep(1.5 * intento)

    if texto is None:
        log.warning("[FRED] Sin respuesta. Usando default %.2f%%.",
                    INFLACION_US_DEFAULT * 100)
        return INFLACION_US_DEFAULT

    try:
        obs = _parse_fred_csv(texto)
        if len(obs) < 13:
            raise ValueError(f"Solo {len(obs)} observaciones válidas (<13).")

        fecha_ult, val_ult = obs[-1]
        _, val_hace_12m = obs[-13]
        if val_hace_12m <= 0:
            raise ValueError("Valor base ≤ 0 en la serie CPI.")

        if (date.today() - fecha_ult).days > _STALE_DIAS:
            log.warning("[FRED] Última observación %s tiene más de %d días — "
                        "posible feed detenido; se usa igual.",
                        fecha_ult.isoformat(), _STALE_DIAS)

        yoy = round((val_ult - val_hace_12m) / val_hace_12m, 4)
        if not (_SANITY_MIN <= yoy <= _SANITY_MAX):
            raise ValueError(f"CPI YoY {yoy:.2%} fuera de banda de sanidad "
                             f"[{_SANITY_MIN:.0%}, {_SANITY_MAX:.0%}].")
        return yoy

    except Exception as exc:  # noqa: BLE001
        log.warning("[FRED] Parsing falló (%s). Usando default %.2f%%.",
                    exc, INFLACION_US_DEFAULT * 100)
        return INFLACION_US_DEFAULT
