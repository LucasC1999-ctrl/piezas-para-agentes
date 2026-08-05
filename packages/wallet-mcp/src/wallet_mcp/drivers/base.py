"""Contrato que cumple todo proveedor de wallets.

El diseño central de esta pieza está en `Capacidades`. Los proveedores **no son
intercambiables**: la API pública de Mercado Pago cobra pero no transfiere a
terceros; BIND transfiere pero exige contrato comercial. Un driver que finge
soportar todo y explota en runtime con un error opaco del proveedor es peor que
no tener driver.

Entonces cada driver **declara qué puede hacer**, y el servicio lo consulta
antes de prometerle nada a nadie. El agente se entera de que no puede
transferir cuando pregunta, no después de decirle al cliente que ya está.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol, runtime_checkable

from ..core.models import Destino, Movimiento, Saldo, Titular, Wallet


@dataclass(frozen=True, slots=True)
class Capacidades:
    """Qué sabe hacer un driver.

    Se consulta ANTES de intentar. `motivos` explica en castellano por qué algo
    no está disponible, para que el error que le llega al humano diga "la API
    pública de Mercado Pago no permite transferir a terceros" y no "501".
    """

    crear_wallet: bool = False
    consultar_saldo: bool = False
    listar_movimientos: bool = False
    transferir: bool = False
    cobrar: bool = False          # generar link de pago / QR
    pagar_servicios: bool = False
    motivos: dict[str, str] = None  # operación -> por qué no se puede

    def __post_init__(self):
        if self.motivos is None:
            object.__setattr__(self, "motivos", {})

    def soporta(self, operacion: str) -> bool:
        return bool(getattr(self, operacion, False))

    def motivo(self, operacion: str) -> str:
        return self.motivos.get(
            operacion, f"el driver no implementa '{operacion}'"
        )

    def to_dict(self) -> dict:
        return {
            "crear_wallet": self.crear_wallet,
            "consultar_saldo": self.consultar_saldo,
            "listar_movimientos": self.listar_movimientos,
            "transferir": self.transferir,
            "cobrar": self.cobrar,
            "pagar_servicios": self.pagar_servicios,
            "motivos": dict(self.motivos),
        }


@runtime_checkable
class WalletDriver(Protocol):
    """Lo que tiene que implementar un proveedor.

    Es un Protocol y no una clase base para que escribir un driver nuevo no
    obligue a heredar de nada: alcanza con tener estos métodos. Un contribuidor
    puede escribir su driver en su propio paquete sin depender de éste.

    Todos los métodos pueden levantar `ProviderError` (que es retryable) o
    `UnsupportedOperation` si la capacidad no está declarada.
    """

    nombre: str

    def capacidades(self) -> Capacidades:
        ...

    async def crear_wallet(self, titular: Titular, *, etiqueta: str = "") -> Wallet:
        """Da de alta la cuenta en el proveedor y devuelve la wallet con su CVU."""
        ...

    async def consultar_saldo(self, wallet: Wallet) -> Saldo:
        ...

    async def listar_movimientos(
        self, wallet: Wallet, *, desde: float | None = None,
        hasta: float | None = None, limite: int = 50,
    ) -> list[Movimiento]:
        ...

    async def transferir(
        self, wallet: Wallet, destino: Destino, monto: Decimal,
        *, concepto: str = "", idempotency_key: str = "",
    ) -> str:
        """Ejecuta la transferencia y devuelve el id del comprobante.

        `idempotency_key` NO es opcional en la práctica: si el proveedor la
        soporta hay que pasársela, y si no la soporta el driver tiene que
        implementar la deduplicación por su cuenta. Reintentar una transferencia
        que en realidad salió es el peor bug que puede tener esta pieza.
        """
        ...
