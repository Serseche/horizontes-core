# HORIZONTES — Guía de instalación de MCPs financieros

**Para:** Sergio · uso personal en Claude Desktop (Windows)
**Objetivo:** activar EdgarTools MCP + FRED MCP para validar señales de HORIZONTES
contra fuentes primarias antes de codificar el `EdgarEnricher.py`.
**Tiempo estimado:** 20-30 minutos.

---

## Pre-requisitos

| Requisito | Verificación rápida |
|---|---|
| Claude Desktop instalado en Windows | Abrir la app — debe aparecer ícono en bandeja del sistema |
| Suscripción Claude Pro o superior | Settings → Account dentro de Claude Desktop |
| Python 3.10+ instalado | `python --version` en PowerShell |
| Permisos para editar `%APPDATA%\Claude\` | Por defecto sí los tenés |

> **Nota Windows:** todos los comandos son para **PowerShell**, no CMD. Si tenés
> dudas, abrí PowerShell con click derecho en el botón Inicio → "Terminal" o
> "Windows PowerShell".

---

## Parte A — EdgarTools MCP (SEC EDGAR)

EdgarTools es **gratis, MIT, sin API key, sin rate limits**. Solo declarás una
identidad SEC (tu nombre + email, requerimiento legal de la SEC para uso
responsable de su API pública).

### A.1 — Instalar `uv` (gestor de paquetes Python rápido)

EdgarTools MCP usa `uvx` para correr el server sin contaminar tu Python global.
Instalá `uv` con un solo comando en PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Verificación:

```powershell
uv --version
```

Tiene que devolver algo como `uv 0.5.x` o más reciente. Si no, cerrá y reabrí
PowerShell para que tome la nueva variable de entorno PATH.

### A.2 — Editar `claude_desktop_config.json`

Ubicación en Windows:

```
%APPDATA%\Claude\claude_desktop_config.json
```

Para abrirlo directo desde PowerShell:

```powershell
notepad "$env:APPDATA\Claude\claude_desktop_config.json"
```

Si el archivo no existe, Notepad ofrece crearlo. Decí que sí.

Contenido a agregar (si el archivo está vacío, copialo entero;
si ya tiene MCPs, agregá solo el bloque de `edgartools` dentro de `mcpServers`):

```json
{
  "mcpServers": {
    "edgartools": {
      "command": "uvx",
      "args": ["--from", "edgartools[ai]", "edgartools-mcp"],
      "env": {
        "EDGAR_IDENTITY": "Sergio Apellido sergio@tu-mail.com"
      }
    }
  }
}
```

**Reemplazá** `Sergio Apellido sergio@tu-mail.com` por tu nombre real y un email
válido. La SEC no te manda nada — solo lo registra como user agent. Usá un
email que controles por si te contactan por uso anómalo.

### A.3 — Reiniciar Claude Desktop

Cerrá Claude Desktop **completamente** (chequeá la bandeja del sistema, no solo
la ventana). Volvé a abrirlo. La primera vez tarda 30-60 segundos porque `uvx`
descarga `edgartools` al cache local.

### A.4 — Validar que carga

En una conversación nueva de Claude Desktop, abajo del input vas a ver un ícono
de herramientas (🔌 o 🛠️). Click ahí — tiene que aparecer "edgartools" en la
lista de servidores conectados con un punto verde.

Si aparece rojo o no aparece, hay un problema de config. Logs en:

```
%APPDATA%\Claude\logs\mcp-server-edgartools.log
```

---

## Parte B — Validar tus 6 señales reales contra SEC

Una vez que EdgarTools esté conectado, abrí una conversación nueva en Claude
Desktop y probá estos prompts en orden. Son específicos para tu cartera Balanz.

### B.1 — Validar señal SELL urgente sobre UNH (UnitedHealth)

```
Usando edgartools, traeme el 10-K más reciente de UNH (UnitedHealth Group).
Quiero ver:
1. Revenue, Operating Income y Net Income de los últimos 3 años
2. Cualquier nota a los estados financieros sobre litigios pendientes o
   investigaciones regulatorias
3. Cambios en el equipo ejecutivo en los últimos 6 meses (Form 4 o 8-K)

Después contrastá con la lectura de mercado: UNH cayó fuerte y mi sistema
dice SELL con score 29.4/100. ¿Los fundamentals confirman o contradicen
esta señal?
```

Esto te muestra si la señal SELL del motor está respaldada por deterioro
fundamental real, o si es solo overshooting de mercado (lo cual cambiaría
la convicción a "AVOID — esperar oversold").

### B.2 — Insider activity en GOOGL (señal WAIT)

```
Usando edgartools, dame todas las transacciones Form 4 de Alphabet (GOOGL)
de los últimos 6 meses. Quiero saber:
- Quiénes compraron (cargo + monto)
- Quiénes vendieron (cargo + monto, separar las ventas planeadas 10b5-1
  de las discrecionales)
- Score neto: si los insiders están comprando o vendiendo más

Mi sistema dice WAIT con convicción 60/100. ¿Los insiders confirman
"esperar pullback" o están comprando ya?
```

### B.3 — Smart money en MELI (HOLD/Acumular)

```
Usando edgartools, traeme el 13F-HR más reciente de los principales fondos
que tienen MercadoLibre (MELI):
- ¿Los grandes (Berkshire, BlackRock, Baillie Gifford, ARK) aumentaron o
  redujeron posición último trimestre?
- Hay algún fondo nuevo de >$1B que entró por primera vez?

Mi sistema dice HOLD con score 45.5/100. Si el smart money está acumulando
silenciosamente, eso refuerza el sesgo a "Acumular" sobre "Hold puro".
```

### B.4 — Eventos materiales 8-K en BABA y TSLA

```
Usando edgartools, dame los 8-K filings de los últimos 60 días de:
- BABA (Alibaba)
- TSLA (Tesla)

Para cada uno, listá los items reportados (1.01, 2.02, 5.02, etc.) con un
resumen de una línea de cada evento material. ¿Hubo alguno que justifique
revisar mi señal HOLD actual?
```

### B.5 — Comparativa Graham + insiders sobre tu cartera

```
Usando edgartools, calculá para UNH, GOOGL, BABA, TSLA, MELI:
- P/E ratio actual (último 10-Q)
- Net debt / EBITDA
- Free cash flow margin
- Insider net buying ratio últimos 6 meses

Devolvelo en una tabla. Quiero ver si hay algún ticker donde Graham
(value) y los insiders (smart money) coincidan en el mismo sentido,
porque esa es la señal más alta convicción que existe.
```

> **Tip:** guardá las salidas de B.5 — me las podés pegar cuando codifiquemos
> el `EdgarEnricher.py` para usarlas como expected output en los unit tests.

---

## Parte C — FRED MCP (datos macro, opcional pero recomendado)

Esto te suma DGS10 (Treasury 10Y), VIX, DXY y HY spread al alcance de tu
chat de Claude Desktop, justo lo que necesitás para refinar el Semáforo
Macro de HORIZONTES.

### C.1 — Conseguir API key de FRED (gratis)

1. Ir a https://fred.stlouisfed.org/docs/api/api_key.html
2. Click en "Request API Key", crear cuenta con tu email
3. La key se ve como `abcdef1234567890abcdef1234567890` (32 caracteres hex)
4. Guardala — la vas a poner en el config de Claude Desktop

### C.2 — Agregar FRED MCP al config (Docker required)

> **Pre-requisito:** Docker Desktop instalado y corriendo en Windows.
> Si no lo tenés, instalá desde https://www.docker.com/products/docker-desktop/
> Si preferís evitar Docker, hay una alternativa Python que muestro abajo.

Editar de nuevo `%APPDATA%\Claude\claude_desktop_config.json` y agregar
**dentro del bloque `mcpServers`** (después de `edgartools`, separado con coma):

```json
{
  "mcpServers": {
    "edgartools": {
      "command": "uvx",
      "args": ["--from", "edgartools[ai]", "edgartools-mcp"],
      "env": {
        "EDGAR_IDENTITY": "Sergio Apellido sergio@tu-mail.com"
      }
    },
    "fred": {
      "command": "docker",
      "args": [
        "run", "-i", "--rm",
        "-e", "FRED_API_KEY",
        "stefanoamorelli/fred-mcp-server:latest"
      ],
      "env": {
        "FRED_API_KEY": "TU_API_KEY_DE_FRED_AQUI"
      }
    }
  }
}
```

### C.3 — Validar Semáforo Macro contra FRED

Después de reiniciar Claude Desktop, probá:

```
Usando fred, traeme las últimas observaciones de:
- DGS10 (Treasury 10Y yield)
- VIXCLS (VIX)
- DTWEXBGS (DXY trade-weighted dollar)
- BAMLH0A0HYM2 (US High Yield spread)
- T10Y2Y (10Y-2Y spread, indicador recesión)

Para cada uno, dame:
1. Valor actual
2. Cambio % vs hace 30 días
3. Posición en su rango histórico (percentil 1Y y 5Y)
4. Lectura macro: ¿risk-on o risk-off?

Esto es input para mi Semáforo Macro de cartera.
```

---

## Parte D — Limitaciones que tenés que conocer

| Limitación | Implicancia para HORIZONTES |
|---|---|
| EdgarTools cubre **solo empresas USA registradas en SEC** | BYMA puro (GGAL, YPF, PAMP) NO funciona; CEDEARs sí porque son ADRs USA |
| FRED tiene 120 calls/min de límite | Para tu uso personal no es problema; para HORIZONTES como SaaS habría que cachear |
| EDGAR_IDENTITY es público | El email queda en logs de la SEC; usá uno operacional, no personal sensible |
| Docker corriendo consume RAM | Mínimo 2GB libres recomendados; si tu equipo es justo, usar pip-only abajo |
| MCPs locales **NO** llegan al SaaS | Lo que hacés acá es validación analítica personal. Para HORIZONTES como producto, integramos las **librerías** Python (`edgartools`, `fredapi`) directamente |

### D.1 — Alternativa pip-only (sin uv ni Docker)

Si preferís evitar `uv` y Docker, podés instalar todo con pip:

```powershell
pip install "edgartools[ai]" fredapi
```

Y en `claude_desktop_config.json` usar:

```json
{
  "mcpServers": {
    "edgartools": {
      "command": "python",
      "args": ["-m", "edgar.ai"],
      "env": {
        "EDGAR_IDENTITY": "Sergio Apellido sergio@tu-mail.com"
      }
    }
  }
}
```

Para FRED en modo pip puro hay servers como `mcp-fredapi` (Jaldekoa) — repo
en GitHub con instrucciones de install vía `uv --directory ...`. Si no querés
ningún Docker en tu máquina, ese es el camino.

---

## Parte E — Qué hacer después de validar

Cuando hayas hecho B.1 a B.5, vas a tener:

1. **Confirmación o desconfirmación** de cada una de las 6 señales actuales
   contra fuentes primarias SEC
2. **Datos crudos de insider activity** que vamos a usar como base para
   calibrar el modulador del Insider Conviction Score
3. **Lectura macro real** del Semáforo en lugar de proxies aproximados

Volvé al chat de HORIZONTES con un resumen de hallazgos (lo que confirmó la
señal, lo que la contradijo, sorpresas) y arrancamos `EdgarEnricher.py` ya
con criterio empírico, no con suposiciones.

---

## Troubleshooting rápido

**"uvx: command not found" después de reiniciar Claude**
→ uv quedó en una ruta no incluida en PATH del sistema. Solución:
poné el path completo en `command`:
`"command": "C:\\Users\\TUUSUARIO\\.local\\bin\\uv.exe"` y `"args": ["tool", "run", "--from", "edgartools[ai]", "edgartools-mcp"]`

**"FRED_API_KEY not set" o connection refused**
→ Docker Desktop no está corriendo. Abrilo desde el menú Inicio y esperá
a que el ícono de la bandeja diga "Docker Desktop is running".

**EdgarTools devuelve "Identity not set"**
→ La variable `EDGAR_IDENTITY` no se cargó. Reiniciá Claude Desktop completo
(cerrá desde bandeja del sistema, no solo la ventana).

**Claude Desktop no muestra el ícono de tools**
→ Necesitás Pro/Max. La cuenta gratuita tiene MCPs limitados a 1 connector.

---

*Última actualización: abril 2026 · referencias verificadas: EdgarTools v5.30
(github.com/dgunning/edgartools), FRED MCP Amorelli v1.0.2 (zenodo:14536707)*
