"""Restaura os dados iniciais: ``uv run python -m app.restaurar``.

Volta reservas e visitantes ao estado de ``dados/*.json`` e apaga as sessões
(o vínculo sessão -> apartamento e o banco de sessões do ADK). Pare a API antes.
"""

from __future__ import annotations

from . import store
from .config import DB_SESSOES, DIR_ARMAZENAMENTO


def main() -> None:
    DIR_ARMAZENAMENTO.mkdir(parents=True, exist_ok=True)
    store.restaurar()
    for sufixo in ("", "-wal", "-shm"):
        arquivo = DB_SESSOES.with_name(DB_SESSOES.name + sufixo)
        arquivo.unlink(missing_ok=True)
    print("Dados restaurados: reservas, visitantes e sessões voltaram ao estado inicial.")


if __name__ == "__main__":
    main()
