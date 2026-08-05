"""API HTTP — la cara para frontends a medida.

Existe para que el cliente pueda tener la pantalla que quiera sin que este
proyecto tenga opinión sobre cómo se ve. No hay UI acá adentro: hay una API
limpia y un OpenAPI en `/docs` para que cualquiera arme la suya en el framework
que le guste.

Comparte el `core` con el servidor MCP. Es la misma regla de aprobación, la
misma validación y el mismo registro — no hay dos implementaciones que se
puedan desincronizar.

Seguridad: por defecto escucha SOLO en 127.0.0.1 y exige un token. Es una API
que mueve plata; abrirla al mundo tiene que ser una decisión explícita y
consciente, no el resultado de no haber configurado nada.
"""
from __future__ import annotations

import os
import secrets
import time
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from pydantic import BaseModel, Field

from ..core.errors import ApprovalRequired, WalletError
from ..core.resumen import FORMATOS, Marca, Resumen, generar
from ..core.service import WalletService
from ..core.store import WalletStore
from ..drivers.sandbox import SandboxDriver


# --- modelos de entrada -------------------------------------------------------

class CrearWallet(BaseModel):
    nombre: str = Field(min_length=1, examples=["Juan Pérez"])
    tipo_documento: str = Field(default="DNI", examples=["DNI"])
    documento: str = Field(min_length=1, examples=["30123456"])
    email: str | None = None
    telefono: str | None = None
    etiqueta: str = ""


class ProponerTransferencia(BaseModel):
    monto: str = Field(examples=["1500.50"], description="Cadena, no float")
    cvu: str | None = None
    alias: str | None = None
    nombre_destino: str = ""
    concepto: str = ""
    clave_idempotencia: str = ""


class Aprobar(BaseModel):
    aprobado_por: str = Field(min_length=1, description="Quién firma. Sin esto no se aprueba.")


class Rechazar(BaseModel):
    motivo: str = ""
    rechazado_por: str = ""


# --- app ----------------------------------------------------------------------

def _service() -> WalletService:
    home = Path(os.environ["WALLET_HOME"]) if os.environ.get("WALLET_HOME") else None
    store = WalletStore(home=home)
    return WalletService(
        SandboxDriver(estado_en=store.home / "sandbox-estado.json"), store,
        limite_sin_aprobacion=Decimal(os.environ.get("WALLET_LIMITE_SIN_APROBACION", "0")),
    )


def build_app(service: WalletService | None = None, token: str | None = None) -> FastAPI:
    svc = service or _service()
    # Si no se configuró token, se genera uno y se imprime. Nunca "sin auth":
    # una API de pagos sin credencial es un accidente esperando pasar.
    api_token = token or os.environ.get("WALLET_API_TOKEN") or secrets.token_urlsafe(24)

    app = FastAPI(
        title="Wallet API",
        version="0.1.0",
        description=(
            "Backend de wallets. El agente propone, una persona aprueba.\n\n"
            "Montá el frontend que quieras sobre esto: no hay UI incluida."
        ),
    )
    app.state.token = api_token

    def auth(authorization: Annotated[str | None, Header()] = None) -> None:
        esperado = f"Bearer {app.state.token}"
        # compare_digest: comparar tokens con == filtra información por el
        # tiempo que tarda en fallar.
        if not authorization or not secrets.compare_digest(authorization, esperado):
            raise HTTPException(401, "token inválido o ausente")

    Auth = Depends(auth)

    def _http(e: WalletError) -> HTTPException:
        codigo = {
            "validacion": 400, "wallet_desconocida": 404, "no_soportado": 501,
            "fondos_insuficientes": 409, "duplicado": 409, "proveedor": 502,
        }.get(e.code, 400)
        return HTTPException(codigo, detail=e.to_dict())

    # --- wallets --------------------------------------------------------------

    @app.get("/wallets", tags=["wallets"], dependencies=[Auth])
    def listar_wallets():
        return [w.publico() for w in svc.listar_wallets()]

    @app.post("/wallets", status_code=201, tags=["wallets"], dependencies=[Auth])
    async def crear_wallet(body: CrearWallet):
        try:
            w = await svc.crear_wallet(**body.model_dump())
            return w.publico()
        except WalletError as e:
            raise _http(e) from e

    @app.get("/wallets/{wallet_id}", tags=["wallets"], dependencies=[Auth])
    def obtener_wallet(wallet_id: str):
        try:
            w = svc.obtener_wallet(wallet_id)
            # El frontend del titular sí puede ver su documento enmascarado:
            # le sirve para reconocer su cuenta.
            return {**w.publico(), "titular_detalle": w.titular.enmascarado()}
        except WalletError as e:
            raise _http(e) from e

    @app.get("/wallets/{wallet_id}/saldo", tags=["wallets"], dependencies=[Auth])
    async def saldo(wallet_id: str):
        try:
            return (await svc.saldo(wallet_id)).to_dict()
        except WalletError as e:
            raise _http(e) from e

    @app.get("/wallets/{wallet_id}/movimientos", tags=["wallets"], dependencies=[Auth])
    async def movimientos(wallet_id: str, limite: int = Query(50, le=1000), dias: int = 0):
        try:
            desde = time.time() - dias * 86400 if dias > 0 else None
            return [m.to_dict() for m in
                    await svc.movimientos(wallet_id, desde=desde, limite=limite)]
        except WalletError as e:
            raise _http(e) from e

    @app.get("/wallets/{wallet_id}/resumen", tags=["resumenes"], dependencies=[Auth])
    async def resumen(
        wallet_id: str, formato: str = Query("json", pattern="|".join(FORMATOS)),
        dias: int = 30, entidad: str = "", color: str = "#1a1a2e", titulo: str = "",
    ):
        """Resumen de cuenta. `html` viene listo para imprimir; `csv` para planilla."""
        try:
            wallet = svc.obtener_wallet(wallet_id)
            desde = time.time() - dias * 86400 if dias > 0 else None
            movs = await svc.movimientos(wallet_id, desde=desde, limite=5000)
            saldo_actual = await svc.saldo(wallet_id)
        except WalletError as e:
            raise _http(e) from e

        cuerpo = generar(
            Resumen(wallet=wallet, movimientos=movs, saldo=saldo_actual,
                    desde=desde, hasta=time.time(), generado_en=time.time()),
            formato,
            Marca(titulo=titulo or "Resumen de cuenta", entidad=entidad, color=color),
        )
        tipos = {
            "json": "application/json; charset=utf-8",
            "csv": "text/csv; charset=utf-8",
            "html": "text/html; charset=utf-8",
            "texto": "text/plain; charset=utf-8",
        }
        headers = {}
        if formato == "csv":
            headers["Content-Disposition"] = f'attachment; filename="resumen-{wallet_id}.csv"'
        return Response(content=cuerpo, media_type=tipos[formato], headers=headers)

    # --- transferencias -------------------------------------------------------

    @app.post("/wallets/{wallet_id}/transferencias", status_code=202,
              tags=["transferencias"], dependencies=[Auth])
    async def proponer(wallet_id: str, body: ProponerTransferencia):
        """Propone una transferencia.

        Devuelve **202** con el intento pendiente si necesita aprobación (que es
        lo normal), o **200** con el comprobante si entró en el límite
        configurado sin firma.
        """
        try:
            intento = await svc.proponer_transferencia(
                wallet_id, cvu=body.cvu, alias=body.alias,
                nombre_destino=body.nombre_destino, monto=body.monto,
                concepto=body.concepto, agente="api",
                idempotency_key=body.clave_idempotencia,
            )
            return Response(
                content=__import__("json").dumps({"estado": "ejecutado", **intento.to_dict()}),
                media_type="application/json", status_code=200,
            )
        except ApprovalRequired as e:
            # No es un error HTTP: es el camino esperado. 202 = aceptado, en
            # proceso. Devolver 4xx acá haría que los clientes lo traten como
            # fallo y reintenten, que es justo lo que no queremos.
            return {"estado": "pendiente_aprobacion", "intent_id": e.intent_id,
                    "mensaje": e.message, "hint": e.hint}
        except WalletError as e:
            raise _http(e) from e

    @app.get("/transferencias/pendientes", tags=["transferencias"], dependencies=[Auth])
    def pendientes(wallet_id: str | None = None):
        return [i.to_dict() for i in svc.pendientes(wallet_id=wallet_id)]

    @app.get("/transferencias/{intento_id}", tags=["transferencias"], dependencies=[Auth])
    def estado_intento(intento_id: str):
        try:
            return svc._intento(intento_id).to_dict()
        except WalletError as e:
            raise _http(e) from e

    @app.post("/transferencias/{intento_id}/aprobar", tags=["transferencias"],
              dependencies=[Auth])
    async def aprobar(intento_id: str, body: Aprobar):
        """Aprueba y ejecuta. Esto es lo que un agente NO debería poder llamar.

        Si exponés esta API, protegé este endpoint con una credencial distinta
        de la que le das al agente.
        """
        try:
            return (await svc.aprobar(intento_id, aprobado_por=body.aprobado_por)).to_dict()
        except WalletError as e:
            raise _http(e) from e

    @app.post("/transferencias/{intento_id}/rechazar", tags=["transferencias"],
              dependencies=[Auth])
    def rechazar(intento_id: str, body: Rechazar):
        try:
            return svc.rechazar(intento_id, motivo=body.motivo,
                                rechazado_por=body.rechazado_por).to_dict()
        except WalletError as e:
            raise _http(e) from e

    # --- meta -----------------------------------------------------------------

    @app.get("/capacidades", tags=["meta"])
    def capacidades():
        """Qué puede el proveedor configurado. Sin auth: no revela nada sensible."""
        return {"driver": svc.driver.nombre, **svc.capacidades().to_dict()}

    @app.get("/salud", tags=["meta"])
    def salud():
        return {"ok": True, "driver": svc.driver.nombre}

    return app


def main() -> None:
    import uvicorn

    app = build_app()
    host = os.environ.get("WALLET_API_HOST", "127.0.0.1")
    puerto = int(os.environ.get("WALLET_API_PORT", "8479"))

    if not os.environ.get("WALLET_API_TOKEN"):
        print("\n  token generado para esta corrida:")
        print(f"    {app.state.token}\n")
        print("  Fijalo en WALLET_API_TOKEN para que no cambie en cada arranque.\n")
    if host not in ("127.0.0.1", "localhost"):
        print(f"  ATENCIÓN: escuchando en {host}, no sólo en localhost.")
        print("  Esta API mueve plata. Poné TLS y un proxy adelante.\n")

    uvicorn.run(app, host=host, port=puerto)


if __name__ == "__main__":
    main()
