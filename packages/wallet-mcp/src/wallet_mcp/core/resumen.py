"""Resúmenes de cuenta, en varios formatos.

Se generan acá y no en el frontend por una razón práctica: el resumen tiene que
salir igual desde el agente en Buzz, desde una API para una web a medida y
desde la línea de comandos. Si el formato viviera en el frontend, habría tres
resúmenes distintos y uno de ellos estaría mal.

Formatos:
  - `json`: para que un frontend lo dibuje como quiera
  - `csv`:  para abrir en una planilla, que es lo que la gente hace de verdad
  - `html`: imprimible y con marca propia; se abre y se exporta a PDF desde el
            navegador, sin arrastrar una dependencia de PDF al proyecto
  - `texto`: para pegar en un chat, que es donde vive el agente

No hay generador de PDF a propósito: agregarlo mete una dependencia binaria
pesada para algo que el navegador ya hace con Ctrl+P.
"""
from __future__ import annotations

import csv
import html
import io
import json
import time
from dataclasses import dataclass
from decimal import Decimal

from .models import Movimiento, Saldo, TipoMovimiento, Wallet


@dataclass(frozen=True, slots=True)
class Marca:
    """Cómo se ve el resumen. Es lo que hace que el cliente lo sienta suyo."""

    titulo: str = "Resumen de cuenta"
    entidad: str = ""
    color: str = "#1a1a2e"
    color_ingreso: str = "#0a7c42"
    color_egreso: str = "#b3261e"
    logo_url: str = ""
    pie: str = ""


def _fecha(ts: float) -> str:
    return time.strftime("%d/%m/%Y %H:%M", time.localtime(ts))


def _monto(m: Decimal) -> str:
    """Formato argentino: punto de miles, coma decimal."""
    entero, _, dec = f"{m:.2f}".partition(".")
    neg = entero.startswith("-")
    entero = entero.lstrip("-")
    grupos = []
    while len(entero) > 3:
        grupos.insert(0, entero[-3:])
        entero = entero[:-3]
    grupos.insert(0, entero)
    return f"{'-' if neg else ''}{'.'.join(grupos)},{dec}"


@dataclass(frozen=True, slots=True)
class Resumen:
    wallet: Wallet
    movimientos: list[Movimiento]
    saldo: Saldo | None = None
    desde: float | None = None
    hasta: float | None = None
    generado_en: float = 0.0

    @property
    def total_ingresos(self) -> Decimal:
        return sum((m.monto for m in self.movimientos
                    if m.tipo == TipoMovimiento.INGRESO), Decimal(0))

    @property
    def total_egresos(self) -> Decimal:
        return sum((m.monto for m in self.movimientos
                    if m.tipo == TipoMovimiento.EGRESO), Decimal(0))

    # --- formatos -------------------------------------------------------------

    def a_json(self) -> str:
        return json.dumps({
            "wallet": self.wallet.publico(),
            "titular": self.wallet.titular.enmascarado(),
            "periodo": {"desde": self.desde, "hasta": self.hasta},
            "saldo": self.saldo.to_dict() if self.saldo else None,
            "totales": {
                "ingresos": str(self.total_ingresos),
                "egresos": str(self.total_egresos),
                "neto": str(self.total_ingresos - self.total_egresos),
                "cantidad": len(self.movimientos),
            },
            "movimientos": [m.to_dict() for m in self.movimientos],
            "generado_en": self.generado_en or time.time(),
        }, indent=2, ensure_ascii=False)

    def a_csv(self) -> str:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["fecha", "tipo", "descripcion", "contraparte", "cvu",
                    "monto", "moneda", "saldo_posterior", "referencia"])
        for m in sorted(self.movimientos, key=lambda x: x.fecha):
            w.writerow([
                _fecha(m.fecha), str(m.tipo), m.descripcion, m.contraparte,
                m.contraparte_cvu, _monto(m.monto), str(m.moneda),
                _monto(m.saldo_posterior) if m.saldo_posterior is not None else "",
                m.referencia,
            ])
        return buf.getvalue()

    def a_texto(self) -> str:
        """Para pegar en un chat. Ancho fijo, sin colores."""
        t = self.wallet.titular.enmascarado()
        out = [
            f"Resumen — {self.wallet.etiqueta or self.wallet.id}",
            f"Titular: {t['nombre']} ({t['tipo_documento']} {t['documento']})",
        ]
        if self.wallet.cvu:
            out.append(f"CVU: {self.wallet.cvu}")
        if self.desde or self.hasta:
            d = _fecha(self.desde) if self.desde else "inicio"
            h = _fecha(self.hasta) if self.hasta else "hoy"
            out.append(f"Período: {d} a {h}")
        out.append("")
        for m in sorted(self.movimientos, key=lambda x: x.fecha):
            signo = "+" if m.tipo == TipoMovimiento.INGRESO else "-"
            desc = (m.descripcion or m.contraparte or "movimiento")[:34]
            out.append(f"  {_fecha(m.fecha):<17} {desc:<34} {signo}{_monto(m.monto):>14}")
        out += [
            "",
            f"  Ingresos: +{_monto(self.total_ingresos)}",
            f"  Egresos:  -{_monto(self.total_egresos)}",
            f"  Neto:      {_monto(self.total_ingresos - self.total_egresos)}",
        ]
        if self.saldo:
            out.append(f"  Saldo actual: {_monto(self.saldo.disponible)} {self.saldo.moneda}")
        return "\n".join(out)

    def a_html(self, marca: Marca | None = None) -> str:
        """HTML imprimible y autocontenido.

        Sin CSS externo ni fuentes remotas: se guarda como un archivo y se abre
        en cualquier lado, incluso sin internet. `@media print` lo deja listo
        para "Guardar como PDF" desde el navegador.
        """
        marca = marca or Marca()
        e = html.escape
        t = self.wallet.titular.enmascarado()

        filas = []
        for m in sorted(self.movimientos, key=lambda x: x.fecha):
            ingreso = m.tipo == TipoMovimiento.INGRESO
            color = marca.color_ingreso if ingreso else marca.color_egreso
            signo = "+" if ingreso else "−"
            filas.append(f"""
      <tr>
        <td class="fecha">{_fecha(m.fecha)}</td>
        <td>{e(m.descripcion or "Movimiento")}
            {f'<span class="cp">{e(m.contraparte)}</span>' if m.contraparte else ''}</td>
        <td class="num" style="color:{color}">{signo} {_monto(m.monto)}</td>
        <td class="num saldo">{_monto(m.saldo_posterior) if m.saldo_posterior is not None else ""}</td>
      </tr>""")

        logo = f'<img src="{e(marca.logo_url)}" alt="" class="logo">' if marca.logo_url else ""
        periodo = ""
        if self.desde or self.hasta:
            d = _fecha(self.desde) if self.desde else "el inicio"
            h = _fecha(self.hasta) if self.hasta else "hoy"
            periodo = f"<p class='periodo'>Período: {d} — {h}</p>"

        return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{e(marca.titulo)} — {e(self.wallet.etiqueta or self.wallet.id)}</title>
<style>
  :root {{ --tinta: {marca.color}; }}
  * {{ box-sizing: border-box; }}
  body {{ font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
         color: #1a1a1a; margin: 0; padding: 2.5rem 1.5rem; background: #fff; }}
  .hoja {{ max-width: 820px; margin: 0 auto; }}
  header {{ display: flex; align-items: flex-start; justify-content: space-between;
           border-bottom: 3px solid var(--tinta); padding-bottom: 1rem; margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.5rem; margin: 0 0 .25rem; color: var(--tinta); }}
  .entidad {{ font-weight: 600; color: var(--tinta); }}
  .logo {{ max-height: 52px; }}
  .datos {{ color: #555; font-size: .9rem; margin: 0; }}
  .datos strong {{ color: #1a1a1a; }}
  .periodo {{ color: #666; font-size: .875rem; margin: .5rem 0 0; }}
  table {{ width: 100%; border-collapse: collapse; margin: 1.5rem 0; font-size: .9rem; }}
  th {{ text-align: left; font-size: .75rem; text-transform: uppercase;
       letter-spacing: .04em; color: #666; border-bottom: 1px solid #ddd;
       padding: .5rem .6rem; }}
  th.num, td.num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  td {{ padding: .6rem; border-bottom: 1px solid #f0f0f0; vertical-align: top; }}
  td.fecha {{ color: #666; white-space: nowrap; font-variant-numeric: tabular-nums; }}
  td.saldo {{ color: #888; }}
  .cp {{ display: block; color: #777; font-size: .8rem; }}
  .totales {{ margin-left: auto; width: min(320px, 100%); }}
  .totales tr td:first-child {{ color: #555; }}
  .totales tr:last-child td {{ border-top: 2px solid var(--tinta); border-bottom: none;
                               font-weight: 700; font-size: 1.05rem; padding-top: .7rem; }}
  footer {{ margin-top: 2.5rem; padding-top: 1rem; border-top: 1px solid #eee;
           color: #888; font-size: .8rem; display: flex; justify-content: space-between; gap: 1rem; }}
  .vacio {{ padding: 2.5rem; text-align: center; color: #888; background: #fafafa;
           border-radius: 8px; }}
  @media print {{
    body {{ padding: 0; font-size: 11pt; }}
    tr {{ break-inside: avoid; }}
    thead {{ display: table-header-group; }}
  }}
</style></head>
<body><div class="hoja">
  <header>
    <div>
      <h1>{e(marca.titulo)}</h1>
      {f'<p class="entidad">{e(marca.entidad)}</p>' if marca.entidad else ''}
      <p class="datos"><strong>{e(t['nombre'])}</strong> · {e(t['tipo_documento'])} {e(t['documento'])}</p>
      {f'<p class="datos">CVU {e(self.wallet.cvu)}</p>' if self.wallet.cvu else ''}
      {periodo}
    </div>
    {logo}
  </header>

  {'<table><thead><tr><th>Fecha</th><th>Detalle</th><th class="num">Monto</th>'
   '<th class="num">Saldo</th></tr></thead><tbody>' + "".join(filas) + '</tbody></table>'
   if filas else '<p class="vacio">No hubo movimientos en este período.</p>'}

  <table class="totales">
    <tr><td>Ingresos</td><td class="num" style="color:{marca.color_ingreso}">+ {_monto(self.total_ingresos)}</td></tr>
    <tr><td>Egresos</td><td class="num" style="color:{marca.color_egreso}">− {_monto(self.total_egresos)}</td></tr>
    <tr><td>{"Saldo actual" if self.saldo else "Neto del período"}</td>
        <td class="num">{_monto(self.saldo.disponible if self.saldo else self.total_ingresos - self.total_egresos)}</td></tr>
  </table>

  <footer>
    <span>{e(marca.pie) if marca.pie else "Generado automáticamente"}</span>
    <span>{_fecha(self.generado_en or time.time())}</span>
  </footer>
</div></body></html>"""


FORMATOS = ("json", "csv", "html", "texto")


def generar(resumen: Resumen, formato: str = "texto", marca: Marca | None = None) -> str:
    if formato not in FORMATOS:
        raise ValueError(f"formato '{formato}' desconocido; hay: {', '.join(FORMATOS)}")
    return {
        "json": resumen.a_json,
        "csv": resumen.a_csv,
        "texto": resumen.a_texto,
        "html": lambda: resumen.a_html(marca),
    }[formato]()
