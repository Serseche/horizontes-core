"""
HORIZONTES — Tests del parser Balanz.
Correr:  pytest tests/ -v
⚠️ Los fixtures son SINTÉTICOS: validar además con un export real de Balanz.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balanz_parser import (  # noqa: E402
    parse_number, parse_date, normalizar_ticker,
    parse_balanz_csv, posiciones_netas, generar_sql,
)

FIX = Path(__file__).parent / "fixtures"


# ─── Unidades ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,esperado", [
    ("18.450,50", 18450.50),      # AR: punto miles, coma decimal
    ("1,234.56", 1234.56),        # US
    ("1234,56", 1234.56),         # AR sin miles
    ("1,234", 1234.0),            # miles US ambiguo → miles
    ("150", 150.0),
    ("$ 6.540,00", 6540.0),
    ("(500,25)", -500.25),        # negativo contable
    ("", None),
    ("-", None),
])
def test_parse_number(raw, esperado):
    assert parse_number(raw) == esperado


@pytest.mark.parametrize("raw,iso", [
    ("15/05/2026", "2026-05-15"),
    ("15-05-2026", "2026-05-15"),
    ("2026-05-15", "2026-05-15"),
    ("2026-05-15 14:30", "2026-05-15"),
])
def test_parse_date(raw, iso):
    assert parse_date(raw).isoformat() == iso


@pytest.mark.parametrize("raw,esperado", [
    ("GOOGL", "GOOGL.BA"),        # CEDEAR conocido → .BA
    ("googl", "GOOGL.BA"),
    ("BRK.B", "BRKB.BA"),         # sinónimo broker → base canónica
    ("MELI", "MELI.BA"),
    ("GGAL", "GGAL"),             # acción argentina → pelada
    ("PAMP", "PAMP"),
])
def test_normalizar_ticker(raw, esperado):
    t, _ = normalizar_ticker(raw)
    assert t == esperado


def test_ticker_desconocido_genera_warning():
    t, warns = normalizar_ticker("ZZTOP")
    assert t == "ZZTOP" and warns


# ─── Fixture AR típico (latin-1, ';', coma decimal) ──────────────────────────

def test_parse_ar_tipico():
    ops, reporte = parse_balanz_csv(FIX / "balanz_ar_tipico.csv")
    # 7 filas de datos: 5 trades válidos (dividendo salteado; ZZTOP entra con warning)
    assert len(ops) == 6
    assert any("Dividendo" in l or "dividendo" in l.lower() for l in reporte)

    googl_compra = next(o for o in ops
                        if o.ticker == "GOOGL.BA" and o.tipo_operacion == "compra")
    assert googl_compra.cantidad == 12
    assert googl_compra.precio_ejecucion == pytest.approx(18450.50)
    assert googl_compra.moneda_ejecucion == "ARS"
    assert googl_compra.fecha_operacion == "2026-05-15"

    assert any(o.ticker == "BRKB.BA" for o in ops)       # BRK.B normalizado
    assert any(o.ticker == "GGAL" for o in ops)          # local pelada
    assert all(len(o.hash_import) == 12 for o in ops)    # dedup hash presente


def test_hash_deterministico():
    ops1, _ = parse_balanz_csv(FIX / "balanz_ar_tipico.csv")
    ops2, _ = parse_balanz_csv(FIX / "balanz_ar_tipico.csv")
    assert [o.hash_import for o in ops1] == [o.hash_import for o in ops2]


def test_posiciones_netas():
    ops, _ = parse_balanz_csv(FIX / "balanz_ar_tipico.csv")
    pos = {p["ticker"]: p for p in posiciones_netas(ops)}
    assert pos["GOOGL.BA"]["cantidad_neta"] == 8            # 12 compradas − 4 vendidas
    assert pos["GOOGL.BA"]["costo_promedio"] == pytest.approx(18450.50)
    assert pos["MELI.BA"]["cantidad_neta"] == 3


# ─── Variantes de formato ─────────────────────────────────────────────────────

def test_parse_us_format_y_alias():
    ops, _ = parse_balanz_csv(FIX / "balanz_us_format.csv")
    assert len(ops) == 2
    nvda = next(o for o in ops if o.ticker == "NVDA.BA")
    assert nvda.tipo_operacion == "compra"                  # 'CPRA' reconocido
    assert nvda.precio_ejecucion == pytest.approx(15200.25)
    qqq = next(o for o in ops if o.ticker == "QQQ.BA")
    assert qqq.tipo_operacion == "venta"                    # 'VTA' reconocido


def test_titulo_antes_del_encabezado_y_moneda_mep():
    ops, reporte = parse_balanz_csv(FIX / "balanz_con_titulo.csv")
    assert len(ops) == 1
    assert ops[0].ticker == "UNH.BA"
    assert ops[0].moneda_ejecucion == "USD"                 # 'Dólar MEP' → USD
    assert "fila 2" in reporte[0]                           # encabezado detectado en fila 2


# ─── Emisor SQL ───────────────────────────────────────────────────────────────

def test_sql_es_idempotente_y_escapa():
    ops, _ = parse_balanz_csv(FIX / "balanz_ar_tipico.csv")
    uid = "ea6ef068-223a-4e59-b6bc-68d4cfb8cdc0"
    sql = generar_sql(ops, uid)
    assert sql.count("INSERT INTO public.investment_journal") == len(ops)
    assert sql.count("WHERE NOT EXISTS") == len(ops)        # dedup por hash
    assert uid in sql
    assert "narrativa_housel" in sql and "BEGIN;" in sql and "COMMIT;" in sql
