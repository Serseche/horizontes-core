"""Tests offline: patch FRED (simulado) y runner diario (stub).
Requiere scoring_engine.py importable — en el repo real está en la raíz junto a estos archivos."""
import json
import sys
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fred_cpi_patch import fetch_us_inflation_cpi_v2, INFLACION_US_DEFAULT  # noqa: E402
import run_daily_scoring  # noqa: E402


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _resp(texto: str, status: int = 200):
    return lambda url, timeout: SimpleNamespace(status_code=status, text=texto)


def _csv_fred(n_meses: int = 15, base: float = 300.0, mensual: float = 0.0025,
              con_puntos: bool = False, header: str = "DATE") -> str:
    """Serie CPI sintética con ~3.05% YoY (0.25% mensual compuesto)."""
    hoy = date.today().replace(day=1)
    filas = [f"{header},CPIAUCSL"]
    val = base
    for i in range(n_meses, 0, -1):
        f = (hoy - timedelta(days=30 * i)).replace(day=1)
        if con_puntos and i == 5:
            filas.append(f"{f.isoformat()},.")        # faltante FRED
            continue
        filas.append(f"{f.isoformat()},{val:.3f}")
        val *= (1 + mensual)
    return "\n".join(filas)


# ─── FRED ─────────────────────────────────────────────────────────────────────

def test_fred_yoy_normal():
    yoy = fetch_us_inflation_cpi_v2(http_get=_resp(_csv_fred()))
    assert 0.025 < yoy < 0.035          # ~3% con 0.25% mensual


def test_fred_ignora_valores_punto():
    yoy = fetch_us_inflation_cpi_v2(http_get=_resp(_csv_fred(con_puntos=True)))
    assert 0.02 < yoy < 0.04            # sigue calculando sobre válidas


def test_fred_header_observation_date():
    yoy = fetch_us_inflation_cpi_v2(
        http_get=_resp(_csv_fred(header="observation_date")))
    assert 0.02 < yoy < 0.04


def test_fred_malformado_cae_a_default():
    assert fetch_us_inflation_cpi_v2(
        http_get=_resp("<html>error</html>")) == INFLACION_US_DEFAULT


def test_fred_pocas_observaciones_cae_a_default():
    assert fetch_us_inflation_cpi_v2(
        http_get=_resp(_csv_fred(n_meses=6))) == INFLACION_US_DEFAULT


def test_fred_fuera_de_banda_cae_a_default():
    loco = _csv_fred(mensual=0.05)      # ~80% anual → fuera de sanidad
    assert fetch_us_inflation_cpi_v2(http_get=_resp(loco)) == INFLACION_US_DEFAULT


def test_fred_http_500_cae_a_default():
    assert fetch_us_inflation_cpi_v2(
        http_get=_resp("", status=500), attempts=1) == INFLACION_US_DEFAULT


def test_fred_override_manda():
    assert fetch_us_inflation_cpi_v2(override=0.042) == 0.042


# ─── Runner diario (pipeline stub, sin red ni Supabase) ──────────────────────

class _StubPipeline:
    def __init__(self, fallar: set[str] | None = None):
        self.fallar = fallar or set()

    def get_score(self, ticker, perfil):
        if ticker in self.fallar:
            raise RuntimeError("fallo simulado de yfinance")
        score = SimpleNamespace(score_final=72.5, clasificacion="ACCUMULATE")
        return score, False


def test_runner_todo_ok():
    res = run_daily_scoring.run(["GOOGL.BA", "GGAL"], ["moderado"],
                                pipeline=_StubPipeline(), delay=0)
    assert res["total_ops"] == 2 and res["fallidas"] == 0
    assert res["tasa_exito_pct"] == 100.0
    assert res["resultados"][0]["clasificacion"] == "ACCUMULATE"


def test_runner_un_ticker_no_tumba_el_batch():
    res = run_daily_scoring.run(["GOOGL.BA", "ROTO", "GGAL"], ["moderado"],
                                pipeline=_StubPipeline(fallar={"ROTO"}), delay=0)
    assert res["exitosas"] == 2 and res["fallidas"] == 1
    assert res["errores"][0]["ticker"] == "ROTO"


def test_runner_dry_run_exit_0(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert run_daily_scoring.main(["--dry-run"]) == 0


def test_runner_env_tickers(monkeypatch):
    monkeypatch.setenv("HORIZONTES_TICKERS", "nvda.ba, meli.ba")
    tickers = run_daily_scoring._env_list("HORIZONTES_TICKERS", ["X"])
    assert tickers == ["NVDA.BA", "MELI.BA"]
