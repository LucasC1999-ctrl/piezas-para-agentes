"""Servidor MCP de la wallet — la cara que ve el AGENTE.

Lo que el agente PUEDE: consultar wallets, saldos, movimientos, generar
resúmenes, y **proponer** transferencias.

Lo que el agente NO PUEDE: aprobar. `wallet_aprobar` no existe en este
servidor, y no es un olvido. Si el agente pudiera aprobar sus propias
propuestas, la aprobación no sería una compuerta sino un trámite. Aprobar se
hace con `wallet-admin`, que corre aparte y lo maneja una persona.

Las credenciales del proveedor salen del entorno o del vault; nunca son un
parámetro de una herramienta, porque todo parámetro es algo que un mensaje bien
redactado puede convencer al agente de cambiar.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

from mcp.server import MCPServer

from ..core.errors import WalletError
from ..core.resumen import FORMATOS, Marca, Resumen, generar
from ..core.service import WalletService
from ..core.store import WalletStore
from ..drivers.sandbox import SandboxDriver

INSTRUCTIONS = """\
Wallet de pagos. Podés consultar y podés PROPONER transferencias, pero no
ejecutarlas.

Cuando propongas una transferencia vas a recibir `requiere_aprobacion` con un
`intent_id`. Eso NO es un error: es que la operación quedó esperando que una
persona la firme. Decíselo al humano con el id, el monto y el destino, y no
vuelvas a intentar la misma transferencia — reintentar puede duplicar el pago.

Antes de proponer un movimiento, consultá el saldo. Antes de prometerle algo al
usuario, mirá `wallet_capacidades`: según el proveedor configurado puede que
transferir no esté disponible.

Los montos son cadenas, no números con coma flotante ("1500.50", no 1500.5).\
"""


def _driver_desde_entorno(home: Path | None = None):
    """Elige el driver por `WALLET_DRIVER`. Sandbox por defecto.

    Que el default sea el sandbox es deliberado: la primera corrida de alguien
    que clona el repo no tiene que poder mover plata de verdad.
    """
    nombre = os.environ.get("WALLET_DRIVER", "sandbox").lower()
    if nombre == "sandbox":
        # Con estado en disco: el MCP y la CLI son procesos distintos y tienen
        # que ver el mismo saldo.
        base = home or WalletStore().home
        return SandboxDriver(estado_en=Path(base) / "sandbox-estado.json")
    raise SystemExit(
        f"wallet-mcp: driver '{nombre}' desconocido.\n"
        f"  Disponibles ahora: sandbox\n"
        f"  Los drivers reales (mercadopago, bind) se agregan como paquetes aparte."
    )


def build_server(service: WalletService | None = None) -> MCPServer:
    if service is None:
        home = Path(os.environ["WALLET_HOME"]) if os.environ.get("WALLET_HOME") else None
        limite = os.environ.get("WALLET_LIMITE_SIN_APROBACION", "0")
        store = WalletStore(home=home)
        service = WalletService(
            _driver_desde_entorno(store.home), store, limite_sin_aprobacion=limite
        )

    agente = os.environ.get("WALLET_AGENT_ID", "agente")
    marca = Marca(
        titulo=os.environ.get("WALLET_MARCA_TITULO", "Resumen de cuenta"),
        entidad=os.environ.get("WALLET_MARCA_ENTIDAD", ""),
        color=os.environ.get("WALLET_MARCA_COLOR", "#1a1a2e"),
        logo_url=os.environ.get("WALLET_MARCA_LOGO", ""),
        pie=os.environ.get("WALLET_MARCA_PIE", ""),
    )

    server = MCPServer(name="wallet", title="Wallet", version="0.1.0",
                       instructions=INSTRUCTIONS)

    def _err(e: WalletError) -> dict:
        return e.to_dict()

    @server.tool()
    def wallet_capacidades() -> dict:
        """Qué puede hacer el proveedor configurado.

        Consultalo ANTES de prometerle algo al usuario: no todos los
        proveedores permiten transferir. Si una operación no está soportada,
        `motivos` explica por qué.
        """
        caps = service.capacidades()
        return {"driver": service.driver.nombre, **caps.to_dict()}

    @server.tool()
    def wallet_listar() -> list[dict]:
        """Lista las wallets disponibles, con su CVU y su estado."""
        return [w.publico() for w in service.listar_wallets()]

    @server.tool()
    async def wallet_crear(
        nombre: str, tipo_documento: str, documento: str,
        email: str = "", telefono: str = "", etiqueta: str = "",
    ) -> dict:
        """Crea una wallet nueva a nombre de un titular.

        El CVU lo emite el proveedor a nombre del titular, con sus datos reales:
        `tipo_documento` es DNI, CUIT, CUIL o PASAPORTE, y `documento` va sin
        puntos ni guiones.
        """
        try:
            w = await service.crear_wallet(
                nombre=nombre, tipo_documento=tipo_documento, documento=documento,
                email=email or None, telefono=telefono or None, etiqueta=etiqueta,
            )
            return w.publico()
        except WalletError as e:
            return _err(e)

    @server.tool()
    async def wallet_saldo(wallet_id: str) -> dict:
        """Saldo actual de una wallet, consultado al proveedor en el momento."""
        try:
            return (await service.saldo(wallet_id)).to_dict()
        except WalletError as e:
            return _err(e)

    @server.tool()
    async def wallet_movimientos(wallet_id: str, limite: int = 20,
                                 dias: int = 0) -> list[dict]:
        """Movimientos de la wallet, del más nuevo al más viejo.

        `dias` acota a los últimos N días (0 = sin límite de fecha).
        """
        try:
            desde = (time.time() - dias * 86400) if dias > 0 else None
            movs = await service.movimientos(wallet_id, desde=desde, limite=limite)
            return [m.to_dict() for m in movs]
        except WalletError as e:
            return [_err(e)]

    @server.tool()
    async def wallet_resumen(wallet_id: str, formato: str = "texto",
                             dias: int = 30) -> str:
        """Arma un resumen de cuenta de los últimos `dias`.

        `formato`: texto (para pegar en el chat), json (para procesar),
        csv (para planilla) o html (imprimible, se exporta a PDF desde el
        navegador con Ctrl+P).
        """
        if formato not in FORMATOS:
            return f"formato '{formato}' desconocido; hay: {', '.join(FORMATOS)}"
        try:
            wallet = service.obtener_wallet(wallet_id)
            desde = time.time() - dias * 86400 if dias > 0 else None
            movs = await service.movimientos(wallet_id, desde=desde, limite=1000)
            saldo = await service.saldo(wallet_id)
            return generar(
                Resumen(wallet=wallet, movimientos=movs, saldo=saldo,
                        desde=desde, hasta=time.time(), generado_en=time.time()),
                formato, marca,
            )
        except WalletError as e:
            return f"error: {e.message}" + (f" ({e.hint})" if e.hint else "")

    @server.tool()
    async def wallet_proponer_transferencia(
        wallet_id: str, monto: str, cvu: str = "", alias: str = "",
        nombre_destino: str = "", concepto: str = "", clave_idempotencia: str = "",
    ) -> dict:
        """Propone una transferencia. NO la ejecuta.

        Devuelve `requiere_aprobacion` con un `intent_id`: la operación queda
        esperando que una persona la firme. Comunicale al usuario el id, el
        monto y el destino.

        `monto` es una cadena ("1500.50"). El destino se indica con `cvu` o con
        `alias`, no hace falta ambos. `clave_idempotencia` evita que un
        reintento duplique el pago: usá la misma cadena si estás reintentando
        LA MISMA operación, y una distinta si es un pago nuevo.

        NO reintentes esta herramienta tras un `requiere_aprobacion`.
        """
        try:
            intento = await service.proponer_transferencia(
                wallet_id, cvu=cvu or None, alias=alias or None,
                nombre_destino=nombre_destino, monto=monto, concepto=concepto,
                agente=agente, idempotency_key=clave_idempotencia,
            )
            return {"estado": "ejecutado", **intento.to_dict()}
        except WalletError as e:
            return _err(e)

    @server.tool()
    def wallet_estado_intento(intento_id: str) -> dict:
        """Consulta cómo quedó una transferencia propuesta.

        Sirve para saber si el humano ya la aprobó, la rechazó, o sigue
        esperando.
        """
        try:
            return service._intento(intento_id).to_dict()
        except WalletError as e:
            return _err(e)

    @server.tool()
    def wallet_pendientes(wallet_id: str = "") -> list[dict]:
        """Transferencias propuestas que todavía esperan aprobación."""
        return [i.to_dict() for i in service.pendientes(wallet_id=wallet_id or None)]

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
