"""`vault-admin` — la cara del DUEÑO. CLI, no MCP.

Es una CLI y no un servidor MCP a propósito. Si administrar el vault fuera un
MCP, en algún momento alguien se lo conectaría a un agente "para que se
configure solo" y ahí se termina la separación que sostiene toda la pieza.
Otorgar permisos lo hace una persona, escribiendo.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from ..core.errors import VaultError
from ..core.models import SecretKind
from ..core.store import VaultStore
from ..core.vault import OwnerVault

DURACION_RE = re.compile(r"^(\d+)([hdm])$")


def parse_duracion(txt: str) -> float:
    """'7d' -> timestamp de dentro de 7 días. Acepta m (minutos), h, d."""
    m = DURACION_RE.match(txt)
    if not m:
        raise SystemExit(f"duración inválida: {txt!r} (usá 30m, 12h, 7d)")
    n, unit = int(m.group(1)), m.group(2)
    segundos = {"m": 60, "h": 3600, "d": 86400}[unit] * n
    return time.time() + segundos


def _store() -> VaultStore:
    home = Path(os.environ["VAULT_HOME"]) if os.environ.get("VAULT_HOME") else None
    return VaultStore(home=home)


def _leer_valor(args) -> str:
    """El valor viene de stdin o de --valor.

    stdin es el camino recomendado y va primero en la ayuda: un secreto pasado
    como argumento queda en el historial del shell y en `ps` mientras corre.
    """
    if args.valor is not None:
        return args.valor
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    import getpass
    return getpass.getpass("valor del secreto (no se muestra): ")


def cmd_init(args) -> int:
    store = _store()
    existia = store.key_path.exists()
    store.init_key()
    store.db  # fuerza la creación del esquema
    if existia:
        print(f"ya estaba iniciado en {store.home}")
    else:
        print(f"vault iniciado en {store.home}")
        print(f"  clave maestra: {store.key_path}  (0600 — si la perdés, perdés los secretos)")
        print(f"  base:          {store.db_path}")
    return 0


def cmd_guardar(args) -> int:
    vault = OwnerVault(_store())
    valor = _leer_valor(args)
    meta = vault.put(
        args.nombre, valor,
        kind=SecretKind(args.kind), description=args.desc or "",
        username=args.usuario, url=args.url,
        tags=tuple(t.strip() for t in (args.tags or "").split(",") if t.strip()),
    )
    print(f"guardado {meta['name']} (v{meta['version']})")
    return 0


def cmd_listar(args) -> int:
    vault = OwnerVault(_store())
    secretos = vault.list_all()
    if args.json:
        print(json.dumps(secretos, indent=2, ensure_ascii=False))
        return 0
    if not secretos:
        print("no hay secretos guardados")
        return 0
    ancho = max(len(s["name"]) for s in secretos)
    for s in secretos:
        agentes = ", ".join(s["habilitado_para"]) or "—"
        print(f"  {s['name']:<{ancho}}  {s['kind']:<11} habilitado a: {agentes}")
    return 0


def cmd_habilitar(args) -> int:
    vault = OwnerVault(_store())
    expira = parse_duracion(args.expira) if args.expira else None
    vault.enable(args.agente, args.secreto, note=args.nota or "", expires_at=expira)
    cuando = f" (vence en {args.expira})" if args.expira else ""
    print(f"habilitado: {args.agente} -> {args.secreto}{cuando}")
    return 0


def cmd_revocar(args) -> int:
    vault = OwnerVault(_store())
    if vault.revoke(args.agente, args.secreto):
        print(f"revocado: {args.agente} -> {args.secreto}")
        return 0
    print(f"{args.agente} no tenía habilitado {args.secreto}", file=sys.stderr)
    return 1


def cmd_permisos(args) -> int:
    vault = OwnerVault(_store())
    grants = vault.grants_of(args.agente)
    if not grants:
        print(f"{args.agente} no tiene ningún secreto habilitado")
        return 0
    for g in grants:
        vence = ""
        if g["expires_at"]:
            restante = g["expires_at"] - time.time()
            vence = f"  vence en {int(restante // 3600)}h" if restante > 0 else "  VENCIDO"
        print(f"  {g['secret_name']}{vence}")
    return 0


def cmd_borrar(args) -> int:
    vault = OwnerVault(_store())
    if vault.delete(args.nombre):
        print(f"borrado {args.nombre} (y sus permisos)")
        return 0
    print(f"no existe {args.nombre}", file=sys.stderr)
    return 1


def cmd_auditoria(args) -> int:
    vault = OwnerVault(_store())
    entradas = vault.audit(agent_id=args.agente, only_denied=args.denegados, limit=args.limite)
    if args.json:
        print(json.dumps(entradas, indent=2, ensure_ascii=False))
        return 0
    if not entradas:
        print("sin registros")
        return 0
    for e in entradas:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(e["ts"]))
        marca = "OK " if e["allowed"] else "NO "
        detalle = f"  {e['detail']}" if e["detail"] else ""
        print(f"  {ts}  {marca} {e['agent_id']:<20} {e['action']:<10} {e['secret_name']}{detalle}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="vault-admin",
        description="Administra el vault: guardar secretos y repartir permisos.",
        epilog="El agente usa `vault-mcp`, que sólo lee lo habilitado.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("init", help="crea la clave maestra y la base")
    s.set_defaults(func=cmd_init)

    s = sub.add_parser("guardar", help="guarda o actualiza un secreto")
    s.add_argument("nombre")
    s.add_argument("--valor", help="valor; si se omite se pide por stdin (recomendado)")
    s.add_argument("--kind", default="note", choices=[str(k) for k in SecretKind])
    s.add_argument("--desc", help="para qué es")
    s.add_argument("--usuario", help="para credenciales: la parte no secreta")
    s.add_argument("--url")
    s.add_argument("--tags", help="separados por coma")
    s.set_defaults(func=cmd_guardar)

    s = sub.add_parser("listar", help="lista los secretos y quién los tiene habilitados")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_listar)

    s = sub.add_parser("habilitar", help="le da un secreto a un agente")
    s.add_argument("agente")
    s.add_argument("secreto")
    s.add_argument("--expira", help="30m, 12h, 7d")
    s.add_argument("--nota")
    s.set_defaults(func=cmd_habilitar)

    s = sub.add_parser("revocar", help="le saca un secreto a un agente")
    s.add_argument("agente")
    s.add_argument("secreto")
    s.set_defaults(func=cmd_revocar)

    s = sub.add_parser("permisos", help="qué tiene habilitado un agente")
    s.add_argument("agente")
    s.set_defaults(func=cmd_permisos)

    s = sub.add_parser("borrar", help="borra un secreto y sus permisos")
    s.add_argument("nombre")
    s.set_defaults(func=cmd_borrar)

    s = sub.add_parser("auditoria", help="quién leyó qué")
    s.add_argument("--agente")
    s.add_argument("--denegados", action="store_true", help="sólo los intentos rechazados")
    s.add_argument("--limite", type=int, default=50)
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_auditoria)

    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(args.func(args))
    except VaultError as e:
        print(f"error: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"  {e.hint}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
