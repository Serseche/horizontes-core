"""
╔══════════════════════════════════════════════════════════════╗
║         HORIZONTES — PortfolioAnalyzer v1.0                  ║
║         Motor de Recomendaciones · Asistente de Cartera      ║
║         Cruza Value × Technical para emitir Señales Advisory ║
╠══════════════════════════════════════════════════════════════╣
║  Responsabilidades                                           ║
║   · Cargar holdings del usuario desde Supabase               ║
║   · Cruzar cada holding contra scoring_cache (último score)  ║
║   · Aplicar la matriz de reglas Value × Technical            ║
║   · Emitir SignalEvent con conviction_score y narrativa      ║
║   · Persistir en signal_events para realtime al frontend     ║
╠══════════════════════════════════════════════════════════════╣
║  Integración                                                 ║
║   from portfolio_analyzer import PortfolioAnalyzer           ║
║   analyzer = PortfolioAnalyzer.from_env()                    ║
║   signals = analyzer.analyze_portfolio(user_id="abc-123...")  ║
║   for sig in signals:                                        ║
║       print(sig.ticker, sig.signal_type, sig.conviction)     ║
╚══════════════════════════════════════════════════════════════╝

Diseñado para ejecutarse:
  · Ad-hoc cuando el user abre el dashboard
  · En batch nocturno (cron Edge Function dispara → POST aquí)
  · Reactivo a webhooks (Bluelytics CCL salta >3% → re-análisis CEDEARs)
"""

from __future__ import annotations

from dotenv import load_dotenv
load_dotenv()

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field, field_validator

# Reusa los modelos del ScoringEngine ya implementados
from scoring_engine import (
    InvestmentScore,
    PerfilInversor,
    MetricasFinancieras,
)

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

log = logging.getLogger("PortfolioAnalyzer")


# ─────────────────────────────────────────────────────────────────────────────
# 1. ENUMS Y CONSTANTES
# ─────────────────────────────────────────────────────────────────────────────

class SignalType(str, Enum):
    """
    Tipos de señales que el sistema puede emitir.

    NOTA: ANTI_PANIC es la señal más diferenciadora del producto.
    Se dispara cuando los fundamentos son débiles PERO el técnico está
    en sobreventa extrema. En ese caso el sistema NO recomienda vender —
    recomienda esperar y aprende del KPI de retención post-alerta.
    """
    STRONG_BUY        = "strong_buy"        # Compra agresiva
    ACCUMULATE        = "accumulate"        # Acumular posición
    WAIT_PULLBACK     = "wait_pullback"     # Mantener pero NO ampliar
    HOLD              = "hold"              # Mantener
    REDUCE            = "reduce"            # Vender parcialmente
    SELL              = "sell"              # Venta total sugerida
    ANTI_PANIC        = "anti_panic"        # NO vender en pánico (Housel)
    INITIATE          = "initiate"          # Activo no en cartera, abrir posición


class HoldingSource(str, Enum):
    """Procedencia del dato del holding (auditoría)."""
    MANUAL    = "manual"
    CSV_IOL   = "csv_iol"
    CSV_PPI   = "csv_ppi"
    CSV_BULL  = "csv_bull_market"
    CSV_COCOS = "csv_cocos"
    API_IOL   = "api_iol"
    API_PPI   = "api_ppi"
    API_ALPACA = "api_alpaca"
    API_IBKR  = "api_ibkr"


# Umbrales del scoring (deben coincidir con ScoringEngine.calcular)
SCORE_STRONG_BUY = 75.0
SCORE_ACCUMULATE = 55.0
SCORE_HOLD       = 35.0

# Umbrales técnicos
RSI_OVERSOLD_EXTREME = 30.0
RSI_OVERSOLD         = 35.0
RSI_NEUTRAL_LOW      = 50.0
RSI_OVERBOUGHT       = 65.0
RSI_OVERBOUGHT_EXTREME = 75.0

# Cuánto tiene que estar el precio sobre SMA200 para considerarse "estirado"
PRICE_VS_SMA200_STRETCHED = 0.15  # +15%


# ─────────────────────────────────────────────────────────────────────────────
# 2. MODELOS DE DATOS
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioHolding(BaseModel):
    """
    Una posición en la cartera del usuario.

    Mapea 1:1 a la tabla `portfolio_holdings` en Supabase.
    """
    id:           Optional[str]      = Field(None, description="UUID generado por Supabase")
    user_id:      str                = Field(..., description="UUID del user (FK a auth.users)")
    ticker:       str                = Field(..., description="Ej: 'AAPL', 'GGAL.BA'")
    cantidad:     float              = Field(..., gt=0, description="Nominales")
    ppc:          float              = Field(..., gt=0, description="Precio Promedio de Compra (USD o ARS según mercado)")
    moneda:       str                = Field("USD", description="USD o ARS")
    fecha_compra: Optional[datetime] = None
    source:       HoldingSource      = HoldingSource.MANUAL
    notes:        Optional[str]      = None

    @field_validator("ticker")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.strip().upper()

    @property
    def es_cedear(self) -> bool:
        return self.ticker.endswith(".BA")


class SignalEvent(BaseModel):
    """
    Evento de señal emitido por el motor.

    Mapea 1:1 a la tabla `signal_events`. Se sirve al frontend
    con realtime en el inbox del usuario.
    """
    user_id:        str
    ticker:         str
    perfil:         PerfilInversor
    signal_type:    SignalType
    conviction:     float = Field(..., ge=0, le=100, description="Convicción 0-100")

    # Razón en una frase (lo que aparece en la card)
    razon_corta:    str

    # Narrativa Housel (lo que aparece en el detalle)
    razon_larga:    str

    # Acción sugerida en texto: '−50% posición', '+5% posición', 'No actuar', etc.
    accion_sugerida: str

    # Snapshot de los datos clave que dispararon la señal (para auditoría)
    score_final:        float
    score_clasificacion: str
    rsi_snapshot:       Optional[float] = None
    precio_vs_sma200:   Optional[float] = None
    bloque_dominante:   Optional[str]   = None

    # Metadata
    is_in_portfolio:    bool       = True
    portfolio_weight:   Optional[float] = None
    emitted_at:         datetime   = Field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class SignalContext:
    """Contexto interno que el clasificador recibe para decidir."""
    score:           InvestmentScore
    metricas:        MetricasFinancieras
    holding:         Optional[PortfolioHolding]
    portfolio_value: float


# ─────────────────────────────────────────────────────────────────────────────
# 3. MATRIZ DE REGLAS — Value × Technical
# ─────────────────────────────────────────────────────────────────────────────

class SignalClassifier:
    """
    Aplica la matriz de reglas para decidir el tipo y convicción de la señal.

    La matriz vive aquí en código (no en DB) porque:
      · Es la lógica de negocio core del producto
      · Cambiarla requiere validación QA (5/5 canary tickers)
      · No queremos race conditions con escrituras en runtime
    """

    @staticmethod
    def classify(ctx: SignalContext) -> Optional[SignalEvent]:
        """
        Devuelve un SignalEvent si la combinación de score + técnico cumple
        algún criterio accionable. Si no, devuelve None (no genera ruido).
        """
        score = ctx.score
        m = ctx.metricas
        rsi = m.rsi
        psma = m.precio_vs_sma200
        in_pf = ctx.holding is not None

        # Bloque dominante = el que más aportó al score final
        bloque_dom = SignalClassifier._bloque_dominante(score)

        sf = score.score_final

        # ─── REGLA 1: STRONG BUY + sobreventa = COMPRA AGRESIVA ────────────
        if sf >= SCORE_STRONG_BUY and rsi is not None and rsi < RSI_OVERSOLD:
            if psma is not None and psma < 0:  # bajo SMA200, máximo descuento
                return SignalClassifier._build(
                    ctx,
                    SignalType.STRONG_BUY,
                    conviction=95,
                    razon_corta=(
                        f"STRONG BUY {sf:.1f} + RSI {rsi:.0f} (sobreventa) + "
                        f"precio {psma*100:.1f}% bajo SMA200. Setup óptimo."
                    ),
                    razon_larga=(
                        "Cuando los fundamentos son sólidos y el mercado castiga el precio "
                        "por miedo, los grandes inversores entran. Es exactamente el momento "
                        "que Graham describía: un margen de seguridad creado por la irracionalidad "
                        "del mercado, no por la debilidad del activo."
                    ),
                    accion_sugerida=("iniciar 8-10% cart." if not in_pf else "+5% posición"),
                    bloque_dominante=bloque_dom,
                )

        # ─── REGLA 2: ACCUMULATE + RSI moderado ────────────────────────────
        if SCORE_ACCUMULATE <= sf < SCORE_STRONG_BUY:
            if rsi is not None and rsi < RSI_NEUTRAL_LOW:
                return SignalClassifier._build(
                    ctx,
                    SignalType.ACCUMULATE,
                    conviction=75,
                    razon_corta=(
                        f"ACCUMULATE {sf:.1f} + RSI {rsi:.0f} + tendencia plana. "
                        f"Bloque dominante {bloque_dom}."
                    ),
                    razon_larga=(
                        "El activo paga la 'tarifa de admisión' correcta para tu horizonte. "
                        "La volatilidad que puedas ver en el camino no es una multa — es el "
                        "precio de entrada a retornos superiores. La paciencia es tu ventaja."
                    ),
                    accion_sugerida=("iniciar 5-8% cart." if not in_pf else "+5% posición"),
                    bloque_dominante=bloque_dom,
                )

            # ─── REGLA 3: ACCUMULATE pero técnico estirado = ESPERAR ───────
            if (rsi is not None and rsi > RSI_OVERBOUGHT
                and psma is not None and psma > PRICE_VS_SMA200_STRETCHED):
                return SignalClassifier._build(
                    ctx,
                    SignalType.WAIT_PULLBACK,
                    conviction=60,
                    razon_corta=(
                        f"ACCUMULATE {sf:.1f} pero RSI {rsi:.0f} + precio "
                        f"{psma*100:.0f}% sobre SMA200. Esperar pullback."
                    ),
                    razon_larga=(
                        "Los fundamentos siguen siendo válidos, pero el momentum técnico está "
                        "agotado en el corto plazo. Comprar acá es pagar por euforia. "
                        "Murphy advierte: el precio siempre vuelve a la media — esperá."
                    ),
                    accion_sugerida=("Esperar pullback al SMA50" if not in_pf else "No ampliar, mantener"),
                    bloque_dominante=bloque_dom,
                )

        # ─── REGLA 4: AVOID + sobrecompra = VENDER ─────────────────────────
        if sf < SCORE_HOLD and rsi is not None and rsi > RSI_OVERBOUGHT:
            if not in_pf:
                return None  # No vendemos lo que no tenemos
            severidad = 90 if rsi > RSI_OVERBOUGHT_EXTREME else 78
            return SignalClassifier._build(
                ctx,
                SignalType.SELL if severidad >= 90 else SignalType.REDUCE,
                conviction=severidad,
                razon_corta=(
                    f"AVOID {sf:.1f} + RSI {rsi:.0f}"
                    + (f" + precio {psma*100:.0f}% sobre SMA200" if psma and psma > 0.10 else "")
                    + ". El descuento ya se evaporó."
                ),
                razon_larga=(
                    "La paciencia que te llevó a entrar cuando todos vendían es la misma virtud "
                    "que ahora te pide salir parcialmente. Vender por convicción no es lo mismo "
                    "que vender por miedo — y tu tesis original ya cumplió su trabajo."
                ),
                accion_sugerida=("−50% posición" if severidad < 90 else "−100% posición"),
                bloque_dominante=bloque_dom,
            )

        # ─── REGLA 5: AVOID + SOBREVENTA EXTREMA = ANTI-PÁNICO ─────────────
        # ESTA ES LA REGLA DIFERENCIADORA DEL PRODUCTO
        if sf < SCORE_HOLD and rsi is not None and rsi < RSI_OVERSOLD_EXTREME:
            if not in_pf:
                return None  # Si no lo tenemos, no aplica el anti-pánico
            return SignalClassifier._build(
                ctx,
                SignalType.ANTI_PANIC,
                conviction=70,
                razon_corta=(
                    f"AVOID {sf:.1f} + RSI {rsi:.0f} (sobreventa extrema). "
                    f"NO es señal de venta — es trampa de Kahneman."
                ),
                razon_larga=(
                    "Vender en pánico cristaliza la pérdida sin garantizar evitar la próxima. "
                    "Si entraste por una tesis específica (dividendos, defensiva, exposición sectorial), "
                    "esa tesis sigue intacta. El mercado está descontando miedo, no fundamentos. "
                    "La aversión a la pérdida de Kahneman te empuja a actuar — el sistema te "
                    "pide que esperes a un nuevo análisis fundamental antes de decidir."
                ),
                accion_sugerida="No actuar. Esperar nuevo análisis fundamental.",
                bloque_dominante=bloque_dom,
            )

        # ─── REGLA 6: HOLD genérico (si está en cartera) ──────────────────
        if SCORE_HOLD <= sf < SCORE_ACCUMULATE and in_pf:
            return SignalClassifier._build(
                ctx,
                SignalType.HOLD,
                conviction=40,
                razon_corta=f"HOLD {sf:.1f}. Sin catalizadores claros para actuar.",
                razon_larga=(
                    "El activo está en transición. No hay urgencia en decidir. Mantener posición "
                    "actual es la decisión correcta hasta nueva confirmación de fundamentales o técnico."
                ),
                accion_sugerida="Mantener. Revisar en 30 días.",
                bloque_dominante=bloque_dom,
            )

        return None

    # ── helpers ──────────────────────────────────────────────────────────
    @staticmethod
    def _bloque_dominante(score: InvestmentScore) -> str:
        """Devuelve el nombre del bloque que más aportó al score final."""
        aportes = {nombre: bloque.aporte for nombre, bloque in score.bloques.items()}
        return max(aportes, key=aportes.get).capitalize() if aportes else "n/a"

    @staticmethod
    def _build(
        ctx: SignalContext,
        signal_type: SignalType,
        conviction: float,
        razon_corta: str,
        razon_larga: str,
        accion_sugerida: str,
        bloque_dominante: str,
    ) -> SignalEvent:
        weight = None
        if ctx.holding and ctx.portfolio_value > 0:
            holding_value = ctx.holding.cantidad * ctx.holding.ppc
            weight = round((holding_value / ctx.portfolio_value) * 100, 2)

        return SignalEvent(
            user_id          = ctx.holding.user_id if ctx.holding else "",
            ticker           = ctx.score.ticker,
            perfil           = ctx.score.perfil,
            signal_type      = signal_type,
            conviction       = conviction,
            razon_corta      = razon_corta,
            razon_larga      = razon_larga,
            accion_sugerida  = accion_sugerida,
            score_final      = ctx.score.score_final,
            score_clasificacion = ctx.score.clasificacion,
            rsi_snapshot     = ctx.metricas.rsi,
            precio_vs_sma200 = ctx.metricas.precio_vs_sma200,
            bloque_dominante = bloque_dominante,
            is_in_portfolio  = ctx.holding is not None,
            portfolio_weight = weight,
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4. PORTFOLIO ANALYZER — orquestador
# ─────────────────────────────────────────────────────────────────────────────

class PortfolioAnalyzer:
    """
    Punto de entrada de alto nivel.

    Uso típico:
        analyzer = PortfolioAnalyzer.from_env()
        signals  = analyzer.analyze_portfolio(user_id="abc-123")
        analyzer.persist_signals(signals)
    """

    def __init__(
        self,
        pipeline,                # CachedScoringPipeline (de DataFetcher.py)
        supabase_client = None,  # supabase.Client | None
    ):
        self.pipeline = pipeline
        self.sb       = supabase_client

    # ── Constructores ────────────────────────────────────────────────────
    @classmethod
    def from_env(cls) -> "PortfolioAnalyzer":
        """Lee SUPABASE_URL/KEY del entorno y arma todo."""
        from DataFetcher import CachedScoringPipeline, SupabaseConfig
        from supabase import create_client

        pipeline = CachedScoringPipeline.from_env()
        sb_cfg   = SupabaseConfig.from_env()
        client   = create_client(sb_cfg.url, sb_cfg.service_role_key)
        return cls(pipeline=pipeline, supabase_client=client)

    # ── Carga de holdings ────────────────────────────────────────────────
    def load_holdings(self, user_id: str) -> list[PortfolioHolding]:
        """Lee los holdings activos del user desde Supabase."""
        if self.sb is None:
            raise RuntimeError("Supabase client no configurado.")

        rows = (
            self.sb.table("portfolio_holdings")
            .select("*")
            .eq("user_id", user_id)
            .is_("deleted_at", "null")
            .execute()
            .data
            or []
        )
        return [PortfolioHolding(**row) for row in rows]

    def get_user_perfil(self, user_id: str) -> PerfilInversor:
        """Lee el perfil del user (de la tabla user_profiles)."""
        if self.sb is None:
            return PerfilInversor.MODERADO  # fallback seguro

        row = (
            self.sb.table("user_profiles")
            .select("perfil_inversor")
            .eq("user_id", user_id)
            .single()
            .execute()
            .data
        )
        if row and row.get("perfil_inversor"):
            return PerfilInversor(row["perfil_inversor"])
        return PerfilInversor.MODERADO

    # ── Análisis principal ───────────────────────────────────────────────
    def analyze_portfolio(
        self,
        user_id: str,
        watchlist: Optional[list[str]] = None,
    ) -> list[SignalEvent]:
        """
        Pipeline completo:
          1. Lee holdings + perfil
          2. Para cada ticker (holdings + watchlist) corre scoring (con caché)
          3. Cruza con regla Value × Technical
          4. Devuelve lista de SignalEvents accionables

        Args:
            user_id:   UUID del user autenticado
            watchlist: Tickers extra a analizar (no necesariamente en cartera)

        Returns:
            Lista de SignalEvent ordenada por convicción descendente.
        """
        log.info("[Analyzer] Inicio análisis para user=%s", user_id)

        holdings = self.load_holdings(user_id)
        perfil   = self.get_user_perfil(user_id)
        watchlist = watchlist or []

        # Map ticker → holding (rápido lookup)
        holdings_by_ticker = {h.ticker: h for h in holdings}

        # Universe de tickers a analizar
        universe = list({*holdings_by_ticker.keys(), *(t.upper() for t in watchlist)})

        # Valor total de la cartera (a precio de PPC, simplificación)
        portfolio_value = sum(h.cantidad * h.ppc for h in holdings)

        signals: list[SignalEvent] = []

        for ticker in universe:
            try:
                score, was_cached = self.pipeline.get_score(
                    ticker  = ticker,
                    perfil  = perfil,
                    user_id = user_id,
                )
                # Recuperar las métricas (rsi/sma) para el clasificador.
                # fetch() es async — se usa asyncio.run() igual que en get_score().
                # El event loop de get_score() ya cerró antes de llegar acá.
                import asyncio
                metricas = asyncio.run(self.pipeline.fetcher.fetch(ticker))

                ctx = SignalContext(
                    score           = score,
                    metricas        = metricas,
                    holding         = holdings_by_ticker.get(ticker),
                    portfolio_value = portfolio_value,
                )
                event = SignalClassifier.classify(ctx)
                if event is not None:
                    # Forzar el user_id por si el holding era None (caso watchlist)
                    event.user_id = user_id
                    signals.append(event)
                    log.info(
                        "[Analyzer] %s → %s (conv=%.0f) cached=%s",
                        ticker, event.signal_type.value, event.conviction, was_cached,
                    )
            except Exception as exc:  # nunca bloquees el análisis por un ticker
                log.warning("[Analyzer] %s falló: %s", ticker, exc)
                continue

        # Ordenar por convicción descendente
        signals.sort(key=lambda s: s.conviction, reverse=True)
        log.info("[Analyzer] %d señales emitidas para user=%s", len(signals), user_id)
        return signals

    # ── Persistencia ─────────────────────────────────────────────────────
    def persist_signals(self, signals: list[SignalEvent]) -> int:
        """
        Guarda los SignalEvents en la tabla signal_events.

        Estrategia: upsert por (user_id, ticker, signal_type) con ventana de 24h.
        Si el mismo tipo de señal ya existe en las últimas 24h para ese par,
        se actualiza en lugar de crear duplicado (evita spam al inbox).
        """
        if self.sb is None or not signals:
            return 0

        rows = [
            {
                "user_id":             s.user_id,
                "ticker":              s.ticker,
                "perfil_inversor":     s.perfil.value,
                "signal_type":         s.signal_type.value,
                "conviction":          s.conviction,
                "razon_corta":         s.razon_corta,
                "razon_larga":         s.razon_larga,
                "accion_sugerida":     s.accion_sugerida,
                "score_final":         s.score_final,
                "score_clasificacion": s.score_clasificacion,
                "rsi_snapshot":        s.rsi_snapshot,
                "precio_vs_sma200":    s.precio_vs_sma200,
                "bloque_dominante":    s.bloque_dominante,
                "is_in_portfolio":     s.is_in_portfolio,
                "portfolio_weight":    s.portfolio_weight,
                "emitted_at":          s.emitted_at.isoformat(),
            }
            for s in signals
        ]
        result = (
            self.sb.table("signal_events")
            .upsert(rows, on_conflict="user_id,ticker,signal_type")
            .execute()
        )
        n = len(result.data or [])
        log.info("[Analyzer] %d señales persistidas en signal_events", n)
        return n


# ─────────────────────────────────────────────────────────────────────────────
# 5. CLI / TESTING
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    """
    Test rápido sin Supabase. Útil para validar la matriz de reglas.
    Corré: python portfolio_analyzer.py
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    from scoring_engine import ScoreBloque

    print("\n" + "═" * 65)
    print("  HORIZONTES — PortfolioAnalyzer · Test de Matriz de Reglas")
    print("═" * 65 + "\n")

    # Caso simulado: TSLA con AVOID + RSI sobrecompra (debe disparar SELL)
    score_tsla = InvestmentScore(
        ticker="TSLA",
        perfil=PerfilInversor.MODERADO,
        score_final=32.4,
        clasificacion="AVOID",
        bloques={
            "graham":       ScoreBloque(score_bruto=28, peso=0.45, aporte=12.6, detalle={}),
            "investingpro": ScoreBloque(score_bruto=52, peso=0.25, aporte=13.0, detalle={}),
            "lynch":        ScoreBloque(score_bruto=35, peso=0.20, aporte=7.0,  detalle={}),
            "murphy":       ScoreBloque(score_bruto=74, peso=0.10, aporte=7.4,  detalle={}),
        },
        valor_real_usd=None,
        narrativa_housel={"titulo": "test", "mensaje": "test"},
        pesos_aplicados={},
        alertas=[],
    )
    metricas_tsla = MetricasFinancieras(
        pe_ratio=60.0, peg_ratio=3.5, current_ratio=1.8, debt_equity=0.3,
        rsi=68.0, precio_vs_sma200=0.18, precio_vs_sma50=0.06,
        roic=15.0, margen_seguridad=0.0, es_cedear=False,
    )
    holding_tsla = PortfolioHolding(
        user_id="test-uuid", ticker="TSLA",
        cantidad=14, ppc=398.20, source=HoldingSource.MANUAL,
    )

    ctx = SignalContext(
        score=score_tsla,
        metricas=metricas_tsla,
        holding=holding_tsla,
        portfolio_value=48210,
    )
    signal = SignalClassifier.classify(ctx)

    if signal:
        print(f"  Señal disparada : {signal.signal_type.value.upper()}")
        print(f"  Convicción      : {signal.conviction:.0f}/100")
        print(f"  Razón corta     : {signal.razon_corta}")
        print(f"  Acción sugerida : {signal.accion_sugerida}")
        print(f"  Bloque dominante: {signal.bloque_dominante}")
        print(f"  En cartera      : {signal.is_in_portfolio}")
        print(f"  Peso cartera    : {signal.portfolio_weight}%")
    else:
        print("  Sin señal emitida.")

    print("\n" + "─" * 65)
    print("  Validación esperada: SELL/REDUCE con convicción 78-90")
    print("─" * 65 + "\n")
