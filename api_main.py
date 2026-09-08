#!/usr/bin/env python3
"""
╔══════════════════════════════════════════════════════════════╗
║   HORIZONTES — api_main.py                                   ║
║   API self-service: ticker → informe, sin intervención        ║
║   manual del propietario en ningún punto del camino.          ║
╠══════════════════════════════════════════════════════════════╣
║   Decisión de arquitectura (ago-2026, requisito legal):       ║
║   Horizontes debe operar como PRODUCTO DE SOFTWARE            ║
║   autoservicio, no como asesoramiento personalizado. Esta     ║
║   API es el único punto de entrada nuevo: reutiliza el mismo  ║
║   CachedScoringPipeline que ya usa el cron nocturno — no hay  ║
║   dos motores, hay un motor con dos disparadores (cron        ║
║   programado + request on-demand del usuario).                ║
║                                                                ║
║   Flujo: usuario autenticado (JWT de Supabase Auth) → POST    ║
║   /score {ticker, perfil} → pipeline → JSON con el score Y    ║
║   el disclaimer legal SIEMPRE incluido (viene por default     ║
║   desde InvestmentScore, no se agrega acá — ver P0).           ║
║                                                                ║
║   Deploy sugerido: un solo servicio (Render/Fly.io), capa     ║
║   gratuita/mínima alcanza para el arranque. Sin servidor      ║
║   propio, sin infraestructura nueva.                          ║
╚══════════════════════════════════════════════════════════════╝

Correr localmente:
    uvicorn api_main:app --reload --port 8000

Requiere en el entorno (mismo .env que el resto de Horizontes):
    SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY   (legacy JWT eyJ...)
    SUPABASE_JWT_SECRET                        (para validar el token del
                                                 usuario final — Settings →
                                                 API → JWT Settings en Supabase)
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timezone
from typing import Optional

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import jwt as pyjwt
from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from DataFetcher import CachedScoringPipeline, TickerSinDatosError
from scoring_engine import DISCLAIMER_HORIZONTES, InvestmentScore, PerfilInversor
from balanz_parser import parse_balanz_csv, subir_a_supabase

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
log = logging.getLogger("horizontes.api")

app = FastAPI(
    title="Horizontes API",
    description="Análisis de inversión automático, self-service. "
                 "Ningún endpoint requiere intervención manual del operador.",
    version="1.0.0",
)

# CORS: ajustar allow_origins a tu dominio real antes de producción.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # TODO producción: restringir a tu dominio del frontend
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────────────────
# Autenticación: valida el JWT que emite Supabase Auth para el usuario final.
# Esto es DISTINTO de SUPABASE_SERVICE_ROLE_KEY (esa es la credencial del
# backend contra la base; esta es la del usuario logueado en el navegador).
# ─────────────────────────────────────────────────────────────────────────────

SUPABASE_JWT_SECRET = os.environ.get("SUPABASE_JWT_SECRET", "")


class UsuarioAutenticado(BaseModel):
    user_id: str
    email: Optional[str] = None


def verificar_usuario(authorization: str = Header(...)) -> UsuarioAutenticado:
    """
    Extrae y valida el JWT del header 'Authorization: Bearer <token>'.
    Nunca confía en un user_id que venga del body del request — siempre
    del token firmado por Supabase. Esto es lo que reemplaza que el
    propietario corra el import "a mano" bajo su propio UUID.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Falta el header Authorization: Bearer <token>.")
    token = authorization.removeprefix("Bearer ").strip()

    if not SUPABASE_JWT_SECRET:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "SUPABASE_JWT_SECRET no configurado en el servidor — no se puede validar sesiones.",
        )
    try:
        payload = pyjwt.decode(
            token, SUPABASE_JWT_SECRET, algorithms=["HS256"], audience="authenticated",
        )
    except pyjwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sesión expirada, volvé a iniciar sesión.")
    except pyjwt.InvalidTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, f"Token inválido: {exc}")

    return UsuarioAutenticado(user_id=payload["sub"], email=payload.get("email"))


# ─────────────────────────────────────────────────────────────────────────────
# Cuota diaria — gate simple Free/Pro contra tabla `usage_quota` en Supabase.
# Tabla nueva (ver migración adjunta): (user_id, fecha, consultas_hoy, plan).
# ─────────────────────────────────────────────────────────────────────────────

CUOTA_DIARIA_FREE = int(os.environ.get("HORIZONTES_CUOTA_FREE", "5"))
CUOTA_DIARIA_PRO  = int(os.environ.get("HORIZONTES_CUOTA_PRO", "100"))


def _pipeline() -> CachedScoringPipeline:
    # Instancia por request: FastAPI + Supabase client son thread-safe para
    # esto en la escala inicial. Si el volumen crece, mover a un singleton
    # con pool de conexiones — no antes, no es el cuello de botella hoy.
    return CachedScoringPipeline.from_env()


def _verificar_y_consumir_cuota(pipeline: CachedScoringPipeline, user_id: str) -> None:
    """Lee y actualiza usage_quota. Si la tabla no existe todavía, no bloquea
    (fail-open) pero deja un warning bien visible — mejor no cobrar de más
    que tumbar el producto por una migración pendiente."""
    if not pipeline.use_cache:
        return  # sin Supabase configurado (tests locales), no aplica cuota
    hoy = date.today().isoformat()
    try:
        resp = (
            pipeline.sb.table("usage_quota")
            .select("*").eq("user_id", user_id).eq("fecha", hoy).limit(1).execute()
        )
        rows = resp.data or []
        plan = (rows[0]["plan"] if rows else "free")
        consultas_hoy = (rows[0]["consultas_hoy"] if rows else 0)
        limite = CUOTA_DIARIA_PRO if plan == "pro" else CUOTA_DIARIA_FREE

        if consultas_hoy >= limite:
            raise HTTPException(
                status.HTTP_429_TOO_MANY_REQUESTS,
                f"Alcanzaste el límite diario de {limite} consultas de tu plan '{plan}'. "
                "Volvé a intentar mañana o mejorá tu plan.",
            )

        pipeline.sb.table("usage_quota").upsert(
            {"user_id": user_id, "fecha": hoy, "consultas_hoy": consultas_hoy + 1, "plan": plan},
            on_conflict="user_id,fecha",
        ).execute()
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001 — la cuota nunca debe tumbar el producto
        log.warning("No se pudo verificar/actualizar usage_quota para %s: %s", user_id, exc)


# ─────────────────────────────────────────────────────────────────────────────
# Modelos de request/response
# ─────────────────────────────────────────────────────────────────────────────

class ScoreRequest(BaseModel):
    ticker: str = Field(..., examples=["AAPL", "GOOGL.BA", "GGAL"])
    perfil: PerfilInversor = Field(default=PerfilInversor.MODERADO)


class ScoreResponseOK(BaseModel):
    status: str = "ok"
    cached: bool
    score: InvestmentScore


class ScoreResponseSinDatos(BaseModel):
    status: str = "sin_datos"
    ticker: str
    mensaje: str
    disclaimer: str = DISCLAIMER_HORIZONTES


# ─────────────────────────────────────────────────────────────────────────────
# Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    """Sin auth — para monitoreo de uptime (Render/Fly.io/UptimeRobot)."""
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/score", response_model=None)
def score_ticker(
    req: ScoreRequest,
    usuario: UsuarioAutenticado = Depends(verificar_usuario),
):
    """
    Punto de entrada central del producto self-service.

    - Nunca requiere que el propietario intervenga: el usuario autenticado
      pide un ticker, el pipeline corre solo, la respuesta sale sola.
    - Si el ticker no tiene datos, responde 200 con status="sin_datos" y un
      mensaje claro — NUNCA un score fabricado (ver TickerSinDatosError).
    - El disclaimer legal viaja siempre embebido (score.disclaimer o
      ScoreResponseSinDatos.disclaimer) — no depende de que el frontend lo
      agregue por su cuenta.
    """
    pipeline = _pipeline()
    _verificar_y_consumir_cuota(pipeline, usuario.user_id)

    try:
        score, was_cached = pipeline.get_score(req.ticker, req.perfil)
        return ScoreResponseOK(cached=was_cached, score=score)

    except TickerSinDatosError as exc:
        log.info("Sin datos para %s (usuario %s): %s", req.ticker, usuario.user_id, exc)
        return ScoreResponseSinDatos(
            ticker=req.ticker.strip().upper(),
            mensaje=(
                f"No pudimos obtener datos de mercado para '{req.ticker.strip().upper()}'. "
                "Puede estar mal escrito, deslistado, o no soportado todavía. "
                "Revisá el símbolo e intentá de nuevo."
            ),
        )

    except Exception as exc:  # noqa: BLE001 — nunca devolver un 500 crudo al usuario final
        log.error("Error inesperado scoring %s para %s: %s", req.ticker, usuario.user_id, exc)
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "No pudimos completar el análisis en este momento. Intentá de nuevo en unos minutos.",
        )


@app.get("/me/quota")
def mi_cuota(usuario: UsuarioAutenticado = Depends(verificar_usuario)):
    """El usuario puede ver su propio consumo — transparencia sin soporte manual."""
    pipeline = _pipeline()
    if not pipeline.use_cache:
        return {"plan": "free", "consultas_hoy": 0, "limite": CUOTA_DIARIA_FREE}
    hoy = date.today().isoformat()
    resp = (
        pipeline.sb.table("usage_quota")
        .select("*").eq("user_id", usuario.user_id).eq("fecha", hoy).limit(1).execute()
    )
    rows = resp.data or []
    plan = rows[0]["plan"] if rows else "free"
    consultas_hoy = rows[0]["consultas_hoy"] if rows else 0
    limite = CUOTA_DIARIA_PRO if plan == "pro" else CUOTA_DIARIA_FREE
    return {"plan": plan, "consultas_hoy": consultas_hoy, "limite": limite}


# ─────────────────────────────────────────────────────────────────────────────
# Import de cartera self-service (P3)
#
# Reemplaza que el propietario corra `balanz_parser.py --supabase` a mano
# sobre el archivo de cada usuario — eso era literalmente asesoramiento
# personalizado operativo, el punto de mayor riesgo legal del pipeline
# original. Acá el usuario sube SU CSV, autenticado con SU sesión, y el
# parser (mismo `balanz_parser.py` que ya usás vos, sin duplicar lógica)
# corre contra SU `user_id` real — nadie del lado del propietario toca
# el archivo ni el resultado en ningún punto del camino.
# ─────────────────────────────────────────────────────────────────────────────

import tempfile
from pathlib import Path as _Path

MAX_CSV_BYTES = 2 * 1024 * 1024  # 2 MB — un export de Balanz nunca pesa esto


class ImportResponse(BaseModel):
    status: str  # "ok" | "sin_operaciones_validas"
    operaciones_importadas: int
    operaciones_duplicadas: int
    avisos: list[str]
    disclaimer: str = DISCLAIMER_HORIZONTES


@app.post("/portfolio/import", response_model=ImportResponse)
async def importar_cartera(
    archivo: UploadFile = File(...),
    usuario: UsuarioAutenticado = Depends(verificar_usuario),
):
    """
    El usuario sube su export CSV de Balanz. Se parsea, se dedupea por hash
    (re-subir el mismo archivo no duplica nada) y se inserta en
    investment_journal bajo SU user_id — nunca bajo el del propietario.
    """
    contenido = await archivo.read()
    if len(contenido) > MAX_CSV_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE,
                            f"El archivo supera el límite de {MAX_CSV_BYTES // 1024} KB.")

    # parse_balanz_csv espera una ruta de archivo — se escribe a un temp
    # (el parser real ya maneja encoding/delimitador/etc. desde el archivo).
    with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as tmp:
        tmp.write(contenido)
        tmp_path = _Path(tmp.name)

    try:
        try:
            ops, reporte = parse_balanz_csv(tmp_path)
        except ValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

        if not ops:
            return ImportResponse(
                status="sin_operaciones_validas", operaciones_importadas=0,
                operaciones_duplicadas=0, avisos=reporte,
            )

        try:
            insertadas, duplicadas = subir_a_supabase(ops, usuario.user_id)
        except SystemExit as exc:
            log.error("Credenciales Supabase faltantes en el servidor: %s", exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "No pudimos guardar tu cartera en este momento.")
        except Exception as exc:  # noqa: BLE001
            log.error("Error insertando cartera de %s: %s", usuario.user_id, exc)
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "No pudimos guardar tu cartera en este momento.")

        return ImportResponse(
            status="ok", operaciones_importadas=insertadas,
            operaciones_duplicadas=duplicadas, avisos=reporte,
        )
    finally:
        tmp_path.unlink(missing_ok=True)
