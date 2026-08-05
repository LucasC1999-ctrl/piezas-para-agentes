"""Errores del dominio.

Un agente de IA no lee stack traces: lee el mensaje y decide qué hacer. Por eso
cada error lleva un `code` estable (para que el agente ramifique sin parsear
texto) y un `hint` en castellano (para que, cuando se lo muestre al humano, se
entienda sin abrir el código).

`retryable` existe por la misma razón: un agente que recibe "el proveedor no
responde" debería reintentar, y uno que recibe "ese CVU no existe" no. Sin ese
campo, el agente reintenta todo o nada.
"""
from __future__ import annotations


class WalletError(Exception):
    """Base de todos los errores del dominio."""

    code = "error"
    retryable = False

    def __init__(self, message: str, *, hint: str | None = None, **context):
        super().__init__(message)
        self.message = message
        self.hint = hint
        self.context = context

    def to_dict(self) -> dict:
        d = {"error": self.code, "message": self.message, "retryable": self.retryable}
        if self.hint:
            d["hint"] = self.hint
        if self.context:
            d["context"] = self.context
        return d


# --- entrada -----------------------------------------------------------------

class ValidationError(WalletError):
    code = "validacion"


class UnknownWallet(WalletError):
    code = "wallet_desconocida"


class UnknownSecret(WalletError):
    code = "secreto_desconocido"


# --- permisos ----------------------------------------------------------------

class PermissionDenied(WalletError):
    """El agente pidió algo para lo que no tiene permiso.

    NO es retryable a propósito: reintentar un acceso denegado es exactamente
    el patrón que un atacante usaría para tantear, y un agente confundido
    puede generar cientos de intentos sin darse cuenta.
    """

    code = "permiso_denegado"


class ApprovalRequired(WalletError):
    """La operación quedó pendiente de que un humano la apruebe.

    No es un fallo: es el camino feliz de toda operación que mueve plata por
    encima del límite. Lleva el id de la intención para que el agente pueda
    consultarla o mostrarla.
    """

    code = "requiere_aprobacion"

    def __init__(self, message: str, *, intent_id: str, **kw):
        super().__init__(message, **kw)
        self.intent_id = intent_id

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["intent_id"] = self.intent_id
        return d


# --- proveedor ---------------------------------------------------------------

class ProviderError(WalletError):
    """Falló el proveedor (Mercado Pago, BIND, el que sea)."""

    code = "proveedor"
    retryable = True


class UnsupportedOperation(WalletError):
    """El driver no soporta esta operación.

    Existe porque los proveedores NO son intercambiables: la API pública de
    Mercado Pago cobra pero no transfiere a terceros. En vez de fingir que sí
    y fallar en runtime con un error opaco del proveedor, el driver lo declara
    de antemano y el agente se entera antes de prometerle nada al usuario.
    """

    code = "no_soportado"


class InsufficientFunds(WalletError):
    code = "fondos_insuficientes"


class DuplicateOperation(WalletError):
    """Ya se ejecutó una operación con esta misma clave de idempotencia.

    Lleva el resultado original: para el que llama, pedir dos veces lo mismo
    devuelve lo mismo, que es justamente el punto de la idempotencia.
    """

    code = "duplicado"

    def __init__(self, message: str, *, original: dict, **kw):
        super().__init__(message, **kw)
        self.original = original

    def to_dict(self) -> dict:
        d = super().to_dict()
        d["original"] = self.original
        return d
