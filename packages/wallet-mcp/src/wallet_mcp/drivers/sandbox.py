"""Driver de mentira, para probar el flujo completo sin cuenta en ningún lado.

Es la referencia de la interfaz y, en la práctica, el driver más importante del
proyecto: cualquiera clona el repo y ve funcionar el ciclo entero —crear wallet,
consultar saldo, proponer transferencia, aprobarla, ver el comprobante— sin
tramitar credenciales con nadie. Un proyecto que exige una cuenta empresarial
antes de la primera corrida no lo prueba nadie.

Simula lo que un proveedor real hace y que es fácil olvidarse al escribir
contra una API amable: falla si no hay fondos, respeta idempotencia, y puede
inyectar demoras y errores para que se pueda testear el camino triste.
"""
from __future__ import annotations

import asyncio
import random
import time
from decimal import Decimal

from ..core.errors import InsufficientFunds, ProviderError, ValidationError
from ..core.models import (
    Destino,
    EstadoWallet,
    Moneda,
    Movimiento,
    Saldo,
    TipoMovimiento,
    Titular,
    Wallet,
    nuevo_id,
)
from .base import Capacidades


class SandboxDriver:
    """Proveedor simulado, todo en memoria.

    `saldo_inicial` arranca en 100.000 para que se pueda transferir apenas se
    crea la wallet: un sandbox que arranca en cero obliga a inventar un ingreso
    antes de poder probar nada.
    """

    nombre = "sandbox"

    def __init__(
        self,
        *,
        saldo_inicial: Decimal = Decimal("100000.00"),
        demora: float = 0.0,
        tasa_de_error: float = 0.0,
        semilla: int | None = None,
    ):
        self.saldo_inicial = saldo_inicial
        self.demora = demora
        self.tasa_de_error = tasa_de_error
        self._rng = random.Random(semilla)
        self._saldos: dict[str, Decimal] = {}
        self._movs: dict[str, list[Movimiento]] = {}
        self._idem: dict[str, str] = {}

    def capacidades(self) -> Capacidades:
        return Capacidades(
            crear_wallet=True, consultar_saldo=True, listar_movimientos=True,
            transferir=True, cobrar=True, pagar_servicios=False,
            motivos={"pagar_servicios": "el sandbox no simula pago de servicios todavía"},
        )

    async def _tick(self) -> None:
        """Demora y fallo simulados, para poder testear el camino triste."""
        if self.demora:
            await asyncio.sleep(self.demora)
        if self.tasa_de_error and self._rng.random() < self.tasa_de_error:
            raise ProviderError(
                "el proveedor simulado falló a propósito",
                hint="bajá tasa_de_error si no querés esto",
            )

    async def crear_wallet(self, titular: Titular, *, etiqueta: str = "") -> Wallet:
        await self._tick()
        wid = nuevo_id("wal")
        # CVU de 22 dígitos como los de verdad, con prefijo 000 para que se vea
        # de una que es falso y nadie lo copie a un formulario real.
        cvu = "000" + "".join(str(self._rng.randint(0, 9)) for _ in range(19))
        self._saldos[wid] = self.saldo_inicial
        self._movs[wid] = []
        if self.saldo_inicial > 0:
            self._movs[wid].append(Movimiento(
                id=nuevo_id("mov"), wallet_id=wid, tipo=TipoMovimiento.INGRESO,
                monto=self.saldo_inicial, moneda=Moneda.ARS, fecha=time.time(),
                descripcion="Saldo inicial de prueba", contraparte="sandbox",
                saldo_posterior=self.saldo_inicial,
            ))
        return Wallet(
            id=wid, titular=titular, driver=self.nombre, alias_externo=wid,
            cvu=cvu, alias=f"sandbox.{wid[-6:]}", estado=EstadoWallet.ACTIVA,
            etiqueta=etiqueta or "Wallet de prueba",
        )

    async def consultar_saldo(self, wallet: Wallet) -> Saldo:
        await self._tick()
        return Saldo(
            wallet_id=wallet.id,
            disponible=self._saldos.get(wallet.id, Decimal(0)),
            moneda=wallet.moneda,
        )

    async def listar_movimientos(
        self, wallet: Wallet, *, desde: float | None = None,
        hasta: float | None = None, limite: int = 50,
    ) -> list[Movimiento]:
        await self._tick()
        movs = self._movs.get(wallet.id, [])
        if desde is not None:
            movs = [m for m in movs if m.fecha >= desde]
        if hasta is not None:
            movs = [m for m in movs if m.fecha <= hasta]
        return sorted(movs, key=lambda m: m.fecha, reverse=True)[:limite]

    async def transferir(
        self, wallet: Wallet, destino: Destino, monto: Decimal,
        *, concepto: str = "", idempotency_key: str = "",
    ) -> str:
        # La idempotencia se chequea ANTES del _tick: si el reintento viene de
        # un timeout, no tiene que volver a arriesgar el fallo simulado.
        if idempotency_key and idempotency_key in self._idem:
            return self._idem[idempotency_key]

        await self._tick()
        if monto <= 0:
            raise ValidationError("el monto tiene que ser mayor que cero")

        saldo = self._saldos.get(wallet.id, Decimal(0))
        if monto > saldo:
            raise InsufficientFunds(
                f"no alcanza: hay {saldo} y se quieren transferir {monto}",
                hint="consultá el saldo antes de proponer la transferencia",
            )

        self._saldos[wallet.id] = saldo - monto
        comprobante = nuevo_id("cmp")
        self._movs.setdefault(wallet.id, []).append(Movimiento(
            id=nuevo_id("mov"), wallet_id=wallet.id, tipo=TipoMovimiento.EGRESO,
            monto=monto, moneda=wallet.moneda, fecha=time.time(),
            descripcion=concepto or "Transferencia",
            contraparte=destino.nombre or "destino externo",
            contraparte_cvu=destino.cvu or destino.alias or "",
            referencia=comprobante, saldo_posterior=self._saldos[wallet.id],
        ))
        if idempotency_key:
            self._idem[idempotency_key] = comprobante
        return comprobante

    # --- ayudas de test, no son parte del contrato ----------------------------

    def acreditar(self, wallet: Wallet, monto: Decimal, descripcion: str = "Ingreso") -> None:
        """Simula que entró plata. Sólo para tests y demos."""
        self._saldos[wallet.id] = self._saldos.get(wallet.id, Decimal(0)) + monto
        self._movs.setdefault(wallet.id, []).append(Movimiento(
            id=nuevo_id("mov"), wallet_id=wallet.id, tipo=TipoMovimiento.INGRESO,
            monto=monto, moneda=wallet.moneda, fecha=time.time(),
            descripcion=descripcion, contraparte="acreditación simulada",
            saldo_posterior=self._saldos[wallet.id],
        ))
