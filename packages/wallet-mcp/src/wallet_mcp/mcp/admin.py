"""`wallet-admin` — la cara del humano. CLI, no MCP.

Acá vive lo único que el agente no puede hacer: aprobar. Es una CLI y no un
servidor MCP por la misma razón que en el vault — si aprobar fuera una
herramienta, tarde o temprano alguien se la conectaría a un agente "para
agilizar", y ahí se termina la compuerta.
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
import time
from decimal import Decimal
from pathlib import Path

from ..core.errors import WalletError
from ..core.resumen import FORMATOS, Marca, Resumen, generar
from ..core.service import WalletService
from ..core.store import WalletStore
from ..drivers.sandbox import SandboxDriver


def _svc() -> WalletService:
    home = Path(os.environ["WALLET_HOME"]) if os.environ.get("WALLET_HOME") else None
    driver_name = os.environ.get("WALLET_DRIVER", "sandbox").lower()
    if driver_name != "sandbox":
        raise SystemExit(f"driver '{driver_name}' desconocido (por ahora sólo: sandbox)")
    store = WalletStore(home=home)
    return WalletService(
        SandboxDriver(estado_en=store.home / "sandbox-estado.json"), store,
        limite_sin_aprobacion=Decimal(os.environ.get("WALLET_LIMITE_SIN_APROBACION", "0")),
    )


def _fecha(ts: float | None) -> str:
    return time.strftime("%d/%m %H:%M", time.localtime(ts)) if ts else "—"


def cmd_wallets(args) -> int:
    svc = _svc()
    ws = svc.listar_wallets()
    if args.json:
        print(json.dumps([w.publico() for w in ws], indent=2, ensure_ascii=False))
        return 0
    if not ws:
        print("no hay wallets. Creá una con: wallet-admin crear --nombre ... --documento ...")
        return 0
    for w in ws:
        print(f"  {w.id}  {w.etiqueta or '(sin etiqueta)':<22} {w.cvu or '—'}  [{w.estado}]")
        print(f"      titular: {w.titular.nombre}")
    return 0


def cmd_crear(args) -> int:
    svc = _svc()
    w = asyncio.run(svc.crear_wallet(
        nombre=args.nombre, tipo_documento=args.tipo_doc, documento=args.documento,
        email=args.email, telefono=args.telefono, etiqueta=args.etiqueta or "",
    ))
    print(f"wallet creada: {w.id}")
    print(f"  titular: {w.titular.nombre}")
    print(f"  CVU:     {w.cvu}")
    print(f"  alias:   {w.alias}")
    return 0


def cmd_saldo(args) -> int:
    s = asyncio.run(_svc().saldo(args.wallet_id))
    print(f"  disponible: {s.disponible} {s.moneda}")
    return 0


def cmd_pendientes(args) -> int:
    svc = _svc()
    pend = svc.pendientes(wallet_id=args.wallet_id)
    if not pend:
        print("no hay nada esperando aprobación")
        return 0
    print(f"{len(pend)} transferencia(s) esperando aprobación:\n")
    for i in pend:
        d = i.destino
        vence = f"vence {_fecha(i.vence_en)}" if i.vence_en else ""
        print(f"  {i.id}")
        print(f"      {i.monto} {i.moneda}  ->  {d.cvu or d.alias}  {d.nombre}")
        print(f"      concepto: {i.concepto or '—'}")
        print(f"      propuso: {i.creado_por or '?'}  {_fecha(i.creado_en)}  {vence}")
        print()
    print("Para aprobar:  wallet-admin aprobar <id>")
    return 0


def cmd_aprobar(args) -> int:
    svc = _svc()
    intento = svc.store.obtener_intento(args.intento_id)
    if intento is None:
        print(f"no existe el intento {args.intento_id}", file=sys.stderr)
        return 1

    # Confirmación interactiva mostrando lo que se va a hacer. Un `--si` a mano
    # existe para scripts, pero el default es que alguien lea el monto y el
    # destino antes de que salga la plata.
    if not args.si:
        d = intento.destino
        print(f"  monto:    {intento.monto} {intento.moneda}")
        print(f"  destino:  {d.cvu or d.alias}  {d.nombre}")
        print(f"  concepto: {intento.concepto or '—'}")
        print(f"  propuso:  {intento.creado_por or '?'}")
        if input("\n¿Aprobar y ejecutar? [escribí 'si'] ") .strip().lower() not in ("si", "sí"):
            print("cancelado")
            return 1

    quien = args.como or os.environ.get("WALLET_APROBADOR") or getpass.getuser()
    resultado = asyncio.run(svc.aprobar(args.intento_id, aprobado_por=quien))
    print(f"ejecutado. comprobante: {resultado.comprobante}")
    return 0


def cmd_rechazar(args) -> int:
    svc = _svc()
    quien = args.como or getpass.getuser()
    i = svc.rechazar(args.intento_id, motivo=args.motivo or "", rechazado_por=quien)
    print(f"rechazado ({i.motivo_rechazo})")
    return 0


def cmd_historial(args) -> int:
    svc = _svc()
    for i in svc.historial_intentos(wallet_id=args.wallet_id, limite=args.limite):
        marca = {"ejecutado": "OK ", "rechazado": "NO ", "fallido": "ERR",
                 "pendiente_aprobacion": "...", "vencido": "VEN"}.get(str(i.estado), "   ")
        print(f"  {_fecha(i.creado_en)}  {marca} {i.monto:>12} {i.moneda}  "
              f"{(i.destino.cvu or i.destino.alias or '')[:24]}  {i.concepto[:22]}")
    return 0


def cmd_resumen(args) -> int:
    svc = _svc()
    wallet = svc.obtener_wallet(args.wallet_id)
    desde = time.time() - args.dias * 86400 if args.dias > 0 else None
    movs = asyncio.run(svc.movimientos(args.wallet_id, desde=desde, limite=5000))
    saldo = asyncio.run(svc.saldo(args.wallet_id))
    marca = Marca(
        titulo=args.titulo or "Resumen de cuenta",
        entidad=args.entidad or "",
        color=args.color or "#1a1a2e",
        logo_url=args.logo or "",
        pie=args.pie or "",
    )
    salida = generar(
        Resumen(wallet=wallet, movimientos=movs, saldo=saldo, desde=desde,
                hasta=time.time(), generado_en=time.time()),
        args.formato, marca,
    )
    if args.salida:
        Path(args.salida).write_text(salida, encoding="utf-8")
        print(f"escrito en {args.salida}")
    else:
        print(salida)
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="wallet-admin",
        description="Administra wallets y aprueba las transferencias que propone el agente.",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("wallets", help="lista las wallets")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_wallets)

    s = sub.add_parser("crear", help="crea una wallet")
    s.add_argument("--nombre", required=True)
    s.add_argument("--documento", required=True)
    s.add_argument("--tipo-doc", default="DNI", dest="tipo_doc",
                   choices=["DNI", "CUIT", "CUIL", "PASAPORTE"])
    s.add_argument("--email")
    s.add_argument("--telefono")
    s.add_argument("--etiqueta")
    s.set_defaults(func=cmd_crear)

    s = sub.add_parser("saldo", help="saldo de una wallet")
    s.add_argument("wallet_id")
    s.set_defaults(func=cmd_saldo)

    s = sub.add_parser("pendientes", help="qué está esperando tu aprobación")
    s.add_argument("--wallet-id", dest="wallet_id")
    s.set_defaults(func=cmd_pendientes)

    s = sub.add_parser("aprobar", help="aprueba y ejecuta una transferencia")
    s.add_argument("intento_id")
    s.add_argument("--si", action="store_true", help="sin preguntar (para scripts)")
    s.add_argument("--como", help="quién aprueba; por defecto tu usuario")
    s.set_defaults(func=cmd_aprobar)

    s = sub.add_parser("rechazar", help="rechaza una transferencia propuesta")
    s.add_argument("intento_id")
    s.add_argument("--motivo")
    s.add_argument("--como")
    s.set_defaults(func=cmd_rechazar)

    s = sub.add_parser("historial", help="todo lo que se propuso")
    s.add_argument("--wallet-id", dest="wallet_id")
    s.add_argument("--limite", type=int, default=30)
    s.set_defaults(func=cmd_historial)

    s = sub.add_parser("resumen", help="genera el resumen de cuenta")
    s.add_argument("wallet_id")
    s.add_argument("--formato", default="texto", choices=FORMATOS)
    s.add_argument("--dias", type=int, default=30)
    s.add_argument("--salida", help="archivo donde escribirlo")
    s.add_argument("--titulo")
    s.add_argument("--entidad", help="nombre que va en el encabezado")
    s.add_argument("--color", help="color de la marca, ej #1a1a2e")
    s.add_argument("--logo", help="URL del logo")
    s.add_argument("--pie")
    s.set_defaults(func=cmd_resumen)

    return p


def main() -> None:
    args = build_parser().parse_args()
    try:
        raise SystemExit(args.func(args))
    except WalletError as e:
        print(f"error: {e.message}", file=sys.stderr)
        if e.hint:
            print(f"  {e.hint}", file=sys.stderr)
        raise SystemExit(1) from e


if __name__ == "__main__":
    main()
