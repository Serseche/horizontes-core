"""
╔══════════════════════════════════════════════════════════════╗
║         HORIZONTES — DataFetcher v1.0                        ║
║         Módulo de Adquisición y Normalización de Datos       ║
║         Fuentes: Yahoo Finance (yfinance) / EODHD            ║
╚══════════════════════════════════════════════════════════════╝

Responsabilidades:
  · Obtener datos de mercado y fundamentales (Yahoo Finance o EODHD)
  · Mapear automáticamente → modelo MetricasFinancieras del ScoringEngine
  · Lógica especializada CEDEARs: CCL implícito, ratio de conversión, VRU
  · Manejo robusto de errores: None si la API no devuelve el dato

Integración con ScoringEngine:
    from DataFetcher import DataFetcher, DataSource
    from scoring_engine import ScoringRequest, PerfilInversor

    fetcher  = DataFetcher(source=DataSource.YFINANCE)
    metricas = fetcher.fetch("AAPL.BA")       # CEDEAR → CCL implícito calculado
    request  = ScoringRequest(
        ticker  = "AAPL.BA",
        perfil  = PerfilInversor.MODERADO,
        metricas = metricas,
    )
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
import requests
import yfinance as yf

# Importa el modelo Pydantic desde el ScoringEngine
from scoring_engine import (
    MetricasFinancieras,
    ScoringEngine,
    ScoringRequest,
    InvestmentScore,
    ScoreBloque,
    ValorRealUSD,
    PerfilInversor,
)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] DataFetcher — %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("DataFetcher")


# ─────────────────────────────────────────────────────────────────────────────
# 1. ENUMERACIONES Y CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

class DataSource(str, Enum):
    YFINANCE = "yfinance"
    EODHD    = "eodhd"


# Ratios de conversión CEDEAR → Subyacente (N CEDEARs = 1 acción subyacente)
# Fuente: tabla oficial BYMA/CNV publicada el 30/09/2025.
# ⚠️  Los ratios cambian con splits y resoluciones de la CNV. Verificar en:
#     https://data-widgets.byma.com.ar antes de operar un CEDEAR nuevo.
CEDEAR_RATIOS: dict[str, float] = {
    # ── Tecnología ─────────────────────────────────────────────────────────
    "AAPL":   20.0,   # Apple Inc.                   BYMA 30/09/2025 (era 9)
    "MSFT":   30.0,   # Microsoft Corp.              BYMA 30/09/2025 (era 4)
    "GOOGL":  58.0,   # Alphabet Inc.                BYMA 30/09/2025 (era 2)
    "AMZN":  144.0,   # Amazon.Com Inc.              BYMA 30/09/2025 (era 7)
    "NVDA":   24.0,   # Nvidia Corporation           BYMA 30/09/2025 (era 10)
    "META":   24.0,   # Meta Platforms Inc           BYMA 30/09/2025 (era 4/8)
    "TSLA":   15.0,   # Tesla Inc.                   BYMA 30/09/2025 (era 5)
    "NFLX":   48.0,   # Netflix Inc.                 BYMA 30/09/2025 (era 4/16)
    "PYPL":    8.0,   # PayPal Holdings Inc.         BYMA 30/09/2025 (era 2)
    "INTC":    5.0,   # Intel Corporation            BYMA 30/09/2025 (era 4)
    "AMD":    10.0,   # Advanced Micro Devices Inc.  BYMA 30/09/2025 (era 4)
    "ADBE":   44.0,   # Adobe Systems Inc.           BYMA 30/09/2025 (era 4/22)
    "ORCL":    3.0,   # Oracle Corporation           BYMA 30/09/2025
    "CSCO":    5.0,   # Cisco Systems Inc            BYMA 30/09/2025 (era 4)
    "CRM":    18.0,   # Salesforce Inc.              BYMA 30/09/2025 (era 3)
    # ── Consumo & Salud ────────────────────────────────────────────────────
    "KO":      5.0,   # The Coca-Cola Company        BYMA 30/09/2025 (era 2)
    "PFE":     4.0,   # Pfizer Inc.                  BYMA 30/09/2025
    "WMT":    18.0,   # Walmart Inc.                 BYMA 30/09/2025 (era 2)
    "DIS":    12.0,   # The Walt Disney Co. (DISN)   BYMA 30/09/2025 (era 2/4)
    "UNH":    33.0,   # UnitedHealth Group Inc.      BYMA 30/09/2025
    # ── Financiero ─────────────────────────────────────────────────────────
    "JPM":    15.0,   # J.P. Morgan & Chase Co.      BYMA 30/09/2025 (era 2/5)
    "BAC":     4.0,   # Bank of America (BA.C)       BYMA 30/09/2025
    "V":      18.0,   # Visa Inc                     BYMA 30/09/2025 (era 2)
    "MA":     33.0,   # Mastercard Inc.              BYMA 30/09/2025 (era 2)
    "BRKB":   22.0,   # Berkshire Hathaway B (BRK-B) BYMA 30/09/2025 (era 3)
    # ── Energía ────────────────────────────────────────────────────────────
    "XOM":    10.0,   # Exxon Mobil Corporation      BYMA 30/09/2025 (era 2/5)
    # ── China & Asia ───────────────────────────────────────────────────────
    "BABA":    9.0,   # Alibaba Group                BYMA 30/09/2025 (era 16)
    # ── LatAm ──────────────────────────────────────────────────────────────
    "MELI":  120.0,   # MercadoLibre Inc.            BYMA 30/09/2025 (era 60)
    # ── ETFs ───────────────────────────────────────────────────────────────
    "QQQ":    20.0,   # INVESCO QQQ TRUST            BYMA 30/09/2025
    "SPY":    20.0,   # SPDR S&P 500                 BYMA 30/09/2025
}

# Mapa de excepciones: ticker de mercado local (BYMA/CNV) → ticker yfinance,
# cuando difieren. Cubre dos casos:
#   1) Subyacente de un CEDEAR (ej: BRKB en BYMA → BRK-B en yfinance/NYSE).
#   2) Acción/ADR argentino cuyo ticker local no coincide con el que usa
#      yfinance (ej: Pampa Energía cotiza en BYMA como "PAMP" pero su ADR
#      en NYSE, que es lo que yfinance conoce, es "PAM").
# Se aplica en fetch() ANTES de pedir los fundamentales (ver bug jul-2026:
# antes solo se usaba para el precio del subyacente en _enrich_cedear,
# dejando pasar el ticker sin mapear a la llamada de fundamentales, que
# fallaba con "No data found, symbol may be delisted").
CEDEAR_YFINANCE_MAP: dict[str, str] = {
    "BRKB": "BRK-B",   # Berkshire Hathaway B: BYMA → "BRKB", yfinance → "BRK-B"
    "PAMP":  "PAM",    # Pampa Energía: BYMA → "PAMP", ADR NYSE/yfinance → "PAM"
}

# Tipo de cambio USD/ARS de fallback si el CCL no puede calcularse.
# ⚠️ Actualizar manualmente si el módulo corre en modo offline.
CCL_FALLBACK_ARS = 1_200.0

# Inflación USA (CPI) por defecto — actualizar con fuente FRED o API de inflación.
INFLACION_US_DEFAULT = 0.031  # ~3.1% 2025

# URL EODHD para datos fundamentales
EODHD_BASE_URL  = "https://eodhd.com/api"
EODHD_TIMEOUT   = 15  # segundos

# Periodos históricos para indicadores técnicos
HIST_PERIOD     = "1y"
RSI_PERIOD      = 14
SMA_LONG        = 200
SMA_SHORT       = 50
VOL_AVG_DAYS    = 20


# ─────────────────────────────────────────────────────────────────────────────
# 2. DATACLASSES DE CONFIGURACIÓN
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class FetchConfig:
    """Parámetros globales del DataFetcher."""
    source:          DataSource = DataSource.YFINANCE
    eodhd_api_key:   Optional[str] = None
    retry_attempts:  int   = 3
    retry_delay_sec: float = 1.5
    # Si True, calcula health_score sintético cuando no hay fuente directa
    calc_synthetic_health: bool = True
    # Inflación USA anualizada (decimal). None → intenta fetch de FRED.
    inflacion_us_override: Optional[float] = None


# ─────────────────────────────────────────────────────────────────────────────
# 3. FUNCIONES AUXILIARES DE CÁLCULO TÉCNICO
# ─────────────────────────────────────────────────────────────────────────────

def _safe_get(d: dict, *keys, default=None):
    """
    Navega un dict anidado de forma segura.
    Retorna default si alguna clave falta o el valor es NaN/None.
    """
    val = d
    for k in keys:
        if not isinstance(val, dict):
            return default
        val = val.get(k, default)
        if val is None:
            return default
    if isinstance(val, float) and (val != val):  # NaN check
        return default
    return val


def _safe_float(value, default=None) -> Optional[float]:
    """Convierte a float con manejo de NaN, None e infinitos."""
    try:
        f = float(value)
        if f != f or abs(f) == float("inf"):  # NaN o inf
            return default
        return f
    except (TypeError, ValueError):
        return default


def calc_rsi(prices: pd.Series, period: int = RSI_PERIOD) -> Optional[float]:
    """
    RSI de Wilder (suavizado exponencial).
    Devuelve None si no hay suficientes datos.
    """
    if prices is None or len(prices) < period + 1:
        return None
    try:
        delta = prices.diff().dropna()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        # Wilder's smoothing (EMA con alpha = 1/period)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
        if avg_loss == 0:
            return 100.0
        rs  = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
        return round(float(rsi), 2)
    except Exception as exc:
        log.warning("RSI calculation failed: %s", exc)
        return None


def calc_technicals(hist: pd.DataFrame) -> dict:
    """
    Calcula todos los indicadores técnicos Murphy a partir del histórico de precios.

    Returns:
        dict con claves: rsi, precio_vs_sma200, precio_vs_sma50, volumen_relativo
    """
    result = {
        "rsi":              None,
        "precio_vs_sma200": None,
        "precio_vs_sma50":  None,
        "volumen_relativo": None,
    }

    if hist is None or hist.empty:
        log.warning("Histórico de precios vacío — indicadores técnicos no disponibles.")
        return result

    try:
        close   = hist["Close"].dropna()
        volume  = hist["Volume"].dropna()

        # RSI
        result["rsi"] = calc_rsi(close)

        # SMA 200
        if len(close) >= SMA_LONG:
            sma200 = close.rolling(SMA_LONG).mean().iloc[-1]
            precio = close.iloc[-1]
            result["precio_vs_sma200"] = round((precio / sma200) - 1, 4)

        # SMA 50
        if len(close) >= SMA_SHORT:
            sma50  = close.rolling(SMA_SHORT).mean().iloc[-1]
            precio = close.iloc[-1]
            result["precio_vs_sma50"] = round((precio / sma50) - 1, 4)

        # Volumen relativo (ratio vs promedio de 20 días)
        if len(volume) >= VOL_AVG_DAYS + 1:
            vol_actual  = volume.iloc[-1]
            vol_prom    = volume.iloc[-(VOL_AVG_DAYS + 1):-1].mean()
            if vol_prom > 0:
                result["volumen_relativo"] = round(float(vol_actual / vol_prom), 2)

    except Exception as exc:
        log.warning("Error calculando indicadores técnicos: %s", exc)

    return result


def calc_interest_coverage(financials: pd.DataFrame) -> Optional[float]:
    """
    Calcula EBIT / Gasto por intereses a partir del Income Statement de yfinance.
    Retorna None si los datos no están disponibles.
    """
    if financials is None or financials.empty:
        return None
    try:
        # yfinance devuelve columnas = fechas, filas = líneas del P&L
        # Intentamos la columna más reciente (primera)
        col = financials.columns[0]
        rows = financials.index.str.lower()

        # Buscar EBIT (Operating Income o EBIT)
        ebit = None
        for label in ["ebit", "operating income", "ebitda"]:
            matches = [i for i, r in enumerate(rows) if label in r]
            if matches:
                ebit = _safe_float(financials.iloc[matches[0]][col])
                break

        # Buscar Interest Expense
        interest = None
        for label in ["interest expense", "interest and debt expense"]:
            matches = [i for i, r in enumerate(rows) if label in r]
            if matches:
                interest = _safe_float(financials.iloc[matches[0]][col])
                break

        if ebit is not None and interest is not None and interest != 0:
            # Interest Expense suele ser negativo en yfinance; usamos valor absoluto
            return round(abs(ebit) / abs(interest), 2)
    except Exception as exc:
        log.warning("Error calculando interest_coverage: %s", exc)
    return None


def calc_roic(info: dict, balance: pd.DataFrame, financials: pd.DataFrame) -> Optional[float]:
    """
    ROIC = NOPAT / Invested Capital
    NOPAT = EBIT × (1 − Tasa efectiva de impuestos)
    Invested Capital = Total Equity + Total Debt − Cash

    Retorna como porcentaje (ej: 25.0 = 25%).
    """
    try:
        tax_rate = _safe_float(info.get("effectiveTaxRate"), default=0.21)

        col_b = balance.columns[0]
        col_f = financials.columns[0]

        rows_b = balance.index.str.lower()
        rows_f = financials.index.str.lower()

        # EBIT
        ebit = None
        for label in ["ebit", "operating income"]:
            matches = [i for i, r in enumerate(rows_f) if label in r]
            if matches:
                ebit = _safe_float(financials.iloc[matches[0]][col_f])
                break

        # Total Stockholders Equity
        equity = None
        for label in ["total stockholder equity", "stockholders equity", "total equity"]:
            matches = [i for i, r in enumerate(rows_b) if label in r]
            if matches:
                equity = _safe_float(balance.iloc[matches[0]][col_b])
                break

        # Total Debt
        debt = None
        for label in ["total debt", "long term debt"]:
            matches = [i for i, r in enumerate(rows_b) if label in r]
            if matches:
                debt = _safe_float(balance.iloc[matches[0]][col_b])
                break

        # Cash
        cash = None
        for label in ["cash", "cash and cash equivalents"]:
            matches = [i for i, r in enumerate(rows_b) if label in r]
            if matches:
                cash = _safe_float(balance.iloc[matches[0]][col_b])
                break

        if ebit is None:
            return None

        nopat = ebit * (1 - tax_rate)
        invested_capital = (equity or 0) + (debt or 0) - (cash or 0)

        if invested_capital <= 0:
            return None

        return round((nopat / invested_capital) * 100, 2)

    except Exception as exc:
        log.warning("Error calculando ROIC: %s", exc)
        return None


def calc_roc_magic_formula(info: dict, balance: pd.DataFrame, financials: pd.DataFrame) -> Optional[float]:
    """
    ROC según la Fórmula Mágica de Greenblatt:
        ROC = EBIT / (Net Working Capital + Net Fixed Assets)

    Retorna como porcentaje.
    """
    try:
        col_b = balance.columns[0]
        col_f = financials.columns[0]
        rows_b = balance.index.str.lower()
        rows_f = financials.index.str.lower()

        # EBIT
        ebit = None
        for label in ["ebit", "operating income"]:
            m = [i for i, r in enumerate(rows_f) if label in r]
            if m:
                ebit = _safe_float(financials.iloc[m[0]][col_f])
                break

        # Current Assets y Current Liabilities → NWC
        current_assets = None
        for label in ["total current assets", "current assets"]:
            m = [i for i, r in enumerate(rows_b) if label in r]
            if m:
                current_assets = _safe_float(balance.iloc[m[0]][col_b])
                break

        current_liab = None
        for label in ["total current liabilities", "current liabilities"]:
            m = [i for i, r in enumerate(rows_b) if label in r]
            if m:
                current_liab = _safe_float(balance.iloc[m[0]][col_b])
                break

        # Net PP&E
        ppe = None
        for label in ["net ppe", "property plant equipment", "net property"]:
            m = [i for i, r in enumerate(rows_b) if label in r]
            if m:
                ppe = _safe_float(balance.iloc[m[0]][col_b])
                break

        if ebit is None:
            return None

        nwc = (current_assets or 0) - (current_liab or 0)
        denominator = nwc + (ppe or 0)
        if denominator <= 0:
            return None

        return round((ebit / denominator) * 100, 2)

    except Exception as exc:
        log.warning("Error calculando ROC Fórmula Mágica: %s", exc)
        return None


def calc_synthetic_health_score(
    current_ratio:     Optional[float],
    debt_equity:       Optional[float],
    interest_coverage: Optional[float],
    roe:               Optional[float],
    roic:              Optional[float],
) -> Optional[float]:
    """
    Health Score sintético (escala 1–5) cuando no hay fuente InvestingPro directa.

    Pondera cinco pilares de salud financiera:
        - Liquidez     (current_ratio)
        - Apalancamiento (debt_equity)
        - Solvencia    (interest_coverage)
        - Rentabilidad (ROE)
        - Eficiencia   (ROIC)

    Retorna None si hay < 2 métricas disponibles.
    """
    signals = []

    if current_ratio is not None:
        # 0–1: CR ≥ 3 = 1.0, CR < 1 = 0.0
        signals.append(min(max((current_ratio - 1) / 2, 0), 1))

    if debt_equity is not None:
        # 0–1: D/E ≤ 0 = 1.0, D/E ≥ 2 = 0.0
        signals.append(min(max(1 - (debt_equity / 2), 0), 1))

    if interest_coverage is not None:
        # 0–1: IC ≥ 10 = 1.0, IC ≤ 1 = 0.0
        signals.append(min(max((interest_coverage - 1) / 9, 0), 1))

    if roe is not None:
        # 0–1: ROE ≥ 30% = 1.0, ROE ≤ 0% = 0.0
        signals.append(min(max(roe / 30, 0), 1))

    if roic is not None:
        # 0–1: ROIC ≥ 25% = 1.0, ROIC ≤ 0% = 0.0
        signals.append(min(max(roic / 25, 0), 1))

    if len(signals) < 2:
        return None

    avg_normalized = sum(signals) / len(signals)
    # Normaliza de [0,1] → [1,5]
    score = 1.0 + avg_normalized * 4.0
    return round(score, 2)


def fetch_us_inflation_cpi(override: Optional[float] = None,
                           http_get=None, timeout: float = 8.0,
                           attempts: int = 2) -> float:
    """
    Inflación USA anualizada (CPI YoY, decimal). Delega en fred_cpi_patch.py
    (parsing endurecido: sesión Fable 5 jul-2026, aplicado ago-2026 — había
    quedado pendiente en el resumen ejecutivo de esa sesión). Firma compatible
    con la versión original; el override sigue funcionando igual.
    """
    from fred_cpi_patch import fetch_us_inflation_cpi_v2
    return fetch_us_inflation_cpi_v2(
        override=override, http_get=http_get or requests.get,
        timeout=timeout, attempts=attempts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 4. FETCHERS POR FUENTE
# ─────────────────────────────────────────────────────────────────────────────

class TickerSinDatosError(Exception):
    """
    Se lanza cuando la fuente de datos (yfinance/EODHD) no devuelve NADA
    utilizable para un ticker tras agotar los reintentos — típicamente
    porque el símbolo está mal escrito, deslistado, o no soportado por
    la fuente. Antes de ago-2026 este caso se tragaba silenciosamente y
    devolvía un MetricasFinancieras vacío, que el ScoringEngine terminaba
    convirtiendo en un score 0.0/AVOID FALSO — indistinguible de un activo
    real y objetivamente malo (ver bug BRKB/PAMP, causa distinta, mismo
    síntoma). Ahora se propaga como excepción explícita para que cada
    consumidor (runner batch, API self-service) decida cómo comunicarlo,
    en vez de mentir con un número.
    """
    def __init__(self, ticker: str, fuente: str, detalle: str = ""):
        self.ticker = ticker
        self.fuente = fuente
        self.detalle = detalle
        msg = f"Sin datos para '{ticker}' en {fuente}."
        if detalle:
            msg += f" ({detalle})"
        super().__init__(msg)


def _fetch_yfinance_raw(ticker: str, attempts: int = 3, delay: float = 1.5) -> dict:
    """
    Descarga datos de Yahoo Finance con reintentos.

    Returns:
        dict con claves: info, history, financials, balance_sheet, cashflow
    """
    for attempt in range(1, attempts + 1):
        try:
            log.info("[yFinance] Descargando '%s' (intento %d/%d)...", ticker, attempt, attempts)
            t = yf.Ticker(ticker)

            info          = t.info or {}
            hist          = t.history(period=HIST_PERIOD, auto_adjust=True)
            financials    = t.financials          # Income Statement anual
            balance_sheet = t.balance_sheet
            cashflow      = t.cashflow

            if not info and hist.empty:
                raise ValueError(f"Yahoo Finance no retornó datos para '{ticker}'.")

            log.info("[yFinance] '%s' descargado (%d días de historial).", ticker, len(hist))
            return {
                "info":          info,
                "history":       hist,
                "financials":    financials,
                "balance_sheet": balance_sheet,
                "cashflow":      cashflow,
            }

        except Exception as exc:
            log.warning("[yFinance] Error en intento %d para '%s': %s", attempt, ticker, exc)
            if attempt < attempts:
                time.sleep(delay * attempt)

    log.error("[yFinance] No se pudo obtener datos para '%s' tras %d intentos.", ticker, attempts)
    raise TickerSinDatosError(
        ticker=ticker, fuente="yfinance",
        detalle=f"{attempts} intentos agotados, sin info ni historial de precios",
    )


def _fetch_eodhd_raw(ticker: str, api_key: str, attempts: int = 3, delay: float = 1.5) -> dict:
    """
    Descarga datos fundamentales y de precios desde EODHD.

    El ticker debe estar en formato 'AAPL.US' (sin sufijo .BA para CEDEARs;
    para el subyacente se llama directamente con el ticker base).
    """
    if not api_key:
        raise ValueError("EODHD requiere una api_key. Configúrala en FetchConfig.eodhd_api_key.")

    # EODHD usa el formato TICKER.EXCHANGE (ej: AAPL.US, MSFT.US)
    if "." not in ticker:
        ticker_eodhd = f"{ticker}.US"
    else:
        ticker_eodhd = ticker

    fundamentals = {}
    eod_prices   = []

    for attempt in range(1, attempts + 1):
        try:
            log.info("[EODHD] Descargando fundamentales '%s' (intento %d)...", ticker_eodhd, attempt)

            # Fundamentales
            url_fund = f"{EODHD_BASE_URL}/fundamentals/{ticker_eodhd}"
            resp = requests.get(
                url_fund,
                params={"api_token": api_key, "fmt": "json"},
                timeout=EODHD_TIMEOUT,
            )
            resp.raise_for_status()
            fundamentals = resp.json()

            # Precios históricos (1 año)
            url_prices = f"{EODHD_BASE_URL}/eod/{ticker_eodhd}"
            resp_p = requests.get(
                url_prices,
                params={"api_token": api_key, "fmt": "json",
                        "period": "d", "from": _one_year_ago()},
                timeout=EODHD_TIMEOUT,
            )
            resp_p.raise_for_status()
            eod_prices = resp_p.json()

            log.info("[EODHD] '%s' descargado (%d días).", ticker_eodhd, len(eod_prices))
            break

        except Exception as exc:
            log.warning("[EODHD] Error intento %d para '%s': %s", attempt, ticker_eodhd, exc)
            if attempt < attempts:
                time.sleep(delay * attempt)

    if not fundamentals and not eod_prices:
        raise TickerSinDatosError(
            ticker=ticker_eodhd, fuente="EODHD",
            detalle=f"{attempts} intentos agotados, sin fundamentales ni precios",
        )

    return {"fundamentals": fundamentals, "eod_prices": eod_prices}


def _one_year_ago() -> str:
    from datetime import date, timedelta
    return (date.today() - timedelta(days=365)).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 5. MAPEADORES AL MODELO MetricasFinancieras
# ─────────────────────────────────────────────────────────────────────────────

def _map_yfinance_to_metricas(raw: dict, ticker: str, config: FetchConfig) -> MetricasFinancieras:
    """
    Mapea el payload crudo de yfinance al modelo MetricasFinancieras.
    Cada campo se extrae de forma segura — None si no está disponible.
    """
    info     = raw.get("info", {})
    hist     = raw.get("history", pd.DataFrame())
    fins     = raw.get("financials", pd.DataFrame())
    bal      = raw.get("balance_sheet", pd.DataFrame())
    cf       = raw.get("cashflow", pd.DataFrame())

    # ── Indicadores Técnicos Murphy ───────────────────────────────────────
    tech = calc_technicals(hist)

    # ── Cálculos derivados ────────────────────────────────────────────────
    interest_cov = calc_interest_coverage(fins)
    roic_val     = calc_roic(info, bal, fins) if (not bal.empty and not fins.empty) else None
    roc_val      = calc_roc_magic_formula(info, bal, fins) if (not bal.empty and not fins.empty) else None

    # ── ROE: yfinance lo entrega como decimal (0.32 = 32%) ───────────────
    roe_raw = _safe_float(info.get("returnOnEquity"))
    roe_val = round(roe_raw * 100, 2) if roe_raw is not None else None

    # ── Deuda/Equity: yfinance lo da como % (ej: 150 → 1.50 D/E) ────────
    de_raw  = _safe_float(info.get("debtToEquity"))
    de_val  = round(de_raw / 100, 4) if de_raw is not None else None

    # ── P/B Ratio ────────────────────────────────────────────────────────
    pb_raw  = _safe_float(info.get("priceToBook"))

    # ── Margen de seguridad: (Precio Objetivo - Precio) / Precio Objetivo ─
    target  = _safe_float(info.get("targetMeanPrice"))
    precio  = _safe_float(info.get("currentPrice") or info.get("regularMarketPrice"))
    margen_seg = None
    if target and precio and target > precio:
        margen_seg = round((target - precio) / target, 4)

    # ── Upside vs Fair Value (%) ──────────────────────────────────────────
    upside = None
    if target and precio and precio > 0:
        upside = round(((target - precio) / precio) * 100, 2)

    # ── Flujo de Caja Score: FCF Yield normalizado a 0–100 ───────────────
    fcf_score = None
    try:
        if not cf.empty:
            col_cf   = cf.columns[0]
            rows_cf  = cf.index.str.lower()
            # Buscamos Free Cash Flow o Operating Cash Flow - Capex
            ocf = None
            for lbl in ["operating cash flow", "total cash from operating activities"]:
                m = [i for i, r in enumerate(rows_cf) if lbl in r]
                if m:
                    ocf = _safe_float(cf.iloc[m[0]][col_cf])
                    break
            cap = None
            for lbl in ["capital expenditures", "capital expenditure"]:
                m = [i for i, r in enumerate(rows_cf) if lbl in r]
                if m:
                    cap = _safe_float(cf.iloc[m[0]][col_cf])
                    break
            mktcap = _safe_float(info.get("marketCap"))
            if ocf and mktcap and mktcap > 0:
                fcf = ocf - abs(cap or 0)
                fcf_yield = (fcf / mktcap) * 100
                # Escala: 0% yield → 0pts; ≥ 8% yield → 100pts
                fcf_score = round(min(max(fcf_yield / 8 * 100, 0), 100), 1)
    except Exception as exc:
        log.warning("Error calculando FCF Score: %s", exc)

    # ── Crecimiento BPA YoY ───────────────────────────────────────────────
    crec_bpa = None
    try:
        eps_cur  = _safe_float(info.get("trailingEps"))
        eps_fwd  = _safe_float(info.get("forwardEps"))
        if eps_cur and eps_fwd and eps_cur != 0:
            crec_bpa = round(((eps_fwd - eps_cur) / abs(eps_cur)) * 100, 2)
    except Exception:
        pass

    # ── Health Score sintético ────────────────────────────────────────────
    current_r = _safe_float(info.get("currentRatio"))
    health_s  = None
    if config.calc_synthetic_health:
        health_s = calc_synthetic_health_score(
            current_ratio=current_r,
            debt_equity=de_val,
            interest_coverage=interest_cov,
            roe=roe_val,
            roic=roic_val,
        )
        if health_s:
            log.info("Health Score sintético calculado: %.2f/5", health_s)

    # ── Ensamblar MetricasFinancieras ─────────────────────────────────────
    return MetricasFinancieras(
        # Graham
        pe_ratio          = _safe_float(info.get("trailingPE") or info.get("forwardPE")),
        pb_ratio          = pb_raw,
        ev_ebitda         = _safe_float(info.get("enterpriseToEbitda")),
        current_ratio     = current_r,
        debt_equity       = de_val,
        interest_coverage = interest_cov,
        margen_seguridad  = margen_seg,
        # InvestingPro
        health_score      = health_s,
        roic              = roic_val,
        roe               = roe_val,
        flujo_caja_score  = fcf_score,
        upside_fair_value = upside,
        # Murphy (técnico)
        rsi               = tech["rsi"],
        precio_vs_sma200  = tech["precio_vs_sma200"],
        precio_vs_sma50   = tech["precio_vs_sma50"],
        volumen_relativo  = tech["volumen_relativo"],
        # Lynch
        peg_ratio         = _safe_float(info.get("pegRatio")),
        crecimiento_bpa   = crec_bpa,
        roc               = roc_val,
        # CEDEAR (se enriquece en etapa posterior)
        es_cedear         = False,
    )


def _map_eodhd_to_metricas(raw: dict, ticker: str, config: FetchConfig) -> MetricasFinancieras:
    """
    Mapea el payload crudo de EODHD al modelo MetricasFinancieras.
    Estructura EODHD: {'Highlights': {...}, 'Valuation': {...}, 'Technicals': {...}, ...}
    """
    fd  = raw.get("fundamentals", {})
    hl  = fd.get("Highlights", {})
    val = fd.get("Valuation", {})
    tch = fd.get("Technicals", {})
    bi  = fd.get("BalanceSheet", {}).get("yearly", {})

    # ── Precio histórico → DataFrame para técnicos ────────────────────────
    prices_raw = raw.get("eod_prices", [])
    hist = pd.DataFrame()
    if prices_raw:
        hist = pd.DataFrame(prices_raw)
        hist["date"]   = pd.to_datetime(hist["date"])
        hist           = hist.set_index("date").sort_index()
        hist.rename(columns={"close": "Close", "volume": "Volume",
                              "open": "Open", "high": "High", "low": "Low"}, inplace=True)

    tech = calc_technicals(hist)

    # ── Métricas fundamentales EODHD ─────────────────────────────────────
    pe_ratio  = _safe_float(_safe_get(hl, "PERatio"))
    pb_ratio  = _safe_float(_safe_get(val, "PriceBookMRQ"))
    ev_ebitda = _safe_float(_safe_get(val, "EnterpriseValueEbitda"))
    roic      = _safe_float(_safe_get(hl, "ReturnOnInvestmentTTM"))
    roe_raw   = _safe_float(_safe_get(hl, "ReturnOnEquityTTM"))
    roe       = round(roe_raw * 100, 2) if roe_raw is not None else None
    peg       = _safe_float(_safe_get(val, "PEGRatio"))

    # EPS growth
    eps_cur   = _safe_float(_safe_get(hl, "EpsEstimateCurrentYear"))
    eps_next  = _safe_float(_safe_get(hl, "EpsEstimateNextYear"))
    crec_bpa  = None
    if eps_cur and eps_next and eps_cur != 0:
        crec_bpa = round(((eps_next - eps_cur) / abs(eps_cur)) * 100, 2)

    # Margen de seguridad
    target_p  = _safe_float(_safe_get(hl, "WallStreetTargetPrice"))
    precio_a  = _safe_float(_safe_get(hl, "MarketCapitalizationMln"))  # fallback
    precio    = _safe_float(hist["Close"].iloc[-1]) if not hist.empty else None
    margen_seg = None
    upside     = None
    if target_p and precio and precio > 0:
        upside     = round(((target_p - precio) / precio) * 100, 2)
        if target_p > precio:
            margen_seg = round((target_p - precio) / target_p, 4)

    # Current ratio y D/E desde Balance Sheet EODHD
    current_r = None
    de_val    = None
    if bi:
        latest_key = sorted(bi.keys(), reverse=True)[0] if bi else None
        if latest_key:
            period   = bi[latest_key]
            cur_a    = _safe_float(period.get("totalCurrentAssets"))
            cur_l    = _safe_float(period.get("totalCurrentLiabilities"))
            t_equity = _safe_float(period.get("totalStockholderEquity"))
            t_debt   = _safe_float(period.get("longTermDebt"))
            if cur_a and cur_l and cur_l > 0:
                current_r = round(cur_a / cur_l, 2)
            if t_debt and t_equity and t_equity > 0:
                de_val = round(t_debt / t_equity, 4)

    # Health score sintético
    health_s = None
    if config.calc_synthetic_health:
        health_s = calc_synthetic_health_score(
            current_ratio=current_r,
            debt_equity=de_val,
            interest_coverage=None,  # EODHD no lo expone directamente
            roe=roe,
            roic=roic,
        )

    return MetricasFinancieras(
        pe_ratio          = pe_ratio,
        pb_ratio          = pb_ratio,
        ev_ebitda         = ev_ebitda,
        current_ratio     = current_r,
        debt_equity       = de_val,
        interest_coverage = None,       # No disponible en EODHD básico
        margen_seguridad  = margen_seg,
        health_score      = health_s,
        roic              = roic,
        roe               = roe,
        flujo_caja_score  = None,       # Requiere endpoint premium EODHD
        upside_fair_value = upside,
        rsi               = tech["rsi"],
        precio_vs_sma200  = tech["precio_vs_sma200"],
        precio_vs_sma50   = tech["precio_vs_sma50"],
        volumen_relativo  = tech["volumen_relativo"],
        peg_ratio         = peg,
        crecimiento_bpa   = crec_bpa,
        roc               = None,       # Requiere balance completo
        es_cedear         = False,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 6. LÓGICA CEDEAR — CCL IMPLÍCITO Y VRU
# ─────────────────────────────────────────────────────────────────────────────

def _enrich_cedear(
    ticker_ba:  str,
    metricas:   MetricasFinancieras,
    inflacion:  float,
    config:     FetchConfig,
) -> MetricasFinancieras:
    """
    Enriquece MetricasFinancieras con los datos CEDEAR necesarios para el VRU.

    Pasos:
    1. Obtiene precio ARS del CEDEAR en BYMA (ticker.BA en yfinance)
    2. Obtiene precio USD del subyacente en NYSE/NASDAQ
    3. Busca ratio_cedear en la tabla de conversión (o usa 1.0 con warning)
    4. Calcula CCL implícito = precio_ars / (precio_usd × ratio_cedear)
    5. Valida que el objeto final pase el @model_validator del ScoringEngine

    Args:
        ticker_ba:  Ticker con sufijo .BA (ej: AAPL.BA, MSFT.BA)
        metricas:   Objeto ya mapeado con datos fundamentales del subyacente
        inflacion:  Inflación USA a pasar al campo inflacion_us

    Returns:
        Nueva instancia de MetricasFinancieras con es_cedear=True y campos CEDEAR completos.
    """
    # Ticker base (sin .BA): ej. "AAPL.BA" → "AAPL"
    base_ticker = ticker_ba.upper().replace(".BA", "")
    log.info("[CEDEAR] Procesando %s (subyacente: %s)", ticker_ba, base_ticker)

    # ── 1. Precio ARS del CEDEAR en BYMA ─────────────────────────────────
    precio_ars = None
    try:
        cedear_data = yf.Ticker(ticker_ba.upper())
        hist_ars = cedear_data.history(period="2d", auto_adjust=True)
        if not hist_ars.empty:
            precio_ars = float(hist_ars["Close"].iloc[-1])
            log.info("[CEDEAR] Precio ARS de %s: $%.2f", ticker_ba, precio_ars)
        else:
            log.warning("[CEDEAR] No se obtuvo historial ARS para %s.", ticker_ba)
    except Exception as exc:
        log.error("[CEDEAR] Error obteniendo precio ARS: %s", exc)

    # ── 2. Precio USD del subyacente ─────────────────────────────────────
    precio_usd = None
    try:
        # Algunos CEDEARs usan un código BYMA distinto al ticker de yfinance
        # (ej: BRKB en BYMA → BRK-B en yfinance). CEDEAR_YFINANCE_MAP resuelve
        # estos casos; si no hay excepción, se usa el base_ticker directamente.
        yf_ticker = CEDEAR_YFINANCE_MAP.get(base_ticker, base_ticker)
        sub_data = yf.Ticker(yf_ticker)
        hist_usd = sub_data.history(period="2d", auto_adjust=True)
        if not hist_usd.empty:
            precio_usd = float(hist_usd["Close"].iloc[-1])
            log.info("[CEDEAR] Precio USD de %s (%s): $%.4f", base_ticker, yf_ticker, precio_usd)
        else:
            log.warning("[CEDEAR] Sin historial USD para %s.", base_ticker)
    except Exception as exc:
        log.error("[CEDEAR] Error obteniendo precio USD subyacente: %s", exc)

    # ── 3. Ratio de conversión CEDEAR ────────────────────────────────────
    ratio = CEDEAR_RATIOS.get(base_ticker)
    if ratio is None:
        log.warning(
            "[CEDEAR] Ratio de conversión para '%s' no encontrado en tabla. "
            "Usando 1.0 por defecto — VERIFICAR manualmente en BYMA/CNV.",
            base_ticker,
        )
        ratio = 1.0

    log.info("[CEDEAR] Ratio %s → %s: %.1f", ticker_ba, base_ticker, ratio)

    # ── 4. CCL implícito ──────────────────────────────────────────────────
    ccl_implied = None
    if precio_ars and precio_usd and precio_usd > 0 and ratio > 0:
        # Fórmula: CCL = P_ARS / (P_USD × Ratio)
        ccl_implied = round(precio_ars / (precio_usd * ratio), 2)
        log.info("[CEDEAR] CCL Implícito calculado: $%.2f ARS/USD", ccl_implied)
    else:
        ccl_implied = CCL_FALLBACK_ARS
        log.warning(
            "[CEDEAR] No se pudo calcular CCL. Usando fallback: $%.0f. "
            "Actualiza CCL_FALLBACK_ARS en DataFetcher.py.",
            CCL_FALLBACK_ARS,
        )

    # ── 5. Reconstruir MetricasFinancieras con campos CEDEAR ─────────────
    data = metricas.model_dump()
    data.update(
        es_cedear             = True,
        precio_ars            = precio_ars,
        ratio_cedear          = ratio,
        ccl_implied           = ccl_implied,
        inflacion_us          = inflacion,
        precio_subyacente_usd = precio_usd,
    )

    try:
        enriched = MetricasFinancieras(**data)
        log.info("[CEDEAR] MetricasFinancieras CEDEAR construidas correctamente.")
        return enriched
    except Exception as exc:
        log.error("[CEDEAR] Error de validación Pydantic: %s", exc)
        raise


# ─────────────────────────────────────────────────────────────────────────────
# 7. CLASE PRINCIPAL — DataFetcher
# ─────────────────────────────────────────────────────────────────────────────

class DataFetcher:
    """
    Módulo de adquisición de datos para Horizontes SaaS.

    Uso básico (Yahoo Finance):
        fetcher  = DataFetcher()
        metricas = fetcher.fetch("AAPL")          # Acción estándar
        metricas = fetcher.fetch("AAPL.BA")        # CEDEAR → CCL calculado automáticamente

    Con EODHD:
        fetcher  = DataFetcher(FetchConfig(
            source       = DataSource.EODHD,
            eodhd_api_key= "TU_API_KEY",
        ))
        metricas = fetcher.fetch("MSFT")

    El resultado es directamente compatible con ScoringRequest del ScoringEngine:
        request = ScoringRequest(
            ticker   = "AAPL",
            perfil   = PerfilInversor.MODERADO,
            metricas = metricas,
        )
    """

    def __init__(self, config: FetchConfig = None):
        self.config = config or FetchConfig()
        self._inflacion_us = None  # Se carga una vez en el primer fetch

    def _get_inflacion(self) -> float:
        if self._inflacion_us is None:
            self._inflacion_us = fetch_us_inflation_cpi(
                override=self.config.inflacion_us_override
            )
            log.info("Inflación USA cargada: %.2f%%", self._inflacion_us * 100)
        return self._inflacion_us

    # ── Método público principal ──────────────────────────────────────────

    def fetch(self, ticker: str) -> MetricasFinancieras:
        """
        Punto de entrada principal. Detecta automáticamente si el ticker
        es un CEDEAR (sufijo .BA) y enriquece con CCL implícito.

        Args:
            ticker: Símbolo del activo. Ej: "AAPL", "MSFT", "AAPL.BA", "TSLA.BA"

        Returns:
            MetricasFinancieras listo para pasar al ScoringEngine.

        Raises:
            RuntimeError: Si todos los intentos de fetch fallan.
        """
        ticker_clean = ticker.strip().upper()
        is_cedear    = ticker_clean.endswith(".BA")

        # Para CEDEARs, obtenemos los fundamentales del subyacente (sin .BA)
        base_ticker  = ticker_clean.replace(".BA", "") if is_cedear else ticker_clean

        # Alias yfinance (fix jul-2026): resolver ANTES de pedir fundamentales.
        # base_ticker se conserva sin mapear para CEDEAR_RATIOS y logs — solo
        # el símbolo que se le pasa a yfinance cambia.
        yf_ticker = CEDEAR_YFINANCE_MAP.get(base_ticker, base_ticker)
        if yf_ticker != base_ticker:
            log.info("[Alias] '%s' → yfinance '%s'", base_ticker, yf_ticker)

        log.info(
            "═══ Iniciando fetch para '%s' [fuente: %s, CEDEAR: %s] ═══",
            ticker_clean, self.config.source.value, is_cedear,
        )

        # ── Fetch según fuente ────────────────────────────────────────────
        if self.config.source == DataSource.YFINANCE:
            metricas = self._fetch_yfinance(yf_ticker)
        elif self.config.source == DataSource.EODHD:
            metricas = self._fetch_eodhd(yf_ticker)
        else:
            raise ValueError(f"DataSource desconocida: {self.config.source}")

        # ── Enriquecimiento CEDEAR ────────────────────────────────────────
        if is_cedear:
            inflacion = self._get_inflacion()
            metricas  = _enrich_cedear(
                ticker_ba  = ticker_clean,
                metricas   = metricas,
                inflacion  = inflacion,
                config     = self.config,
            )

        log.info("Fetch completado para '%s'.", ticker_clean)
        return metricas

    def fetch_batch(self, tickers: list[str]) -> dict[str, MetricasFinancieras]:
        """
        Fetch en lote con delay entre requests para respetar rate limits.

        Returns:
            dict {ticker: MetricasFinancieras | Exception}
        """
        results = {}
        for i, ticker in enumerate(tickers):
            try:
                results[ticker] = self.fetch(ticker)
            except Exception as exc:
                log.error("Error en fetch de '%s': %s", ticker, exc)
                results[ticker] = exc
            if i < len(tickers) - 1:
                time.sleep(self.config.retry_delay_sec)
        return results

    # ── Métodos privados por fuente ───────────────────────────────────────

    def _fetch_yfinance(self, ticker: str) -> MetricasFinancieras:
        raw = _fetch_yfinance_raw(
            ticker   = ticker,
            attempts = self.config.retry_attempts,
            delay    = self.config.retry_delay_sec,
        )
        return _map_yfinance_to_metricas(raw, ticker, self.config)

    def _fetch_eodhd(self, ticker: str) -> MetricasFinancieras:
        if not self.config.eodhd_api_key:
            raise ValueError(
                "EODHD requiere eodhd_api_key en FetchConfig. "
                "Obtén tu clave en https://eodhd.com"
            )
        raw = _fetch_eodhd_raw(
            ticker   = ticker,
            api_key  = self.config.eodhd_api_key,
            attempts = self.config.retry_attempts,
            delay    = self.config.retry_delay_sec,
        )
        return _map_eodhd_to_metricas(raw, ticker, self.config)


# ─────────────────────────────────────────────────────────────────────────────
# 8. SUPABASE CONFIG + CACHED SCORING PIPELINE
#    Orquesta DataFetcher → ScoringEngine → caché en Supabase (scoring_cache).
#    Agregado ago-2026: la auditoría de julio entregó run_daily_scoring.py,
#    warmup_cache.py y portfolio_analyzer.py asumiendo que esta capa ya
#    existía en producción. No existía. Esto la implementa contra el schema
#    real (horizontes_supabase_schema.sql, tabla scoring_cache, TTL 24h
#    vía columna expira_at, UNIQUE(ticker, perfil_inversor)).
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class SupabaseConfig:
    """Credenciales de Supabase leídas del entorno."""
    url: str
    service_role_key: str

    @classmethod
    def from_env(cls) -> "SupabaseConfig":
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError(
                "Faltan SUPABASE_URL y/o SUPABASE_SERVICE_ROLE_KEY en el entorno. "
                "(Usar la clave legacy JWT 'eyJhbGci...', NO la 'sb_secret_'.)"
            )
        return cls(url=url, service_role_key=key)


class CachedScoringPipeline:
    """
    Punto de entrada canónico para obtener un InvestmentScore con caché.

    Uso típico:
        pipeline = CachedScoringPipeline.from_env()
        score, was_cached = pipeline.get_score("AAPL", PerfilInversor.MODERADO)

    Lee primero `scoring_cache` (Supabase); si hay una fila vigente
    (expira_at > ahora) la reconstruye como InvestmentScore sin llamar a
    yfinance/EODHD. Si no hay caché vigente, corre DataFetcher + ScoringEngine
    y persiste el resultado (upsert por ticker+perfil).
    """

    CACHE_TABLE = "scoring_cache"

    def __init__(
        self,
        fetcher: "DataFetcher" = None,
        engine: ScoringEngine = None,
        supabase_client=None,
        use_cache: bool = True,
    ):
        self.fetcher = fetcher or DataFetcher()
        self.engine = engine or ScoringEngine()
        self.sb = supabase_client
        self.use_cache = use_cache and (supabase_client is not None)

    @classmethod
    def from_env(cls) -> "CachedScoringPipeline":
        """Arma el pipeline canónico leyendo SUPABASE_URL/SERVICE_ROLE_KEY del entorno."""
        from supabase import create_client  # noqa: WPS433 — dependencia opcional/lazy

        cfg = SupabaseConfig.from_env()
        client = create_client(cfg.url, cfg.service_role_key)
        return cls(supabase_client=client)

    # ── Lectura de caché ────────────────────────────────────────────────
    def _leer_cache(self, ticker: str, perfil: PerfilInversor) -> Optional[dict]:
        if not self.use_cache:
            return None
        try:
            resp = (
                self.sb.table(self.CACHE_TABLE)
                .select("*")
                .eq("ticker", ticker)
                .eq("perfil_inversor", perfil.value)
                .gt("expira_at", datetime.now(timezone.utc).isoformat())
                .order("calculado_at", desc=True)
                .limit(1)
                .execute()
            )
            rows = resp.data or []
            return rows[0] if rows else None
        except Exception as exc:  # noqa: BLE001 — el caché nunca debe tumbar el scoring
            log.warning("No se pudo leer scoring_cache para %s/%s: %s", ticker, perfil.value, exc)
            return None

    @staticmethod
    def _row_to_score(row: dict) -> InvestmentScore:
        def bloque(prefix: str) -> ScoreBloque:
            return ScoreBloque(
                score_bruto=float(row.get(f"{prefix}_score_bruto") or 0),
                peso=float(row.get(f"{prefix}_peso") or 0),
                aporte=float(row.get(f"{prefix}_aporte") or 0),
                detalle=row.get(f"{prefix}_detalle") or {},
            )

        vru = None
        if row.get("vru_valor_usd") is not None:
            vru = ValorRealUSD(
                vru=float(row["vru_valor_usd"]),
                precio_ars=0.0,
                ccl_implied=0.0,
                inflacion_us=0.0,
                ratio_cedear=0.0,
                precio_subyacente_usd=None,
                alerta_trampa=bool(row.get("vru_alerta_trampa") or False),
            )

        return InvestmentScore(
            ticker=row["ticker"],
            perfil=PerfilInversor(row["perfil_inversor"]),
            score_final=float(row["score_final"]),
            clasificacion=row["clasificacion"],
            bloques={
                "graham": bloque("graham"),
                "murphy": bloque("murphy"),
                "lynch": bloque("lynch"),
                "investingpro": bloque("investingpro"),
            },
            valor_real_usd=vru,
            narrativa_housel={
                "titulo": row.get("narrativa_titulo") or "",
                "mensaje": row.get("narrativa_mensaje") or "",
            },
            pesos_aplicados=row.get("pesos_aplicados") or {},
            alertas=row.get("alertas") or [],
        )

    # ── Escritura de caché ──────────────────────────────────────────────
    def _escribir_cache(self, score: InvestmentScore) -> None:
        if not self.use_cache:
            return
        b = score.bloques
        payload = {
            "ticker": score.ticker,
            "perfil_inversor": score.perfil.value,
            "score_final": round(score.score_final, 2),
            "clasificacion": score.clasificacion,
            "graham_score_bruto": b["graham"].score_bruto,
            "graham_peso": b["graham"].peso,
            "graham_aporte": b["graham"].aporte,
            "graham_detalle": b["graham"].detalle,
            "murphy_score_bruto": b["murphy"].score_bruto,
            "murphy_peso": b["murphy"].peso,
            "murphy_aporte": b["murphy"].aporte,
            "murphy_detalle": b["murphy"].detalle,
            "lynch_score_bruto": b["lynch"].score_bruto,
            "lynch_peso": b["lynch"].peso,
            "lynch_aporte": b["lynch"].aporte,
            "lynch_detalle": b["lynch"].detalle,
            "investingpro_score_bruto": b["investingpro"].score_bruto,
            "investingpro_peso": b["investingpro"].peso,
            "investingpro_aporte": b["investingpro"].aporte,
            "investingpro_detalle": b["investingpro"].detalle,
            "es_cedear": score.valor_real_usd is not None,
            "vru_valor_usd": score.valor_real_usd.vru if score.valor_real_usd else None,
            "vru_alerta_trampa": (
                score.valor_real_usd.alerta_trampa if score.valor_real_usd else False
            ),
            "alertas": score.alertas,
            "narrativa_titulo": score.narrativa_housel.get("titulo"),
            "narrativa_mensaje": score.narrativa_housel.get("mensaje"),
            "pesos_aplicados": score.pesos_aplicados,
            "calculado_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            (
                self.sb.table(self.CACHE_TABLE)
                .upsert(payload, on_conflict="ticker,perfil_inversor")
                .execute()
            )
        except Exception as exc:  # noqa: BLE001 — fallo de escritura no tumba el scoring
            log.warning(
                "No se pudo escribir scoring_cache para %s/%s: %s",
                score.ticker, score.perfil.value, exc,
            )

    # ── Punto de entrada público ─────────────────────────────────────────
    def get_score(
        self, ticker: str, perfil: PerfilInversor, forzar_recalculo: bool = False
    ) -> tuple[InvestmentScore, bool]:
        """
        Devuelve (InvestmentScore, was_cached: bool).
        Si hay caché vigente (<24h) y `forzar_recalculo` es False, lo usa.
        Si no, corre fetch + scoring y persiste el resultado.
        """
        ticker = ticker.strip().upper()

        if not forzar_recalculo:
            cached_row = self._leer_cache(ticker, perfil)
            if cached_row is not None:
                return self._row_to_score(cached_row), True

        metricas = self.fetcher.fetch(ticker)
        req = ScoringRequest(ticker=ticker, perfil=perfil, metricas=metricas)
        score = self.engine.calcular(req)
        self._escribir_cache(score)
        return score, False

    def get_score_multi(
        self, ticker: str, perfiles: list[PerfilInversor], forzar_recalculo: bool = False
    ) -> dict[str, tuple[InvestmentScore, bool]]:
        """
        Variante de get_score() para pedir varios perfiles del mismo ticker
        en una sola llamada — pensada para el frontend self-service, que
        muestra los 4 horizontes de inversión de una vez en vez de que el
        usuario elija uno antes de analizar.

        Reutiliza un único fetch de métricas crudas (yfinance/EODHD) para
        todos los perfiles que no tengan caché vigente — evita golpear la
        fuente de datos una vez por perfil (4x más lento y 4x más cuota de
        la fuente externa sin necesidad, ya que las métricas del ticker no
        cambian entre perfiles, solo el ponderado del score).

        Devuelve {perfil.value: (InvestmentScore, was_cached)}. Si el
        ticker no tiene datos, TickerSinDatosError se propaga una sola vez
        (no tiene sentido reintentar el fetch por cada perfil pendiente).
        """
        ticker = ticker.strip().upper()
        resultados: dict[str, tuple[InvestmentScore, bool]] = {}
        pendientes: list[PerfilInversor] = []

        if not forzar_recalculo:
            for perfil in perfiles:
                cached_row = self._leer_cache(ticker, perfil)
                if cached_row is not None:
                    resultados[perfil.value] = (self._row_to_score(cached_row), True)
                else:
                    pendientes.append(perfil)
        else:
            pendientes = list(perfiles)

        if pendientes:
            metricas = self.fetcher.fetch(ticker)  # una sola vez para todos los pendientes
            for perfil in pendientes:
                req = ScoringRequest(ticker=ticker, perfil=perfil, metricas=metricas)
                score = self.engine.calcular(req)
                self._escribir_cache(score)
                resultados[perfil.value] = (score, False)

        return resultados


# ─────────────────────────────────────────────────────────────────────────────
# 9. CLI / EJEMPLO DE USO STANDALONE
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json
    import sys

    print("\n" + "═" * 65)
    print("  HORIZONTES — DataFetcher · Prueba de Integración")
    print("═" * 65)

    fetcher = DataFetcher(FetchConfig(
        source                 = DataSource.YFINANCE,
        calc_synthetic_health  = True,
        inflacion_us_override  = None,   # None → intenta fetch de FRED
    ))

    # ── Test 1: Acción estándar ───────────────────────────────────────────
    ticker_test = sys.argv[1] if len(sys.argv) > 1 else "MSFT"
    print(f"\n[TEST 1] Acción estándar: {ticker_test}")
    try:
        m = fetcher.fetch(ticker_test)
        print(f"  P/E Ratio       : {m.pe_ratio}")
        print(f"  P/B Ratio       : {m.pb_ratio}")
        print(f"  EV/EBITDA       : {m.ev_ebitda}")
        print(f"  Current Ratio   : {m.current_ratio}")
        print(f"  Debt/Equity     : {m.debt_equity}")
        print(f"  Interest Cov.   : {m.interest_coverage}")
        print(f"  Health Score    : {m.health_score}")
        print(f"  ROIC            : {m.roic}%")
        print(f"  ROE             : {m.roe}%")
        print(f"  FCF Score       : {m.flujo_caja_score}")
        print(f"  Upside FV       : {m.upside_fair_value}%")
        print(f"  RSI (14d)       : {m.rsi}")
        print(f"  vs SMA200       : {m.precio_vs_sma200}")
        print(f"  vs SMA50        : {m.precio_vs_sma50}")
        print(f"  Vol Relativo    : {m.volumen_relativo}")
        print(f"  PEG Ratio       : {m.peg_ratio}")
        print(f"  Crec. BPA YoY   : {m.crecimiento_bpa}%")
        print(f"  ROC (Greenblatt): {m.roc}%")
        print(f"  Margen Seguridad: {m.margen_seguridad}")
    except Exception as e:
        print(f"  ⚠️  Error: {e}")

    # ── Test 2: CEDEAR con CCL implícito ──────────────────────────────────
    cedear_test = sys.argv[2] if len(sys.argv) > 2 else "AAPL.BA"
    print(f"\n[TEST 2] CEDEAR: {cedear_test}")
    try:
        mc = fetcher.fetch(cedear_test)
        print(f"  es_cedear             : {mc.es_cedear}")
        print(f"  precio_ars            : ${mc.precio_ars:,.2f}" if mc.precio_ars else "  precio_ars            : None")
        print(f"  precio_subyacente_usd : ${mc.precio_subyacente_usd:.4f}" if mc.precio_subyacente_usd else "  precio_subyacente_usd : None")
        print(f"  ratio_cedear          : {mc.ratio_cedear}")
        print(f"  ccl_implied           : ${mc.ccl_implied:,.2f}" if mc.ccl_implied else "  ccl_implied           : None")
        print(f"  inflacion_us          : {mc.inflacion_us * 100:.2f}%" if mc.inflacion_us else "  inflacion_us          : None")

        # Mostrar VRU calculado manualmente para verificación
        if mc.precio_ars and mc.ratio_cedear and mc.ccl_implied and mc.inflacion_us:
            vru = (mc.precio_ars * mc.ratio_cedear) / (mc.ccl_implied * (1 + mc.inflacion_us))
            print(f"\n  → VRU pre-calculado   : USD {vru:.4f}")
            if mc.precio_subyacente_usd:
                delta = (vru - mc.precio_subyacente_usd) / mc.precio_subyacente_usd * 100
                print(f"  → Delta vs Subyacente : {delta:+.2f}%")
                print(f"  → Alerta Trampa       : {'🚨 SÍ' if delta < -5 else '✅ NO'}")

    except Exception as e:
        print(f"  ⚠️  Error: {e}")

    print("\n" + "═" * 65)
    print("  Integración con ScoringEngine:")
    print("  ─────────────────────────────────────────────────────────")
    print("  from DataFetcher import DataFetcher, FetchConfig, DataSource")
    print("  from scoring_engine import ScoringRequest, PerfilInversor, ScoringEngine")
    print()
    print("  fetcher  = DataFetcher()")
    print("  metricas = fetcher.fetch('AAPL.BA')")
    print("  req      = ScoringRequest(ticker='AAPL.BA',")
    print("                 perfil=PerfilInversor.MODERADO, metricas=metricas)")
    print("  score    = ScoringEngine().calcular(req)")
    print("═" * 65 + "\n")
