"""Tests del puente con Buzz.

Lo que más importa acá: que una reacción de alguien NO autorizado no mueva un
peso. Es el agujero natural de "aprobar con un emoji en un canal".
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from wallet_mcp.buzz.puente import ConfigBuzz, PuenteBuzz
from wallet_mcp.core.errors import ApprovalRequired
from wallet_mcp.core.models import EstadoIntento
from wallet_mcp.core.service import WalletService
from wallet_mcp.core.store import WalletStore
from wallet_mcp.drivers.sandbox import SandboxDriver

JEFE = "a" * 64      # pubkey autorizada
CURIOSO = "b" * 64   # pubkey cualquiera del canal


class BuzzFalso:
    """Cliente de Buzz de mentira: guarda lo enviado y devuelve reacciones a pedido."""

    def __init__(self):
        self.enviados: list[dict] = []
        self._reacciones: dict[str, list[dict]] = {}
        self._n = 0

    def enviar(self, texto: str, *, responder_a: str | None = None) -> str:
        self._n += 1
        eid = f"evt{self._n:04d}"
        self.enviados.append({"event_id": eid, "texto": texto, "responder_a": responder_a})
        return eid

    def reacciones(self, event_id: str) -> list[dict]:
        return self._reacciones.get(event_id, [])

    def reaccionar(self, event_id: str, emoji: str, pubkey: str) -> None:
        self._reacciones.setdefault(event_id, []).append({"content": emoji, "pubkey": pubkey})


@pytest.fixture
def store(tmp_path):
    s = WalletStore(home=tmp_path)
    yield s
    s.close()


@pytest.fixture
def svc(store, tmp_path):
    return WalletService(SandboxDriver(semilla=7), store)


@pytest.fixture
async def wallet(svc):
    return await svc.crear_wallet(nombre="Ana", tipo_documento="DNI",
                                  documento="27111222", etiqueta="Cuenta test")


@pytest.fixture
def buzz():
    return BuzzFalso()


@pytest.fixture
def puente(svc, buzz):
    cfg = ConfigBuzz(canal="canal-1", clave_privada="nsec-falsa", aprobadores=(JEFE,))
    return PuenteBuzz(svc, cfg, cliente=buzz)


async def _proponer(svc, wallet, monto="5000"):
    with pytest.raises(ApprovalRequired) as e:
        await svc.proponer_transferencia(
            wallet.id, cvu="0" * 22, monto=monto, nombre_destino="Proveedor",
            concepto="Factura 1", agente="agente-x",
        )
    return e.value.intent_id


# --- publicación --------------------------------------------------------------

async def test_publica_el_pendiente(puente, svc, wallet, buzz):
    await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    assert len(buzz.enviados) == 1


async def test_el_mensaje_tiene_lo_necesario_para_decidir(puente, svc, wallet, buzz):
    await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    texto = buzz.enviados[0]["texto"]
    assert "5000" in texto
    assert "Factura 1" in texto
    assert "agente-x" in texto
    assert "Cuenta test" in texto


async def test_no_publica_dos_veces_el_mismo(puente, svc, wallet, buzz):
    await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    await puente.revisar_una_vez()
    assert len(buzz.enviados) == 1


# --- la regla central: sólo los autorizados ----------------------------------

async def test_reaccion_de_no_autorizado_no_hace_nada(puente, svc, wallet, buzz):
    iid = await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    saldo_antes = (await svc.saldo(wallet.id)).disponible

    buzz.reaccionar(buzz.enviados[0]["event_id"], "✅", CURIOSO)
    await puente.revisar_una_vez()

    assert svc.store.obtener_intento(iid).estado == EstadoIntento.PENDIENTE
    assert (await svc.saldo(wallet.id)).disponible == saldo_antes


async def test_reaccion_de_autorizado_aprueba_y_ejecuta(puente, svc, wallet, buzz):
    iid = await _proponer(svc, wallet)
    await puente.revisar_una_vez()

    buzz.reaccionar(buzz.enviados[0]["event_id"], "✅", JEFE)
    await puente.revisar_una_vez()

    intento = svc.store.obtener_intento(iid)
    assert intento.estado == EstadoIntento.EJECUTADO
    assert intento.comprobante
    assert (await svc.saldo(wallet.id)).disponible == Decimal("95000.00")


async def test_sin_aprobadores_configurados_nadie_aprueba(svc, wallet, buzz):
    """El default seguro: lista vacía = nadie puede aprobar desde el chat."""
    cfg = ConfigBuzz(canal="c", clave_privada="k", aprobadores=())
    puente = PuenteBuzz(svc, cfg, cliente=buzz)
    iid = await _proponer(svc, wallet)
    await puente.revisar_una_vez()

    buzz.reaccionar(buzz.enviados[0]["event_id"], "✅", JEFE)
    await puente.revisar_una_vez()
    assert svc.store.obtener_intento(iid).estado == EstadoIntento.PENDIENTE


# --- rechazo ------------------------------------------------------------------

async def test_rechazo_no_mueve_plata(puente, svc, wallet, buzz):
    iid = await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    antes = (await svc.saldo(wallet.id)).disponible

    buzz.reaccionar(buzz.enviados[0]["event_id"], "❌", JEFE)
    await puente.revisar_una_vez()

    assert svc.store.obtener_intento(iid).estado == EstadoIntento.RECHAZADO
    assert (await svc.saldo(wallet.id)).disponible == antes


async def test_si_hay_aprobacion_y_rechazo_gana_el_rechazo(puente, svc, wallet, buzz):
    """Ante desacuerdo, la opción segura es no mover la plata."""
    iid = await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    eid = buzz.enviados[0]["event_id"]

    buzz.reaccionar(eid, "✅", JEFE)
    buzz.reaccionar(eid, "❌", JEFE)
    await puente.revisar_una_vez()

    assert svc.store.obtener_intento(iid).estado == EstadoIntento.RECHAZADO


# --- respuesta en el hilo -----------------------------------------------------

async def test_avisa_el_comprobante_en_el_hilo(puente, svc, wallet, buzz):
    await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    eid = buzz.enviados[0]["event_id"]

    buzz.reaccionar(eid, "✅", JEFE)
    await puente.revisar_una_vez()

    respuesta = buzz.enviados[-1]
    assert respuesta["responder_a"] == eid
    assert "omprobante" in respuesta["texto"]


async def test_avisa_cuando_falla(puente, svc, wallet, buzz):
    """Sin fondos: el humano tiene que enterarse en el chat, no en un log."""
    iid = await _proponer(svc, wallet, monto="500000")
    await puente.revisar_una_vez()
    buzz.reaccionar(buzz.enviados[0]["event_id"], "✅", JEFE)
    await puente.revisar_una_vez()

    assert "No se pudo" in buzz.enviados[-1]["texto"]
    assert svc.store.obtener_intento(iid).estado == EstadoIntento.FALLIDO


# --- emojis equivalentes ------------------------------------------------------

@pytest.mark.parametrize("emoji", ["✅", "👍", "✔️"])
async def test_varios_emojis_aprueban(svc, wallet, buzz, emoji):
    cfg = ConfigBuzz(canal="c", clave_privada="k", aprobadores=(JEFE,))
    puente = PuenteBuzz(svc, cfg, cliente=buzz)
    iid = await _proponer(svc, wallet, monto="100")
    await puente.revisar_una_vez()
    buzz.reaccionar(buzz.enviados[0]["event_id"], emoji, JEFE)
    await puente.revisar_una_vez()
    assert svc.store.obtener_intento(iid).estado == EstadoIntento.EJECUTADO


async def test_un_emoji_cualquiera_no_aprueba(puente, svc, wallet, buzz):
    iid = await _proponer(svc, wallet)
    await puente.revisar_una_vez()
    buzz.reaccionar(buzz.enviados[0]["event_id"], "🎉", JEFE)
    await puente.revisar_una_vez()
    assert svc.store.obtener_intento(iid).estado == EstadoIntento.PENDIENTE
