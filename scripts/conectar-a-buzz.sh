#!/usr/bin/env bash
# Conecta las piezas (wallet y vault) a un agente de Buzz.
#
#   ./conectar-a-buzz.sh <nombre-del-agente> [--wallet] [--vault]
#
# Sin flags conecta las dos. Ejemplos:
#   ./conectar-a-buzz.sh Claude
#   ./conectar-a-buzz.sh Fizz --vault
#
# Qué hace: agrega los servidores MCP al `mcp_command` del agente en la
# configuración de Buzz desktop, con las variables de entorno que cada pieza
# necesita. Hace backup antes de tocar nada y no pisa lo que ya estaba.
set -euo pipefail

AGENTE="${1:?uso: conectar-a-buzz.sh <nombre-del-agente> [--wallet] [--vault]}"
shift || true

CON_WALLET=0
CON_VAULT=0
for arg in "$@"; do
    case "$arg" in
        --wallet) CON_WALLET=1 ;;
        --vault)  CON_VAULT=1 ;;
        *) echo "opción desconocida: $arg" >&2; exit 1 ;;
    esac
done
# Sin flags: las dos.
if [ "$CON_WALLET" = 0 ] && [ "$CON_VAULT" = 0 ]; then
    CON_WALLET=1; CON_VAULT=1
fi

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BUZZ_CFG="${BUZZ_AGENTS_JSON:-$HOME/.local/share/xyz.block.buzz.app/agents/managed-agents.json}"

[ -f "$BUZZ_CFG" ] || {
    echo "no encuentro la config de Buzz en:" >&2
    echo "  $BUZZ_CFG" >&2
    echo "Abrí Buzz al menos una vez, o pasá la ruta en BUZZ_AGENTS_JSON." >&2
    exit 1
}

# El agent_id que va a usar el vault para decidir permisos sale del NOMBRE del
# agente en Buzz, no de su pubkey: los pubkeys cambian si se recrea el agente y
# los permisos quedarían huérfanos sin que se note.
AGENT_ID="buzz:$(echo "$AGENTE" | tr '[:upper:]' '[:lower:]')"

BACKUP="${BUZZ_CFG}.bak-$(date +%Y%m%d-%H%M%S)"
cp "$BUZZ_CFG" "$BACKUP"

python3 - "$BUZZ_CFG" "$AGENTE" "$AGENT_ID" "$REPO" "$CON_WALLET" "$CON_VAULT" <<'PY'
import json, shlex, sys

cfg_path, agente, agent_id, repo, con_wallet, con_vault = sys.argv[1:7]
con_wallet, con_vault = con_wallet == "1", con_vault == "1"

with open(cfg_path) as f:
    agentes = json.load(f)

objetivo = [a for a in agentes if a.get("name") == agente]
if not objetivo:
    nombres = sorted({a.get("name", "?") for a in agentes})
    print(f"no hay ningún agente llamado '{agente}' en Buzz.", file=sys.stderr)
    print(f"  hay: {', '.join(nombres)}", file=sys.stderr)
    raise SystemExit(1)

# Un mismo nombre puede tener varias entradas (una por comunidad). Se configuran
# todas: si sólo se tocara la primera, el agente andaría en una comunidad y no
# en la otra, que es de los bugs más molestos de diagnosticar.
comandos = []
if con_vault:
    comandos.append(f"VAULT_AGENT_ID={shlex.quote(agent_id)} {repo}/packages/vault-mcp/.venv/bin/vault-mcp")
if con_wallet:
    comandos.append(f"WALLET_AGENT_ID={shlex.quote(agent_id)} {repo}/packages/wallet-mcp/.venv/bin/wallet-mcp")

for a in objetivo:
    previo = (a.get("mcp_command") or "").strip()
    partes = [p for p in previo.split(" && ") if p]
    for cmd in comandos:
        binario = cmd.split()[-1]
        # Idempotente: si ya estaba, no se duplica.
        if not any(binario in p for p in partes):
            partes.append(cmd)
    a["mcp_command"] = " && ".join(partes)

with open(cfg_path, "w") as f:
    json.dump(agentes, f, indent=2)

print(f"configurado '{agente}' ({len(objetivo)} instancia(s))")
print(f"  agent_id para permisos: {agent_id}")
for c in comandos:
    print(f"  + {c.split()[-1].split('/')[-1]}")
PY

echo
echo "backup de la config: $BACKUP"
echo
if [ "$CON_VAULT" = 1 ]; then
    echo "Para que el agente pueda leer un secreto, habilitáselo:"
    echo "    vault-admin habilitar $AGENT_ID <nombre-del-secreto>"
    echo
fi
echo "Reiniciá Buzz desktop para que tome los cambios."
