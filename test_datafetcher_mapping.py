"""
Tests de DataFetcher._map_yfinance_to_metricas — cubren específicamente el
bug de sept-2026: Yahoo Finance dejó de devolver de forma confiable varios
campos del módulo 'financialData'/'defaultKeyStatistics' vía `Ticker.info`
(currentRatio, debtToEquity, trailingEps/forwardEps, pegRatio — este último
confirmado en yfinance#2569/#2570: "PEG ratio missing... since June 2025").

Sin fallback, esto colapsaba Graham y Lynch a un puñado de valores casi
constantes para CUALQUIER ticker (ver informe de auditoría), porque la
mayoría de sus señales quedaban en None sin que nada lo hiciera visible.

Estos tests simulan exactamente ese escenario — `info` con los campos rotos
ausentes — y verifican que el fallback (calculado desde balance_sheet /
financials, que sí siguen funcionando) rellena los valores igual.
"""
import sys
import pathlib

import pandas as pd
import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from DataFetcher import _map_yfinance_to_metricas, FetchConfig


def _balance_sheet_ok() -> pd.DataFrame:
    return pd.DataFrame(
        {pd.Timestamp("2025-12-31"): [50_000.0, 20_000.0, 80_000.0, 60_000.0]},
        index=["Total Current Assets", "Total Current Liabilities",
               "Total Debt", "Total Stockholder Equity"],
    )


def _financials_ok() -> pd.DataFrame:
    return pd.DataFrame(
        {
            pd.Timestamp("2025-12-31"): [10_000.0, 500.0, 5.75],
            pd.Timestamp("2024-12-31"): [8_000.0, 400.0, 5.00],
        },
        index=["Operating Income", "Interest Expense", "Basic EPS"],
    )


def _info_con_financial_data_roto() -> dict:
    """`info` tal como lo devuelve Yahoo desde 2025: sin currentRatio,
    debtToEquity, trailingEps/forwardEps ni pegRatio (módulos restringidos),
    pero CON precio y demás campos básicos ('price'/'quoteType')."""
    return {
        "currentPrice": 115.0,
        "regularMarketPrice": 115.0,
        # currentRatio, debtToEquity, trailingEps, forwardEps, pegRatio: AUSENTES
    }


def test_current_ratio_usa_fallback_de_balance_cuando_info_no_lo_trae():
    raw = {
        "info": _info_con_financial_data_roto(),
        "history": pd.DataFrame(),
        "financials": _financials_ok(),
        "balance_sheet": _balance_sheet_ok(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "TEST", FetchConfig())
    assert m.current_ratio == 2.5   # 50000 / 20000 — nunca None por falta de info


def test_debt_equity_usa_fallback_de_balance_cuando_info_no_lo_trae():
    raw = {
        "info": _info_con_financial_data_roto(),
        "history": pd.DataFrame(),
        "financials": _financials_ok(),
        "balance_sheet": _balance_sheet_ok(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "TEST", FetchConfig())
    assert m.debt_equity == pytest.approx(1.3333, abs=0.001)   # 80000 / 60000


def test_crecimiento_bpa_usa_fallback_de_financials_cuando_info_no_lo_trae():
    raw = {
        "info": _info_con_financial_data_roto(),
        "history": pd.DataFrame(),
        "financials": _financials_ok(),
        "balance_sheet": _balance_sheet_ok(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "TEST", FetchConfig())
    assert m.crecimiento_bpa == 15.0   # (5.75 - 5.00) / 5.00 * 100


def test_pe_ratio_usa_fallback_precio_sobre_eps_cuando_info_no_lo_trae():
    raw = {
        "info": _info_con_financial_data_roto(),
        "history": pd.DataFrame(),
        "financials": _financials_ok(),
        "balance_sheet": _balance_sheet_ok(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "TEST", FetchConfig())
    assert m.pe_ratio == pytest.approx(115.0 / 5.75, abs=0.01)


def test_peg_ratio_se_reconstruye_con_formula_lynch_cuando_info_no_lo_trae():
    """Confirma el fix del bug documentado en yfinance#2569/#2570 (PEG
    ausente de info desde jun-2025): PEG = P/E ÷ crecimiento BPA%."""
    raw = {
        "info": _info_con_financial_data_roto(),
        "history": pd.DataFrame(),
        "financials": _financials_ok(),
        "balance_sheet": _balance_sheet_ok(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "TEST", FetchConfig())
    pe_esperado = 115.0 / 5.75
    peg_esperado = round(pe_esperado / 15.0, 2)
    assert m.peg_ratio == pytest.approx(peg_esperado, abs=0.01)


def test_sin_balance_ni_financials_todo_sigue_siendo_none_no_crashea():
    """Un ETF (sin income/balance statement) no debe romper el fetch —
    debe devolver None en vez de lanzar una excepción."""
    raw = {
        "info": {"currentPrice": 50.0},
        "history": pd.DataFrame(),
        "financials": pd.DataFrame(),
        "balance_sheet": pd.DataFrame(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "ETF_TEST", FetchConfig())
    assert m.current_ratio is None
    assert m.debt_equity is None
    assert m.crecimiento_bpa is None
    assert m.peg_ratio is None


def test_info_sano_sigue_priorizando_los_campos_de_info_sobre_el_fallback():
    """Si Yahoo alguna vez vuelve a exponer estos campos, deben seguir
    usándose directo de `info` — el fallback es solo un respaldo."""
    info = _info_con_financial_data_roto()
    info.update(currentRatio=3.3, debtToEquity=45.0, pegRatio=1.1)
    raw = {
        "info": info,
        "history": pd.DataFrame(),
        "financials": _financials_ok(),
        "balance_sheet": _balance_sheet_ok(),
        "cashflow": pd.DataFrame(),
    }
    m = _map_yfinance_to_metricas(raw, "TEST", FetchConfig())
    assert m.current_ratio == 3.3      # no el 2.5 del fallback
    assert m.debt_equity == 0.45       # 45/100, no el 1.3333 del fallback
    assert m.peg_ratio == 1.1          # no el reconstruido
