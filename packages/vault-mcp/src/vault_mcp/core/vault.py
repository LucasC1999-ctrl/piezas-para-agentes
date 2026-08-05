"""El vault: secretos con permiso por agente.

Toda decisión de "¿este agente puede?" pasa por acá. El store de abajo puede
leer cualquier cosa; es esta clase la que dice que no. Concentrarlo en un
archivo es lo que hace que la seguridad se pueda auditar leyendo cien líneas
en vez de rastreando consultas por todo el proyecto.

Dos modos, y la diferencia es el punto entero de la pieza:

- **modo agente** (`Vault`): leer y listar, sólo lo habilitado. No puede
  otorgarse permisos ni ver lo que no le dieron.
- **modo dueño** (`OwnerVault`): guardar, habilitar, revocar, auditar.

Son clases distintas y no una bandera booleana a propósito. Un `if es_admin`
suelto es una línea de distancia entre un agente y el vault completo; para
llegar a `OwnerVault` hay que instanciar otra clase, que es algo que no pasa
por accidente ni por un prompt bien redactado.
"""
from __future__ import annotations

import re
import time

from .errors import AccessDenied, InvalidName, SecretNotFound
from .models import AccessLog, Grant, Secret, SecretKind
from .store import VaultStore

# Nombres tipo `mercadopago/token-prod`. Se restringe para que el nombre sea
# seguro de mostrar en logs, URLs y paneles sin escapar nada.
NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._/-]{0,127}$")


def validate_name(name: str) -> str:
    if not NAME_RE.match(name or ""):
        raise InvalidName(
            f"nombre inválido: {name!r}",
            hint="letras, números, punto, guión, guión bajo y barra; hasta 128 caracteres",
        )
    return name


class Vault:
    """Cara del vault que ve un agente. Sólo lectura, sólo lo habilitado."""

    def __init__(self, store: VaultStore, agent_id: str):
        if not agent_id:
            raise InvalidName("hace falta un agent_id", hint="es la identidad del agente que pregunta")
        self.store = store
        self.agent_id = agent_id

    # --- lectura --------------------------------------------------------------

    def list_available(self) -> list[dict]:
        """Metadatos de los secretos habilitados para este agente.

        Nunca incluye el valor: para eso está `read`, que se audita aparte.
        Un agente puede saber que existe `mercadopago/token-prod` y aun así
        no poder leerlo — eso es útil, porque le permite pedirle el permiso
        al humano por su nombre exacto.
        """
        out = []
        now = time.time()
        for grant in self.store.list_grants(self.agent_id):
            if not grant.is_valid(now):
                continue
            meta = self.store.get_secret_meta(grant.secret_name)
            if meta is None:
                continue  # el secreto se borró; el grant huérfano se ignora
            d = meta.public()
            d["expires_at"] = grant.expires_at
            out.append(d)
        self._log("listar", "*", True, f"{len(out)} secretos")
        return out

    def read(self, name: str) -> dict:
        """Devuelve el secreto completo, si está habilitado."""
        validate_name(name)
        grant = self.store.get_grant(self.agent_id, name)
        now = time.time()

        if grant is None or not grant.is_valid(now):
            # Mismo error exista o no el secreto: ver errors.AccessDenied.
            motivo = "sin permiso" if grant is None else "permiso vencido"
            self._log("leer", name, False, motivo)
            raise AccessDenied(
                f"el agente '{self.agent_id}' no tiene acceso a '{name}'",
                hint=f"habilitalo con: vault-admin habilitar {self.agent_id} {name}",
            )

        meta = self.store.get_secret_meta(name)
        value = self.store.get_secret_value(name)
        if meta is None or value is None:
            self._log("leer", name, False, "grant huérfano")
            raise AccessDenied(
                f"el agente '{self.agent_id}' no tiene acceso a '{name}'",
                hint="el permiso apunta a un secreto que ya no existe",
            )

        self._log("leer", name, True, f"v{meta.version}")
        d = meta.public()
        d["value"] = value
        return d

    def _log(self, action: str, name: str, allowed: bool, detail: str = "") -> None:
        self.store.log(AccessLog(time.time(), self.agent_id, name, action, allowed, detail))


class OwnerVault:
    """Cara del dueño: crear secretos y repartir permisos.

    No se expone en el MCP que usa el agente. Vive en `vault-admin`, que corre
    por separado.
    """

    def __init__(self, store: VaultStore, owner_id: str = "owner"):
        self.store = store
        self.owner_id = owner_id

    # --- secretos -------------------------------------------------------------

    def put(self, name: str, value: str, *, kind: SecretKind | str = SecretKind.NOTE,
            description: str = "", username: str | None = None, url: str | None = None,
            tags: tuple[str, ...] = ()) -> dict:
        validate_name(name)
        if not value:
            raise InvalidName("el valor no puede estar vacío", hint="para borrar usá `borrar`")
        secret = Secret(
            name=name, kind=SecretKind(kind), description=description,
            username=username, url=url, tags=tuple(tags),
        )
        saved = self.store.put_secret(secret, value)
        self._log("guardar", name, True, f"v{saved.version}")
        return saved.public()

    def list_all(self) -> list[dict]:
        """Todos los secretos, con a qué agentes están habilitados.

        Es la vista que contesta la pregunta que importa antes de un incidente:
        "¿quién puede leer esto?".
        """
        by_secret: dict[str, list[str]] = {}
        for g in self.store.list_grants():
            by_secret.setdefault(g.secret_name, []).append(g.agent_id)
        out = []
        for s in self.store.list_secrets():
            d = s.public()
            d["habilitado_para"] = sorted(by_secret.get(s.name, []))
            out.append(d)
        return out

    def delete(self, name: str) -> bool:
        validate_name(name)
        ok = self.store.delete_secret(name)  # los grants caen por ON DELETE CASCADE
        self._log("borrar", name, ok, "" if ok else "no existía")
        return ok

    # --- permisos -------------------------------------------------------------

    def enable(self, agent_id: str, secret_name: str, *, note: str = "",
               expires_at: float | None = None) -> dict:
        validate_name(secret_name)
        if self.store.get_secret_meta(secret_name) is None:
            # Acá SÍ decimos que no existe: quien pregunta es el dueño, y un
            # permiso sobre un secreto inexistente es casi siempre un typo.
            raise SecretNotFound(
                f"no existe el secreto '{secret_name}'",
                hint="mirá los nombres con `vault-admin listar`",
            )
        grant = Grant(agent_id=agent_id, secret_name=secret_name,
                      granted_by=self.owner_id, note=note, expires_at=expires_at)
        self.store.put_grant(grant)
        self._log("habilitar", secret_name, True, f"para {agent_id}")
        return {"agent_id": agent_id, "secret_name": secret_name,
                "granted_at": grant.granted_at, "expires_at": expires_at}

    def revoke(self, agent_id: str, secret_name: str) -> bool:
        ok = self.store.delete_grant(agent_id, secret_name)
        self._log("revocar", secret_name, ok, f"a {agent_id}")
        return ok

    def grants_of(self, agent_id: str) -> list[dict]:
        return [
            {"secret_name": g.secret_name, "granted_at": g.granted_at,
             "expires_at": g.expires_at, "note": g.note}
            for g in self.store.list_grants(agent_id)
        ]

    # --- auditoría ------------------------------------------------------------

    def audit(self, *, agent_id: str | None = None, only_denied: bool = False,
              limit: int = 100) -> list[dict]:
        return [e.to_dict() for e in self.store.read_log(
            agent_id=agent_id, only_denied=only_denied, limit=limit)]

    def _log(self, action: str, name: str, allowed: bool, detail: str = "") -> None:
        self.store.log(AccessLog(time.time(), self.owner_id, name, action, allowed, detail))
