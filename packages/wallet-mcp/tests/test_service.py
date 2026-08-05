"""Tests del servicio de wallets.

El foco está en la regla que sostiene la pieza: el agente propone, no ejecuta.
Y en que reintentar nunca transfiera dos veces.
"""
from __future__ import annotations

import time
from decimal import Decimal

import pytest

from wallet_mcp.core.errors import (
    ApprovalRequired,
    DuplicateOperation,
    InsufficientFunds,
    UnknownWallet,
    UnsupportedOperation,
    ValidationError,
)
from wallet_mcp.core.models import EstadoIntento
from wallet_mcp.core.service import WalletService
from wallet_mcp.core.store import WalletStore
from wallet_mcp.drivers.base import Capacidades
from wallet_mcp.drivers.sandbox import SandboxDriver


@pytest.fixture
def store(tmp_path):
    s = WalletStore(home=tmp_path)
    yield s
    s.close()


@pytest.fixture
def driver():
    return SandboxDriver(semilla=42)


@pytest.fixture
def svc(driver, store):
    return WalletService(driver, store)


@pytest.fixture
async def wallet(svc):
    return await svc.crear_wallet(
        nombre="Juan Pérez", tipo_documento="DNI", documento="30123456",
        email="juan@ejemplo.com", etiqueta="Mi cuenta",
    )


# --- alta --------------------------------------------------------------------

async def test_crear_wallet_devuelve_cvu(svc):
    w = await svc.crear_wallet(nombre="Ana", tipo_documento="DNI", documento="12345678")
    assert w.cvu and len(w.cvu) == 22
    assert w.titular.nombre == "Ana"


async def test_la_wallet_persiste(svc, wallet):
    assert svc.obtener_wallet(wallet.id).id == wallet.id
    assert len(svc.listar_wallets()) == 1


async def test_wallet_inexistente(svc):
    with pytest.raises(UnknownWallet):
        svc.obtener_wallet("wal_nada")


async def test_titular_sin_nombre_falla(svc):
    with pytest.raises(ValidationError):
        await svc.crear_wallet(nombre="  ", tipo_documento="DNI", documento="123")


async def test_el_documento_no_se_expone_al_agente(svc, wallet):
    """El agente no necesita el DNI para consultar un saldo."""
    publico = wallet.publico()
    assert "30123456" not in str(publico)


async def test_el_documento_enmascarado_deja_ver_el_final(wallet):
    m = wallet.titular.enmascarado()
    assert m["documento"].endswith("456")
    assert "30123" not in m["documento"]


# --- consulta ----------------------------------------------------------------

async def test_saldo_inicial(svc, wallet):
    s = await svc.saldo(wallet.id)
    assert s.disponible == Decimal("100000.00")


async def test_movimientos_incluyen_el_saldo_inicial(svc, wallet):
    movs = await svc.movimientos(wallet.id)
    assert len(movs) == 1 and movs[0].tipo == "ingreso"


# --- la regla central: el agente propone, no ejecuta --------------------------

async def test_transferir_requiere_aprobacion(svc, wallet):
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(
            wallet.id, cvu="0000031000000000000001", monto="5000", agente="agente-1"
        )
    assert e.value.intent_id


async def test_la_plata_no_se_mueve_hasta_aprobar(svc, wallet):
    antes = (await svc.saldo(wallet.id)).disponible
    with pytest.raises(ApprovalRequired):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="5000")
    assert (await svc.saldo(wallet.id)).disponible == antes


async def test_aprobar_ejecuta(svc, wallet):
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="5000")

    intento = await svc.aprobar(e.value.intent_id, aprobado_por="lucas")
    assert intento.estado == EstadoIntento.EJECUTADO
    assert intento.comprobante
    assert (await svc.saldo(wallet.id)).disponible == Decimal("95000.00")


async def test_aprobacion_anonima_rechazada(svc, wallet):
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="100")
    with pytest.raises(ValidationError, match="quién aprueba"):
        await svc.aprobar(e.value.intent_id, aprobado_por="")


async def test_rechazar_no_mueve_plata(svc, wallet):
    antes = (await svc.saldo(wallet.id)).disponible
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="5000")

    i = svc.rechazar(e.value.intent_id, motivo="no lo pedí", rechazado_por="lucas")
    assert i.estado == EstadoIntento.RECHAZADO
    assert (await svc.saldo(wallet.id)).disponible == antes


async def test_no_se_aprueba_dos_veces(svc, wallet):
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="1000")
    await svc.aprobar(e.value.intent_id, aprobado_por="lucas")
    with pytest.raises(ValidationError):
        await svc.aprobar(e.value.intent_id, aprobado_por="lucas")


async def test_intento_vencido_no_se_aprueba(driver, store, wallet):
    svc = WalletService(driver, store, vencimiento_intentos=-1)
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="1000")
    with pytest.raises(ValidationError, match="venció"):
        await svc.aprobar(e.value.intent_id, aprobado_por="lucas")


# --- límite sin aprobación ----------------------------------------------------

async def test_el_default_exige_aprobar_hasta_para_un_peso(svc, wallet):
    """El default es cero: nada pasa solo."""
    with pytest.raises(ApprovalRequired):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="1")


async def test_bajo_el_limite_sale_derecho(driver, store, wallet):
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("1000"))
    i = await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="999")
    assert i.estado == EstadoIntento.EJECUTADO


async def test_justo_en_el_limite_sale_derecho(driver, store, wallet):
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("1000"))
    i = await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="1000")
    assert i.estado == EstadoIntento.EJECUTADO


async def test_un_centavo_arriba_del_limite_pide_aprobacion(driver, store, wallet):
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("1000"))
    with pytest.raises(ApprovalRequired):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="1000.01")


# --- idempotencia -------------------------------------------------------------

async def test_misma_clave_no_crea_dos_intentos(svc, wallet):
    with pytest.raises(ApprovalRequired):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="500", idempotency_key="k1")
    with pytest.raises(DuplicateOperation) as e:
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="500", idempotency_key="k1")
    assert e.value.original["monto"] == "500"


async def test_el_duplicado_no_transfiere_de_nuevo(driver, store, wallet):
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("10000"))
    await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="5000", idempotency_key="k")
    saldo = (await svc.saldo(wallet.id)).disponible
    with pytest.raises(DuplicateOperation):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="5000", idempotency_key="k")
    assert (await svc.saldo(wallet.id)).disponible == saldo


# --- validación y errores del proveedor ---------------------------------------

async def test_monto_negativo(svc, wallet):
    with pytest.raises(ValidationError):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="-100")


async def test_monto_cero(svc, wallet):
    with pytest.raises(ValidationError):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="0")


async def test_destino_vacio(svc, wallet):
    with pytest.raises(ValidationError):
        await svc.proponer_transferencia(wallet.id, monto="100")


async def test_sin_fondos(driver, store, wallet):
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("999999999"))
    with pytest.raises(InsufficientFunds):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="500000")


async def test_el_intento_fallido_queda_registrado(driver, store, wallet):
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("999999999"))
    with pytest.raises(InsufficientFunds):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="500000")
    fallidos = [i for i in svc.historial_intentos() if i.estado == EstadoIntento.FALLIDO]
    assert len(fallidos) == 1 and fallidos[0].error


async def test_monto_no_numerico(svc, wallet):
    with pytest.raises(ValidationError):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="mil pesos")


async def test_decimales_exactos(driver, store, wallet):
    """Nada de float: 0.1 + 0.2 tiene que dar 0.3 exacto."""
    svc = WalletService(driver, store, limite_sin_aprobacion=Decimal("100"))
    await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="0.1")
    await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="0.2")
    assert (await svc.saldo(wallet.id)).disponible == Decimal("99999.70")


# --- capacidades del driver ---------------------------------------------------

class DriverSoloLectura:
    nombre = "solo-lectura"

    def capacidades(self):
        return Capacidades(
            consultar_saldo=True, listar_movimientos=True, transferir=False,
            motivos={"transferir": "la API pública de este proveedor no permite money-out"},
        )


async def test_driver_sin_transferir_avisa_antes(store):
    svc = WalletService(DriverSoloLectura(), store)
    with pytest.raises(UnsupportedOperation) as e:
        await svc.proponer_transferencia("wal_x", cvu="0" * 22, monto="100")
    assert "money-out" in e.value.hint


async def test_capacidades_se_pueden_consultar(svc):
    caps = svc.capacidades()
    assert caps.transferir and caps.crear_wallet


# --- pendientes ---------------------------------------------------------------

async def test_listar_pendientes(svc, wallet):
    for _ in range(3):
        with pytest.raises(ApprovalRequired):
            await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="100")
    assert len(svc.pendientes()) == 3


async def test_los_vencidos_salen_de_pendientes(driver, store, wallet):
    svc = WalletService(driver, store, vencimiento_intentos=-1)
    with pytest.raises(ApprovalRequired):
        await svc.proponer_transferencia(wallet.id, cvu="0" * 22, monto="100")
    assert svc.pendientes() == []
