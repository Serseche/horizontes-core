#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════════╗
║   HORIZONTES — Parser de exports CSV de Balanz Capital           ║
╠══════════════════════════════════════════════════════════════════╣
║   Convierte el export de operaciones del broker en filas listas  ║
║   para `public.investment_journal` (tabla ya existente — no      ║
║   requiere migración nueva).                                     ║
║                                                                  ║
║   Tolerante por diseño (los exports de brokers AR varían):       ║
║     · Encoding: utf-8 / utf-8-sig / latin-1 / cp1252             ║
║     · Delimitador: ';' ',' o tab (autodetección)                 ║
║     · Números: coma decimal AR (1.234,56) o punto US (1,234.56)  ║
║     · Fechas: dd/mm/aaaa, dd-mm-aaaa, aaaa-mm-dd                 ║
║     · Encabezados con alias (Especie/Ticker/Instrumento, etc.)   ║
║                                                                  ║
║   Uso:                                                           ║
║     python balanz_parser.py export.csv                           ║
║     python balanz_parser.py export.csv --out-json ops.json       ║
║     python balanz_parser.py export.csv --sql --user-id <UUID>    ║
║     python balanz_parser.py export.csv --supabase --user-id ...  ║
║                                                                  ║
║   ⚠️ VALIDAR con un export real de Balanz antes de confiar:      ║
║   los fixtures de test son sintéticos. Si tu export trae otros   ║
║   encabezados, agregalos a COLUMN_ALIASES (una línea).           ║
╚══════════════════════════════════════════════════════════════════╝
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# 1. NORMALIZACIÓN DE TICKERS → CONVENCIÓN HORIZONTES
# ─────────────────────────────────────────────────────────────────────────────
# En Supabase, los CEDEARs llevan sufijo .BA (GOOGL.BA) y las acciones
# argentinas van "peladas" (GGAL, PAMP). Un export de Balanz AR es mercado
# local, así que un subyacente USA conocido ⇒ CEDEAR ⇒ agregar .BA.

CEDEAR_BASES: set[str] = {
    # Cartera viva + tabla CEDEAR_RATIOS del DataFetcher + agregados jun-2026
    "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA", "META", "NFLX", "PYPL",
    "BABA", "BRKB", "JPM", "XOM", "WMT", "DIS", "BAC", "KO", "PFE", "INTC",
    "AMD", "CRM", "ADBE", "ORCL", "CSCO", "V", "MA",
    "MELI", "QQQ", "SPY", "UNH", "VIST", "MSTR", "COIN",
}

ACCIONES_ARG: set[str] = {
    "GGAL", "PAMP", "YPFD", "YPF", "ALUA", "TXAR", "BMA", "CEPU", "COME",
    "TGSU2", "CRES", "SUPV", "EDN", "LOMA", "MIRG", "BYMA", "VALO", "TRAN",
}

# Variantes de escritura frecuentes en brokers → base canónica
TICKER_SYNONYMS: dict[str, str] = {
    "BRK.B": "BRKB", "BRK-B": "BRKB", "BRKB.B": "BRKB",
    "GOOG": "GOOGL",
}


def normalizar_ticker(raw: str) -> tuple[str, list[str]]:
    """Devuelve (ticker_horizontes, warnings)."""
    warns: list[str] = []
    t = raw.strip().upper()
    t = re.sub(r"\s+", "", t)
    t = TICKER_SYNONYMS.get(t, t)
    base = t.replace(".BA", "")
    base = TICKER_SYNONYMS.get(base, base)

    if base in ACCIONES_ARG:
        return base, warns                     # local ⇒ pelado
    if base in CEDEAR_BASES:
        return f"{base}.BA", warns             # CEDEAR ⇒ .BA
    if t.endswith(".BA"):
        return t, warns                        # ya venía con sufijo
    warns.append(f"Ticker '{raw}' no reconocido en tablas Horizontes — se deja tal cual. "
                 f"Verificar si es CEDEAR (agregar a CEDEAR_BASES) o local (ACCIONES_ARG).")
    return t, warns


# ─────────────────────────────────────────────────────────────────────────────
# 2. PARSEO DE NÚMEROS Y FECHAS (formatos AR y US)
# ─────────────────────────────────────────────────────────────────────────────

def parse_number(s) -> Optional[float]:
    """'1.234,56' → 1234.56 · '1,234.56' → 1234.56 · '150' → 150.0 · '' → None."""
    if s is None:
        return None
    txt = str(s).strip().replace("$", "").replace("ARS", "").replace("USD", "").strip()
    txt = txt.replace("\u00a0", "").replace(" ", "")
    if txt in ("", "-", "--"):
        return None
    neg = txt.startswith("(") and txt.endswith(")")
    txt = txt.strip("()")
    if "," in txt and "." in txt:
        # El separador que aparece ÚLTIMO es el decimal
        if txt.rfind(",") > txt.rfind("."):
            txt = txt.replace(".", "").replace(",", ".")      # formato AR
        else:
            txt = txt.replace(",", "")                        # formato US
    elif "," in txt:
        # Solo coma: miles US ('1,234') vs decimal AR ('1234,56')
        partes = txt.split(",")
        if len(partes) == 2 and len(partes[1]) == 3 and len(partes[0]) <= 3:
            txt = txt.replace(",", "")                        # probable miles US
        else:
            txt = txt.replace(",", ".")                       # decimal AR
    try:
        val = float(txt)
    except ValueError:
        return None
    return -val if neg else val


_DATE_PATTERNS = ("%d/%m/%Y", "%d-%m-%Y", "%Y-%m-%d", "%d/%m/%y", "%Y/%m/%d")


def parse_date(s) -> Optional[date]:
    if not s:
        return None
    txt = str(s).strip().split(" ")[0]
    for fmt in _DATE_PATTERNS:
        try:
            return datetime.strptime(txt, fmt).date()
        except ValueError:
            continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 3. RESOLUCIÓN DE ENCABEZADOS (alias, sin tildes, case-insensitive)
# ─────────────────────────────────────────────────────────────────────────────

def _slug(s: str) -> str:
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", s.lower())


COLUMN_ALIASES: dict[str, set[str]] = {
    "ticker":   {"especie", "ticker", "instrumento", "simbolo", "activo", "papel"},
    "tipo":     {"operacion", "tipooperacion", "tipo", "detalle", "descripcion",
                 "concepto", "movimiento"},
    "cantidad": {"cantidad", "nominales", "cant", "cantidadvn", "vn"},
    "precio":   {"precio", "precioponderado", "px", "precioprom", "preciopromedio",
                 "preciounitario"},
    "fecha":    {"fecha", "fechaconcertacion", "concertacion", "fechaoperacion",
                 "fechaliquidacion", "liquidacion"},
    "importe":  {"importe", "monto", "neto", "importeneto", "total", "montototal", "bruto"},
    "moneda":   {"moneda", "divisa", "currency"},
}


def resolver_columnas(headers: list[str]) -> dict[str, int]:
    """Mapea campo lógico → índice de columna. Lanza si faltan las críticas."""
    slugs = [_slug(h) for h in headers]
    mapa: dict[str, int] = {}
    for campo, aliases in COLUMN_ALIASES.items():
        for idx, sl in enumerate(slugs):
            if sl in aliases:
                mapa[campo] = idx
                break
    faltantes = [c for c in ("ticker", "tipo", "cantidad", "fecha") if c not in mapa]
    if faltantes:
        raise ValueError(
            f"No pude identificar columnas críticas: {faltantes}. "
            f"Encabezados encontrados: {headers}. "
            f"Agregá el alias que corresponda en COLUMN_ALIASES."
        )
    return mapa


# ─────────────────────────────────────────────────────────────────────────────
# 4. CLASIFICACIÓN DE OPERACIONES
# ─────────────────────────────────────────────────────────────────────────────

_COMPRA_PAT = re.compile(r"\b(compra|cpra|buy|suscripcion)\b", re.I)
_VENTA_PAT  = re.compile(r"\b(venta|vta|sell|rescate)\b", re.I)
# Movimientos que NO son trades → se reportan aparte, no se insertan
_NO_TRADE_PAT = re.compile(
    r"(dividendo|renta|cupon|acreditacion|debito|credito|caucion|deposito|"
    r"extraccion|transferencia|comision|impuesto|iva|saldo)", re.I)


def clasificar_tipo(texto: str) -> Optional[str]:
    t = _slug(texto or "")
    plano = texto or ""
    if _NO_TRADE_PAT.search(plano) and not (_COMPRA_PAT.search(plano) or _VENTA_PAT.search(plano)):
        return None
    if _COMPRA_PAT.search(plano) or t.startswith("c"):
        if _VENTA_PAT.search(plano):
            return None  # ambiguo — mejor reportar que adivinar
        return "compra"
    if _VENTA_PAT.search(plano) or t.startswith("v"):
        return "venta"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 5. MODELO NORMALIZADO
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Operacion:
    ticker:            str
    tipo_operacion:    str            # 'compra' | 'venta' (CHECK de investment_journal)
    cantidad:          float
    precio_ejecucion:  Optional[float]
    moneda_ejecucion:  str
    fecha_operacion:   str            # ISO yyyy-mm-dd
    importe:           Optional[float]
    hash_import:       str            # dedup: sha1 corto de la fila normalizada

    def journal_row(self, user_id: str) -> dict:
        """Fila lista para public.investment_journal."""
        return {
            "user_id": user_id,
            "ticker": self.ticker,
            "tipo_operacion": self.tipo_operacion,
            "precio_ejecucion": self.precio_ejecucion,
            "moneda_ejecucion": self.moneda_ejecucion,
            "cantidad": self.cantidad,
            "narrativa_housel": ("Importado desde Balanz — tesis pendiente de completar. "
                                 "Releé tu 'por qué' y editá esta entrada."),
            "tags": ["balanz_import", f"balanz:{self.hash_import}"],
            "fecha_operacion": self.fecha_operacion,
        }


def _hash_op(fecha: str, ticker: str, tipo: str, cantidad: float,
             precio: Optional[float]) -> str:
    base = f"{fecha}|{ticker}|{tipo}|{cantidad:.6f}|{precio if precio is not None else 'NA'}"
    return hashlib.sha1(base.encode()).hexdigest()[:12]


# ─────────────────────────────────────────────────────────────────────────────
# 6. LECTURA DEL ARCHIVO (encoding + delimitador autodetectados)
# ─────────────────────────────────────────────────────────────────────────────

def _leer_texto(path: Path) -> str:
    datos = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            return datos.decode(enc)
        except UnicodeDecodeError:
            continue
    return datos.decode("latin-1", errors="replace")


def _detectar_delimitador(texto: str) -> str:
    muestra = "\n".join(texto.splitlines()[:5])
    conteo = {d: muestra.count(d) for d in (";", ",", "\t")}
    return max(conteo, key=conteo.get) if max(conteo.values()) > 0 else ","


def parse_balanz_csv(path: str | Path) -> tuple[list[Operacion], list[str]]:
    """
    Devuelve (operaciones, reporte). El reporte incluye filas salteadas,
    tickers no reconocidos y toda decisión que merezca revisión humana.
    """
    path = Path(path)
    texto = _leer_texto(path)
    delim = _detectar_delimitador(texto)
    reader = csv.reader(io.StringIO(texto), delimiter=delim)

    filas = [f for f in reader if any(c.strip() for c in f)]
    if not filas:
        raise ValueError(f"'{path.name}' está vacío o no es un CSV legible.")

    # El encabezado puede no ser la primera fila (Balanz a veces antepone
    # título/período). Buscamos la primera fila que resuelva columnas críticas.
    mapa = None
    header_idx = 0
    for idx, fila in enumerate(filas[:10]):
        try:
            mapa = resolver_columnas(fila)
            header_idx = idx
            break
        except ValueError:
            continue
    if mapa is None:
        resolver_columnas(filas[0])  # relanza con mensaje útil

    ops: list[Operacion] = []
    reporte: list[str] = [f"Archivo: {path.name} · delimitador '{delim}' · "
                          f"encabezado en fila {header_idx + 1}"]

    for nline, fila in enumerate(filas[header_idx + 1:], start=header_idx + 2):
        def _celda(campo: str) -> str:
            i = mapa.get(campo)
            return fila[i].strip() if (i is not None and i < len(fila)) else ""

        tipo = clasificar_tipo(_celda("tipo"))
        if tipo is None:
            desc = _celda("tipo") or "(sin descripción)"
            reporte.append(f"Fila {nline}: '{desc[:60]}' no es compra/venta — salteada.")
            continue

        ticker, warns = normalizar_ticker(_celda("ticker"))
        reporte.extend(f"Fila {nline}: {w}" for w in warns)

        cantidad = parse_number(_celda("cantidad"))
        precio   = parse_number(_celda("precio"))
        importe  = parse_number(_celda("importe"))
        fecha    = parse_date(_celda("fecha"))

        if not ticker or cantidad is None or fecha is None:
            reporte.append(f"Fila {nline}: datos críticos ilegibles "
                           f"(ticker/cantidad/fecha) — salteada.")
            continue

        cantidad = abs(cantidad)          # el signo lo da tipo_operacion
        if precio is None and importe and cantidad:
            precio = round(abs(importe) / cantidad, 4)
            reporte.append(f"Fila {nline}: precio derivado de importe/cantidad.")

        moneda_raw = _celda("moneda") or "ARS"
        msl = _slug(moneda_raw)
        if any(k in msl for k in ("usd", "dolar", "mep", "ccl", "us")):
            moneda = "USD"
        elif any(k in msl for k in ("peso", "ars")) or moneda_raw.strip() in ("$", "AR$"):
            moneda = "ARS"
        else:
            moneda = moneda_raw.strip().upper()[:8]

        fecha_iso = fecha.isoformat()
        ops.append(Operacion(
            ticker=ticker, tipo_operacion=tipo, cantidad=cantidad,
            precio_ejecucion=precio, moneda_ejecucion=moneda,
            fecha_operacion=fecha_iso, importe=importe,
            hash_import=_hash_op(fecha_iso, ticker, tipo, cantidad, precio),
        ))

    reporte.append(f"Total: {len(ops)} operaciones válidas.")
    return ops, reporte


# ─────────────────────────────────────────────────────────────────────────────
# 7. AGREGADO DE POSICIONES (control visual rápido)
# ─────────────────────────────────────────────────────────────────────────────

def posiciones_netas(ops: list[Operacion]) -> list[dict]:
    acc: dict[str, dict] = {}
    for op in sorted(ops, key=lambda o: o.fecha_operacion):
        p = acc.setdefault(op.ticker, {"ticker": op.ticker, "cantidad": 0.0,
                                       "costo_total": 0.0, "moneda": op.moneda_ejecucion})
        if op.tipo_operacion == "compra":
            p["cantidad"] += op.cantidad
            if op.precio_ejecucion:
                p["costo_total"] += op.cantidad * op.precio_ejecucion
        else:
            # Venta a costo promedio (simplificación estándar para control)
            if p["cantidad"] > 0:
                cpp = p["costo_total"] / p["cantidad"]
                p["costo_total"] -= min(op.cantidad, p["cantidad"]) * cpp
            p["cantidad"] -= op.cantidad
    salida = []
    for p in acc.values():
        cpp = (p["costo_total"] / p["cantidad"]) if p["cantidad"] > 0 else None
        salida.append({"ticker": p["ticker"],
                       "cantidad_neta": round(p["cantidad"], 6),
                       "costo_promedio": round(cpp, 4) if cpp else None,
                       "moneda": p["moneda"]})
    return sorted(salida, key=lambda x: x["ticker"])


# ─────────────────────────────────────────────────────────────────────────────
# 8. EMISORES: SQL / SUPABASE
# ─────────────────────────────────────────────────────────────────────────────

def _sql_lit(v) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, list):
        inner = ", ".join("'" + str(x).replace("'", "''") + "'" for x in v)
        return f"ARRAY[{inner}]"
    return "'" + str(v).replace("'", "''") + "'"


def generar_sql(ops: list[Operacion], user_id: str) -> str:
    cols = ["user_id", "ticker", "tipo_operacion", "precio_ejecucion",
            "moneda_ejecucion", "cantidad", "narrativa_housel", "tags",
            "fecha_operacion"]
    lineas = [
        "-- HORIZONTES · Import Balanz → investment_journal",
        f"-- Generado: {datetime.now().isoformat(timespec='seconds')} · {len(ops)} filas",
        "-- Dedup: el tag 'balanz:<hash>' identifica cada operación importada.",
        "BEGIN;",
    ]
    for op in ops:
        row = op.journal_row(user_id)
        vals = ", ".join(_sql_lit(row[c]) for c in cols)
        lineas.append(
            f"INSERT INTO public.investment_journal ({', '.join(cols)})\n"
            f"SELECT {vals}\n"
            f"WHERE NOT EXISTS (SELECT 1 FROM public.investment_journal "
            f"WHERE tags @> ARRAY['balanz:{op.hash_import}']);"
        )
    lineas.append("COMMIT;")
    return "\n".join(lineas) + "\n"


def subir_a_supabase(ops: list[Operacion], user_id: str) -> tuple[int, int]:
    """Inserta con dedup por hash. Requiere SUPABASE_URL y
    SUPABASE_SERVICE_ROLE_KEY (legacy JWT) en el entorno."""
    from supabase import create_client  # import lazy — opcional

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise SystemExit("Faltan SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY en el entorno.")
    sb = create_client(url, key)

    insertadas = saltadas = 0
    for op in ops:
        ya = (sb.table("investment_journal").select("id")
                .contains("tags", [f"balanz:{op.hash_import}"])
                .limit(1).execute())
        if ya.data:
            saltadas += 1
            continue
        sb.table("investment_journal").insert(op.journal_row(user_id)).execute()
        insertadas += 1
    return insertadas, saltadas


# ─────────────────────────────────────────────────────────────────────────────
# 9. CLI
# ─────────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Parser de export CSV de Balanz Capital")
    ap.add_argument("archivo", help="Ruta al CSV exportado desde Balanz")
    ap.add_argument("--user-id", help="UUID del usuario en auth.users (para SQL/Supabase)")
    ap.add_argument("--out-json", help="Guardar operaciones normalizadas en JSON")
    ap.add_argument("--out-csv", help="Guardar operaciones normalizadas en CSV")
    ap.add_argument("--sql", action="store_true",
                    help="Imprimir INSERTs idempotentes para investment_journal")
    ap.add_argument("--supabase", action="store_true",
                    help="Insertar directo en Supabase (con dedup por hash)")
    args = ap.parse_args(argv)

    ops, reporte = parse_balanz_csv(args.archivo)

    print("\n── REPORTE DE PARSEO " + "─" * 44)
    for linea in reporte:
        print("  " + linea)

    print("\n── POSICIONES NETAS DETECTADAS " + "─" * 34)
    for p in posiciones_netas(ops):
        cpp = f"@ {p['costo_promedio']:,.2f} {p['moneda']}" if p["costo_promedio"] else ""
        print(f"  {p['ticker']:<10} {p['cantidad_neta']:>12,.2f}  {cpp}")

    if args.out_json:
        Path(args.out_json).write_text(
            json.dumps([asdict(o) for o in ops], indent=2, ensure_ascii=False),
            encoding="utf-8")
        print(f"\n✅ JSON → {args.out_json}")

    if args.out_csv:
        with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=list(asdict(ops[0]).keys()) if ops else
                               ["ticker"])
            w.writeheader()
            for o in ops:
                w.writerow(asdict(o))
        print(f"✅ CSV → {args.out_csv}")

    if args.sql or args.supabase:
        if not args.user_id:
            raise SystemExit("--sql/--supabase requieren --user-id <UUID de auth.users>.")

    if args.sql:
        print("\n" + generar_sql(ops, args.user_id))

    if args.supabase:
        ins, skip = subir_a_supabase(ops, args.user_id)
        print(f"\n✅ Supabase: {ins} insertadas · {skip} ya existían (dedup por hash).")

    return 0


if __name__ == "__main__":
    sys.exit(main())
