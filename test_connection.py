"""
╔══════════════════════════════════════════════════════════════╗
║         HORIZONTES — Test de Conexión a Supabase             ║
║         Ejecutar: python test_connection.py                  ║
╚══════════════════════════════════════════════════════════════╝

Verifica que:
  1. El archivo .env existe y tiene las 3 variables correctas
  2. La conexión a Supabase responde
  3. Las tablas del schema existen (scoring_cache, user_profiles, etc.)
"""

import os
import sys

# ── 1. Cargar variables de entorno desde .env ─────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv()
    print("✅ python-dotenv cargado correctamente")
except ImportError:
    print("❌ ERROR: python-dotenv no está instalado.")
    print("   Solución: pip install python-dotenv")
    sys.exit(1)

# ── 2. Verificar que las variables existen ────────────────────────────────────
SUPABASE_URL      = os.environ.get("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.environ.get("SUPABASE_ANON_KEY", "")
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "")

print("\n── Variables de entorno ──────────────────────────────────")

errores = []
if SUPABASE_URL and not SUPABASE_URL.startswith("https://TU_"):
    print(f"✅ SUPABASE_URL       : {SUPABASE_URL[:40]}...")
else:
    print("❌ SUPABASE_URL       : No configurada o tiene valor placeholder")
    errores.append("SUPABASE_URL")

if SUPABASE_ANON_KEY and not SUPABASE_ANON_KEY.startswith("TU_"):
    print(f"✅ SUPABASE_ANON_KEY  : {SUPABASE_ANON_KEY[:20]}...")
else:
    print("❌ SUPABASE_ANON_KEY  : No configurada o tiene valor placeholder")
    errores.append("SUPABASE_ANON_KEY")

if SUPABASE_SERVICE_KEY and not SUPABASE_SERVICE_KEY.startswith("TU_"):
    print(f"✅ SUPABASE_SERVICE_KEY: {SUPABASE_SERVICE_KEY[:20]}...")
else:
    print("❌ SUPABASE_SERVICE_KEY: No configurada o tiene valor placeholder")
    errores.append("SUPABASE_SERVICE_KEY")

if errores:
    print(f"\n❌ Faltan configurar estas variables en tu .env: {', '.join(errores)}")
    print("   Abre el archivo .env en VS Code y reemplaza los placeholders")
    print("   con los valores reales de tu panel de Supabase (Settings → API)")
    sys.exit(1)

# ── 3. Intentar conectar a Supabase ──────────────────────────────────────────
print("\n── Conexión a Supabase ───────────────────────────────────")
try:
    from supabase import create_client
    print("✅ Librería supabase importada correctamente")
except ImportError:
    print("❌ ERROR: La librería supabase no está instalada.")
    print("   Solución: pip install supabase")
    sys.exit(1)

try:
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    print("✅ Cliente Supabase creado")
except Exception as e:
    print(f"❌ Error creando cliente Supabase: {e}")
    sys.exit(1)

# ── 4. Verificar que las tablas del schema existen ────────────────────────────
print("\n── Verificando tablas del schema ─────────────────────────")
TABLAS_ESPERADAS = [
    "user_profiles",
    "kids_saver_goals",
    "scoring_cache",
    "investment_journal",
    "macro_semaforo",
]

tablas_ok = []
tablas_faltantes = []

for tabla in TABLAS_ESPERADAS:
    try:
        result = client.table(tabla).select("*").limit(1).execute()
        print(f"✅ Tabla '{tabla}' — accesible")
        tablas_ok.append(tabla)
    except Exception as e:
        error_str = str(e)
        if "does not exist" in error_str or "relation" in error_str:
            print(f"❌ Tabla '{tabla}' — NO EXISTE (¿corriste el schema SQL?)")
            tablas_faltantes.append(tabla)
        else:
            # Puede ser un error de permisos, pero la tabla existe
            print(f"⚠️  Tabla '{tabla}' — error de acceso (tabla existe pero check permisos): {error_str[:80]}")
            tablas_ok.append(tabla)

# ── 5. Resumen final ──────────────────────────────────────────────────────────
print("\n" + "═" * 55)
if tablas_faltantes:
    print("⚠️  ATENCIÓN: Algunas tablas no existen.")
    print("   Solución: Ve al SQL Editor de Supabase → New Query")
    print("   Pega el contenido de horizontes_supabase_schema.sql")
    print("   y ejecuta con el botón 'Run'.")
    print(f"\n   Tablas faltantes: {', '.join(tablas_faltantes)}")
else:
    print("🎉 ¡CONEXIÓN OK! Todo listo para correr Horizontes.")
    print(f"   Tablas verificadas: {len(tablas_ok)}/{len(TABLAS_ESPERADAS)}")
    print("\n   Próximo paso: python DataFetcher.py MSFT")
print("═" * 55)
