"""Modelo del vault.

Tres cosas y nada más: un secreto, un permiso, y el registro de quién miró qué.

El permiso es deliberadamente un booleano. No hay roles, ni niveles, ni
patrones con comodines: el agente X tiene acceso al secreto Y, o no lo tiene.
Todo esquema de permisos que arranca con "después le agregamos niveles"
termina siendo imposible de auditar de un vistazo, y un permiso que no se
entiende de un vistazo es un permiso que nadie revisa.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum


class SecretKind(StrEnum):
    """Para qué sirve el secreto.

    No cambia cómo se guarda (todo se cifra igual). Sirve para que el agente
    sepa qué recibió sin tener que adivinar por el contenido, y para que un
    frontend lo muestre con el ícono correcto.
    """

    PASSWORD = "password"
    TOKEN = "token"
    API_KEY = "api_key"
    LINK = "link"
    NOTE = "note"
    CREDENTIAL = "credential"  # usuario + contraseña juntos


@dataclass(frozen=True, slots=True)
class Secret:
    """Un secreto guardado. `value` NUNCA se persiste en claro.

    Es inmutable a propósito: actualizar un secreto crea uno nuevo y sube
    `version`. Así un agente que cacheó un valor viejo puede darse cuenta,
    y queda rastro de que la credencial rotó.
    """

    name: str
    kind: SecretKind = SecretKind.NOTE
    description: str = ""
    username: str | None = None  # para CREDENTIAL: la parte no secreta
    url: str | None = None
    tags: tuple[str, ...] = ()
    version: int = 1
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def public(self) -> dict:
        """Metadatos sin el valor secreto.

        Es lo que se puede listar sin exponer nada. Un agente con acceso a
        listar ve QUE existe una credencial de Mercado Pago; para ver el token
        necesita permiso explícito sobre ese secreto.
        """
        return {
            "name": self.name,
            "kind": str(self.kind),
            "description": self.description,
            "username": self.username,
            "url": self.url,
            "tags": list(self.tags),
            "version": self.version,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True, slots=True)
class Grant:
    """Permiso de un agente sobre un secreto. Existe = habilitado.

    Revocar BORRA la fila en vez de poner enabled=False. Un permiso revocado
    que sigue existiendo en la tabla es una trampa: cualquier bug que lea mal
    el booleano vuelve a abrir el acceso. Si no está la fila, no hay acceso
    posible.
    """

    agent_id: str
    secret_name: str
    granted_at: float = field(default_factory=time.time)
    granted_by: str = "owner"
    note: str = ""
    expires_at: float | None = None

    def is_valid(self, now: float | None = None) -> bool:
        if self.expires_at is None:
            return True
        return (now or time.time()) < self.expires_at


@dataclass(frozen=True, slots=True)
class AccessLog:
    """Un intento de acceso. Se registran los denegados también.

    Los denegados son los que más importan: un agente que pide diez secretos
    que no tiene habilitados no es un agente roto, es la señal de que alguien
    le cambió el prompt.
    """

    ts: float
    agent_id: str
    secret_name: str
    action: str  # leer | listar | guardar | habilitar | revocar
    allowed: bool
    detail: str = ""

    def to_dict(self) -> dict:
        return {
            "ts": self.ts,
            "agent_id": self.agent_id,
            "secret_name": self.secret_name,
            "action": self.action,
            "allowed": self.allowed,
            "detail": self.detail,
        }
