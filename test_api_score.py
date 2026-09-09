"""
Tests de api_main.py — 100% mockeados: sin red real, sin Supabase real,
sin depender de que exista un usuario de verdad. Verifican el contrato
del endpoint self-service, no la infraestructura.
"""
import os
import sys
import pathlib
from datetime import datetime, timezone
from unittest.mock import MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

TEST_JWT_SECRET = "test-secret-para-tests-nunca-usar-en-produccion"
os.environ["SUPABASE_JWT_SECRET"] = TEST_JWT_SECRET

import jwt as pyjwt
import pytest
from fastapi.testclient import TestClient

import api_main
from DataFetcher import TickerSinDatosError
from scoring_engine import (DISCLAIMER_HORIZONTES, InvestmentScore, PerfilInversor,
                            ScoreBloque)

client = TestClient(api_main.app)

USER_ID = "ea6ef068-223a-4e59-b6bc-68d4cfb8cdc0"


def _token(user_id: str = USER_ID, exp_delta: int = 3600) -> str:
    payload = {
        "sub": user_id, "email": "sergio@example.com", "aud": "authenticated",
        "exp": int(datetime.now(timezone.utc).timestamp()) + exp_delta,
    }
    return pyjwt.encode(payload, TEST_JWT_SECRET, algorithm="HS256")


def _score_dummy(ticker="AAPL") -> InvestmentScore:
    bloque = ScoreBloque(score_bruto=70.0, peso=0.25, aporte=17.5, detalle={})
    return InvestmentScore(
        ticker=ticker, perfil=PerfilInversor.MODERADO, score_final=69.2,
        clasificacion="ACCUMULATE",
        bloques={"graham": bloque, "murphy": bloque, "lynch": bloque, "investingpro": bloque},
        narrativa_housel={"titulo": "t", "mensaje": "m"},
        pesos_aplicados={"graham": "25%"}, alertas=[],
    )


class _FakePipeline:
    """Reemplaza CachedScoringPipeline sin tocar Supabase real."""
    def __init__(self, resultado=None, excepcion=None, resultado_multi=None):
        self.resultado = resultado
        self.excepcion = excepcion
        self.resultado_multi = resultado_multi
        self.use_cache = False   # cuota fail-open en estos tests

    def get_score(self, ticker, perfil):
        if self.excepcion:
            raise self.excepcion
        return self.resultado

    def get_score_multi(self, ticker, perfiles):
        if self.excepcion:
            raise self.excepcion
        return self.resultado_multi


# ── Autenticación ────────────────────────────────────────────────────────────

def test_sin_token_devuelve_401():
    r = client.post("/score", json={"ticker": "AAPL"})
    assert r.status_code == 422 or r.status_code == 401  # FastAPI exige el header


def test_token_invalido_devuelve_401():
    r = client.post("/score", json={"ticker": "AAPL"},
                    headers={"Authorization": "Bearer token-basura-invalido"})
    assert r.status_code == 401


def test_token_expirado_devuelve_401():
    tok = _token(exp_delta=-10)
    r = client.post("/score", json={"ticker": "AAPL"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401
    assert "expir" in r.json()["detail"].lower()


# ── Camino feliz ──────────────────────────────────────────────────────────────

def test_score_ok_incluye_disclaimer(monkeypatch):
    monkeypatch.setattr(api_main, "_pipeline",
                        lambda: _FakePipeline(resultado=(_score_dummy(), False)))
    tok = _token()
    r = client.post("/score", json={"ticker": "AAPL", "perfil": "moderado"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["score"]["ticker"] == "AAPL"
    assert body["score"]["disclaimer"] == DISCLAIMER_HORIZONTES
    assert body["cached"] is False


def test_score_default_perfil_moderado(monkeypatch):
    monkeypatch.setattr(api_main, "_pipeline",
                        lambda: _FakePipeline(resultado=(_score_dummy(), True)))
    tok = _token()
    r = client.post("/score", json={"ticker": "GGAL"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["cached"] is True


# ── Estado sin_datos (el fix P1, ahora expuesto vía API) ─────────────────────

def test_ticker_sin_datos_devuelve_200_no_500(monkeypatch):
    monkeypatch.setattr(
        api_main, "_pipeline",
        lambda: _FakePipeline(excepcion=TickerSinDatosError("ZZFANTASMA", "yfinance", "sin datos")),
    )
    tok = _token()
    r = client.post("/score", json={"ticker": "ZZFANTASMA"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200   # nunca un 500 crudo por un ticker inválido
    body = r.json()
    assert body["status"] == "sin_datos"
    assert "ZZFANTASMA" in body["mensaje"]
    assert body["disclaimer"] == DISCLAIMER_HORIZONTES


def test_error_inesperado_devuelve_503_no_detalle_interno(monkeypatch):
    monkeypatch.setattr(
        api_main, "_pipeline",
        lambda: _FakePipeline(excepcion=RuntimeError("boom interno con detalle sensible")),
    )
    tok = _token()
    r = client.post("/score", json={"ticker": "AAPL"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 503
    assert "boom interno" not in r.text  # no filtrar detalles internos al usuario


# ── /score/multi (4 horizontes de una sola vez, 1 sola cuota) ────────────────

def test_score_multi_ok_devuelve_los_4_horizontes(monkeypatch):
    multi = {p.value: (_score_dummy(), False) for p in PerfilInversor}
    monkeypatch.setattr(api_main, "_pipeline",
                        lambda: _FakePipeline(resultado_multi=multi))
    tok = _token()
    r = client.post("/score/multi", json={"ticker": "AAPL"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ticker"] == "AAPL"
    assert set(body["resultados"].keys()) == {p.value for p in PerfilInversor}


def test_score_multi_sin_datos_devuelve_200_no_500(monkeypatch):
    monkeypatch.setattr(
        api_main, "_pipeline",
        lambda: _FakePipeline(excepcion=TickerSinDatosError("ZZFANTASMA", "yfinance", "sin datos")),
    )
    tok = _token()
    r = client.post("/score/multi", json={"ticker": "ZZFANTASMA"},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["status"] == "sin_datos"


def test_score_multi_requiere_auth():
    r = client.post("/score/multi", json={"ticker": "AAPL"})
    assert r.status_code in (401, 422)


# ── Salud ─────────────────────────────────────────────────────────────────────

def test_health_sin_auth():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# ── Import de cartera self-service (P3) ──────────────────────────────────────

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


def test_import_requiere_auth():
    csv_bytes = (FIXTURES / "balanz_ar_tipico.csv").read_bytes()
    r = client.post("/portfolio/import",
                    files={"archivo": ("export.csv", csv_bytes, "text/csv")})
    assert r.status_code in (401, 422)


def test_import_ok_usa_user_id_del_token_no_del_body(monkeypatch):
    """Verifica el punto legal central: el user_id SIEMPRE sale del JWT,
    nunca de algo que el cliente pueda mandar en el request."""
    llamadas = {}

    def fake_subir(ops, user_id):
        llamadas["user_id"] = user_id
        llamadas["n_ops"] = len(ops)
        return len(ops), 0

    monkeypatch.setattr(api_main, "subir_a_supabase", fake_subir)
    tok = _token(user_id=USER_ID)
    csv_bytes = (FIXTURES / "balanz_ar_tipico.csv").read_bytes()

    r = client.post("/portfolio/import",
                    files={"archivo": ("export.csv", csv_bytes, "text/csv")},
                    headers={"Authorization": f"Bearer {tok}"})

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["operaciones_importadas"] > 0
    assert body["disclaimer"] == DISCLAIMER_HORIZONTES
    assert llamadas["user_id"] == USER_ID   # nunca otro user_id


def test_import_otro_usuario_no_puede_forzar_user_id_ajeno(monkeypatch):
    """Dos usuarios distintos, mismo archivo: cada uno debe insertarse bajo
    su propio user_id — el endpoint no acepta un user_id explícito del body."""
    llamadas = []
    monkeypatch.setattr(api_main, "subir_a_supabase",
                        lambda ops, user_id: (llamadas.append(user_id), (len(ops), 0))[1])

    otro_user = "11111111-2222-3333-4444-555555555555"
    csv_bytes = (FIXTURES / "balanz_ar_tipico.csv").read_bytes()

    for uid in (USER_ID, otro_user):
        tok = _token(user_id=uid)
        r = client.post("/portfolio/import",
                        files={"archivo": ("export.csv", csv_bytes, "text/csv")},
                        headers={"Authorization": f"Bearer {tok}"})
        assert r.status_code == 200

    assert llamadas == [USER_ID, otro_user]


def test_import_archivo_vacio_de_operaciones(monkeypatch):
    monkeypatch.setattr(api_main, "subir_a_supabase",
                        lambda ops, user_id: pytest.fail("no debería llamarse sin ops"))
    tok = _token()
    # CSV con solo un dividendo (ninguna operación de compra/venta real)
    csv_bytes = b"Fecha;Especie;Operacion;Cantidad;Precio;Importe;Moneda\n15/05/2026;GOOGL;Dividendo;;;100,00;Pesos\n"
    r = client.post("/portfolio/import",
                    files={"archivo": ("export.csv", csv_bytes, "text/csv")},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200
    assert r.json()["status"] == "sin_operaciones_validas"


def test_import_archivo_demasiado_grande():
    tok = _token()
    csv_bytes = b"a" * (api_main.MAX_CSV_BYTES + 1)
    r = client.post("/portfolio/import",
                    files={"archivo": ("export.csv", csv_bytes, "text/csv")},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 413


def test_import_fallo_de_guardado_no_expone_detalle_interno(monkeypatch):
    def fake_subir(ops, user_id):
        raise RuntimeError("timeout interno de supabase con detalle sensible")
    monkeypatch.setattr(api_main, "subir_a_supabase", fake_subir)
    tok = _token()
    csv_bytes = (FIXTURES / "balanz_ar_tipico.csv").read_bytes()
    r = client.post("/portfolio/import",
                    files={"archivo": ("export.csv", csv_bytes, "text/csv")},
                    headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 503
    assert "timeout interno" not in r.text
