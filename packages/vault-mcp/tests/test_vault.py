"""Tests del vault.

El foco está en los NO: lo que importa de esta pieza no es que devuelva el
secreto cuando corresponde, sino que no lo devuelva cuando no.
"""
from __future__ import annotations

import os
import time

import pytest

from vault_mcp.core.errors import AccessDenied, InvalidName, SecretNotFound, VaultLocked
from vault_mcp.core.models import SecretKind
from vault_mcp.core.store import VaultStore
from vault_mcp.core.vault import OwnerVault, Vault


@pytest.fixture
def store(tmp_path):
    s = VaultStore(home=tmp_path)
    s.init_key()
    yield s
    s.close()


@pytest.fixture
def owner(store):
    return OwnerVault(store)


# --- lo básico ---------------------------------------------------------------

def test_guardar_y_leer_con_permiso(store, owner):
    owner.put("mp/token", "APP_USR-secreto", kind=SecretKind.TOKEN)
    owner.enable("agente-1", "mp/token")

    v = Vault(store, "agente-1")
    assert v.read("mp/token")["value"] == "APP_USR-secreto"


def test_sin_permiso_no_lee(store, owner):
    owner.put("mp/token", "APP_USR-secreto")

    v = Vault(store, "agente-2")
    with pytest.raises(AccessDenied):
        v.read("mp/token")


def test_el_secreto_inexistente_da_el_mismo_error_que_el_prohibido(store, owner):
    """Si los errores difirieran, un agente mapearía el vault preguntando."""
    owner.put("existe", "valor")
    v = Vault(store, "curioso")

    with pytest.raises(AccessDenied) as e_existe:
        v.read("existe")
    with pytest.raises(AccessDenied) as e_no_existe:
        v.read("no-existe")

    assert e_existe.value.message == e_no_existe.value.message.replace("no-existe", "existe")


def test_revocar_corta_el_acceso(store, owner):
    owner.put("s", "v")
    owner.enable("a", "s")
    v = Vault(store, "a")
    assert v.read("s")["value"] == "v"

    owner.revoke("a", "s")
    with pytest.raises(AccessDenied):
        v.read("s")


def test_listar_solo_muestra_lo_habilitado(store, owner):
    owner.put("uno", "1")
    owner.put("dos", "2")
    owner.put("tres", "3")
    owner.enable("a", "uno")
    owner.enable("a", "tres")

    nombres = {s["name"] for s in Vault(store, "a").list_available()}
    assert nombres == {"uno", "tres"}


def test_listar_no_expone_valores(store, owner):
    owner.put("s", "SUPERSECRETO")
    owner.enable("a", "s")
    for s in Vault(store, "a").list_available():
        assert "value" not in s
        assert "SUPERSECRETO" not in str(s)


# --- vencimiento -------------------------------------------------------------

def test_permiso_vencido_no_sirve(store, owner):
    owner.put("s", "v")
    owner.enable("a", "s", expires_at=time.time() - 1)

    with pytest.raises(AccessDenied):
        Vault(store, "a").read("s")


def test_permiso_con_vencimiento_futuro_sirve(store, owner):
    owner.put("s", "v")
    owner.enable("a", "s", expires_at=time.time() + 3600)
    assert Vault(store, "a").read("s")["value"] == "v"


def test_vencido_no_aparece_en_el_listado(store, owner):
    owner.put("s", "v")
    owner.enable("a", "s", expires_at=time.time() - 1)
    assert Vault(store, "a").list_available() == []


# --- aislamiento entre agentes ------------------------------------------------

def test_un_agente_no_ve_lo_de_otro(store, owner):
    owner.put("de-uno", "x")
    owner.enable("agente-1", "de-uno")

    assert Vault(store, "agente-2").list_available() == []
    with pytest.raises(AccessDenied):
        Vault(store, "agente-2").read("de-uno")


def test_el_agente_no_tiene_forma_de_habilitarse(store):
    """El Vault del agente no expone ningún método de escritura."""
    v = Vault(store, "a")
    for prohibido in ("enable", "put", "revoke", "delete", "grants_of"):
        assert not hasattr(v, prohibido), f"Vault expone {prohibido}: el agente podría escalar"


# --- cifrado -----------------------------------------------------------------

def test_el_valor_no_queda_en_claro_en_el_disco(store, owner, tmp_path):
    owner.put("s", "CADENA-QUE-NO-DEBE-APARECER")
    store.db.execute("PRAGMA wal_checkpoint(FULL)")

    for archivo in tmp_path.glob("vault.db*"):
        assert b"CADENA-QUE-NO-DEBE-APARECER" not in archivo.read_bytes()


def test_los_metadatos_si_son_legibles(store, owner, tmp_path):
    """Contraparte del test anterior: es el trade-off, y queda explícito."""
    owner.put("nombre-visible", "secreto", description="descripcion-visible")
    store.db.execute("PRAGMA wal_checkpoint(FULL)")
    blob = b"".join(a.read_bytes() for a in tmp_path.glob("vault.db*"))
    assert b"nombre-visible" in blob


def test_clave_con_permisos_flojos_no_se_usa(tmp_path):
    s = VaultStore(home=tmp_path)
    s.init_key()
    os.chmod(s.key_path, 0o644)
    s._fernet = None
    with pytest.raises(VaultLocked, match="permisos"):
        _ = s.fernet


# --- versionado y validación --------------------------------------------------

def test_actualizar_sube_la_version(store, owner):
    assert owner.put("s", "v1")["version"] == 1
    assert owner.put("s", "v2")["version"] == 2
    owner.enable("a", "s")
    assert Vault(store, "a").read("s")["value"] == "v2"


def test_nombres_invalidos(store, owner):
    for malo in ("", "con espacio", "../escape", "a" * 200, "@raro"):
        with pytest.raises(InvalidName):
            owner.put(malo, "v")


def test_habilitar_secreto_inexistente_avisa(store, owner):
    with pytest.raises(SecretNotFound):
        owner.enable("a", "no-existe")


def test_borrar_secreto_borra_sus_permisos(store, owner):
    owner.put("s", "v")
    owner.enable("a", "s")
    owner.delete("s")
    assert owner.grants_of("a") == []


# --- auditoría ---------------------------------------------------------------

def test_los_denegados_quedan_registrados(store, owner):
    owner.put("s", "v")
    with pytest.raises(AccessDenied):
        Vault(store, "intruso").read("s")

    denegados = owner.audit(only_denied=True)
    assert len(denegados) == 1
    assert denegados[0]["agent_id"] == "intruso"
    assert denegados[0]["secret_name"] == "s"


def test_las_lecturas_ok_quedan_registradas(store, owner):
    owner.put("s", "v")
    owner.enable("a", "s")
    Vault(store, "a").read("s")

    lecturas = [e for e in owner.audit(agent_id="a") if e["action"] == "leer"]
    assert len(lecturas) == 1 and lecturas[0]["allowed"]


def test_la_auditoria_no_guarda_el_valor(store, owner):
    owner.put("s", "VALOR-SECRETO")
    owner.enable("a", "s")
    Vault(store, "a").read("s")
    assert "VALOR-SECRETO" not in str(owner.audit())
