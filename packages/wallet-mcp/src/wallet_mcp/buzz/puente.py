"""Puente con Buzz: aprobar transferencias desde el chat.

El flujo que cierra la pieza:

    1. el agente propone            -> intento pendiente
    2. el puente lo publica         -> mensaje en un canal de Buzz
    3. una persona reacciona con ✅  -> el puente aprueba y ejecuta
    4. el puente publica el comprobante en el mismo hilo

**La seguridad está en `aprobadores`, no en el canal.** Un canal privado no
alcanza: cualquiera que esté adentro podría reaccionar. Sólo los pubkeys de la
lista pueden aprobar, y una reacción de cualquier otro se ignora y se registra.

Se habla con Buzz por su CLI en vez de por HTTP porque el CLI ya resuelve la
firma Nostr, el manejo de la clave y el formato de los eventos. Duplicar eso
sería reimplementar NIP-01 para no invocar un proceso.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field

from ..core.errors import WalletError
from ..core.models import EstadoIntento, IntentoPago
from ..core.service import WalletService

APROBAR = ("✅", "👍", "✔️")
RECHAZAR = ("❌", "👎", "🚫")


class BuzzError(WalletError):
    code = "buzz"
    retryable = True


@dataclass(slots=True)
class ConfigBuzz:
    """Cómo hablar con Buzz y quién puede aprobar."""

    canal: str
    clave_privada: str
    relay: str = "http://localhost:3000"
    binario: str = "buzz"
    # Pubkeys (hex) que pueden aprobar. VACÍA = nadie aprueba desde el chat.
    # El default seguro es que no funcione, no que funcione para todos.
    aprobadores: tuple[str, ...] = ()
    intervalo: float = 5.0
    # Base pública de la API de aprobación. Con esto, el mensaje lleva un link
    # a la página de botones; sin esto, se cae a las reacciones.
    base_aprobacion: str = ""

    @classmethod
    def desde_entorno(cls) -> ConfigBuzz:
        canal = os.environ.get("BUZZ_CHANNEL", "")
        clave = os.environ.get("BUZZ_PRIVATE_KEY", "")
        if not canal or not clave:
            raise BuzzError(
                "faltan BUZZ_CHANNEL y/o BUZZ_PRIVATE_KEY",
                hint="son el canal donde publicar y la identidad con la que publicar",
            )
        aprob = tuple(
            p.strip() for p in os.environ.get("BUZZ_APROBADORES", "").split(",") if p.strip()
        )
        return cls(
            canal=canal, clave_privada=clave,
            relay=os.environ.get("BUZZ_RELAY_URL", "http://localhost:3000"),
            binario=os.environ.get("BUZZ_BINARY", "buzz"),
            aprobadores=aprob,
            intervalo=float(os.environ.get("BUZZ_INTERVALO", "5")),
            base_aprobacion=os.environ.get("WALLET_APROBACION_URL", ""),
        )


class ClienteBuzz:
    """Envoltorio finito sobre el CLI de `buzz`."""

    def __init__(self, cfg: ConfigBuzz):
        self.cfg = cfg
        if shutil.which(cfg.binario) is None and not os.path.isfile(cfg.binario):
            raise BuzzError(
                f"no encuentro el binario '{cfg.binario}'",
                hint="instalá el CLI de Buzz o poné la ruta en BUZZ_BINARY",
            )

    def _correr(self, *args: str) -> dict | list:
        env = {
            **os.environ,
            "BUZZ_PRIVATE_KEY": self.cfg.clave_privada,
            "BUZZ_RELAY_URL": self.cfg.relay,
        }
        try:
            p = subprocess.run(
                [self.cfg.binario, *args], env=env, capture_output=True,
                text=True, timeout=30,
            )
        except subprocess.TimeoutExpired as e:
            raise BuzzError("el CLI de Buzz no respondió en 30s") from e

        if p.returncode != 0:
            detalle = (p.stderr or p.stdout).strip()
            try:
                detalle = json.loads(detalle).get("message", detalle)
            except json.JSONDecodeError:
                pass
            raise BuzzError(f"buzz {args[0]} falló: {detalle}")

        salida = (p.stdout or "").strip()
        if not salida:
            return {}
        try:
            return json.loads(salida)
        except json.JSONDecodeError:
            return {"raw": salida}

    def enviar(self, texto: str, *, responder_a: str | None = None) -> str:
        """Publica un mensaje y devuelve su event_id."""
        args = ["messages", "send", "--channel", self.cfg.canal, "--content", texto]
        if responder_a:
            args += ["--reply-to", responder_a]
        r = self._correr(*args)
        return r.get("event_id", "") if isinstance(r, dict) else ""

    def reacciones(self, event_id: str) -> list[dict]:
        r = self._correr("reactions", "get", "--event", event_id)
        if isinstance(r, list):
            return r
        return r.get("reactions", []) if isinstance(r, dict) else []


def _plantilla(intento: IntentoPago, etiqueta_wallet: str = "", link: str = "") -> str:
    """El mensaje que ve la persona.

    Tiene que poder decidirse SIN abrir otra pantalla: monto, a quién, por qué y
    quién lo pidió. Una aprobación que obliga a ir a buscar contexto a otro lado
    es una aprobación que se termina dando sin mirar.
    """
    d = intento.destino
    destino = d.cvu or d.alias or "?"
    return (
        f"**Transferencia esperando aprobación**\n\n"
        f"**{intento.monto} {intento.moneda}** → `{destino}`"
        f"{f' ({d.nombre})' if d.nombre else ''}\n"
        f"{f'Concepto: {intento.concepto}' if intento.concepto else ''}\n"
        f"Cuenta: {etiqueta_wallet or intento.wallet_id}\n"
        f"Lo pidió: {intento.creado_por or 'desconocido'}\n\n"
        # El link lleva a una página con dos botones (verde/rojo) que muestra
        # el monto y el destino antes de que se toque nada. Se prefiere al
        # emoji: un ✅ puede ser un pulgar arriba de "buenísimo", un botón que
        # dice APROBAR Y TRANSFERIR no se aprieta sin querer.
        + (f"**[Abrir para decidir]({link})**\n\n" if link else
           "Reaccioná con ✅ para aprobar o ❌ para rechazar.\n")
        + f"`{intento.id}`"
    )


class PuenteBuzz:
    """Publica intentos en Buzz y aplica las reacciones autorizadas."""

    def __init__(self, service: WalletService, cfg: ConfigBuzz,
                 cliente: ClienteBuzz | None = None, tickets=None):
        self.svc = service
        self.cfg = cfg
        self.buzz = cliente or ClienteBuzz(cfg)
        self.tickets = tickets
        # intento_id -> event_id del mensaje publicado
        self._publicados: dict[str, str] = {}
        self._ya_avisados: set[str] = set()

    # --- publicar -------------------------------------------------------------

    def publicar_pendiente(self, intento: IntentoPago) -> str:
        if intento.id in self._publicados:
            return self._publicados[intento.id]
        etiqueta = ""
        try:
            etiqueta = self.svc.obtener_wallet(intento.wallet_id).etiqueta
        except WalletError:
            pass
        link = ""
        if self.tickets is not None and self.cfg.base_aprobacion:
            link = f"{self.cfg.base_aprobacion.rstrip('/')}/aprobar/{self.tickets.emitir(intento.id)}"
        event_id = self.buzz.enviar(_plantilla(intento, etiqueta, link))
        if event_id:
            self._publicados[intento.id] = event_id
        return event_id

    # --- escuchar -------------------------------------------------------------

    def _veredicto(self, event_id: str) -> tuple[str, str] | None:
        """Mira las reacciones y devuelve (accion, pubkey) del primer autorizado.

        Rechazar gana sobre aprobar: si dos personas reaccionan distinto, la
        opción segura es no mover la plata.
        """
        try:
            reacciones = self.buzz.reacciones(event_id)
        except BuzzError:
            return None

        aprobacion: tuple[str, str] | None = None
        for r in reacciones:
            emoji = (r.get("content") or r.get("emoji") or "").strip()
            autor = r.get("pubkey") or r.get("author") or ""
            if autor not in self.cfg.aprobadores:
                continue  # reacción de alguien sin permiso: se ignora
            if emoji in RECHAZAR:
                return ("rechazar", autor)
            if emoji in APROBAR and aprobacion is None:
                aprobacion = ("aprobar", autor)
        return aprobacion

    async def revisar_una_vez(self) -> list[str]:
        """Publica los pendientes nuevos y aplica las reacciones. Devuelve novedades."""
        novedades: list[str] = []

        for intento in self.svc.pendientes():
            event_id = self._publicados.get(intento.id)
            if event_id is None:
                event_id = self.publicar_pendiente(intento)
                if event_id:
                    novedades.append(f"publicado {intento.id}")
                continue

            veredicto = self._veredicto(event_id)
            if veredicto is None:
                continue
            accion, quien = veredicto

            if accion == "aprobar":
                try:
                    r = await self.svc.aprobar(intento.id, aprobado_por=f"buzz:{quien[:12]}")
                    self.buzz.enviar(
                        f"Aprobada y ejecutada.\nComprobante: `{r.comprobante}`",
                        responder_a=event_id,
                    )
                    novedades.append(f"aprobado {intento.id}")
                except WalletError as e:
                    self.buzz.enviar(f"No se pudo ejecutar: {e.message}", responder_a=event_id)
                    novedades.append(f"falló {intento.id}: {e.message}")
            else:
                self.svc.rechazar(intento.id, motivo="rechazado desde Buzz",
                                  rechazado_por=f"buzz:{quien[:12]}")
                self.buzz.enviar("Rechazada. No se movió nada.", responder_a=event_id)
                novedades.append(f"rechazado {intento.id}")

        return novedades

    async def correr(self) -> None:
        """Bucle principal. Se corta con Ctrl-C."""
        if not self.cfg.aprobadores:
            print(
                "  ATENCIÓN: no hay aprobadores configurados (BUZZ_APROBADORES).\n"
                "  Los intentos se van a publicar pero NADIE va a poder aprobarlos\n"
                "  desde el chat. Es el default seguro, pero probablemente no sea\n"
                "  lo que querés."
            )
        print(f"  escuchando el canal {self.cfg.canal} cada {self.cfg.intervalo}s")
        print(f"  aprobadores autorizados: {len(self.cfg.aprobadores)}")
        while True:
            try:
                for n in await self.revisar_una_vez():
                    print(f"  {time.strftime('%H:%M:%S')}  {n}")
            except Exception as e:  # el bucle no se puede caer por un error puntual
                print(f"  error en la ronda: {e}")
            await asyncio.sleep(self.cfg.intervalo)


def main() -> None:
    """`wallet-buzz`: corre el puente."""
    import argparse
    from decimal import Decimal
    from pathlib import Path

    from ..core.store import WalletStore
    from ..drivers.sandbox import SandboxDriver

    p = argparse.ArgumentParser(
        prog="wallet-buzz",
        description="Publica en Buzz las transferencias pendientes y aplica las aprobaciones.",
        epilog="Variables: BUZZ_CHANNEL, BUZZ_PRIVATE_KEY, BUZZ_APROBADORES (pubkeys separadas por coma).",
    )
    p.add_argument("--una-vez", action="store_true", help="una sola ronda y sale")
    args = p.parse_args()

    home = Path(os.environ["WALLET_HOME"]) if os.environ.get("WALLET_HOME") else None
    store = WalletStore(home=home)
    svc = WalletService(
        SandboxDriver(estado_en=store.home / "sandbox-estado.json"), store,
        limite_sin_aprobacion=Decimal(os.environ.get("WALLET_LIMITE_SIN_APROBACION", "0")),
    )
    puente = PuenteBuzz(svc, ConfigBuzz.desde_entorno())

    if args.una_vez:
        for n in asyncio.run(puente.revisar_una_vez()):
            print(f"  {n}")
        return
    try:
        asyncio.run(puente.correr())
    except KeyboardInterrupt:
        print("\n  cortado")


if __name__ == "__main__":
    main()
