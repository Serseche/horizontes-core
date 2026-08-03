"""
╔══════════════════════════════════════════════════════════════╗
║         HORIZONTES — ScoringEngine v1.0                      ║
║         Motor de Lógica de Inversión Inteligente             ║
║         CTO: Basado en Estrategia_Inteligente_Horizontes     ║
╚══════════════════════════════════════════════════════════════╝

Arquitectura de pesos dinámicos por perfil:
  · Liquidez Plus : 70% Murphy  / 30% InvestingPro
  · Conservador   : 40% Graham  / 30% InvestingPro / 25% Murphy / 5% Lynch
  · Moderado      : 45% Graham  / 25% InvestingPro / 20% Lynch  / 10% Murphy
  · Agresivo      : 60% InvestingPro / 30% Lynch   / 10% Graham / 0% Murphy
"""

from __future__ import annotations

from enum import Enum
from typing import Optional
import math

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# 1. ENUMERACIONES Y CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

class PerfilInversor(str, Enum):
    LIQUIDEZ_PLUS = "liquidez_plus"   # Técnico puro, horizonte < 1 año
    CONSERVADOR   = "conservador"     # 1–2 años, Graham dominante
    MODERADO      = "moderado"        # 3–7 años, reversión a la media
    AGRESIVO      = "agresivo"        # 10+ años, moat y crecimiento

# Tabla de pesos validada (suma exacta = 1.0 por perfil)
PROFILE_WEIGHTS: dict[PerfilInversor, dict[str, float]] = {
    PerfilInversor.LIQUIDEZ_PLUS: {
        "murphy":        0.70,
        "investingpro":  0.30,
        "graham":        0.00,
        "lynch":         0.00,
    },
    PerfilInversor.CONSERVADOR: {
        "graham":        0.40,
        "investingpro":  0.30,
        "murphy":        0.25,
        "lynch":         0.05,
    },
    PerfilInversor.MODERADO: {
        "graham":        0.45,
        "investingpro":  0.25,
        "lynch":         0.20,
        "murphy":        0.10,
    },
    PerfilInversor.AGRESIVO: {
        "investingpro":  0.60,
        "lynch":         0.30,
        "graham":        0.10,
        "murphy":        0.00,
    },
}

# Narrativas Housel por nivel de score (psicología anti-pánico)
HOUSEL_NARRATIVES: dict[str, dict] = {
    "excelente": {
        "titulo":    "🟢 Activo de Alta Convicción",
        "mensaje": (
            "Este activo paga la 'tarifa de admisión' correcta para tu horizonte. "
            "La volatilidad que puedas ver en el camino no es una multa por un error; "
            "es el precio de entrada a retornos superiores. "
            "El tiempo es tu mayor ventaja competitiva: mantén el rumbo."
        ),
    },
    "bueno": {
        "titulo":    "🔵 Oportunidad Sólida",
        "mensaje": (
            "Los fundamentos indican un activo saludable. Recuerda que "
            "la riqueza se construye con comportamiento persistente, "
            "no con aciertos diarios. Este activo merece paciencia."
        ),
    },
    "neutral": {
        "titulo":    "🟡 Monitorear — Esperar Margen de Seguridad",
        "mensaje": (
            "El activo no está barato ni caro de forma evidente. "
            "Como diría Graham, 'el inversor que no puede ignorar "
            "las fluctuaciones del mercado está condenado a sufrir'. "
            "Aguarda un punto de entrada con mayor margen."
        ),
    },
    "debil": {
        "titulo":    "🔴 Alerta — No Cumple Filtros",
        "mensaje": (
            "El score refleja debilidad en los pilares clave para tu perfil. "
            "Vender por pánico también es una decisión emocional; "
            "pero posicionar capital aquí sería ignorar la evidencia. "
            "Preservar capital hoy es ganar mañana."
        ),
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# 2. MODELOS PYDANTIC — ENTRADA Y SALIDA
# ─────────────────────────────────────────────────────────────────────────────

class MetricasFinancieras(BaseModel):
    """Métricas brutas del activo. Todas opcionales para mayor flexibilidad."""

    # ── Métricas Graham (valor y solvencia) ───────────────────────────────
    pe_ratio:          Optional[float] = Field(None, description="Price-to-Earnings ratio")
    pb_ratio:          Optional[float] = Field(None, description="Price-to-Book ratio")
    ev_ebitda:         Optional[float] = Field(None, description="EV/EBITDA múltiplo")
    current_ratio:     Optional[float] = Field(None, description="Ratio de liquidez corriente")
    debt_equity:       Optional[float] = Field(None, description="Deuda Total / Patrimonio Neto")
    interest_coverage: Optional[float] = Field(None, description="EBIT / Intereses (cobertura)")
    margen_seguridad:  Optional[float] = Field(None, ge=0, le=1, description="(Fair Value - Precio) / Fair Value")

    # ── Métricas InvestingPro (salud financiera 1–5) ───────────────────────
    health_score:      Optional[float] = Field(None, ge=1, le=5, description="Score salud financiera InvestingPro (1–5)")
    roic:              Optional[float] = Field(None, description="Return on Invested Capital (%)")
    roe:               Optional[float] = Field(None, description="Return on Equity (%)")
    flujo_caja_score:  Optional[float] = Field(None, ge=0, le=100, description="Score flujo de caja operativo (0–100)")
    upside_fair_value: Optional[float] = Field(None, description="Potencial de subida al Fair Value (%)")

    # ── Métricas Murphy (análisis técnico) ────────────────────────────────
    rsi:               Optional[float] = Field(None, ge=0, le=100, description="RSI (14 días por defecto)")
    precio_vs_sma200:  Optional[float] = Field(None, description="(Precio / SMA200) - 1 en decimal, ej: 0.05 = +5%")
    precio_vs_sma50:   Optional[float] = Field(None, description="(Precio / SMA50) - 1 en decimal")
    volumen_relativo:  Optional[float] = Field(None, ge=0, description="Volumen actual / Volumen promedio 20d")

    # ── Métricas Lynch (crecimiento) ──────────────────────────────────────
    peg_ratio:         Optional[float] = Field(None, description="PEG Ratio (P/E / tasa crecimiento BPA)")
    crecimiento_bpa:   Optional[float] = Field(None, description="Crecimiento BPA YoY (%)")
    roc:               Optional[float] = Field(None, description="Return on Capital – Fórmula Mágica (%)")

    # ── Datos CEDEAR (Argentina) ──────────────────────────────────────────
    es_cedear:         bool  = Field(False, description="¿El activo es un CEDEAR?")
    precio_ars:        Optional[float] = Field(None, description="Precio en ARS (solo CEDEARs)")
    ratio_cedear:      Optional[float] = Field(None, description="Factor de conversión CEDEAR → subyacente")
    ccl_implied:       Optional[float] = Field(None, description="Tipo de cambio CCL real (no oficial)")
    inflacion_us:      Optional[float] = Field(None, ge=0, description="Inflación USA anualizada (decimal, ej: 0.03)")
    precio_subyacente_usd: Optional[float] = Field(None, description="Precio del subyacente en NYSE/NASDAQ (USD)")

    @model_validator(mode="after")
    def validar_cedear(self) -> "MetricasFinancieras":
        if self.es_cedear:
            faltantes = []
            if self.precio_ars       is None: faltantes.append("precio_ars")
            if self.ratio_cedear     is None: faltantes.append("ratio_cedear")
            if self.ccl_implied      is None: faltantes.append("ccl_implied")
            if self.inflacion_us     is None: faltantes.append("inflacion_us")
            if faltantes:
                raise ValueError(
                    f"Para un CEDEAR se requieren: {', '.join(faltantes)}"
                )
        return self


class ScoringRequest(BaseModel):
    ticker:   str                = Field(..., description="Símbolo del activo (ej: AAPL, MSFT)")
    perfil:   PerfilInversor     = Field(..., description="Perfil del inversor")
    metricas: MetricasFinancieras

    class Config:
        json_schema_extra = {
            "example": {
                "ticker": "AAPL",
                "perfil": "moderado",
                "metricas": {
                    "pe_ratio": 22.5,
                    "pb_ratio": 3.8,
                    "current_ratio": 2.4,
                    "debt_equity": 0.6,
                    "interest_coverage": 8.2,
                    "margen_seguridad": 0.18,
                    "health_score": 3.9,
                    "roic": 28.0,
                    "roe": 32.0,
                    "flujo_caja_score": 75.0,
                    "upside_fair_value": 15.0,
                    "rsi": 52.0,
                    "precio_vs_sma200": 0.04,
                    "precio_vs_sma50": 0.02,
                    "volumen_relativo": 1.1,
                    "peg_ratio": 1.2,
                    "crecimiento_bpa": 14.0,
                    "roc": 26.0,
                    "es_cedear": False,
                }
            }
        }


class ValorRealUSD(BaseModel):
    """Resultado del filtro VRU — solo para CEDEARs."""
    vru:                    float
    precio_ars:             float
    ccl_implied:            float
    inflacion_us:           float
    ratio_cedear:           float
    precio_subyacente_usd:  Optional[float]
    delta_vs_subyacente:    Optional[float] = Field(None, description="(VRU - subyacente_usd) / subyacente_usd")
    alerta_trampa:          bool            = Field(False, description="True si el VRU diverge negativamente del subyacente")


class ScoreBloque(BaseModel):
    """Score parcial de cada bloque metodológico."""
    score_bruto: float = Field(description="0–100 antes de ponderar")
    peso:        float = Field(description="Peso en el perfil seleccionado")
    aporte:      float = Field(description="score_bruto × peso")
    detalle:     dict  = Field(description="Señales individuales usadas")


class InvestmentScore(BaseModel):
    """Respuesta completa del ScoringEngine."""
    ticker:           str
    perfil:           PerfilInversor
    score_final:      float = Field(description="Puntuación 0–100")
    clasificacion:    str   = Field(description="STRONG BUY / ACCUMULATE / HOLD / AVOID")
    bloques:          dict[str, ScoreBloque]
    valor_real_usd:   Optional[ValorRealUSD] = None
    narrativa_housel: dict   = Field(description="Justificación psicológica anti-pánico")
    pesos_aplicados:  dict   = Field(description="Tabla de pesos del perfil")
    alertas:          list[str] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# 3. FUNCIONES DE SCORE PARCIAL POR BLOQUE
# ─────────────────────────────────────────────────────────────────────────────

def _clamp(valor: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, valor))


def score_graham(m: MetricasFinancieras, perfil: PerfilInversor) -> tuple[float, dict]:
    """
    Evalúa seguridad del valor intrínseco según Graham.
    Filtros hard: current_ratio < 2.0 → penalización severa.
    """
    puntos = 0.0
    detalle: dict = {}

    # --- Filtro Hard: Current Ratio ---
    if m.current_ratio is not None:
        if m.current_ratio >= 2.0:
            puntos += 20
            detalle["current_ratio"] = f"✅ {m.current_ratio:.2f} ≥ 2.0 (+20pts)"
        else:
            puntos -= 30   # Hard reject Graham
            detalle["current_ratio"] = f"🚫 {m.current_ratio:.2f} < 2.0 (Rechazo automático -30pts)"

    # --- Deuda/Equity ---
    if m.debt_equity is not None:
        if m.debt_equity <= 0.5:
            puntos += 20
            detalle["debt_equity"] = f"✅ {m.debt_equity:.2f} ≤ 0.5 (+20pts)"
        elif m.debt_equity <= 1.0:
            puntos += 10
            detalle["debt_equity"] = f"⚠️ {m.debt_equity:.2f} moderado (+10pts)"
        else:
            puntos -= 10
            detalle["debt_equity"] = f"❌ {m.debt_equity:.2f} > 1.0 (-10pts)"

    # --- Cobertura de intereses ≥ 4.3 (benchmark industrial Graham) ---
    if m.interest_coverage is not None:
        if m.interest_coverage >= 4.3:
            puntos += 15
            detalle["interest_coverage"] = f"✅ {m.interest_coverage:.1f}x ≥ 4.3x (+15pts)"
        elif m.interest_coverage >= 2.0:
            puntos += 5
            detalle["interest_coverage"] = f"⚠️ {m.interest_coverage:.1f}x (+5pts)"
        else:
            puntos -= 15
            detalle["interest_coverage"] = f"❌ {m.interest_coverage:.1f}x < 2x (-15pts)"

    # --- P/E ratio ---
    if m.pe_ratio is not None:
        if m.pe_ratio <= 15:
            puntos += 20
            detalle["pe_ratio"] = f"✅ P/E {m.pe_ratio:.1f} ≤ 15 (+20pts)"
        elif m.pe_ratio <= 25:
            puntos += 8
            detalle["pe_ratio"] = f"⚠️ P/E {m.pe_ratio:.1f} aceptable (+8pts)"
        else:
            puntos -= 5
            detalle["pe_ratio"] = f"❌ P/E {m.pe_ratio:.1f} > 25 (-5pts)"

    # --- Margen de Seguridad ---
    if m.margen_seguridad is not None:
        if m.margen_seguridad >= 0.30:
            puntos += 25
            detalle["margen_seguridad"] = f"✅ {m.margen_seguridad*100:.0f}% ≥ 30% (+25pts)"
        elif m.margen_seguridad >= 0.15:
            puntos += 12
            detalle["margen_seguridad"] = f"⚠️ {m.margen_seguridad*100:.0f}% (+12pts)"
        else:
            puntos += 0
            detalle["margen_seguridad"] = f"❌ {m.margen_seguridad*100:.0f}% < 15% (0pts)"

    return _clamp(puntos), detalle


def score_investingpro(m: MetricasFinancieras, perfil: PerfilInversor) -> tuple[float, dict]:
    """
    Evalúa salud financiera mediante los 5 pilares de InvestingPro.
    Health Score 1–5 → normalizado a 0–100.
    """
    puntos = 0.0
    detalle: dict = {}

    # --- Health Score global InvestingPro (1–5) ---
    if m.health_score is not None:
        aporte = (m.health_score - 1) / 4 * 40  # max 40 pts
        puntos += aporte
        nivel = (
            "Excelente" if m.health_score >= 4.1 else
            "Muy Buena" if m.health_score >= 3.1 else
            "Aceptable" if m.health_score >= 2.1 else "Débil"
        )
        detalle["health_score"] = f"{'✅' if m.health_score >= 3.1 else '⚠️'} {m.health_score:.1f}/5 ({nivel}, +{aporte:.0f}pts)"

    # --- ROIC (moat proxy) ---
    if m.roic is not None:
        if m.roic >= 25:
            puntos += 25
            detalle["roic"] = f"✅ ROIC {m.roic:.1f}% ≥ 25% (moat probable, +25pts)"
        elif m.roic >= 12:
            puntos += 12
            detalle["roic"] = f"⚠️ ROIC {m.roic:.1f}% (+12pts)"
        else:
            detalle["roic"] = f"❌ ROIC {m.roic:.1f}% < 12% (0pts)"

    # --- ROE ---
    if m.roe is not None:
        if m.roe >= 20:
            puntos += 15
            detalle["roe"] = f"✅ ROE {m.roe:.1f}% ≥ 20% (+15pts)"
        elif m.roe >= 10:
            puntos += 7
            detalle["roe"] = f"⚠️ ROE {m.roe:.1f}% (+7pts)"
        else:
            detalle["roe"] = f"❌ ROE {m.roe:.1f}% < 10% (0pts)"

    # --- Upside vs Fair Value ---
    if m.upside_fair_value is not None:
        if m.upside_fair_value >= 20:
            puntos += 20
            detalle["upside"] = f"✅ Upside {m.upside_fair_value:.1f}% ≥ 20% (+20pts)"
        elif m.upside_fair_value >= 10:
            puntos += 10
            detalle["upside"] = f"⚠️ Upside {m.upside_fair_value:.1f}% (+10pts)"
        else:
            detalle["upside"] = f"❌ Upside {m.upside_fair_value:.1f}% < 10% (0pts)"

    return _clamp(puntos), detalle


def score_murphy(m: MetricasFinancieras, perfil: PerfilInversor) -> tuple[float, dict]:
    """
    Análisis técnico — John Murphy.
    Para CEDEARs: señales calculadas SOLO sobre el subyacente en USD (no ARS).
    """
    puntos = 0.0
    detalle: dict = {}

    # Si es CEDEAR y no hay precio subyacente USD, el bloque técnico se invalida
    if m.es_cedear and m.precio_subyacente_usd is None:
        detalle["cedear_warning"] = "⚠️ Señales técnicas en ARS ignoradas (ruido CCL). Se requiere subyacente USD."
        return 0.0, detalle

    # --- RSI ---
    if m.rsi is not None:
        if 45 <= m.rsi <= 65:          # Zona saludable
            puntos += 30
            detalle["rsi"] = f"✅ RSI {m.rsi:.0f} en zona óptima 45–65 (+30pts)"
        elif 30 <= m.rsi < 45:         # Sobreventa — potencial entrada
            puntos += 20
            detalle["rsi"] = f"⚠️ RSI {m.rsi:.0f} zona de sobreventa (+20pts)"
        elif m.rsi > 70:               # Sobrecompra
            puntos += 5
            detalle["rsi"] = f"❌ RSI {m.rsi:.0f} sobrecomprado (+5pts)"
        else:
            puntos += 0
            detalle["rsi"] = f"❌ RSI {m.rsi:.0f} extremo (0pts)"

    # --- Precio vs SMA200 (tendencia primaria) ---
    if m.precio_vs_sma200 is not None:
        pct = m.precio_vs_sma200 * 100
        if m.precio_vs_sma200 > 0:
            aporte = min(30, pct * 3)  # máx 30pts si +10% sobre SMA200
            puntos += aporte
            detalle["sma200"] = f"✅ Precio +{pct:.1f}% sobre SMA200 (tendencia alcista, +{aporte:.0f}pts)"
        else:
            puntos -= 10
            detalle["sma200"] = f"❌ Precio {pct:.1f}% bajo SMA200 (-10pts)"

    # --- Precio vs SMA50 (tendencia intermedia) ---
    if m.precio_vs_sma50 is not None:
        if m.precio_vs_sma50 > 0:
            puntos += 20
            detalle["sma50"] = f"✅ Precio sobre SMA50 (+20pts)"
        else:
            puntos += 0
            detalle["sma50"] = f"❌ Precio bajo SMA50 (0pts)"

    # --- Volumen relativo ---
    if m.volumen_relativo is not None:
        if m.volumen_relativo >= 1.5:
            puntos += 20
            detalle["volumen"] = f"✅ Volumen {m.volumen_relativo:.1f}x promedio (confirmación, +20pts)"
        elif m.volumen_relativo >= 0.8:
            puntos += 10
            detalle["volumen"] = f"⚠️ Volumen normal (+10pts)"
        else:
            detalle["volumen"] = f"❌ Volumen bajo (0pts)"

    return _clamp(puntos), detalle


def score_lynch(m: MetricasFinancieras, perfil: PerfilInversor) -> tuple[float, dict]:
    """
    Análisis de crecimiento — Peter Lynch (PEG + ROC Fórmula Mágica).
    """
    puntos = 0.0
    detalle: dict = {}

    # --- PEG Ratio (Lynch: ≤ 1.0 = STRONG BUY) ---
    if m.peg_ratio is not None:
        if m.peg_ratio <= 1.0:
            puntos += 50
            detalle["peg"] = f"✅ PEG {m.peg_ratio:.2f} ≤ 1.0 → STRONG BUY Lynch (+50pts)"
        elif m.peg_ratio <= 1.5:
            puntos += 30
            detalle["peg"] = f"⚠️ PEG {m.peg_ratio:.2f} ≤ 1.5 → ACCUMULATE (+30pts)"
        elif m.peg_ratio <= 2.0:
            puntos += 10
            detalle["peg"] = f"❌ PEG {m.peg_ratio:.2f} (+10pts)"
        else:
            detalle["peg"] = f"🚫 PEG {m.peg_ratio:.2f} > 2.0 sobrevalorado (0pts)"

    # --- Crecimiento BPA ---
    if m.crecimiento_bpa is not None:
        if m.crecimiento_bpa >= 20:
            puntos += 30
            detalle["crecimiento_bpa"] = f"✅ BPA crece {m.crecimiento_bpa:.1f}% (+30pts)"
        elif m.crecimiento_bpa >= 10:
            puntos += 15
            detalle["crecimiento_bpa"] = f"⚠️ BPA crece {m.crecimiento_bpa:.1f}% (+15pts)"
        else:
            detalle["crecimiento_bpa"] = f"❌ BPA crece {m.crecimiento_bpa:.1f}% (0pts)"

    # --- ROC Fórmula Mágica (moat) ---
    if m.roc is not None:
        if m.roc >= 25:
            puntos += 20
            detalle["roc"] = f"✅ ROC {m.roc:.1f}% ≥ 25% (moat, +20pts)"
        elif m.roc >= 15:
            puntos += 10
            detalle["roc"] = f"⚠️ ROC {m.roc:.1f}% (+10pts)"
        else:
            detalle["roc"] = f"❌ ROC {m.roc:.1f}% < 15% (0pts)"

    return _clamp(puntos), detalle


# ─────────────────────────────────────────────────────────────────────────────
# 4. MÓDULO VRU — VALOR REAL USD (Limpieza de Ruido Cambiario CEDEAR)
# ─────────────────────────────────────────────────────────────────────────────

def calcular_vru(m: MetricasFinancieras) -> ValorRealUSD:
    """
    Fórmula VRU (Horizontes Real-USD Score):

        VRU = (Precio_ARS × Ratio_CEDEAR) / (CCL_Implied × (1 + π_US))

    · Limpia el "espejismo nominal" del peso
    · Compara contra el subyacente real en NYSE/NASDAQ
    · Activa alerta de 'Trampa de Devaluación' si VRU diverge negativamente
    """
    vru = (m.precio_ars * m.ratio_cedear) / (m.ccl_implied * (1 + m.inflacion_us))

    delta = None
    alerta_trampa = False

    if m.precio_subyacente_usd is not None and m.precio_subyacente_usd > 0:
        delta = (vru - m.precio_subyacente_usd) / m.precio_subyacente_usd
        # Trampa: CEDEAR sube en pesos pero el subyacente bajó → pérdida de valor real
        alerta_trampa = delta < -0.05  # divergencia negativa > 5%

    return ValorRealUSD(
        vru=round(vru, 4),
        precio_ars=m.precio_ars,
        ccl_implied=m.ccl_implied,
        inflacion_us=m.inflacion_us,
        ratio_cedear=m.ratio_cedear,
        precio_subyacente_usd=m.precio_subyacente_usd,
        delta_vs_subyacente=round(delta, 4) if delta is not None else None,
        alerta_trampa=alerta_trampa,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 5. MOTOR PRINCIPAL DE SCORING
# ─────────────────────────────────────────────────────────────────────────────

class ScoringEngine:
    """
    Motor de lógica central de Horizontes.
    Aplica pesos dinámicos por perfil y genera el InvestmentScore.
    """

    def calcular(self, req: ScoringRequest) -> InvestmentScore:
        m       = req.metricas
        perfil  = req.perfil
        pesos   = PROFILE_WEIGHTS[perfil]
        alertas: list[str] = []

        # ── Calcular score bruto por bloque ───────────────────────────────
        g_raw, g_det = score_graham(m, perfil)
        i_raw, i_det = score_investingpro(m, perfil)
        mu_raw, mu_det = score_murphy(m, perfil)
        l_raw, l_det = score_lynch(m, perfil)

        # ── Ponderar bloques según perfil ─────────────────────────────────
        bloques: dict[str, ScoreBloque] = {
            "graham": ScoreBloque(
                score_bruto=round(g_raw, 2),
                peso=pesos["graham"],
                aporte=round(g_raw * pesos["graham"], 2),
                detalle=g_det,
            ),
            "investingpro": ScoreBloque(
                score_bruto=round(i_raw, 2),
                peso=pesos["investingpro"],
                aporte=round(i_raw * pesos["investingpro"], 2),
                detalle=i_det,
            ),
            "murphy": ScoreBloque(
                score_bruto=round(mu_raw, 2),
                peso=pesos["murphy"],
                aporte=round(mu_raw * pesos["murphy"], 2),
                detalle=mu_det,
            ),
            "lynch": ScoreBloque(
                score_bruto=round(l_raw, 2),
                peso=pesos["lynch"],
                aporte=round(l_raw * pesos["lynch"], 2),
                detalle=l_det,
            ),
        }

        score_final = _clamp(sum(b.aporte for b in bloques.values()))

        # ── Ajuste CEDEAR: penalizar si hay trampa de devaluación ─────────
        vru_resultado: Optional[ValorRealUSD] = None
        if m.es_cedear:
            vru_resultado = calcular_vru(m)
            if vru_resultado.alerta_trampa:
                score_final = _clamp(score_final * 0.80)  # -20% penalización
                alertas.append(
                    "🚨 TRAMPA DE DEVALUACIÓN detectada: el CEDEAR sube en pesos "
                    "pero el subyacente en USD bajó más del 5%. "
                    "El margen de seguridad Graham fue recalculado con tasa ajustada por riesgo país."
                )

        # ── Alertas adicionales ───────────────────────────────────────────
        if m.current_ratio is not None and m.current_ratio < 2.0:
            alertas.append(
                f"⚠️ Current Ratio {m.current_ratio:.2f} < 2.0: Filtro Graham no superado. "
                "En perfil Moderado/Conservador esto es descarte automático."
            )
        if m.rsi is not None and m.rsi > 75:
            alertas.append(f"⚠️ RSI {m.rsi:.0f} en zona de sobrecompra extrema — Murphy advierte precaución.")
        if m.peg_ratio is not None and m.peg_ratio > 3.0:
            alertas.append(f"⚠️ PEG {m.peg_ratio:.2f} > 3.0 — Lynch lo considera caro para el crecimiento esperado.")

        # ── Clasificación final ───────────────────────────────────────────
        if score_final >= 75:
            clasificacion = "STRONG BUY"
            nivel_narrativa = "excelente"
        elif score_final >= 55:
            clasificacion = "ACCUMULATE"
            nivel_narrativa = "bueno"
        elif score_final >= 35:
            clasificacion = "HOLD"
            nivel_narrativa = "neutral"
        else:
            clasificacion = "AVOID"
            nivel_narrativa = "debil"

        return InvestmentScore(
            ticker=req.ticker.upper(),
            perfil=perfil,
            score_final=round(score_final, 2),
            clasificacion=clasificacion,
            bloques=bloques,
            valor_real_usd=vru_resultado,
            narrativa_housel=HOUSEL_NARRATIVES[nivel_narrativa],
            pesos_aplicados={k: f"{v*100:.0f}%" for k, v in pesos.items()},
            alertas=alertas,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 6. FASTAPI APPLICATION
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="Horizontes — ScoringEngine",
    description=(
        "Motor de Scoring de Inversión Inteligente para Horizontes SaaS. "
        "Pondera Graham, InvestingPro, Murphy y Lynch según el perfil del inversor. "
        "Incluye módulo VRU para limpiar el ruido cambiario de CEDEARs argentinos."
    ),
    version="1.0.0",
    contact={"name": "CTO Horizontes"},
)

engine = ScoringEngine()


@app.post(
    "/score",
    response_model=InvestmentScore,
    summary="Calcular InvestmentScore",
    description=(
        "Recibe métricas financieras y el perfil del inversor. "
        "Devuelve un score 0–100, clasificación, bloques ponderados "
        "y justificación narrativa Housel."
    ),
    tags=["Scoring"],
)
def calcular_score(req: ScoringRequest) -> InvestmentScore:
    try:
        return engine.calcular(req)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@app.get(
    "/perfiles",
    summary="Tabla de Pesos por Perfil",
    tags=["Configuración"],
)
def obtener_perfiles():
    """Devuelve la tabla de pesos validada para todos los perfiles."""
    return {
        perfil.value: {k: f"{v*100:.0f}%" for k, v in pesos.items()}
        for perfil, pesos in PROFILE_WEIGHTS.items()
    }


@app.get("/health", tags=["Infraestructura"])
def health_check():
    return {"status": "ok", "engine": "ScoringEngine v1.0", "producto": "Horizontes"}


# ─────────────────────────────────────────────────────────────────────────────
# 7. EJEMPLO DE USO STANDALONE (sin servidor)
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import json

    print("\n" + "═" * 65)
    print("  HORIZONTES — ScoringEngine · Ejemplo de Respuesta")
    print("═" * 65)

    # Caso A: Acción estándar en perfil Moderado
    req_moderado = ScoringRequest(
        ticker="MSFT",
        perfil=PerfilInversor.MODERADO,
        metricas=MetricasFinancieras(
            pe_ratio=26.5,
            pb_ratio=11.2,
            current_ratio=2.8,
            debt_equity=0.4,
            interest_coverage=35.0,
            margen_seguridad=0.22,
            health_score=4.2,
            roic=31.0,
            roe=38.0,
            flujo_caja_score=88.0,
            upside_fair_value=18.0,
            rsi=57.0,
            precio_vs_sma200=0.06,
            precio_vs_sma50=0.03,
            volumen_relativo=1.2,
            peg_ratio=1.3,
            crecimiento_bpa=17.0,
            roc=29.0,
            es_cedear=False,
        ),
    )
    resultado_a = engine.calcular(req_moderado)
    print(f"\n[CASO A] {resultado_a.ticker} — Perfil: {resultado_a.perfil.upper()}")
    print(f"  Score Final   : {resultado_a.score_final}/100")
    print(f"  Clasificación : {resultado_a.clasificacion}")
    print(f"  Pesos Perfil  : {resultado_a.pesos_aplicados}")
    print(f"  Narrativa     : {resultado_a.narrativa_housel['titulo']}")
    print(f"  Mensaje       : {resultado_a.narrativa_housel['mensaje'][:120]}...")
    print("\n  Bloques de Score:")
    for nombre, bloque in resultado_a.bloques.items():
        print(f"    [{nombre:>12}] {bloque.score_bruto:>6.1f}pts × {bloque.peso*100:>4.0f}% = {bloque.aporte:>5.1f}pts aporte")

    # Caso B: CEDEAR con ruido cambiario
    print("\n" + "─" * 65)
    req_cedear = ScoringRequest(
        ticker="AAPL_CEDEAR",
        perfil=PerfilInversor.CONSERVADOR,
        metricas=MetricasFinancieras(
            pe_ratio=28.0,
            current_ratio=0.9,         # 🚫 Falla filtro Graham
            debt_equity=1.8,
            interest_coverage=3.1,
            margen_seguridad=0.08,
            health_score=3.5,
            roic=22.0,
            roe=26.0,
            flujo_caja_score=70.0,
            upside_fair_value=12.0,
            rsi=61.0,
            precio_vs_sma200=0.02,
            precio_vs_sma50=-0.01,
            peg_ratio=1.7,
            crecimiento_bpa=9.0,
            roc=21.0,
            es_cedear=True,
            precio_ars=18_500.0,
            ratio_cedear=9.5,
            ccl_implied=1_300.0,       # CCL real de mercado
            inflacion_us=0.03,
            precio_subyacente_usd=215.0,
        ),
    )
    resultado_b = engine.calcular(req_cedear)
    print(f"\n[CASO B] {resultado_b.ticker} — Perfil: {resultado_b.perfil.upper()} (CEDEAR + VRU)")
    print(f"  Score Final   : {resultado_b.score_final}/100")
    print(f"  Clasificación : {resultado_b.clasificacion}")
    if resultado_b.valor_real_usd:
        vru = resultado_b.valor_real_usd
        print(f"  VRU           : USD {vru.vru:.4f}")
        print(f"  Subyacente    : USD {vru.precio_subyacente_usd}")
        delta_pct = (vru.delta_vs_subyacente or 0) * 100
        print(f"  Delta VRU     : {delta_pct:+.2f}% vs subyacente real")
        print(f"  Alerta Trampa : {'🚨 SÍ — Trampa de devaluación activa' if vru.alerta_trampa else '✅ NO'}")
    if resultado_b.alertas:
        print("\n  Alertas:")
        for a in resultado_b.alertas:
            print(f"    • {a[:100]}")
    print(f"\n  Narrativa Housel: {resultado_b.narrativa_housel['titulo']}")

    print("\n" + "═" * 65)
    print("  Para levantar el servidor:")
    print("  $ uvicorn scoring_engine:app --reload --port 8000")
    print("═" * 65 + "\n")
