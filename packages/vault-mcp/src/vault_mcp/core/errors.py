"""Errores del vault.

Mismo criterio que en el resto de las piezas: `code` estable para que el
agente ramifique sin parsear texto, `hint` en castellano para el humano que
termina leyéndolo.
"""
from __future__ import annotations


class VaultError(Exception):
    code = "error"

    def __init__(self, message: str, *, hint: str | None = None, **context):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.context = context

    def to_dict(self) -> dict:
        d = {"error": self.code, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        if self.context:
            d["context"] = self.context
        return d


class VaultLocked(VaultError):
    """No hay clave maestra utilizable."""

    code = "vault_bloqueado"


class VaultCorrupted(VaultError):
    """La clave no corresponde a la base."""

    code = "vault_corrupto"


class SecretNotFound(VaultError):
    code = "secreto_inexistente"


class AccessDenied(VaultError):
    """El agente no tiene ese secreto habilitado.

    El mensaje dice SIEMPRE lo mismo exista o no el secreto. Si dijera
    "ese secreto no existe" cuando no existe y "no tenés permiso" cuando
    existe, un agente podría mapear el vault entero a fuerza de preguntar.
    """

    code = "acceso_denegado"


class InvalidName(VaultError):
    code = "nombre_invalido"
