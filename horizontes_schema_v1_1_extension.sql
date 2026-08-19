-- ═════════════════════════════════════════════════════════════════════════════
-- HORIZONTES — Extensión Schema v1.1 · Asistente de Cartera
-- ─────────────────────────────────────────────────────────────────────────────
-- Agrega dos tablas al schema base:
--   · portfolio_holdings → posiciones del usuario (con RLS)
--   · signal_events      → inbox de señales emitidas por PortfolioAnalyzer
--
-- IMPORTANTE: Ejecutar DESPUÉS de horizontes_supabase_schema.sql.
-- Asume que ya existen: auth.users, scoring_cache, investment_journal.
-- ═════════════════════════════════════════════════════════════════════════════

-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 5: portfolio_holdings — Posiciones del Usuario
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Almacena la cartera real de cada usuario. Cada fila = 1 posición.
-- Soft-delete (deleted_at) para mantener histórico sin perder integridad referencial.
-- El campo `source` permite auditar de dónde vino el dato (manual, CSV, API).
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.portfolio_holdings (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Símbolo en mayúsculas. CEDEARs llevan sufijo .BA (ej: 'GGAL.BA').
    ticker          TEXT NOT NULL,

    cantidad        NUMERIC(14, 4) NOT NULL CHECK (cantidad > 0),

    -- Precio Promedio de Compra. Moneda según `moneda`.
    -- Para CEDEARs argentinos típicamente en ARS; para US directo en USD.
    ppc             NUMERIC(14, 4) NOT NULL CHECK (ppc > 0),

    moneda          TEXT NOT NULL DEFAULT 'USD' CHECK (moneda IN ('USD', 'ARS')),

    fecha_compra    TIMESTAMPTZ,

    -- Procedencia del dato. Vital para auditar inconsistencias.
    source          TEXT NOT NULL DEFAULT 'manual' CHECK (source IN (
                        'manual',
                        'csv_iol', 'csv_ppi', 'csv_bull_market', 'csv_cocos',
                        'api_iol', 'api_ppi',
                        'api_alpaca', 'api_ibkr'
                    )),

    notes           TEXT,

    -- Audit
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    -- Soft-delete: marcamos en lugar de borrar para mantener journal coherente.
    deleted_at      TIMESTAMPTZ
);

COMMENT ON TABLE public.portfolio_holdings IS
    'Posiciones reales del usuario. Soft-delete preserva histórico para investment_journal.';

CREATE INDEX IF NOT EXISTS idx_portfolio_user_active
    ON public.portfolio_holdings (user_id)
    WHERE deleted_at IS NULL;

CREATE INDEX IF NOT EXISTS idx_portfolio_ticker
    ON public.portfolio_holdings (ticker);

-- Trigger: actualiza updated_at automáticamente.
CREATE OR REPLACE FUNCTION public.touch_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_portfolio_touch ON public.portfolio_holdings;
CREATE TRIGGER trg_portfolio_touch
    BEFORE UPDATE ON public.portfolio_holdings
    FOR EACH ROW EXECUTE FUNCTION public.touch_updated_at();

-- ─── Row Level Security ─────────────────────────────────────────────────────
-- Cada usuario solo ve y modifica sus propias posiciones.
ALTER TABLE public.portfolio_holdings ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users see only their holdings" ON public.portfolio_holdings;
CREATE POLICY "Users see only their holdings"
    ON public.portfolio_holdings
    FOR SELECT TO authenticated
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users insert their own holdings" ON public.portfolio_holdings;
CREATE POLICY "Users insert their own holdings"
    ON public.portfolio_holdings
    FOR INSERT TO authenticated
    WITH CHECK (auth.uid() = user_id);

DROP POLICY IF EXISTS "Users update their own holdings" ON public.portfolio_holdings;
CREATE POLICY "Users update their own holdings"
    ON public.portfolio_holdings
    FOR UPDATE TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);


-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 6: signal_events — Inbox de Señales del PortfolioAnalyzer
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Cada vez que el motor cruza Value × Technical y emite una señal,
-- queda registrada acá. El frontend consume esta tabla con Supabase Realtime
-- para mostrar el inbox en vivo. La unicidad por (user_id, ticker, signal_type)
-- evita spam: si la misma señal sigue vigente, se actualiza en lugar de duplicar.
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.signal_events (
    id                    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id               UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    ticker                TEXT NOT NULL,
    perfil_inversor       TEXT NOT NULL CHECK (perfil_inversor IN (
                              'liquidez_plus', 'conservador', 'moderado', 'agresivo'
                          )),

    -- Tipo de señal emitida.
    signal_type           TEXT NOT NULL CHECK (signal_type IN (
                              'strong_buy',
                              'accumulate',
                              'wait_pullback',
                              'hold',
                              'reduce',
                              'sell',
                              'anti_panic',     -- ← señal diferenciadora del producto
                              'initiate'
                          )),

    -- Convicción 0-100 (peso del bloque dominante × claridad técnica).
    conviction            NUMERIC(5, 2) NOT NULL CHECK (conviction BETWEEN 0 AND 100),

    -- Texto que aparece en la card del inbox (1 frase).
    razon_corta           TEXT NOT NULL,

    -- Narrativa Housel para el detalle (la cápsula).
    razon_larga           TEXT NOT NULL,

    -- Acción sugerida en lenguaje natural.
    accion_sugerida       TEXT NOT NULL,

    -- ── Snapshot de los datos que dispararon la señal (auditoría) ─────
    score_final           NUMERIC(5, 2) NOT NULL,
    score_clasificacion   TEXT NOT NULL,
    rsi_snapshot          NUMERIC(5, 2),
    precio_vs_sma200      NUMERIC(7, 4),
    bloque_dominante      TEXT,

    -- Metadata
    is_in_portfolio       BOOLEAN NOT NULL DEFAULT TRUE,
    portfolio_weight      NUMERIC(5, 2),

    -- ── Estado del usuario respecto a la señal ────────────────────────
    -- Permite que el user marque qué hizo con la señal (cierra el loop UX).
    user_action           TEXT CHECK (user_action IN (
                              'pending',           -- recién emitida
                              'executed',          -- el user la ejecutó en su broker
                              'snoozed',           -- pospuesta (típicamente 7 días)
                              'ignored',           -- el user decidió no actuar
                              'expired'            -- expirada por TTL sin acción
                          )) DEFAULT 'pending',

    user_action_at        TIMESTAMPTZ,
    user_thesis           TEXT,                    -- la tesis que escribió el user en el cooling step

    -- ── Vigencia ──────────────────────────────────────────────────────
    emitted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at            TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '72 hours'),

    -- Una señal vigente del mismo tipo por par (user, ticker).
    UNIQUE (user_id, ticker, signal_type)
);

COMMENT ON TABLE public.signal_events IS
    'Inbox de señales emitidas por PortfolioAnalyzer. Realtime al frontend.';
COMMENT ON COLUMN public.signal_events.signal_type IS
    'anti_panic es la señal diferenciadora: AVOID + RSI sobreventa NO dispara venta.';
COMMENT ON COLUMN public.signal_events.user_thesis IS 'Texto libre que el user escribe en el cooling step antes de actuar. Se replica también en investment_journal vinculado por signal_event_id.';

CREATE INDEX IF NOT EXISTS idx_signal_user_pending
    ON public.signal_events (user_id, emitted_at DESC)
    WHERE user_action = 'pending';

CREATE INDEX IF NOT EXISTS idx_signal_expires
    ON public.signal_events (expires_at)
    WHERE user_action = 'pending';

-- ─── Row Level Security ─────────────────────────────────────────────────────
ALTER TABLE public.signal_events ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Users see only their signals" ON public.signal_events;
CREATE POLICY "Users see only their signals"
    ON public.signal_events
    FOR SELECT TO authenticated
    USING (auth.uid() = user_id);

DROP POLICY IF EXISTS "Service role inserts signals" ON public.signal_events;
CREATE POLICY "Service role inserts signals"
    ON public.signal_events
    FOR INSERT TO service_role
    WITH CHECK (TRUE);

DROP POLICY IF EXISTS "Users update only their action state" ON public.signal_events;
CREATE POLICY "Users update only their action state"
    ON public.signal_events
    FOR UPDATE TO authenticated
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);


-- ═════════════════════════════════════════════════════════════════════════════
-- VINCULO: signal_events ↔ investment_journal
-- ─────────────────────────────────────────────────────────────────────────────
-- Cuando el usuario marca una señal como 'executed' y escribe su tesis, esa
-- entrada se replica al investment_journal para preservar el "diario de a bordo".
-- Esto cierra el loop conductual: cada acción ejecutada queda con su porqué.
-- ═════════════════════════════════════════════════════════════════════════════

ALTER TABLE public.investment_journal
    ADD COLUMN IF NOT EXISTS signal_event_id UUID REFERENCES public.signal_events(id);

CREATE INDEX IF NOT EXISTS idx_journal_signal
    ON public.investment_journal (signal_event_id)
    WHERE signal_event_id IS NOT NULL;


-- ═════════════════════════════════════════════════════════════════════════════
-- HOUSEKEEPING: limpieza de señales expiradas (cron job sugerido)
-- ─────────────────────────────────────────────────────────────────────────────
-- Marcar como 'expired' las señales pendientes que pasaron su expires_at.
-- Ejecutar via Supabase pg_cron o Edge Function diaria.
-- ═════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.expire_stale_signals()
RETURNS INTEGER AS $$
DECLARE
    n INTEGER;
BEGIN
    UPDATE public.signal_events
    SET    user_action    = 'expired',
           user_action_at = NOW()
    WHERE  user_action = 'pending'
      AND  expires_at  < NOW();
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END;
$$ LANGUAGE plpgsql;

COMMENT ON FUNCTION public.expire_stale_signals IS
    'Marca señales pendientes vencidas. Schedule diario vía pg_cron.';
