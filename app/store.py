"""Armazenamento dos dados do condomínio (SQLite).

Toda regra que precisa valer "no instante da gravação" mora aqui, no banco:

* ``ux_reserva_ativa``: índice único parcial (area, data) WHERE status = 'ativa'.
  Duas gravações simultâneas para a mesma área e data nunca coexistem: o SQLite
  serializa os escritores e a segunda recebe ``IntegrityError``.
* ``reservas.codigo`` é PRIMARY KEY e as reservas canceladas nunca são apagadas
  (só mudam de status), então um código nunca é reaproveitado.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Iterator

from .config import DB_CONDOMINIO, DIR_ARMAZENAMENTO, DIR_DADOS

# ---------------------------------------------------------------------------
# Catálogo (somente leitura): apartamentos e áreas vêm de dados/*.json
# ---------------------------------------------------------------------------


def _ler_json(nome: str) -> list[dict]:
    with open(DIR_DADOS / nome, encoding="utf-8") as f:
        return json.load(f)


def _normalizar(texto: str) -> str:
    sem_acento = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in sem_acento if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.lower()).strip("-")


def areas() -> dict[str, dict]:
    return {a["id"]: a for a in _ler_json("areas.json")}


def apartamento_existe(numero: str) -> bool:
    return any(a["numero"] == numero for a in _ler_json("apartamentos.json"))


def resolver_area(valor: str | None) -> dict | None:
    """Aceita o id (``salao-de-festas``) ou o nome (``Salão de festas``)."""
    if not valor:
        return None
    alvo = _normalizar(str(valor))
    for area in areas().values():
        if alvo in (_normalizar(area["id"]), _normalizar(area["nome"])):
            return area
    return None


def validar_data(valor: str | None) -> str | None:
    """Devolve a data em AAAA-MM-DD ou ``None`` se o formato for inválido."""
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor).strip()).isoformat()
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Conexão e esquema
# ---------------------------------------------------------------------------

_ESQUEMA = """
CREATE TABLE IF NOT EXISTS reservas (
    codigo       TEXT PRIMARY KEY,
    apartamento  TEXT NOT NULL,
    area         TEXT NOT NULL,
    data         TEXT NOT NULL,
    status       TEXT NOT NULL CHECK (status IN ('ativa', 'cancelada')),
    criada_em    TEXT NOT NULL,
    cancelada_em TEXT
);

-- Garantia 5: no máximo uma reserva ATIVA por área e data, imposto pelo banco.
CREATE UNIQUE INDEX IF NOT EXISTS ux_reserva_ativa
    ON reservas (area, data) WHERE status = 'ativa';

CREATE TABLE IF NOT EXISTS visitantes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    apartamento  TEXT NOT NULL,
    nome         TEXT NOT NULL,
    nome_chave   TEXT NOT NULL,
    data         TEXT NOT NULL,
    UNIQUE (apartamento, nome_chave, data)
);

-- Vínculo imutável sessão -> apartamento, gravado na criação da sessão.
CREATE TABLE IF NOT EXISTS sessoes (
    session_id   TEXT PRIMARY KEY,
    apartamento  TEXT NOT NULL,
    criada_em    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    chave TEXT PRIMARY KEY,
    valor TEXT NOT NULL
);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _conexao() -> Iterator[sqlite3.Connection]:
    DIR_ARMAZENAMENTO.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: transações explícitas (BEGIN IMMEDIATE) abaixo.
    conn = sqlite3.connect(DB_CONDOMINIO, timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        yield conn
    finally:
        conn.close()


def _carregar_dados_iniciais(conn: sqlite3.Connection) -> None:
    for r in _ler_json("reservas.json"):
        conn.execute(
            "INSERT INTO reservas (codigo, apartamento, area, data, status, criada_em)"
            " VALUES (?, ?, ?, ?, 'ativa', ?)",
            (r["codigo"], r["apartamento"], r["area"], r["data"], _agora()),
        )
    for v in _ler_json("visitantes.json"):
        conn.execute(
            "INSERT INTO visitantes (apartamento, nome, nome_chave, data) VALUES (?, ?, ?, ?)",
            (v["apartamento"], v["nome"], _normalizar(v["nome"]), v["data"]),
        )
    conn.execute("INSERT OR REPLACE INTO meta (chave, valor) VALUES ('semeado', '1')")


def inicializar() -> None:
    """Cria o esquema e carrega os dados iniciais na primeira execução.

    Chamado a cada subida da API: se o banco já foi semeado, não toca em nada
    (é isso que preserva os dados no reinício).
    """
    with _conexao() as conn:
        conn.executescript(_ESQUEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            ja = conn.execute("SELECT 1 FROM meta WHERE chave = 'semeado'").fetchone()
            if not ja:
                _carregar_dados_iniciais(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def restaurar() -> None:
    """Volta reservas e visitantes ao estado de dados/*.json e apaga sessões."""
    with _conexao() as conn:
        conn.executescript(_ESQUEMA)
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("DELETE FROM reservas")
            conn.execute("DELETE FROM visitantes")
            conn.execute("DELETE FROM sessoes")
            conn.execute("DELETE FROM meta")
            _carregar_dados_iniciais(conn)
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ---------------------------------------------------------------------------
# Sessões
# ---------------------------------------------------------------------------


def registrar_sessao(session_id: str, apartamento: str) -> None:
    with _conexao() as conn:
        conn.execute(
            "INSERT INTO sessoes (session_id, apartamento, criada_em) VALUES (?, ?, ?)",
            (session_id, apartamento, _agora()),
        )


def apartamento_da_sessao(session_id: str) -> str | None:
    with _conexao() as conn:
        linha = conn.execute(
            "SELECT apartamento FROM sessoes WHERE session_id = ?", (session_id,)
        ).fetchone()
    return linha["apartamento"] if linha else None


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------


def listar_reservas(apartamento: str) -> list[dict]:
    with _conexao() as conn:
        linhas = conn.execute(
            "SELECT codigo, area, data FROM reservas"
            " WHERE apartamento = ? AND status = 'ativa' ORDER BY data, codigo",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def data_livre(area: str, data: str) -> bool:
    """Só devolve se a data está livre; nunca de quem é a reserva."""
    with _conexao() as conn:
        ocupada = conn.execute(
            "SELECT 1 FROM reservas WHERE area = ? AND data = ? AND status = 'ativa'",
            (area, data),
        ).fetchone()
    return ocupada is None


def _proximo_codigo(conn: sqlite3.Connection) -> str:
    # Considera TODAS as linhas (inclusive canceladas, que nunca são apagadas).
    maior = conn.execute(
        "SELECT MAX(CAST(SUBSTR(codigo, 5) AS INTEGER)) FROM reservas"
    ).fetchone()[0]
    return f"RSV-{(maior or 0) + 1}"


def criar_reserva(apartamento: str, area: str, data: str) -> str | None:
    """Grava a reserva. Devolve o código, ou ``None`` se a data já está ocupada.

    A conferência de disponibilidade e a gravação são a mesma instrução: o
    índice único parcial decide quem vence, mesmo com escritas simultâneas.
    """
    with _conexao() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            codigo = _proximo_codigo(conn)
            conn.execute(
                "INSERT INTO reservas (codigo, apartamento, area, data, status, criada_em)"
                " VALUES (?, ?, ?, ?, 'ativa', ?)",
                (codigo, apartamento, area, data, _agora()),
            )
            conn.execute("COMMIT")
            return codigo
        except sqlite3.IntegrityError:
            conn.execute("ROLLBACK")
            return None
        except Exception:
            conn.execute("ROLLBACK")
            raise


def cancelar_reserva(apartamento: str, area: str, data: str) -> str | None:
    """Cancela a reserva ATIVA do próprio apartamento. Devolve o código."""
    with _conexao() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            linha = conn.execute(
                "SELECT codigo FROM reservas"
                " WHERE apartamento = ? AND area = ? AND data = ? AND status = 'ativa'",
                (apartamento, area, data),
            ).fetchone()
            if linha:
                conn.execute(
                    "UPDATE reservas SET status = 'cancelada', cancelada_em = ?"
                    " WHERE codigo = ?",
                    (_agora(), linha["codigo"]),
                )
            conn.execute("COMMIT")
            return linha["codigo"] if linha else None
        except Exception:
            conn.execute("ROLLBACK")
            raise


# ---------------------------------------------------------------------------
# Visitantes
# ---------------------------------------------------------------------------


def listar_visitantes(apartamento: str) -> list[dict]:
    with _conexao() as conn:
        linhas = conn.execute(
            "SELECT nome, data FROM visitantes WHERE apartamento = ? ORDER BY data, nome",
            (apartamento,),
        ).fetchall()
    return [dict(l) for l in linhas]


def autorizar_visitante(apartamento: str, nome: str, data: str) -> bool:
    """Registra a autorização. ``False`` se já existia (idempotente)."""
    with _conexao() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO visitantes (apartamento, nome, nome_chave, data)"
            " VALUES (?, ?, ?, ?)",
            (apartamento, nome, _normalizar(nome), data),
        )
        return cur.rowcount == 1
