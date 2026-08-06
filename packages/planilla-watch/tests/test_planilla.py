"""Tests del vigilante.

Dos cosas se testean con saña, porque son las que lo hacen usable o inútil:

  1. **Que nunca salga el valor de un secreto.** Si sale, el canal de Buzz se
     convierte en el historial completo de las credenciales del estudio.
  2. **Que reordenar la planilla no genere ruido.** Si avisa por cada vez que
     alguien ordena por cliente, lo silencian en una semana.
"""
from __future__ import annotations

import pytest
from openpyxl import Workbook

from planilla_watch.diff import TipoCambio, comparar, resumir
from planilla_watch.planilla import es_secreta, leer

COLUMNAS = ["cliente", "portal", "usuario", "clave", "otros", "link"]


def escribir(ruta, filas, *, hoja="Impuestos", columnas=None, hojas_extra=None):
    wb = Workbook()
    ws = wb.active
    ws.title = hoja
    ws.append(columnas or COLUMNAS)
    for f in filas:
        ws.append(f)
    for nombre, cols, fs in (hojas_extra or []):
        w2 = wb.create_sheet(nombre)
        w2.append(cols)
        for f in fs:
            w2.append(f)
    wb.save(ruta)
    return ruta


@pytest.fixture
def base(tmp_path):
    return escribir(tmp_path / "cred.xlsx", [
        ["Pérez SA", "ARCA", "30111", "clave-secreta-1", "CUIT rep", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "clave-secreta-2", "token", "galicia.com"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
    ])


# --- lo que nunca puede pasar -------------------------------------------------

def test_el_valor_del_secreto_no_queda_en_la_foto(base):
    foto = leer(base)
    assert "clave-secreta-1" not in str(foto.a_dict())


def test_el_valor_del_secreto_no_sale_en_el_aviso(base, tmp_path):
    antes = leer(base)
    escribir(base, [
        ["Pérez SA", "ARCA", "30111", "NUEVA-CLAVE-CONFIDENCIAL", "CUIT rep", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "clave-secreta-2", "token", "galicia.com"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
    ])
    texto = resumir(comparar(antes, leer(base)))
    assert "NUEVA-CLAVE-CONFIDENCIAL" not in texto
    assert "clave" in texto.lower()  # sí dice QUE cambió


def test_reordenar_no_genera_ningun_aviso(base):
    """Ordenar por cliente cambia todas las posiciones y no debe avisar nada."""
    antes = leer(base)
    escribir(base, [
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "clave-secreta-2", "token", "galicia.com"],
        ["Pérez SA", "ARCA", "30111", "clave-secreta-1", "CUIT rep", "afip.gob.ar"],
    ])
    assert comparar(antes, leer(base)) == []


def test_sin_cambios_no_avisa(base):
    antes = leer(base)
    assert comparar(antes, leer(base)) == []
    assert resumir([]) == ""


# --- detección ----------------------------------------------------------------

def test_detecta_clave_cambiada(base):
    antes = leer(base)
    escribir(base, [
        ["Pérez SA", "ARCA", "30111", "OTRA", "CUIT rep", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "clave-secreta-2", "token", "galicia.com"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
    ])
    c = comparar(antes, leer(base))
    assert len(c) == 1
    assert c[0].tipo is TipoCambio.EDICION
    assert c[0].secretos_tocados == ("clave",)
    assert "Pérez SA" in c[0].identidad


def test_detecta_alta(base):
    antes = leer(base)
    escribir(base, [
        ["Pérez SA", "ARCA", "30111", "clave-secreta-1", "CUIT rep", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "clave-secreta-2", "token", "galicia.com"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
        ["Nuevo SRL", "ARCA", "30999", "clave-nueva", "", "afip.gob.ar"],
    ])
    c = comparar(antes, leer(base))
    assert len(c) == 1 and c[0].tipo is TipoCambio.ALTA
    assert "Nuevo SRL" in c[0].identidad


def test_detecta_baja(base):
    antes = leer(base)
    escribir(base, [
        ["Pérez SA", "ARCA", "30111", "clave-secreta-1", "CUIT rep", "afip.gob.ar"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
    ])
    c = comparar(antes, leer(base))
    assert len(c) == 1 and c[0].tipo is TipoCambio.BAJA


def test_detecta_edicion_de_campo_visible(base):
    antes = leer(base)
    escribir(base, [
        ["Pérez SA", "ARCA", "30111", "clave-secreta-1", "OTRO DATO", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "clave-secreta-2", "token", "galicia.com"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
    ])
    c = comparar(antes, leer(base))
    assert c[0].columnas == ("otros",) and c[0].secretos_tocados == ()


def test_varias_hojas_son_pestanas(tmp_path):
    r = escribir(tmp_path / "m.xlsx", [["A", "ARCA", "u", "c", "", ""]],
                 hojas_extra=[("Bancos", COLUMNAS, [["A", "Galicia", "u2", "c2", "", ""]])])
    foto = leer(r)
    assert set(foto.hojas) == {"Impuestos", "Bancos"}
    assert len(foto.filas) == 2


def test_hoja_nueva_avisa(tmp_path):
    r = tmp_path / "h.xlsx"
    escribir(r, [["A", "ARCA", "u", "c", "", ""]])
    antes = leer(r)
    escribir(r, [["A", "ARCA", "u", "c", "", ""]],
             hojas_extra=[("Sueldos", COLUMNAS, [["B", "AFIP", "u", "c", "", ""]])])
    tipos = {c.tipo for c in comparar(antes, leer(r))}
    assert TipoCambio.HOJA_NUEVA in tipos


# --- tolerancia al desorden real ----------------------------------------------

def test_filas_vacias_se_ignoran(tmp_path):
    r = escribir(tmp_path / "v.xlsx", [
        ["A", "ARCA", "u", "c", "", ""],
        [None, None, None, None, None, None],
        ["", "", "", "", "", ""],
        ["B", "ARCA", "u", "c", "", ""],
    ])
    assert len(leer(r).filas) == 2


def test_columnas_con_otro_nombre_igual_detecta_el_secreto():
    assert es_secreta("Clave fiscal")
    assert es_secreta("CONTRASEÑA")
    assert es_secreta("password")
    assert es_secreta("PIN")
    assert not es_secreta("cliente")
    assert not es_secreta("link")


def test_columnas_extra_no_rompen(tmp_path):
    """El estudio va a agregar columnas y el vigilante tiene que seguir."""
    cols = COLUMNAS + ["vencimiento", "responsable"]
    r = escribir(tmp_path / "e.xlsx",
                 [["A", "ARCA", "u", "c", "", "", "2026-12-01", "Marina"]],
                 columnas=cols)
    foto = leer(r)
    f = next(iter(foto.filas.values()))
    assert f.valores["responsable"] == "Marina"


def test_dos_claves_en_la_misma_fila_se_cifran_las_dos(tmp_path):
    cols = ["cliente", "portal", "usuario", "clave fiscal", "clave token"]
    r = escribir(tmp_path / "d.xlsx", [["A", "ARCA", "u", "SECRETO-A", "SECRETO-B"]],
                 columnas=cols)
    d = str(leer(r).a_dict())
    assert "SECRETO-A" not in d and "SECRETO-B" not in d


def test_filas_duplicadas_no_se_pisan(tmp_path):
    r = escribir(tmp_path / "dup.xlsx", [
        ["A", "ARCA", "u1", "c1", "", ""],
        ["A", "ARCA", "u2", "c2", "", ""],
    ])
    assert len(leer(r).filas) == 2


# --- el mensaje ---------------------------------------------------------------

def test_el_aviso_destaca_las_credenciales(base):
    antes = leer(base)
    escribir(base, [
        ["Pérez SA", "ARCA", "30111", "X", "CUIT rep", "afip.gob.ar"],
        ["Pérez SA", "Galicia", "perez.op", "Y", "token", "galicia.com"],
        ["ACME", "ARCA", "30222", "clave-secreta-3", "", "afip.gob.ar"],
    ])
    t = resumir(comparar(antes, leer(base)), quien="Marina")
    assert "Marina" in t
    assert "2 credenciales cambiaron" in t
    assert "pedila de nuevo" in t


def test_muchos_cambios_se_recortan(tmp_path):
    r = tmp_path / "m.xlsx"
    escribir(r, [["A", "ARCA", "u", "c", "", ""]])
    antes = leer(r)
    escribir(r, [[f"Cliente {i}", "ARCA", "u", "c", "", ""] for i in range(40)])
    t = resumir(comparar(antes, leer(r)), maximo=5)
    assert "cambios más" in t
    assert t.count("\n- ") <= 6
