"""`planilla-watch` — mira la planilla y avisa al canal cuando algo cambia.

    planilla-watch --archivo ~/OneDrive/credenciales.xlsx --canal <uuid>
    planilla-watch --rclone ECYA:Credenciales/credenciales.xlsx --canal <uuid>
    planilla-watch ... --seguir          # se queda mirando

No depende del resto de las piezas: es un programa suelto que sirve aunque no
uses ni la wallet ni el vault. Esa independencia es a propósito — el estudio
puede tener el aviso andando mañana sin adoptar nada más.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .diff import comparar, resumir
from .planilla import Foto, leer


def estado_por_defecto() -> Path:
    return Path(os.environ.get(
        "PLANILLA_ESTADO", Path.home() / ".local/share/planilla-watch/estado.json"))


def cargar_estado(ruta: Path) -> Foto | None:
    if not ruta.exists():
        return None
    try:
        return Foto.desde_dict(json.loads(ruta.read_text()))
    except (json.JSONDecodeError, OSError, KeyError):
        # Estado ilegible: se arranca de cero. Peor que perder el historial
        # sería reventar y dejar de avisar.
        return None


def guardar_estado(ruta: Path, foto: Foto) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = ruta.with_suffix(".tmp")
    tmp.write_text(json.dumps(foto.a_dict(), ensure_ascii=False))
    tmp.replace(ruta)  # atómico: nunca queda un estado a medio escribir


def bajar_con_rclone(remoto: str, destino: Path) -> Path:
    """Trae el archivo del remoto. Devuelve la ruta local."""
    if shutil.which("rclone") is None:
        raise SystemExit("falta rclone y se pidió --rclone")
    destino.parent.mkdir(parents=True, exist_ok=True)
    p = subprocess.run(
        ["rclone", "copyto", remoto, str(destino)],
        capture_output=True, text=True, timeout=180,
    )
    if p.returncode != 0:
        detalle = (p.stderr or p.stdout).strip().splitlines()[-1:] or ["sin detalle"]
        raise SystemExit(
            f"rclone no pudo traer {remoto}:\n  {detalle[0]}\n"
            f"  Si dice 'drive_id', reconectá con: rclone config reconnect {remoto.split(':')[0]}:"
        )
    return destino


def avisar_a_buzz(texto: str, canal: str, *, binario: str = "buzz",
                  relay: str = "", clave: str = "") -> bool:
    env = {**os.environ}
    if relay:
        env["BUZZ_RELAY_URL"] = relay
    if clave:
        env["BUZZ_PRIVATE_KEY"] = clave
    p = subprocess.run(
        [binario, "messages", "send", "--channel", canal, "--content", texto],
        env=env, capture_output=True, text=True, timeout=30,
    )
    if p.returncode != 0:
        print(f"  no pude avisar a Buzz: {(p.stderr or p.stdout).strip()[:160]}", file=sys.stderr)
        return False
    return True


def una_ronda(args, estado: Path) -> int:
    ruta = Path(args.archivo) if args.archivo else bajar_con_rclone(
        args.rclone, Path(args.cache or "/tmp/planilla-watch.xlsx"))

    if not ruta.exists():
        raise SystemExit(f"no encuentro la planilla: {ruta}")

    ahora = leer(ruta, fila_encabezado=args.fila_encabezado)
    antes = cargar_estado(estado)

    if antes is None:
        # Primera corrida: se guarda la línea de base y NO se avisa. Avisar de
        # las 200 filas existentes como si fueran nuevas es la forma más rápida
        # de que alguien silencie el canal el primer día.
        guardar_estado(estado, ahora)
        print(f"  línea de base guardada: {len(ahora.filas)} entradas en "
              f"{len(ahora.hojas)} pestaña(s). Desde ahora sí aviso los cambios.")
        return 0

    cambios = comparar(antes, ahora)
    if not cambios:
        if args.verboso:
            print("  sin cambios")
        return 0

    texto = resumir(cambios, quien=args.quien or "")
    print(texto)
    if args.canal and not args.simular:
        avisar_a_buzz(texto, args.canal, binario=args.buzz,
                      relay=args.relay or "", clave=args.clave or "")
    guardar_estado(estado, ahora)
    return len(cambios)


def main() -> None:
    p = argparse.ArgumentParser(
        prog="planilla-watch",
        description="Vigila la planilla de credenciales y avisa al canal cuando cambia.",
        epilog="El valor de las claves NUNCA se publica ni se guarda: sólo su hash.",
    )
    origen = p.add_mutually_exclusive_group(required=True)
    origen.add_argument("--archivo", help="ruta local del .xlsx")
    origen.add_argument("--rclone", help="remoto rclone, ej: ECYA:Cred/credenciales.xlsx")

    p.add_argument("--canal", help="UUID del canal de Buzz donde avisar")
    p.add_argument("--quien", help="a quién atribuirle el cambio")
    p.add_argument("--seguir", action="store_true", help="se queda mirando")
    p.add_argument("--intervalo", type=int, default=300, help="segundos entre chequeos")
    p.add_argument("--simular", action="store_true", help="muestra el aviso pero no lo manda")
    p.add_argument("--fila-encabezado", type=int, default=1, dest="fila_encabezado")
    p.add_argument("--estado", help="dónde guardar la foto anterior")
    p.add_argument("--cache", help="dónde dejar el archivo bajado con rclone")
    p.add_argument("--buzz", default=os.environ.get("BUZZ_BINARY", "buzz"))
    p.add_argument("--relay", default=os.environ.get("BUZZ_RELAY_URL", ""))
    p.add_argument("--clave", default=os.environ.get("BUZZ_PRIVATE_KEY", ""))
    p.add_argument("--verboso", action="store_true")
    args = p.parse_args()

    estado = Path(args.estado) if args.estado else estado_por_defecto()

    if not args.seguir:
        raise SystemExit(0 if una_ronda(args, estado) >= 0 else 1)

    print(f"  vigilando cada {args.intervalo}s (Ctrl-C para cortar)")
    while True:
        try:
            una_ronda(args, estado)
        except SystemExit as e:
            print(f"  {e}", file=sys.stderr)
        except Exception as e:
            # El bucle no se cae por un error puntual: si OneDrive no responde
            # un rato, se reintenta en la próxima vuelta.
            print(f"  error en la ronda: {e}", file=sys.stderr)
        time.sleep(args.intervalo)


if __name__ == "__main__":
    main()
