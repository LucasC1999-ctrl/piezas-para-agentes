"""Página de aprobación: dos botones, verde y rojo.

Buzz no tiene botones interactivos en los mensajes — son markdown, y el canvas
es un documento por canal, no por mensaje. Así que el mensaje del chat lleva un
link y el link abre esto: una página que muestra la operación y ofrece dos
botones grandes.

Es mejor que aprobar con una reacción por tres motivos:

  - **Se ve lo que se firma.** Monto, destino y concepto en grande, antes de
    tocar nada. Una reacción se da con el pulgar sin leer.
  - **La confirmación es inequívoca.** Un ✅ puede ser un pulgar arriba de
    "buenísimo"; un botón que dice APROBAR Y TRANSFERIR, no.
  - **El link es de un solo uso y vence.** Una reacción sirve para siempre y la
    puede poner cualquiera del canal.

La página no necesita JavaScript ni pide credenciales: el token de un solo uso
va en el link, y la acción se hace con un POST de formulario. Se abre desde el
teléfono sin instalar nada.
"""
from __future__ import annotations

import html
import secrets
import time
from dataclasses import dataclass, field

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse

from ..core.errors import WalletError
from ..core.service import WalletService

VENCIMIENTO_LINK = 3600.0  # una hora


@dataclass(slots=True)
class Ticket:
    """Permiso de un solo uso para decidir sobre un intento.

    De un solo uso y con vencimiento a propósito: un link de aprobación que
    sirve para siempre es una llave que queda dando vueltas en el historial de
    un chat.
    """

    intento_id: str
    creado_en: float = field(default_factory=time.time)
    usado: bool = False

    def valido(self) -> bool:
        return not self.usado and (time.time() - self.creado_en) < VENCIMIENTO_LINK


class Tickets:
    """Registro en memoria de los links emitidos.

    En memoria a propósito: si el proceso se reinicia, los links viejos dejan
    de servir. Para esto, perder estado es una propiedad deseable.
    """

    def __init__(self):
        self._t: dict[str, Ticket] = {}

    def emitir(self, intento_id: str) -> str:
        token = secrets.token_urlsafe(32)
        self._t[token] = Ticket(intento_id)
        return token

    def leer(self, token: str) -> Ticket | None:
        t = self._t.get(token)
        return t if (t and t.valido()) else None

    def consumir(self, token: str) -> Ticket | None:
        t = self.leer(token)
        if t:
            t.usado = True
        return t


def _pagina(cuerpo: str, *, titulo: str = "Aprobación") -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(titulo)}</title>
<style>
  * {{ box-sizing: border-box; }}
  body {{ font: 16px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
         margin: 0; min-height: 100vh; display: grid; place-items: center;
         background: #f4f5f7; color: #1a1a1a; padding: 1.5rem; }}
  .tarjeta {{ background: #fff; border-radius: 14px; padding: 2rem;
             box-shadow: 0 2px 20px rgba(0,0,0,.09); max-width: 460px; width: 100%; }}
  h1 {{ font-size: 1.15rem; margin: 0 0 1.5rem; color: #444; font-weight: 600; }}
  .monto {{ font-size: 2.4rem; font-weight: 700; letter-spacing: -.02em;
           font-variant-numeric: tabular-nums; margin: 0 0 .25rem; }}
  .moneda {{ font-size: 1.1rem; color: #777; font-weight: 500; }}
  dl {{ margin: 1.5rem 0; display: grid; grid-template-columns: auto 1fr;
       gap: .6rem 1rem; font-size: .92rem; }}
  dt {{ color: #777; }}
  dd {{ margin: 0; font-weight: 500; word-break: break-all; }}
  .aviso {{ background: #fff8e1; border-left: 3px solid #f0a500; padding: .8rem 1rem;
           border-radius: 6px; font-size: .87rem; color: #6b4e00; margin: 1.5rem 0; }}
  form {{ display: grid; grid-template-columns: 1fr 1fr; gap: .8rem; margin-top: 1.75rem; }}
  button {{ font: inherit; font-weight: 600; padding: 1.05rem 1rem; border: 0;
           border-radius: 10px; cursor: pointer; color: #fff; transition: filter .15s; }}
  button:hover {{ filter: brightness(1.08); }}
  button:active {{ transform: translateY(1px); }}
  .si {{ background: #0a7c42; }}
  .no {{ background: #b3261e; }}
  .resultado {{ text-align: center; padding: 1rem 0; }}
  .grande {{ font-size: 3rem; line-height: 1; margin-bottom: .75rem; }}
  code {{ background: #f0f0f2; padding: .15rem .4rem; border-radius: 4px;
         font-size: .85em; }}
  .pie {{ color: #999; font-size: .8rem; text-align: center; margin-top: 1.5rem; }}
</style></head>
<body><div class="tarjeta">{cuerpo}</div></body></html>""")


def router_aprobacion(svc: WalletService, tickets: Tickets) -> APIRouter:
    r = APIRouter(tags=["aprobación"])

    @r.get("/aprobar/{token}", response_class=HTMLResponse)
    def mostrar(token: str, request: Request):
        t = tickets.leer(token)
        if t is None:
            return _pagina(
                '<div class="resultado"><div class="grande">⏳</div>'
                "<h1>Este link ya no sirve</h1>"
                "<p>Los links de aprobación duran una hora y se usan una sola vez.</p>"
                "<p>Pedile al agente que proponga la operación de nuevo.</p></div>",
                titulo="Link vencido",
            )
        try:
            intento = svc._intento(t.intento_id)
        except WalletError:
            return _pagina('<div class="resultado"><div class="grande">❓</div>'
                           "<h1>No encuentro esa operación</h1></div>")

        if str(intento.estado) != "pendiente_aprobacion":
            return _pagina(
                f'<div class="resultado"><div class="grande">✔️</div>'
                f"<h1>Esta operación ya está {html.escape(str(intento.estado))}</h1>"
                f"<p>No hace falta que hagas nada.</p></div>",
                titulo="Ya resuelta",
            )

        d = intento.destino
        e = html.escape
        etiqueta = ""
        try:
            etiqueta = svc.obtener_wallet(intento.wallet_id).etiqueta
        except WalletError:
            pass

        return _pagina(f"""
    <h1>Confirmás esta transferencia?</h1>
    <p class="monto">{e(str(intento.monto))} <span class="moneda">{e(str(intento.moneda))}</span></p>
    <dl>
      <dt>Destino</dt><dd>{e(d.cvu or d.alias or "?")}</dd>
      {f"<dt>A nombre de</dt><dd>{e(d.nombre)}</dd>" if d.nombre else ""}
      {f"<dt>Concepto</dt><dd>{e(intento.concepto)}</dd>" if intento.concepto else ""}
      <dt>Sale de</dt><dd>{e(etiqueta or intento.wallet_id)}</dd>
      <dt>Lo pidió</dt><dd>{e(intento.creado_por or "desconocido")}</dd>
    </dl>
    <div class="aviso">Si aprobás, la plata sale ahora y no se puede deshacer.</div>
    <form method="post" action="/aprobar/{e(token)}">
      <button class="no" name="accion" value="rechazar" type="submit">Rechazar</button>
      <button class="si" name="accion" value="aprobar" type="submit">Aprobar y transferir</button>
    </form>
    <p class="pie"><code>{e(intento.id)}</code></p>""",
            titulo=f"Aprobar {intento.monto} {intento.moneda}")

    @r.post("/aprobar/{token}", response_class=HTMLResponse)
    async def decidir(token: str, accion: str = Form(...), request: Request = None):
        # Se consume ANTES de ejecutar: si el usuario recarga o hace doble clic,
        # el segundo POST ya no encuentra ticket válido y no dispara otro pago.
        t = tickets.consumir(token)
        if t is None:
            return _pagina('<div class="resultado"><div class="grande">⏳</div>'
                           "<h1>Este link ya se usó o venció</h1></div>")

        quien = f"web:{(request.client.host if request and request.client else 'desconocido')}"

        if accion == "rechazar":
            try:
                svc.rechazar(t.intento_id, motivo="rechazado desde la web", rechazado_por=quien)
            except WalletError as e:
                return _pagina(f'<div class="resultado"><div class="grande">⚠️</div>'
                               f"<h1>No se pudo rechazar</h1><p>{html.escape(e.message)}</p></div>")
            return _pagina('<div class="resultado"><div class="grande">🚫</div>'
                           "<h1>Rechazada</h1><p>No se movió nada.</p></div>",
                           titulo="Rechazada")

        try:
            r_ = await svc.aprobar(t.intento_id, aprobado_por=quien)
        except WalletError as e:
            return _pagina(
                f'<div class="resultado"><div class="grande">⚠️</div>'
                f"<h1>No se pudo ejecutar</h1><p>{html.escape(e.message)}</p>"
                f"{f'<p>{html.escape(e.hint)}</p>' if e.hint else ''}</div>",
                titulo="Falló")

        return _pagina(
            f'<div class="resultado"><div class="grande">✅</div>'
            f"<h1>Transferencia realizada</h1>"
            f"<p>Comprobante:<br><code>{html.escape(r_.comprobante or '')}</code></p></div>",
            titulo="Listo")

    return r
