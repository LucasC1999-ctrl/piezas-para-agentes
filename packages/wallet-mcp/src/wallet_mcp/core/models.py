"""Modelo de dominio de la wallet.

La decisión que ordena todo este archivo: **la plata no la lleva esta pieza**.
El saldo real vive en el proveedor (Mercado Pago, BIND, el que sea) y el CVU
está a nombre del cliente, no nuestro. Acá no hay un ledger de saldos porque
llevar la cuenta de plata ajena es exactamente lo que convierte un proyecto de
software en una entidad regulada.

Lo que sí se guarda es **qué hizo el agente y quién lo autorizó**: intenciones,
aprobaciones y comprobantes. Eso es un registro de auditoría, no un ledger.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum


class Moneda(StrEnum):
    ARS = "ARS"
    USD = "USD"


class TipoDocumento(StrEnum):
    DNI = "DNI"
    CUIT = "CUIT"
    CUIL = "CUIL"
    PASAPORTE = "PASAPORTE"


class EstadoWallet(StrEnum):
    PENDIENTE = "pendiente"      # falta que el proveedor confirme el alta
    ACTIVA = "activa"
    SUSPENDIDA = "suspendida"
    CERRADA = "cerrada"


class TipoMovimiento(StrEnum):
    INGRESO = "ingreso"
    EGRESO = "egreso"


class EstadoIntento(StrEnum):
    """Ciclo de vida de una operación que mueve plata.

    `PENDIENTE` no es un estado de error: es el estado normal de toda
    transferencia que supera el límite del agente. El agente propone, el humano
    firma, y recién ahí se ejecuta.
    """

    PENDIENTE = "pendiente_aprobacion"
    APROBADO = "aprobado"
    RECHAZADO = "rechazado"
    EJECUTADO = "ejecutado"
    FALLIDO = "fallido"
    VENCIDO = "vencido"


def nuevo_id(prefijo: str) -> str:
    return f"{prefijo}_{uuid.uuid4().hex[:20]}"


@dataclass(frozen=True, slots=True)
class Titular:
    """Quién es el dueño de la wallet.

    Estos datos NO son decorativos: el proveedor los exige para emitir el CVU
    a nombre del cliente, que es lo que nos mantiene fuera de la custodia de
    fondos ajenos. Si el titular fuera opcional, la wallet sería nuestra.

    El documento se guarda porque el proveedor lo pide en el alta. No se expone
    en las respuestas del MCP: un agente no necesita el DNI del cliente para
    consultar un saldo, y todo dato que el agente no necesita es un dato que no
    puede filtrar.
    """

    nombre: str
    tipo_documento: TipoDocumento
    documento: str
    email: str | None = None
    telefono: str | None = None

    def __post_init__(self):
        if not self.nombre.strip():
            raise ValueError("el titular necesita un nombre")
        if not self.documento.strip():
            raise ValueError("el titular necesita un documento")

    def publico(self) -> dict:
        """Sin el documento. Es lo que ve el agente."""
        return {"nombre": self.nombre, "email": self.email}

    def completo(self) -> dict:
        """Con el documento. Sólo para el dueño y para el alta en el proveedor."""
        return {
            "nombre": self.nombre,
            "tipo_documento": str(self.tipo_documento),
            "documento": self.documento,
            "email": self.email,
            "telefono": self.telefono,
        }

    def enmascarado(self) -> dict:
        """Con el documento tapado salvo los últimos 3 dígitos.

        Para pantallas y resúmenes: alcanza para que el cliente reconozca que
        es su cuenta, y no alcanza para que sirva si la captura se filtra.
        """
        doc = self.documento
        visible = doc[-3:] if len(doc) > 3 else doc
        return {
            "nombre": self.nombre,
            "tipo_documento": str(self.tipo_documento),
            "documento": f"{'•' * max(0, len(doc) - 3)}{visible}",
            "email": self.email,
        }


@dataclass(frozen=True, slots=True)
class Wallet:
    """Una cuenta de pago. El CVU lo emite el proveedor, no nosotros."""

    id: str
    titular: Titular
    driver: str                      # "sandbox", "mercadopago", "bind"...
    alias_externo: str | None = None  # id de la cuenta en el proveedor
    cvu: str | None = None
    alias: str | None = None          # alias amigable tipo "mi.alias.mp"
    moneda: Moneda = Moneda.ARS
    estado: EstadoWallet = EstadoWallet.PENDIENTE
    etiqueta: str = ""                # cómo la llama el cliente
    creada_en: float = field(default_factory=time.time)

    def publico(self) -> dict:
        return {
            "id": self.id,
            "etiqueta": self.etiqueta,
            "titular": self.titular.publico(),
            "driver": self.driver,
            "cvu": self.cvu,
            "alias": self.alias,
            "moneda": str(self.moneda),
            "estado": str(self.estado),
            "creada_en": self.creada_en,
        }


@dataclass(frozen=True, slots=True)
class Saldo:
    """Foto del saldo en un instante. Siempre viene del proveedor.

    `consultado_en` no es un detalle: un saldo sin timestamp invita a que el
    agente lo trate como verdad permanente y decida sobre datos viejos.
    """

    wallet_id: str
    disponible: Decimal
    moneda: Moneda
    consultado_en: float = field(default_factory=time.time)
    pendiente: Decimal = Decimal(0)

    def to_dict(self) -> dict:
        return {
            "wallet_id": self.wallet_id,
            "disponible": str(self.disponible),
            "pendiente": str(self.pendiente),
            "moneda": str(self.moneda),
            "consultado_en": self.consultado_en,
        }


@dataclass(frozen=True, slots=True)
class Movimiento:
    """Un movimiento del extracto, tal como lo reporta el proveedor."""

    id: str
    wallet_id: str
    tipo: TipoMovimiento
    monto: Decimal
    moneda: Moneda
    fecha: float
    descripcion: str = ""
    contraparte: str = ""       # nombre de quien mandó o recibió
    contraparte_cvu: str = ""
    referencia: str = ""        # id en el proveedor
    saldo_posterior: Decimal | None = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "tipo": str(self.tipo),
            "monto": str(self.monto),
            "moneda": str(self.moneda),
            "fecha": self.fecha,
            "descripcion": self.descripcion,
            "contraparte": self.contraparte,
            "contraparte_cvu": self.contraparte_cvu,
            "referencia": self.referencia,
            "saldo_posterior": str(self.saldo_posterior) if self.saldo_posterior is not None else None,
        }


@dataclass(frozen=True, slots=True)
class Destino:
    """A dónde va una transferencia."""

    cvu: str | None = None
    alias: str | None = None
    nombre: str = ""

    def __post_init__(self):
        if not self.cvu and not self.alias:
            raise ValueError("el destino necesita un CVU/CBU o un alias")

    def to_dict(self) -> dict:
        return {"cvu": self.cvu, "alias": self.alias, "nombre": self.nombre}


@dataclass(slots=True)
class IntentoPago:
    """Una operación que mueve plata, con su rastro de autorización.

    Es el corazón de la pieza. El agente NO transfiere: crea un intento. Alguien
    con manos lo aprueba, y recién ahí se ejecuta contra el proveedor.

    `idempotency_key` está para que reintentar no duplique un pago. Es la
    diferencia entre un timeout molesto y transferir dos veces.
    """

    id: str
    wallet_id: str
    destino: Destino
    monto: Decimal
    moneda: Moneda
    concepto: str = ""
    estado: EstadoIntento = EstadoIntento.PENDIENTE
    creado_por: str = ""            # id del agente que lo propuso
    creado_en: float = field(default_factory=time.time)
    vence_en: float | None = None
    aprobado_por: str | None = None
    aprobado_en: float | None = None
    motivo_rechazo: str | None = None
    idempotency_key: str = ""
    comprobante: str | None = None  # id de la operación en el proveedor
    error: str | None = None

    def esta_vencido(self, ahora: float | None = None) -> bool:
        if self.vence_en is None:
            return False
        return (ahora or time.time()) > self.vence_en

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "wallet_id": self.wallet_id,
            "destino": self.destino.to_dict(),
            "monto": str(self.monto),
            "moneda": str(self.moneda),
            "concepto": self.concepto,
            "estado": str(self.estado),
            "creado_por": self.creado_por,
            "creado_en": self.creado_en,
            "vence_en": self.vence_en,
            "aprobado_por": self.aprobado_por,
            "aprobado_en": self.aprobado_en,
            "motivo_rechazo": self.motivo_rechazo,
            "comprobante": self.comprobante,
            "error": self.error,
        }
