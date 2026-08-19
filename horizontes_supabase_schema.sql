-- ╔══════════════════════════════════════════════════════════════════════════╗
-- ║          HORIZONTES SaaS — Schema Completo para Supabase               ║
-- ║          Ingeniero de Datos Senior · PostgreSQL + RLS                  ║
-- ║          Versión 1.0 · Listo para copiar y pegar en el SQL Editor      ║
-- ╚══════════════════════════════════════════════════════════════════════════╝
--
-- INSTRUCCIONES:
--   1. Abrí el SQL Editor en tu proyecto de Supabase.
--   2. Pegá TODO este script y ejecutalo con el botón "Run".
--   3. Verificá en "Table Editor" que las 5 tablas aparecen correctamente.
--   4. Las políticas RLS se activan automáticamente.
-- ─────────────────────────────────────────────────────────────────────────────


-- ═════════════════════════════════════════════════════════════════════════════
-- PASO 0: EXTENSIONES NECESARIAS
-- ═════════════════════════════════════════════════════════════════════════════

-- uuid-ossp genera IDs únicos automáticos para cada fila.
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 1: user_profiles — Perfil del Inversor
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Es la "ficha de identidad" de cada usuario dentro de Horizontes.
-- Guarda en qué país opera (para aplicar reglas específicas de CEDEARs en
-- Argentina, o el contexto fiscal de EE.UU.), qué perfil de riesgo eligió
-- (Conservador, Moderado, etc.), y cuándo fue el onboarding.
-- El campo `user_id` es el mismo ID que Supabase Auth crea automáticamente.
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.user_profiles (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- FK a la tabla de autenticación nativa de Supabase. ON DELETE CASCADE
    -- asegura que si el usuario borra su cuenta, sus datos desaparecen también.
    user_id         UUID NOT NULL UNIQUE REFERENCES auth.users(id) ON DELETE CASCADE,

    -- País donde opera el inversor. Determina las reglas de CEDEAR (AR),
    -- conversión de divisas o contexto regulatorio.
    pais            TEXT NOT NULL CHECK (pais IN ('AR', 'MX', 'US')),

    -- Perfil de inversión, alineado con los 4 perfiles del ScoringEngine.
    -- Cada perfil tiene una tabla de pesos distinta (Graham, Lynch, Murphy, InvestingPro).
    perfil_inversor TEXT NOT NULL CHECK (perfil_inversor IN (
                        'liquidez_plus',   -- Técnico puro, horizonte < 1 año
                        'conservador',     -- 1-2 años, Graham dominante (40%)
                        'moderado',        -- 3-7 años, reversión a la media
                        'agresivo'         -- 10+ años, moat y crecimiento Lynch
                    )),

    -- Nombre visible que el usuario quiere usar en la app.
    nombre_display  TEXT,

    -- Moneda base preferida para mostrar los valores (USD, ARS, MXN).
    moneda_base     TEXT NOT NULL DEFAULT 'USD' CHECK (moneda_base IN ('USD', 'ARS', 'MXN')),

    -- Fecha en que el usuario completó el test de idoneidad / onboarding.
    onboarding_at   TIMESTAMPTZ,

    -- Auditoría estándar.
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.user_profiles IS
    'Ficha de identidad del inversor en Horizontes. Define país, perfil de riesgo y moneda base.';
COMMENT ON COLUMN public.user_profiles.pais IS
    'AR=Argentina (lógica CEDEAR+CCL activa), MX=México, US=Estados Unidos.';
COMMENT ON COLUMN public.user_profiles.perfil_inversor IS
    'Mapea directamente a PROFILE_WEIGHTS del ScoringEngine v1.0.';


-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 2: kids_saver_goals — Metas Familiares (Kids Saver)
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Almacena cada "sueño con fecha" que el usuario quiere financiar:
-- la universidad del hijo, el primer departamento, el auto, etc.
-- Guarda el monto objetivo en USD (así sobrevive a devaluaciones), la fecha
-- límite, y el depósito mensual comprometido. También tiene los parámetros
-- del portafolio Core-Satellite (90% ETFs Bogle + 10% acciones Lynch).
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.kids_saver_goals (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- A qué usuario pertenece esta meta.
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Nombre humano de la meta, ej: "Universidad de Martín", "Depto primer hogar".
    nombre_meta         TEXT NOT NULL,

    -- Descripción libre y emotiva de la meta (refuerza el anclaje conductual Housel).
    descripcion         TEXT,

    -- Monto objetivo expresado siempre en USD para blindarlo de devaluaciones.
    monto_objetivo_usd  NUMERIC(14, 2) NOT NULL CHECK (monto_objetivo_usd > 0),

    -- Capital ya acumulado en esta meta (se actualiza en cada aporte).
    capital_actual_usd  NUMERIC(14, 2) NOT NULL DEFAULT 0.00,

    -- Aporte mensual comprometido (compatible con protocolo SMarT).
    deposito_mensual_usd NUMERIC(10, 2) CHECK (deposito_mensual_usd >= 0),

    -- Porcentaje de aumento automático anual del depósito (protocolo SMarT).
    incremento_anual_pct NUMERIC(5, 2) DEFAULT 0.00,

    -- Fecha límite para alcanzar la meta.
    fecha_limite        DATE NOT NULL,

    -- Tipo de meta para la UI (icono y visualización adaptada).
    tipo_meta           TEXT DEFAULT 'educacion' CHECK (tipo_meta IN (
                            'educacion',    -- Universidad, colegio
                            'vivienda',     -- Primer depto, casa
                            'negocio',      -- Emprendimiento del heredero
                            'emergencia',   -- Fondo de reserva familiar
                            'otro'
                        )),

    -- Tickers de las acciones Lynch "Ten-bagger" elegidas para el 10% Satélite.
    -- Guardado como array de texto, ej: ['NVDA', 'AMD', 'TSLA']
    acciones_lynch      TEXT[] DEFAULT '{}',

    -- ETF principal del Núcleo (90% Bogle), ej: 'SPY', 'VT', 'QQQ'.
    etf_core            TEXT DEFAULT 'SPY',

    -- Resultado de la última simulación Monte Carlo (P10, P50, P90 en USD).
    montecarlo_p10_usd  NUMERIC(14, 2),
    montecarlo_p50_usd  NUMERIC(14, 2),
    montecarlo_p90_usd  NUMERIC(14, 2),

    -- Fecha de la última simulación (para saber si está desactualizada).
    montecarlo_run_at   TIMESTAMPTZ,

    -- ¿Está activa la meta o fue completada/archivada?
    activa              BOOLEAN NOT NULL DEFAULT TRUE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.kids_saver_goals IS
    'Metas de ahorro intergeneracional. Montos en USD para inmunizar contra devaluaciones. Compatible con protocolo SMarT y arquitectura Core-Satellite (Bogle + Lynch).';
COMMENT ON COLUMN public.kids_saver_goals.acciones_lynch IS
    'Hasta 3 acciones Ten-bagger seleccionadas por la IA (10% Satélite). Ej: [''NVDA'',''AMZN'',''MELI''].';


-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 3: scoring_cache — Cerebro de Scoring (Caché de Análisis)
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Actúa como la "memoria fotográfica" del ScoringEngine.
-- Cuando la IA analiza una acción (ej: AAPL para perfil Moderado), guarda
-- el resultado completo aquí. La próxima vez que cualquier usuario Moderado
-- consulte AAPL, el sistema devuelve este caché instantáneamente en lugar
-- de hacer todos los cálculos de nuevo. Tiene TTL de 24h para datos frescos.
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.scoring_cache (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Símbolo de la acción en mayúsculas, ej: 'AAPL', 'GGAL', 'MELI'.
    ticker              TEXT NOT NULL,

    -- El score varía por perfil, así que (ticker + perfil) es la clave única.
    perfil_inversor     TEXT NOT NULL CHECK (perfil_inversor IN (
                            'liquidez_plus', 'conservador', 'moderado', 'agresivo'
                        )),

    -- Score final compuesto (0-100). El número que ve el usuario.
    score_final         NUMERIC(5, 2) NOT NULL CHECK (score_final BETWEEN 0 AND 100),

    -- Clasificación resultante del score.
    clasificacion       TEXT NOT NULL CHECK (clasificacion IN (
                            'STRONG BUY',   -- score >= 75
                            'ACCUMULATE',   -- score >= 55
                            'HOLD',         -- score >= 35
                            'AVOID'         -- score <  35
                        )),

    -- ── Desglose de los 4 bloques del ScoringEngine ──────────────────────
    -- Score bruto del Bloque Graham (fundamentos de valor y seguridad).
    graham_score_bruto  NUMERIC(5, 2),
    graham_peso         NUMERIC(4, 2),   -- Peso aplicado según perfil (0.00-1.00)
    graham_aporte       NUMERIC(5, 2),   -- Contribución al score final
    graham_detalle      JSONB,           -- Desglose de cada métrica Graham

    -- Score bruto del Bloque Murphy (análisis técnico: RSI, SMAs, volumen).
    murphy_score_bruto  NUMERIC(5, 2),
    murphy_peso         NUMERIC(4, 2),
    murphy_aporte       NUMERIC(5, 2),
    murphy_detalle      JSONB,

    -- Score bruto del Bloque Lynch (crecimiento: PEG, EPS, Ten-baggers).
    lynch_score_bruto   NUMERIC(5, 2),
    lynch_peso          NUMERIC(4, 2),
    lynch_aporte        NUMERIC(5, 2),
    lynch_detalle       JSONB,

    -- Score bruto del Bloque InvestingPro (salud financiera y fair value).
    investingpro_score_bruto NUMERIC(5, 2),
    investingpro_peso        NUMERIC(4, 2),
    investingpro_aporte      NUMERIC(5, 2),
    investingpro_detalle     JSONB,

    -- ── Módulo CEDEAR (solo para acciones argentinas) ─────────────────────
    -- ¿Es un CEDEAR? Activa la lógica de limpieza cambiaria (VRU).
    es_cedear           BOOLEAN NOT NULL DEFAULT FALSE,

    -- Valor Real en USD (fórmula VRU): limpia el ruido del CCL/inflación.
    -- Detecta si el CEDEAR sube en pesos pero cae en USD ("trampa de devaluación").
    vru_valor_usd       NUMERIC(12, 4),
    vru_alerta_trampa   BOOLEAN DEFAULT FALSE,
    vru_penalizacion_aplicada BOOLEAN DEFAULT FALSE,

    -- Alertas generadas por el engine (ej: "RSI > 75", "Current Ratio < 2.0").
    alertas             TEXT[] DEFAULT '{}',

    -- Narrativa Housel generada (título + mensaje anti-pánico).
    narrativa_titulo    TEXT,
    narrativa_mensaje   TEXT,

    -- Pesos efectivamente aplicados en este cálculo (JSON para auditoría).
    pesos_aplicados     JSONB,

    -- Snapshot de las métricas crudas usadas en este cálculo (auditoría).
    metricas_snapshot   JSONB,

    -- ── Control de vigencia del caché ────────────────────────────────────
    -- El caché expira a las 24h. Pasado ese tiempo, se recalcula.
    -- Para CEDEARs con alta volatilidad cambiaria se recomienda reducir a 6h.
    calculado_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expira_at           TIMESTAMPTZ NOT NULL DEFAULT (NOW() + INTERVAL '24 hours'),

    -- Un ticker+perfil tiene un único caché vigente.
    UNIQUE (ticker, perfil_inversor)
);

COMMENT ON TABLE public.scoring_cache IS
    'Caché del ScoringEngine. Evita recalcular Graham+Murphy+Lynch+InvestingPro en cada consulta. TTL: 24h estándar (recomendado 6h para CEDEARs volátiles).';
COMMENT ON COLUMN public.scoring_cache.vru_valor_usd IS
    'Valor Real USD: fórmula VRU = (Precio_ARS * Ratio_CEDEAR) / (CCL_Implied * (1 + π_US)). Elimina el espejismo nominal de la inflación en pesos.';

-- Índice para consultas frecuentes: "dame el score de AAPL para perfil Moderado".
CREATE INDEX IF NOT EXISTS idx_scoring_cache_ticker_perfil
    ON public.scoring_cache (ticker, perfil_inversor);

-- Índice para el job de limpieza: "borrá todos los registros expirados".
CREATE INDEX IF NOT EXISTS idx_scoring_cache_expira
    ON public.scoring_cache (expira_at);


-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 4: investment_journal — Diario de Inversión (Cápsulas de Decisión)
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Es el "diario de a bordo" del inversor. Cada vez que compra
-- un activo, queda registrado aquí: por qué lo compró, cuál era el contexto
-- macro, qué decía la narrativa Housel en ese momento y cuál fue el score.
-- Esto es clave para el módulo Kids Saver (el padre deja Cápsulas de Decisión
-- para que su hijo entienda la tesis cuando herede el portafolio) y para la
-- psicología anti-pánico: en las caídas, el usuario puede releer su "por qué".
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.investment_journal (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- A qué usuario pertenece esta entrada.
    user_id             UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Ticker del activo comprado/vendido.
    ticker              TEXT NOT NULL,

    -- Tipo de operación registrada.
    tipo_operacion      TEXT NOT NULL CHECK (tipo_operacion IN (
                            'compra',
                            'venta',
                            'aporte_kids_saver',   -- Aporte periódico a una meta Kids Saver
                            'rebalanceo',          -- Ajuste de bandas Swensen (5-10%)
                            'revision'             -- Revisión periódica sin transacción
                        )),

    -- Precio de ejecución al momento de la operación (en la moneda_base del usuario).
    precio_ejecucion    NUMERIC(14, 4),
    moneda_ejecucion    TEXT DEFAULT 'USD',

    -- Cantidad de unidades/acciones operadas.
    cantidad            NUMERIC(14, 6),

    -- Score del ScoringEngine en el momento de la decisión (para comparar después).
    score_al_momento    NUMERIC(5, 2),
    clasificacion_al_momento TEXT,

    -- ── La Narrativa Housel (el corazón de esta tabla) ────────────────────
    -- Razón principal de la compra en palabras del propio usuario.
    -- Ej: "Compro AAPL porque creo en el ecosistema iOS a 5 años. No me importa
    -- si cae 20% en el camino, eso es la tarifa de entrada."
    narrativa_housel    TEXT NOT NULL,

    -- Contexto macro registrado al momento (¿estaba el mercado en pánico?).
    contexto_macro      TEXT,

    -- ¿Fue una operación emocional o racional? (auto-diagnóstico del usuario).
    es_decision_emocional BOOLEAN DEFAULT FALSE,

    -- ¿Está vinculada a una meta Kids Saver?
    kids_saver_goal_id  UUID REFERENCES public.kids_saver_goals(id) ON DELETE SET NULL,

    -- Tags libres para filtrar entradas (ej: ['lynch', 'ten-bagger', 'cedear']).
    tags                TEXT[] DEFAULT '{}',

    -- VRU snapshot al momento de la compra (vital para CEDEARs argentinos).
    vru_snapshot_usd    NUMERIC(12, 4),
    ccl_al_momento      NUMERIC(10, 2),   -- Tipo de cambio CCL del día

    -- Fecha real de la operación (puede diferir de created_at si se carga tarde).
    fecha_operacion     DATE NOT NULL DEFAULT CURRENT_DATE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.investment_journal IS
    'Diario de decisiones del inversor. Registra la Narrativa Housel de cada compra/venta para combatir el pánico futuro y dejar Cápsulas de Decisión para los herederos del módulo Kids Saver.';
COMMENT ON COLUMN public.investment_journal.narrativa_housel IS
    'El "por qué" de la decisión. Es la herramienta anti-pánico principal: en una caída, el usuario relee su convicción original y evita vender en el peor momento.';

-- Índice para recuperar el historial de un usuario ordenado por fecha.
CREATE INDEX IF NOT EXISTS idx_journal_user_fecha
    ON public.investment_journal (user_id, fecha_operacion DESC);

-- Índice para el módulo Kids Saver (cápsulas vinculadas a una meta).
CREATE INDEX IF NOT EXISTS idx_journal_kids_saver
    ON public.investment_journal (kids_saver_goal_id)
    WHERE kids_saver_goal_id IS NOT NULL;


-- ═════════════════════════════════════════════════════════════════════════════
-- TABLA 5: macro_semaforo — Semáforo Macro (Contexto de Mercado Global)
-- ─────────────────────────────────────────────────────────────────────────────
-- ¿QUÉ HACE? Almacena el estado del "Semáforo Macro" que supervisa el ciclo
-- económico global (inspirado en los Ciclos de Deuda de Ray Dalio).
-- Cuando el semáforo está en ROJO (fin de ciclo), el ScoringEngine puede
-- aplicar una penalización automática a todos los scores. Es la tabla que
-- da contexto sistémico a las decisiones individuales de cada ticker.
-- ═════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.macro_semaforo (
    id                  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Nombre del indicador macro monitoreado.
    indicador           TEXT NOT NULL,

    -- Región geográfica del indicador.
    region              TEXT NOT NULL DEFAULT 'GLOBAL' CHECK (region IN (
                            'GLOBAL', 'US', 'AR', 'MX', 'EM'
                        )),

    -- Estado del semáforo para este indicador.
    estado              TEXT NOT NULL CHECK (estado IN (
                            'VERDE',    -- Ciclo expansivo, condiciones favorables
                            'AMARILLO', -- Desaceleración o incertidumbre
                            'ROJO'      -- Contracción, fin de ciclo de deuda Dalio
                        )),

    -- Valor numérico actual del indicador (ej: 3.5 para inflación 3.5%).
    valor_actual        NUMERIC(12, 4),
    unidad              TEXT,           -- '%', 'índice', 'bps', etc.

    -- Fuente del dato y notas del analista.
    fuente              TEXT,
    notas               TEXT,

    -- ¿Activa penalización automática en el ScoringEngine?
    activa_penalizacion BOOLEAN NOT NULL DEFAULT FALSE,

    -- Factor de penalización a aplicar si activa_penalizacion = TRUE.
    -- Ej: 0.90 = reduce todos los scores un 10% cuando el semáforo está en ROJO.
    factor_penalizacion NUMERIC(4, 3) DEFAULT 1.000 CHECK (factor_penalizacion BETWEEN 0.50 AND 1.00),

    -- Fecha de validez del dato.
    fecha_dato          DATE NOT NULL DEFAULT CURRENT_DATE,

    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE public.macro_semaforo IS
    'Contexto macro global (Ciclos Dalio). Cuando el semáforo está en ROJO, el ScoringEngine aplica factor_penalizacion a todos los scores automáticamente.';

-- Índice para consultas por región y estado actual.
CREATE INDEX IF NOT EXISTS idx_semaforo_region_estado
    ON public.macro_semaforo (region, estado, fecha_dato DESC);


-- ═════════════════════════════════════════════════════════════════════════════
-- TRIGGERS DE AUDITORÍA: updated_at automático en todas las tablas
-- ─────────────────────────────────────────────────────────────────────────────
-- Cada vez que se modifica una fila, updated_at se actualiza solo.
-- ═════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE FUNCTION public.handle_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

-- Aplicar el trigger a las tablas que tienen updated_at.
CREATE TRIGGER trg_user_profiles_updated_at
    BEFORE UPDATE ON public.user_profiles
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

CREATE TRIGGER trg_kids_saver_updated_at
    BEFORE UPDATE ON public.kids_saver_goals
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

CREATE TRIGGER trg_journal_updated_at
    BEFORE UPDATE ON public.investment_journal
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();

CREATE TRIGGER trg_semaforo_updated_at
    BEFORE UPDATE ON public.macro_semaforo
    FOR EACH ROW EXECUTE FUNCTION public.handle_updated_at();


-- ═════════════════════════════════════════════════════════════════════════════
-- SEGURIDAD: ROW LEVEL SECURITY (RLS)
-- ─────────────────────────────────────────────────────────────────────────────
-- La "regla de oro": cada usuario solo puede ver, crear, editar y borrar
-- sus propios datos. auth.uid() es el ID del usuario autenticado en Supabase.
-- El scoring_cache y macro_semaforo son de lectura pública (datos compartidos).
-- ═════════════════════════════════════════════════════════════════════════════

-- ── Activar RLS en todas las tablas ──────────────────────────────────────────
ALTER TABLE public.user_profiles      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.kids_saver_goals   ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scoring_cache      ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.investment_journal ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.macro_semaforo     ENABLE ROW LEVEL SECURITY;


-- ── POLÍTICAS: user_profiles ─────────────────────────────────────────────────

-- El usuario puede ver SOLO su propio perfil.
CREATE POLICY "user_profiles: SELECT propio"
    ON public.user_profiles FOR SELECT
    USING (auth.uid() = user_id);

-- El usuario puede insertar SOLO un perfil con su propio user_id.
CREATE POLICY "user_profiles: INSERT propio"
    ON public.user_profiles FOR INSERT
    WITH CHECK (auth.uid() = user_id);

-- El usuario puede actualizar SOLO su propio perfil.
CREATE POLICY "user_profiles: UPDATE propio"
    ON public.user_profiles FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

-- El usuario puede borrar SOLO su propio perfil.
CREATE POLICY "user_profiles: DELETE propio"
    ON public.user_profiles FOR DELETE
    USING (auth.uid() = user_id);


-- ── POLÍTICAS: kids_saver_goals ──────────────────────────────────────────────

CREATE POLICY "kids_saver: SELECT propio"
    ON public.kids_saver_goals FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "kids_saver: INSERT propio"
    ON public.kids_saver_goals FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "kids_saver: UPDATE propio"
    ON public.kids_saver_goals FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "kids_saver: DELETE propio"
    ON public.kids_saver_goals FOR DELETE
    USING (auth.uid() = user_id);


-- ── POLÍTICAS: scoring_cache ─────────────────────────────────────────────────
-- El caché es un recurso COMPARTIDO: cualquier usuario autenticado puede leer
-- el score de cualquier ticker (no tiene sentido recalcular para cada usuario).
-- Solo el backend (service_role) puede escribir/actualizar el caché.

CREATE POLICY "scoring_cache: SELECT público autenticado"
    ON public.scoring_cache FOR SELECT
    TO authenticated
    USING (true);

-- INSERT y UPDATE solo por service_role (el backend de FastAPI).
-- No se crean políticas para INSERT/UPDATE/DELETE: por defecto quedan bloqueadas
-- para usuarios normales. El service_role de Supabase bypasea RLS.


-- ── POLÍTICAS: investment_journal ────────────────────────────────────────────

CREATE POLICY "journal: SELECT propio"
    ON public.investment_journal FOR SELECT
    USING (auth.uid() = user_id);

CREATE POLICY "journal: INSERT propio"
    ON public.investment_journal FOR INSERT
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "journal: UPDATE propio"
    ON public.investment_journal FOR UPDATE
    USING (auth.uid() = user_id)
    WITH CHECK (auth.uid() = user_id);

CREATE POLICY "journal: DELETE propio"
    ON public.investment_journal FOR DELETE
    USING (auth.uid() = user_id);


-- ── POLÍTICAS: macro_semaforo ────────────────────────────────────────────────
-- El Semáforo Macro es PÚBLICO para todos los usuarios autenticados (es
-- información de mercado, no datos personales). Solo el service_role escribe.

CREATE POLICY "semaforo: SELECT público autenticado"
    ON public.macro_semaforo FOR SELECT
    TO authenticated
    USING (true);


-- ═════════════════════════════════════════════════════════════════════════════
-- DATOS INICIALES: Semáforo Macro (valores de ejemplo al arrancar)
-- ═════════════════════════════════════════════════════════════════════════════

INSERT INTO public.macro_semaforo
    (indicador, region, estado, valor_actual, unidad, fuente, notas, activa_penalizacion, factor_penalizacion)
VALUES
    ('Tasa Fed Funds', 'US', 'AMARILLO', 5.25, '%',
     'Federal Reserve', 'Zona de restricción monetaria. Ciclo Dalio: Desaceleración.',
     FALSE, 1.000),

    ('Inflación CPI YoY', 'US', 'AMARILLO', 3.20, '%',
     'BLS', 'Por encima del target del 2% pero bajando.',
     FALSE, 1.000),

    ('Riesgo País EMBI', 'AR', 'ROJO', 1650, 'bps',
     'JP Morgan EMBI', 'Riesgo país elevado. Penalización activa para scores de CEDEARs.',
     TRUE, 0.900),  -- CEDEARs reciben un descuento del 10% en su score

    ('Tipo Cambio CCL', 'AR', 'AMARILLO', 1085, 'ARS/USD',
     'Mercado OTC', 'Se usa para calcular el VRU de CEDEARs.',
     FALSE, 1.000),

    ('S&P 500 Trend (SMA200)', 'US', 'VERDE', 1.06, 'ratio precio/SMA200',
     'Cálculo interno', 'Precio 6% por encima de SMA200. Tendencia alcista confirmada.',
     FALSE, 1.000);


-- ═════════════════════════════════════════════════════════════════════════════
-- FIN DEL SCRIPT
-- ─────────────────────────────────────────────────────────────────────────────
-- Tablas creadas: 5
-- Índices creados: 5
-- Triggers de auditoría: 4
-- Políticas RLS: 14
-- Datos iniciales (semáforo macro): 5 filas
-- ═════════════════════════════════════════════════════════════════════════════
