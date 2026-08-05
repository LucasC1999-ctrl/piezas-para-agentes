"""Persistencia: wallets, intentos de pago y su rastro de autorización.

Lo que NO está acá es tan importante como lo que está: **no hay tabla de
saldos**. El saldo lo tiene el proveedor. Guardar una copia sería empezar a
llevar la cuenta de plata ajena, que es justo lo que esta pieza evita.

Lo que sí se guarda es el rastro: quién propuso qué, quién lo aprobó, cuándo,
y con qué comprobante volvió el proveedor.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from decimal import Decimal
from pathlib import Path

from .models import (
    Destino,
    EstadoIntento,
    EstadoWallet,
    IntentoPago,
    Moneda,
    TipoDocumento,
    Titular,
    Wallet,
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS wallets (
    id            TEXT PRIMARY KEY,
    driver        TEXT NOT NULL,
    alias_externo TEXT,
    cvu           TEXT,
    alias         TEXT,
    moneda        TEXT NOT NULL,
    estado        TEXT NOT NULL,
    etiqueta      TEXT NOT NULL DEFAULT '',
    titular_json  TEXT NOT NULL,
    creada_en     REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS intentos (
    id              TEXT PRIMARY KEY,
    wallet_id       TEXT NOT NULL,
    destino_json    TEXT NOT NULL,
    monto           TEXT NOT NULL,
    moneda          TEXT NOT NULL,
    concepto        TEXT NOT NULL DEFAULT '',
    estado          TEXT NOT NULL,
    creado_por      TEXT NOT NULL DEFAULT '',
    creado_en       REAL NOT NULL,
    vence_en        REAL,
    aprobado_por    TEXT,
    aprobado_en     REAL,
    motivo_rechazo  TEXT,
    idempotency_key TEXT,
    comprobante     TEXT,
    error           TEXT,
    FOREIGN KEY (wallet_id) REFERENCES wallets(id)
);

-- Idempotencia a nivel base: dos intentos con la misma clave no pueden existir,
-- aunque haya una carrera entre dos llamadas del agente.
CREATE UNIQUE INDEX IF NOT EXISTS ux_intentos_idem
    ON intentos(idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key != '';
CREATE INDEX IF NOT EXISTS ix_intentos_wallet ON intentos(wallet_id, creado_en DESC);
CREATE INDEX IF NOT EXISTS ix_intentos_estado ON intentos(estado, creado_en DESC);
"""


def default_home() -> Path:
    return Path(os.environ.get("WALLET_HOME", Path.home() / ".local/share/wallet-mcp"))


class WalletStore:
    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.home / "wallets.db"
        # Una conexión POR THREAD. SQLite prohíbe compartir una conexión entre
        # threads, y la API HTTP atiende cada request en uno distinto: con una
        # sola conexión, el segundo request muere con ProgrammingError.
        # `threading.local` da a cada thread la suya sin locks ni colas.
        self._local = threading.local()

    @property
    def db(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            if not self.db_path.exists():
                os.close(os.open(self.db_path, os.O_WRONLY | os.O_CREAT, 0o600))
            conn = sqlite3.connect(self.db_path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            # WAL permite que varios lectores y un escritor convivan, que es
            # justo el patrón de una API con varios workers.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.executescript(SCHEMA)
            self._local.conn = conn
        return conn

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # --- wallets --------------------------------------------------------------

    def guardar_wallet(self, w: Wallet) -> Wallet:
        self.db.execute(
            """INSERT INTO wallets (id, driver, alias_externo, cvu, alias, moneda,
                                    estado, etiqueta, titular_json, creada_en)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 cvu=excluded.cvu, alias=excluded.alias, estado=excluded.estado,
                 etiqueta=excluded.etiqueta, titular_json=excluded.titular_json""",
            (w.id, w.driver, w.alias_externo, w.cvu, w.alias, str(w.moneda),
             str(w.estado), w.etiqueta, json.dumps(w.titular.completo()), w.creada_en),
        )
        return w

    def obtener_wallet(self, wallet_id: str) -> Wallet | None:
        row = self.db.execute("SELECT * FROM wallets WHERE id=?", (wallet_id,)).fetchone()
        return self._row_to_wallet(row) if row else None

    def listar_wallets(self) -> list[Wallet]:
        rows = self.db.execute("SELECT * FROM wallets ORDER BY creada_en DESC").fetchall()
        return [self._row_to_wallet(r) for r in rows]

    @staticmethod
    def _row_to_wallet(row: sqlite3.Row) -> Wallet:
        t = json.loads(row["titular_json"])
        return Wallet(
            id=row["id"],
            titular=Titular(
                nombre=t["nombre"], tipo_documento=TipoDocumento(t["tipo_documento"]),
                documento=t["documento"], email=t.get("email"), telefono=t.get("telefono"),
            ),
            driver=row["driver"], alias_externo=row["alias_externo"], cvu=row["cvu"],
            alias=row["alias"], moneda=Moneda(row["moneda"]),
            estado=EstadoWallet(row["estado"]), etiqueta=row["etiqueta"],
            creada_en=row["creada_en"],
        )

    # --- intentos -------------------------------------------------------------

    def guardar_intento(self, i: IntentoPago) -> IntentoPago:
        self.db.execute(
            """INSERT INTO intentos (id, wallet_id, destino_json, monto, moneda,
                    concepto, estado, creado_por, creado_en, vence_en, aprobado_por,
                    aprobado_en, motivo_rechazo, idempotency_key, comprobante, error)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                 estado=excluded.estado, aprobado_por=excluded.aprobado_por,
                 aprobado_en=excluded.aprobado_en, motivo_rechazo=excluded.motivo_rechazo,
                 comprobante=excluded.comprobante, error=excluded.error""",
            (i.id, i.wallet_id, json.dumps(i.destino.to_dict()), str(i.monto),
             str(i.moneda), i.concepto, str(i.estado), i.creado_por, i.creado_en,
             i.vence_en, i.aprobado_por, i.aprobado_en, i.motivo_rechazo,
             i.idempotency_key or None, i.comprobante, i.error),
        )
        return i

    def obtener_intento(self, intento_id: str) -> IntentoPago | None:
        row = self.db.execute("SELECT * FROM intentos WHERE id=?", (intento_id,)).fetchone()
        return self._row_to_intento(row) if row else None

    def buscar_por_idempotency(self, key: str) -> IntentoPago | None:
        if not key:
            return None
        row = self.db.execute(
            "SELECT * FROM intentos WHERE idempotency_key=?", (key,)
        ).fetchone()
        return self._row_to_intento(row) if row else None

    def listar_intentos(self, *, wallet_id: str | None = None,
                        estado: EstadoIntento | None = None,
                        limite: int = 50) -> list[IntentoPago]:
        sql = "SELECT * FROM intentos WHERE 1=1"
        params: list = []
        if wallet_id:
            sql += " AND wallet_id=?"
            params.append(wallet_id)
        if estado:
            sql += " AND estado=?"
            params.append(str(estado))
        sql += " ORDER BY creado_en DESC LIMIT ?"
        params.append(limite)
        return [self._row_to_intento(r) for r in self.db.execute(sql, params).fetchall()]

    @staticmethod
    def _row_to_intento(row: sqlite3.Row) -> IntentoPago:
        d = json.loads(row["destino_json"])
        return IntentoPago(
            id=row["id"], wallet_id=row["wallet_id"],
            destino=Destino(cvu=d.get("cvu"), alias=d.get("alias"), nombre=d.get("nombre", "")),
            monto=Decimal(row["monto"]), moneda=Moneda(row["moneda"]),
            concepto=row["concepto"], estado=EstadoIntento(row["estado"]),
            creado_por=row["creado_por"], creado_en=row["creado_en"],
            vence_en=row["vence_en"], aprobado_por=row["aprobado_por"],
            aprobado_en=row["aprobado_en"], motivo_rechazo=row["motivo_rechazo"],
            idempotency_key=row["idempotency_key"] or "",
            comprobante=row["comprobante"], error=row["error"],
        )

    def vencer_pendientes(self, ahora: float | None = None) -> int:
        """Marca como vencidos los intentos que nadie aprobó a tiempo.

        Un intento pendiente para siempre es una bomba: alguien lo aprueba tres
        semanas después, cuando el contexto que lo justificaba ya no existe.
        """
        ahora = ahora or time.time()
        cur = self.db.execute(
            "UPDATE intentos SET estado=? WHERE estado=? AND vence_en IS NOT NULL AND vence_en < ?",
            (str(EstadoIntento.VENCIDO), str(EstadoIntento.PENDIENTE), ahora),
        )
        return cur.rowcount
