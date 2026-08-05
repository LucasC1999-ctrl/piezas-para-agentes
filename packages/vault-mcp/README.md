# vault-mcp

Secretos para agentes de IA, con permiso explícito por agente.

Guardás una vez tus claves, tokens, usuarios y links. Después decidís, uno por
uno, qué agente puede leer qué. El agente no ve lo que no le habilitaste, y no
tiene forma de habilitarse solo.

## Por qué

Un agente con una API key en su variable de entorno tiene esa API key para
siempre y para todo. Si le cambian el prompt, si alguien le pega un mensaje
raro en un canal, si lo compartís con un compañero — la clave se va con él.

Acá el permiso es un dato, no una variable de entorno: se otorga, se revoca, se
vence, y cada lectura queda registrada.

## Instalación

```bash
uv pip install vault-mcp     # o: pip install vault-mcp
vault-admin init             # crea la clave maestra
```

## Uso

Guardás un secreto y decidís quién lo lee:

```bash
vault-admin guardar mp/token-prod --kind token --desc "Mercado Pago producción"
vault-admin habilitar agente-contable mp/token-prod
vault-admin habilitar agente-contable mp/token-prod --expira 7d
```

El agente lo consume por MCP:

```json
{
  "mcpServers": {
    "vault": {
      "command": "vault-mcp",
      "env": { "VAULT_AGENT_ID": "agente-contable" }
    }
  }
}
```

Y ve solamente lo suyo:

```
vault_listar()              -> [{"name": "mp/token-prod", "kind": "token", ...}]
vault_leer("mp/token-prod") -> {"value": "APP_USR-..."}
vault_leer("otra-cosa")     -> error: acceso_denegado
```

## Dos servidores, a propósito

| servidor | quién lo corre | qué puede |
|---|---|---|
| `vault-mcp` | el agente | listar y leer lo habilitado |
| `vault-admin` | vos | guardar, habilitar, revocar, auditar |

Están separados porque si las herramientas de otorgar permiso viven en el mismo
MCP que usa el agente, el agente se habilita solo y el vault no protege nada.
No es una bandera de configuración: son dos programas.

## Auditoría

```bash
vault-admin auditoria --denegados
```

Los accesos denegados son los que más importan. Un agente que pide cinco
secretos que no tiene habilitados no está roto: es la señal de que alguien le
cambió el prompt.

## Cómo guarda

- **Valores**: cifrados con Fernet (AES-128-CBC + HMAC).
- **Clave maestra**: en `~/.local/share/vault-mcp/master.key`, con permisos
  `0600`. Si el archivo tiene permisos más flojos, el vault se niega a abrir.
- **Metadatos** (nombre, descripción, tags): en claro, para poder listar y
  buscar sin descifrar nada. **No pongas el secreto en el nombre.**
- **Base**: SQLite en `~/.local/share/vault-mcp/vault.db`.

Si perdés `master.key`, los secretos no se recuperan. Es a propósito.

## Licencia

Apache-2.0
