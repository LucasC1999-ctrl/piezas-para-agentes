"""Compara dos fotos de la planilla y arma los avisos.

La regla que gobierna todo este archivo: **el valor de un secreto no sale
nunca**. Ni al canal, ni al log, ni al archivo de estado. Cuando cambia una
clave, el aviso dice que cambió y de quién es — no cuál es la nueva. Si el
aviso llevara la clave, el canal de Buzz se convertiría en el lugar menos
seguro del estudio, con el historial completo de todas las credenciales.

Segunda regla: **poco ruido**. Un vigilante que avisa por cada celda que se
toca termina silenciado. Se agrupan los cambios de una misma fila en un solo
aviso, y no se dice nada cuando alguien sólo reordena la planilla.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .planilla import Fila, Foto


class TipoCambio(StrEnum):
    ALTA = "alta"
    BAJA = "baja"
    EDICION = "edicion"
    HOJA_NUEVA = "hoja_nueva"


@dataclass(frozen=True, slots=True)
class Cambio:
    tipo: TipoCambio
    hoja: str
    identidad: str
    # Para EDICION: qué columnas cambiaron. Los secretos aparecen por nombre,
    # nunca con su valor.
    columnas: tuple[str, ...] = ()
    secretos_tocados: tuple[str, ...] = ()
    detalle: str = ""

    def texto(self) -> str:
        """Una línea para el canal. Sin valores de secretos, nunca."""
        if self.tipo is TipoCambio.HOJA_NUEVA:
            return f"Nueva pestaña: **{self.hoja}**"
        if self.tipo is TipoCambio.ALTA:
            return f"Nueva entrada en **{self.hoja}**: {self.identidad}"
        if self.tipo is TipoCambio.BAJA:
            return f"Se borró de **{self.hoja}**: {self.identidad}"

        partes = []
        if self.secretos_tocados:
            cols = ", ".join(self.secretos_tocados)
            partes.append(f"cambió {cols}")
        otras = [c for c in self.columnas if c not in self.secretos_tocados]
        if otras:
            partes.append("se editó " + ", ".join(otras))
        return f"**{self.hoja}** · {self.identidad}: {'; '.join(partes)}"


def comparar(antes: Foto, ahora: Foto) -> list[Cambio]:
    cambios: list[Cambio] = []

    for hoja in ahora.hojas:
        if hoja not in antes.hojas and antes.hojas:
            cambios.append(Cambio(TipoCambio.HOJA_NUEVA, hoja=hoja, identidad=""))

    claves_antes = set(antes.filas)
    claves_ahora = set(ahora.filas)

    for clave in sorted(claves_ahora - claves_antes):
        f = ahora.filas[clave]
        cambios.append(Cambio(TipoCambio.ALTA, hoja=f.hoja, identidad=f.identidad))

    for clave in sorted(claves_antes - claves_ahora):
        f = antes.filas[clave]
        cambios.append(Cambio(TipoCambio.BAJA, hoja=f.hoja, identidad=f.identidad))

    for clave in sorted(claves_ahora & claves_antes):
        a, b = antes.filas[clave], ahora.filas[clave]
        distintas = tuple(
            col for col in sorted(set(a.valores) | set(b.valores))
            if a.valores.get(col, "") != b.valores.get(col, "")
        )
        if not distintas:
            continue
        secretos = tuple(c for c in distintas if c in b.secretas)
        cambios.append(Cambio(
            TipoCambio.EDICION, hoja=b.hoja, identidad=b.identidad,
            columnas=distintas, secretos_tocados=secretos,
        ))

    return cambios


def resumir(cambios: list[Cambio], *, quien: str = "", maximo: int = 12) -> str:
    """Arma el mensaje para el canal.

    `maximo` corta la lista: si alguien pegó doscientas filas, no tiene sentido
    doscientas líneas. Un aviso que no se lee es igual a no avisar.
    """
    if not cambios:
        return ""

    secretos = sum(1 for c in cambios if c.secretos_tocados)
    encabezado = "**Cambios en la planilla de credenciales**"
    if quien:
        encabezado += f" — {quien}"
    if secretos:
        encabezado += (
            f"\n\n⚠️ {secretos} "
            + ("credencial cambió" if secretos == 1 else "credenciales cambiaron")
            + " — si la usabas, pedila de nuevo."
        )

    lineas = [f"- {c.texto()}" for c in cambios[:maximo]]
    if len(cambios) > maximo:
        lineas.append(f"- …y {len(cambios) - maximo} cambios más.")

    return encabezado + "\n\n" + "\n".join(lineas)
