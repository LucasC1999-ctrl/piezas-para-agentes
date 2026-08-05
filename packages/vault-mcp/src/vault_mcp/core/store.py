"""Persistencia del vault: SQLite para la estructura, Fernet para los valores.

Decisiones que importan:

**El valor se cifra, los metadatos no.** Poder buscar "¿qué credenciales tengo
de Mercado Pago?" sin descifrar nada hace que el listado sea barato y que un
volcado accidental de la base no entregue ni un secreto. El precio es que los
nombres y descripciones son legibles: no pongas el secreto en el nombre.

**La clave maestra vive en un archivo aparte, con 0600.** No en la base. Si
alguien se lleva el .db —backup mal configurado, sync a la nube, lo que sea—
sin la clave no tiene nada. Si se pierde la clave, los secretos no se
recuperan: eso es una propiedad, no un bug.

**SQLite y no Postgres** porque esto corre al lado del agente, en la máquina
del usuario. Una pieza que exige levantar un servidor de base de datos no es
una pieza que alguien enchufe en su flujo un martes a la tarde.
"""
from __future__ import annotations

import base64
import os
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from .errors import VaultCorrupted, VaultLocked
from .models import AccessLog, Grant, Secret, SecretKind

SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
    name        TEXT PRIMARY KEY,
    kind        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    username    TEXT,
    url         TEXT,
    tags        TEXT NOT NULL DEFAULT '',
    version     INTEGER NOT NULL DEFAULT 1,
    value_enc   BLOB NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS grants (
    agent_id    TEXT NOT NULL,
    secret_name TEXT NOT NULL,
    granted_at  REAL NOT NULL,
    granted_by  TEXT NOT NULL DEFAULT 'owner',
    note        TEXT NOT NULL DEFAULT '',
    expires_at  REAL,
    PRIMARY KEY (agent_id, secret_name),
    FOREIGN KEY (secret_name) REFERENCES secrets(name) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL NOT NULL,
    agent_id    TEXT NOT NULL,
    secret_name TEXT NOT NULL,
    action      TEXT NOT NULL,
    allowed     INTEGER NOT NULL,
    detail      TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS ix_log_ts     ON access_log(ts DESC);
CREATE INDEX IF NOT EXISTS ix_log_agent  ON access_log(agent_id, ts DESC);
CREATE INDEX IF NOT EXISTS ix_log_denied ON access_log(allowed, ts DESC);
"""


def default_home() -> Path:
    return Path(os.environ.get("VAULT_HOME", Path.home() / ".local/share/vault-mcp"))


class VaultStore:
    """Acceso a disco. No sabe nada de permisos — de eso se encarga `Vault`.

    La separación es a propósito: el store puede leer cualquier secreto, y es
    el servicio de arriba el que decide si el que pregunta tiene derecho. Así
    la lógica de permisos vive en un solo lugar auditable en vez de estar
    desparramada entre las consultas SQL.
    """

    def __init__(self, home: Path | None = None):
        self.home = Path(home) if home else default_home()
        self.home.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = self.home / "vault.db"
        self.key_path = self.home / "master.key"
        self._fernet: Fernet | None = None
        self._db: sqlite3.Connection | None = None

    # --- clave maestra --------------------------------------------------------

    def init_key(self) -> bytes:
        """Crea la clave maestra si no existe. Idempotente."""
        if self.key_path.exists():
            return self.key_path.read_bytes()
        key = Fernet.generate_key()
        # Se escribe con 0600 desde el arranque: crear y después chmod deja una
        # ventana en la que la clave es world-readable.
        fd = os.open(self.key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(key)
        return key

    @property
    def fernet(self) -> Fernet:
        if self._fernet is None:
            if not self.key_path.exists():
                raise VaultLocked(
                    "no hay clave maestra",
                    hint=f"corré `vault-admin init` o creá {self.key_path}",
                )
            mode = self.key_path.stat().st_mode & 0o777
            if mode & 0o077:
                raise VaultLocked(
                    f"la clave maestra tiene permisos {oct(mode)}: la puede leer alguien más",
                    hint=f"chmod 600 {self.key_path}",
                )
            self._fernet = Fernet(self.key_path.read_bytes())
        return self._fernet

    # --- conexión -------------------------------------------------------------

    @property
    def db(self) -> sqlite3.Connection:
        if self._db is None:
            first = not self.db_path.exists()
            if first:
                # 0600 antes de que SQLite escriba nada.
                os.close(os.open(self.db_path, os.O_WRONLY | os.O_CREAT, 0o600))
            self._db = sqlite3.connect(self.db_path, isolation_level=None)
            self._db.row_factory = sqlite3.Row
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
        return self._db

    def close(self) -> None:
        if self._db is not None:
            self._db.close()
            self._db = None

    # --- secretos -------------------------------------------------------------

    def put_secret(self, secret: Secret, value: str) -> Secret:
        enc = self.fernet.encrypt(value.encode())
        now = time.time()
        prev = self.get_secret_meta(secret.name)
        version = (prev.version + 1) if prev else 1
        created = prev.created_at if prev else now

        self.db.execute(
            """INSERT INTO secrets
                 (name, kind, description, username, url, tags, version,
                  value_enc, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(name) DO UPDATE SET
                 kind=excluded.kind, description=excluded.description,
                 username=excluded.username, url=excluded.url, tags=excluded.tags,
                 version=excluded.version, value_enc=excluded.value_enc,
                 updated_at=excluded.updated_at""",
            (secret.name, str(secret.kind), secret.description, secret.username,
             secret.url, ",".join(secret.tags), version, enc, created, now),
        )
        return Secret(
            name=secret.name, kind=secret.kind, description=secret.description,
            username=secret.username, url=secret.url, tags=secret.tags,
            version=version, created_at=created, updated_at=now,
        )

    def get_secret_meta(self, name: str) -> Secret | None:
        row = self.db.execute("SELECT * FROM secrets WHERE name=?", (name,)).fetchone()
        return self._row_to_secret(row) if row else None

    def get_secret_value(self, name: str) -> str | None:
        row = self.db.execute("SELECT value_enc FROM secrets WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        try:
            return self.fernet.decrypt(row["value_enc"]).decode()
        except InvalidToken as e:
            raise VaultCorrupted(
                f"no pude descifrar '{name}': la clave maestra no corresponde a esta base",
                hint="¿se mezclaron un vault.db y un master.key de instalaciones distintas?",
            ) from e

    def list_secrets(self) -> list[Secret]:
        rows = self.db.execute("SELECT * FROM secrets ORDER BY name").fetchall()
        return [self._row_to_secret(r) for r in rows]

    def delete_secret(self, name: str) -> bool:
        cur = self.db.execute("DELETE FROM secrets WHERE name=?", (name,))
        return cur.rowcount > 0

    @staticmethod
    def _row_to_secret(row: sqlite3.Row) -> Secret:
        return Secret(
            name=row["name"],
            kind=SecretKind(row["kind"]),
            description=row["description"],
            username=row["username"],
            url=row["url"],
            tags=tuple(t for t in (row["tags"] or "").split(",") if t),
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    # --- permisos -------------------------------------------------------------

    def put_grant(self, grant: Grant) -> None:
        self.db.execute(
            """INSERT INTO grants (agent_id, secret_name, granted_at, granted_by, note, expires_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(agent_id, secret_name) DO UPDATE SET
                 granted_at=excluded.granted_at, granted_by=excluded.granted_by,
                 note=excluded.note, expires_at=excluded.expires_at""",
            (grant.agent_id, grant.secret_name, grant.granted_at,
             grant.granted_by, grant.note, grant.expires_at),
        )

    def get_grant(self, agent_id: str, secret_name: str) -> Grant | None:
        row = self.db.execute(
            "SELECT * FROM grants WHERE agent_id=? AND secret_name=?",
            (agent_id, secret_name),
        ).fetchone()
        return self._row_to_grant(row) if row else None

    def list_grants(self, agent_id: str | None = None) -> list[Grant]:
        if agent_id:
            rows = self.db.execute(
                "SELECT * FROM grants WHERE agent_id=? ORDER BY secret_name", (agent_id,)
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM grants ORDER BY agent_id, secret_name"
            ).fetchall()
        return [self._row_to_grant(r) for r in rows]

    def delete_grant(self, agent_id: str, secret_name: str) -> bool:
        cur = self.db.execute(
            "DELETE FROM grants WHERE agent_id=? AND secret_name=?", (agent_id, secret_name)
        )
        return cur.rowcount > 0

    @staticmethod
    def _row_to_grant(row: sqlite3.Row) -> Grant:
        return Grant(
            agent_id=row["agent_id"], secret_name=row["secret_name"],
            granted_at=row["granted_at"], granted_by=row["granted_by"],
            note=row["note"], expires_at=row["expires_at"],
        )

    # --- auditoría ------------------------------------------------------------

    def log(self, entry: AccessLog) -> None:
        self.db.execute(
            "INSERT INTO access_log (ts, agent_id, secret_name, action, allowed, detail)"
            " VALUES (?,?,?,?,?,?)",
            (entry.ts, entry.agent_id, entry.secret_name, entry.action,
             int(entry.allowed), entry.detail),
        )

    def read_log(self, *, agent_id: str | None = None, only_denied: bool = False,
                 limit: int = 100) -> list[AccessLog]:
        sql = "SELECT * FROM access_log WHERE 1=1"
        params: list = []
        if agent_id:
            sql += " AND agent_id=?"
            params.append(agent_id)
        if only_denied:
            sql += " AND allowed=0"
        sql += " ORDER BY ts DESC LIMIT ?"
        params.append(limit)
        return [
            AccessLog(ts=r["ts"], agent_id=r["agent_id"], secret_name=r["secret_name"],
                      action=r["action"], allowed=bool(r["allowed"]), detail=r["detail"])
            for r in self.db.execute(sql, params).fetchall()
        ]
