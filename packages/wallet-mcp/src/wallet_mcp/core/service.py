"""El servicio de wallets: driver + reglas de autorización + rastro.

La regla que define la pieza: **el agente nunca transfiere**. Propone. Crear un
intento y ejecutarlo son dos operaciones distintas, y la segunda requiere una
identidad humana. No hay un parámetro `forzar=True` ni un modo "confiable": si
existiera, el primer prompt injection bien escrito lo encontraría.

`limite_sin_aprobacion` permite que montos chicos salgan derecho. Arranca en
CERO a propósito: que el default sea "todo pasa" es cómo se construyen los
sistemas que un día transfieren solos.
"""
from __future__ import annotations

import time
from decimal import Decimal, InvalidOperation

from ..drivers.base import Capacidades, WalletDriver
from .errors import (
    ApprovalRequired,
    DuplicateOperation,
    UnknownWallet,
    UnsupportedOperation,
    ValidationError,
)
from .models import (
    Destino,
    EstadoIntento,
    IntentoPago,
    Moneda,
    Movimiento,
    Saldo,
    TipoDocumento,
    Titular,
    Wallet,
    nuevo_id,
)
from .store import WalletStore

VENCIMIENTO_POR_DEFECTO = 24 * 3600  # un día


def _a_decimal(monto) -> Decimal:
    """Convierte a Decimal sin pasar por float.

    Nunca float para plata: 0.1 + 0.2 != 0.3, y en un sistema de pagos eso es
    un centavo que aparece o desaparece.
    """
    try:
        return Decimal(str(monto))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise ValidationError(f"monto inválido: {monto!r}") from e


class WalletService:
    def __init__(
        self,
        driver: WalletDriver,
        store: WalletStore,
        *,
        limite_sin_aprobacion: Decimal = Decimal(0),
        vencimiento_intentos: float = VENCIMIENTO_POR_DEFECTO,
    ):
        self.driver = driver
        self.store = store
        self.limite_sin_aprobacion = _a_decimal(limite_sin_aprobacion)
        self.vencimiento_intentos = vencimiento_intentos

    # --- capacidades ----------------------------------------------------------

    def capacidades(self) -> Capacidades:
        return self.driver.capacidades()

    def _exigir(self, operacion: str) -> None:
        caps = self.driver.capacidades()
        if not caps.soporta(operacion):
            raise UnsupportedOperation(
                f"el driver '{self.driver.nombre}' no puede '{operacion}'",
                hint=caps.motivo(operacion),
                driver=self.driver.nombre,
            )

    # --- wallets --------------------------------------------------------------

    async def crear_wallet(
        self, *, nombre: str, tipo_documento: str, documento: str,
        email: str | None = None, telefono: str | None = None, etiqueta: str = "",
    ) -> Wallet:
        self._exigir("crear_wallet")
        try:
            titular = Titular(
                nombre=nombre, tipo_documento=TipoDocumento(tipo_documento.upper()),
                documento=documento, email=email, telefono=telefono,
            )
        except ValueError as e:
            raise ValidationError(str(e)) from e

        wallet = await self.driver.crear_wallet(titular, etiqueta=etiqueta)
        return self.store.guardar_wallet(wallet)

    def obtener_wallet(self, wallet_id: str) -> Wallet:
        w = self.store.obtener_wallet(wallet_id)
        if w is None:
            raise UnknownWallet(
                f"no existe la wallet '{wallet_id}'",
                hint="listá las wallets para ver los ids disponibles",
            )
        return w

    def listar_wallets(self) -> list[Wallet]:
        return self.store.listar_wallets()

    # --- consulta -------------------------------------------------------------

    async def saldo(self, wallet_id: str) -> Saldo:
        self._exigir("consultar_saldo")
        return await self.driver.consultar_saldo(self.obtener_wallet(wallet_id))

    async def movimientos(
        self, wallet_id: str, *, desde: float | None = None,
        hasta: float | None = None, limite: int = 50,
    ) -> list[Movimiento]:
        self._exigir("listar_movimientos")
        return await self.driver.listar_movimientos(
            self.obtener_wallet(wallet_id), desde=desde, hasta=hasta, limite=limite
        )

    # --- movimiento de plata --------------------------------------------------

    async def proponer_transferencia(
        self, wallet_id: str, *, cvu: str | None = None, alias: str | None = None,
        nombre_destino: str = "", monto, concepto: str = "",
        agente: str = "", idempotency_key: str = "",
    ) -> IntentoPago:
        """Crea un intento. NO transfiere.

        Si el monto entra en `limite_sin_aprobacion`, se ejecuta acá mismo. Si
        no, queda pendiente y levanta `ApprovalRequired` — que no es un error
        sino el camino esperado.
        """
        self._exigir("transferir")
        wallet = self.obtener_wallet(wallet_id)
        monto = _a_decimal(monto)

        if monto <= 0:
            raise ValidationError("el monto tiene que ser mayor que cero")

        # Idempotencia antes que nada: un reintento no debe crear otro intento.
        if idempotency_key:
            previo = self.store.buscar_por_idempotency(idempotency_key)
            if previo is not None:
                raise DuplicateOperation(
                    f"ya existe una operación con la clave '{idempotency_key}'",
                    original=previo.to_dict(),
                    hint="si querés repetir el pago, usá otra clave de idempotencia",
                )

        try:
            destino = Destino(cvu=cvu, alias=alias, nombre=nombre_destino)
        except ValueError as e:
            raise ValidationError(str(e)) from e

        intento = IntentoPago(
            id=nuevo_id("int"), wallet_id=wallet.id, destino=destino, monto=monto,
            moneda=wallet.moneda, concepto=concepto, creado_por=agente,
            vence_en=time.time() + self.vencimiento_intentos,
            idempotency_key=idempotency_key,
        )

        if monto <= self.limite_sin_aprobacion:
            intento.estado = EstadoIntento.APROBADO
            intento.aprobado_por = f"automático (≤ {self.limite_sin_aprobacion})"
            intento.aprobado_en = time.time()
            self.store.guardar_intento(intento)
            return await self._ejecutar(intento, wallet)

        self.store.guardar_intento(intento)
        raise ApprovalRequired(
            f"la transferencia de {monto} {wallet.moneda} necesita aprobación",
            intent_id=intento.id,
            hint=f"un humano tiene que aprobarla: wallet-admin aprobar {intento.id}",
        )

    async def aprobar(self, intento_id: str, *, aprobado_por: str) -> IntentoPago:
        """Aprueba y ejecuta. `aprobado_por` identifica a la persona.

        No hay default para `aprobado_por` a propósito: una aprobación sin
        nombre no sirve de nada cuando hay que reconstruir qué pasó.
        """
        if not aprobado_por:
            raise ValidationError(
                "hace falta saber quién aprueba",
                hint="una aprobación anónima no es una aprobación",
            )
        intento = self._intento(intento_id)

        if intento.estado != EstadoIntento.PENDIENTE:
            raise ValidationError(
                f"el intento {intento_id} está '{intento.estado}', no pendiente",
                hint="sólo se puede aprobar algo que esté pendiente",
            )
        if intento.esta_vencido():
            intento.estado = EstadoIntento.VENCIDO
            self.store.guardar_intento(intento)
            raise ValidationError(
                f"el intento {intento_id} venció sin aprobarse",
                hint="creá uno nuevo si la operación sigue siendo válida",
            )

        intento.estado = EstadoIntento.APROBADO
        intento.aprobado_por = aprobado_por
        intento.aprobado_en = time.time()
        self.store.guardar_intento(intento)
        return await self._ejecutar(intento, self.obtener_wallet(intento.wallet_id))

    def rechazar(self, intento_id: str, *, motivo: str = "", rechazado_por: str = "") -> IntentoPago:
        intento = self._intento(intento_id)
        if intento.estado != EstadoIntento.PENDIENTE:
            raise ValidationError(f"el intento está '{intento.estado}', no pendiente")
        intento.estado = EstadoIntento.RECHAZADO
        intento.motivo_rechazo = motivo or "sin motivo"
        intento.aprobado_por = rechazado_por or None
        intento.aprobado_en = time.time()
        return self.store.guardar_intento(intento)

    async def _ejecutar(self, intento: IntentoPago, wallet: Wallet) -> IntentoPago:
        """Manda la operación al proveedor. Sólo se llama con intento aprobado."""
        try:
            comprobante = await self.driver.transferir(
                wallet, intento.destino, intento.monto,
                concepto=intento.concepto,
                # El id del intento ES la clave de idempotencia hacia el
                # proveedor: aunque el llamador no haya pasado ninguna, un
                # reintento interno nunca duplica el pago.
                idempotency_key=intento.idempotency_key or intento.id,
            )
        except Exception as e:
            intento.estado = EstadoIntento.FALLIDO
            intento.error = str(e)
            self.store.guardar_intento(intento)
            raise
        intento.estado = EstadoIntento.EJECUTADO
        intento.comprobante = comprobante
        return self.store.guardar_intento(intento)

    def _intento(self, intento_id: str) -> IntentoPago:
        i = self.store.obtener_intento(intento_id)
        if i is None:
            raise ValidationError(f"no existe el intento '{intento_id}'")
        return i

    def pendientes(self, *, wallet_id: str | None = None) -> list[IntentoPago]:
        self.store.vencer_pendientes()
        return self.store.listar_intentos(wallet_id=wallet_id, estado=EstadoIntento.PENDIENTE)

    def historial_intentos(self, *, wallet_id: str | None = None, limite: int = 50):
        return self.store.listar_intentos(wallet_id=wallet_id, limite=limite)
