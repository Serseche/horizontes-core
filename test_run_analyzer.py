"""
Test E2E de PortfolioAnalyzer - solo analisis, sin persistencia.
Uso: python test_run_analyzer.py
"""

import logging
import sys
import os
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_run_analyzer")

from portfolio_analyzer import PortfolioAnalyzer


def get_user_id(sb_client):
    """Intenta obtener el primer user_id disponible en Supabase."""
    # Intento 1: Auth Admin API (requiere service_role key)
    try:
        users = sb_client.auth.admin.list_users()
        if users:
            uid = users[0].id
            log.info("User ID obtenido via auth.admin: %s", uid)
            return uid
    except Exception as exc:
        log.warning("Auth admin API no disponible: %s", exc)

    # Intento 2: Tabla user_profiles
    try:
        result = (
            sb_client.table("user_profiles")
            .select("user_id")
            .limit(1)
            .execute()
        )
        if result.data:
            uid = result.data[0]["user_id"]
            log.info("User ID obtenido via user_profiles: %s", uid)
            return uid
    except Exception as exc:
        log.warning("user_profiles fallback fallo: %s", exc)

    # Intento 3: Tabla portfolio_holdings
    try:
        result = (
            sb_client.table("portfolio_holdings")
            .select("user_id")
            .limit(1)
            .execute()
        )
        if result.data:
            uid = result.data[0]["user_id"]
            log.info("User ID obtenido via portfolio_holdings: %s", uid)
            return uid
    except Exception as exc:
        log.warning("portfolio_holdings fallback fallo: %s", exc)

    return None


def main():
    print("\n" + "=" * 68)
    print("  HORIZONTES - Test E2E - PortfolioAnalyzer - Sin Persistencia")
    print("=" * 68 + "\n")

    # Paso 1: Inicializar analyzer
    print("[1/3] Inicializando PortfolioAnalyzer.from_env()...")
    try:
        analyzer = PortfolioAnalyzer.from_env()
        print("      OK - Pipeline y Supabase client listos.\n")
    except Exception as exc:
        print(f"      ERROR: {exc}")
        sys.exit(1)

    # Paso 2: Obtener user_id
    print("[2/3] Buscando user_id en Supabase...")
    user_id = get_user_id(analyzer.sb)
    if not user_id:
        print(
            "      ERROR: No se encontro ningun user_id.\n"
            "      Edita el script y asignale manualmente:\n"
            "        user_id = 'tu-uuid-aqui'"
        )
        sys.exit(1)
    print(f"      user_id: {user_id}\n")

    # Paso 3: Correr analisis
    print("[3/3] Ejecutando analyze_portfolio()...")
    print("      (El perfil se lee desde user_profiles; fallback = MODERADO)\n")

    try:
        signals = analyzer.analyze_portfolio(user_id=user_id)
    except Exception as exc:
        print(f"      ERROR en analyze_portfolio: {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

    # Resultado
    print("\n" + "=" * 68)
    print(f"  SENALES EMITIDAS: {len(signals)}")
    print("=" * 68)

    if not signals:
        print("\n  Sin senales accionables para este portfolio.")
        print("  Posibles causas:")
        print("    - El portfolio esta vacio (no hay holdings en Supabase)")
        print("    - Ningun ticker cumple los umbrales de la matriz de reglas")
        print("    - Los datos de mercado no tienen RSI/SMA200 calculados")
    else:
        for i, sig in enumerate(signals, 1):
            print(f"\n  [{i}] {sig.ticker}  -  {sig.signal_type.value.upper()}")
            print(f"       Conviccion : {sig.conviction:.0f}/100")
            print(f"       Razon      : {sig.razon_corta}")

    print("\n" + "=" * 68 + "\n")


if __name__ == "__main__":
    main()
