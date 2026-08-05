"""Servidor MCP del vault — la cara que ve el AGENTE.

Expone dos herramientas y nada más: listar lo habilitado y leer uno. No hay
forma de guardar, habilitar ni revocar desde acá; para eso está `vault-admin`,
que es otro programa.

La identidad del agente sale de `VAULT_AGENT_ID`, del entorno, NO de un
parámetro de la herramienta. Es la decisión de seguridad central de este
archivo: si el agente pudiera pasar su propio `agent_id`, pedir los secretos
de otro agente sería tan difícil como escribir otro nombre.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server import MCPServer

from ..core.errors import VaultError
from ..core.store import VaultStore
from ..core.vault import Vault

INSTRUCTIONS = """\
Bóveda de secretos con permisos por agente.

Tenés acceso SOLAMENTE a los secretos que el dueño te habilitó explícitamente.
`vault_listar` te dice cuáles son; `vault_leer` te da el valor de uno.

Si necesitás un secreto que no tenés habilitado, no reintentes: pedíselo al
humano por su nombre exacto. Cada intento denegado queda registrado.

Los valores que leas son credenciales reales: usalos para la tarea que te
pidieron y no los repitas en tus respuestas ni los escribas en archivos.\
"""


def build_server(store: VaultStore | None = None, agent_id: str | None = None) -> MCPServer:
    agent_id = agent_id or os.environ.get("VAULT_AGENT_ID", "")
    if not agent_id:
        print(
            "vault-mcp: falta VAULT_AGENT_ID.\n"
            "  Es la identidad del agente que va a pedir secretos, y es lo que\n"
            "  decide qué puede leer. Ejemplo:\n"
            "    VAULT_AGENT_ID=agente-contable vault-mcp",
            file=sys.stderr,
        )
        raise SystemExit(2)

    home = Path(os.environ["VAULT_HOME"]) if os.environ.get("VAULT_HOME") else None
    store = store or VaultStore(home=home)
    vault = Vault(store, agent_id)

    server = MCPServer(
        name="vault",
        title="Vault de secretos",
        version="0.1.0",
        instructions=INSTRUCTIONS,
    )

    @server.tool()
    def vault_listar() -> list[dict]:
        """Lista los secretos habilitados para vos.

        Devuelve sólo metadatos (nombre, tipo, descripción, usuario, url), nunca
        el valor. Usalo para saber qué tenés disponible antes de pedir un valor
        concreto con vault_leer.
        """
        try:
            return vault.list_available()
        except VaultError as e:
            return [e.to_dict()]

    @server.tool()
    def vault_leer(nombre: str) -> dict:
        """Devuelve el valor de un secreto habilitado para vos.

        `nombre` es el identificador exacto que devuelve vault_listar
        (por ejemplo "mercadopago/token-prod").

        Si no lo tenés habilitado devuelve un error `acceso_denegado`: en ese
        caso NO reintentes ni pruebes variantes del nombre — pedile al humano
        que te lo habilite.
        """
        try:
            return vault.read(nombre)
        except VaultError as e:
            return e.to_dict()

    return server


def main() -> None:
    build_server().run()


if __name__ == "__main__":
    main()
