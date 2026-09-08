-- ═══════════════════════════════════════════════════════════════════
-- HORIZONTES · Migración: usage_quota (gate Free/Pro para la API self-service)
-- Idempotente — se puede correr más de una vez sin romper nada.
-- ═══════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS public.usage_quota (
    user_id         UUID    NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    fecha           DATE    NOT NULL DEFAULT CURRENT_DATE,
    consultas_hoy   INTEGER NOT NULL DEFAULT 0,
    plan            TEXT    NOT NULL DEFAULT 'free' CHECK (plan IN ('free', 'pro')),
    PRIMARY KEY (user_id, fecha)
);

COMMENT ON TABLE public.usage_quota IS
    'Contador diario de consultas /score por usuario. Gate Free/Pro de la API self-service (ago-2026).';

-- RLS: el usuario puede LEER su propia fila (para /me/quota), pero solo el
-- backend (service_role) puede escribir — evita que alguien infle su propio
-- plan modificando la fila desde el cliente.
ALTER TABLE public.usage_quota ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "usuario lee su propia cuota" ON public.usage_quota;
CREATE POLICY "usuario lee su propia cuota"
    ON public.usage_quota FOR SELECT
    USING (auth.uid() = user_id);

-- Sin política de INSERT/UPDATE para authenticated: solo service_role escribe
-- (el backend usa SUPABASE_SERVICE_ROLE_KEY, que bypassa RLS por diseño).
