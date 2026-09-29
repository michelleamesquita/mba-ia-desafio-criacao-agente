"""Tools do assistente.

Regras que valem para TODAS as tools de reservas/visitantes:

* Nenhuma recebe o apartamento como parâmetro. O apartamento é lido de
  ``tool_context.state["apartamento"]``, gravado uma única vez na criação da
  sessão (``service.criar_sessao``). O modelo não tem como escolher outro.
* Os retornos nunca trazem dados de outro apartamento: para saber se uma data
  está livre, só se devolve ``disponivel: true/false``.
"""

from __future__ import annotations

from typing import Any, Literal

from google.adk.tools import ToolContext

from . import regulamento, store

AreaId = Literal["salao-de-festas", "churrasqueira", "quadra"]

_LIMITE_NOME = 120


def _apartamento(tool_context: ToolContext) -> str | None:
    apto = tool_context.state.get("apartamento")
    return str(apto) if apto else None


_SEM_SESSAO = {
    "status": "erro",
    "mensagem": "Sessão sem apartamento definido. Não é possível continuar.",
}


# ---------------------------------------------------------------------------
# Reservas
# ---------------------------------------------------------------------------


def consultar_disponibilidade(area: AreaId, data: str) -> dict[str, Any]:
    """Diz se uma área comum está livre ou ocupada em uma data.

    Não informa quem reservou. Use antes de propor uma reserva.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    area_info = store.resolver_area(area)
    data_ok = store.validar_data(data)
    if not area_info:
        return {"status": "erro", "mensagem": "Área inexistente. Áreas: " + ", ".join(store.areas())}
    if not data_ok:
        return {"status": "erro", "mensagem": "Data inválida. Use AAAA-MM-DD."}
    return {"area": area_info["id"], "data": data_ok, "disponivel": store.data_livre(area_info["id"], data_ok)}


def listar_minhas_reservas(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as reservas ativas do apartamento do morador desta conversa."""
    apto = _apartamento(tool_context)
    if not apto:
        return _SEM_SESSAO
    return {"reservas": store.listar_reservas(apto)}


def reservar_area(area: AreaId, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Reserva uma área comum para o apartamento do morador em uma data.

    Áreas com taxa geram cobrança e exigem a confirmação do morador (o sistema
    pede essa confirmação automaticamente; NÃO peça confirmação por texto).

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data no formato AAAA-MM-DD.
    """
    apto = _apartamento(tool_context)
    if not apto:
        return _SEM_SESSAO
    area_info = store.resolver_area(area)
    data_ok = store.validar_data(data)
    if not area_info or not data_ok:
        return {"status": "erro", "mensagem": "Área ou data inválida (use AAAA-MM-DD)."}

    codigo = store.criar_reserva(apto, area_info["id"], data_ok)
    if codigo is None:
        # Sem dizer de quem é a reserva que ocupa a data.
        return {
            "status": "indisponivel",
            "area": area_info["id"],
            "data": data_ok,
            "mensagem": "Essa data já está ocupada para essa área.",
        }
    taxa = float(area_info["taxa"])
    return {
        "status": "reservada",
        "codigo": codigo,
        "area": area_info["id"],
        "data": data_ok,
        "cobranca_gerada": taxa > 0,
        "taxa": taxa,
    }


def reserva_exige_confirmacao(area: str = "", data: str = "", **_: Any) -> bool:
    """Regra de negócio 2: área com taxa > 0 gera cobrança => exige confirmação.

    Depende só da área (estática, vinda de dados/areas.json), nunca do modelo.
    """
    area_info = store.resolver_area(area)
    return bool(area_info and float(area_info["taxa"]) > 0)


def cancelar_reserva(area: AreaId, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Cancela a reserva do PRÓPRIO apartamento para uma área e data. Não pede confirmação.

    Args:
        area: id da área (salao-de-festas, churrasqueira ou quadra).
        data: data da reserva, formato AAAA-MM-DD.
    """
    apto = _apartamento(tool_context)
    if not apto:
        return _SEM_SESSAO
    area_info = store.resolver_area(area)
    data_ok = store.validar_data(data)
    if not area_info or not data_ok:
        return {"status": "erro", "mensagem": "Área ou data inválida (use AAAA-MM-DD)."}
    codigo = store.cancelar_reserva(apto, area_info["id"], data_ok)
    if codigo is None:
        return {
            "status": "nao_encontrada",
            "mensagem": "Este apartamento não tem reserva ativa dessa área nessa data.",
        }
    return {"status": "cancelada", "codigo": codigo, "area": area_info["id"], "data": data_ok}


# ---------------------------------------------------------------------------
# Visitantes
# ---------------------------------------------------------------------------


def listar_meus_visitantes(tool_context: ToolContext) -> dict[str, Any]:
    """Lista as autorizações de visita do apartamento do morador desta conversa."""
    apto = _apartamento(tool_context)
    if not apto:
        return _SEM_SESSAO
    return {"visitantes": store.listar_visitantes(apto)}


def autorizar_visitante(nome: str, data: str, tool_context: ToolContext) -> dict[str, Any]:
    """Autoriza a entrada de um visitante no prédio em uma data.

    Libera acesso ao prédio, por isso exige a confirmação do morador (o sistema
    pede essa confirmação automaticamente; NÃO peça confirmação por texto).

    Args:
        nome: nome completo do visitante.
        data: data da visita, formato AAAA-MM-DD.
    """
    apto = _apartamento(tool_context)
    if not apto:
        return _SEM_SESSAO
    nome_ok = " ".join((nome or "").split())[:_LIMITE_NOME]
    data_ok = store.validar_data(data)
    if not nome_ok or not data_ok:
        return {"status": "erro", "mensagem": "Informe o nome do visitante e a data (AAAA-MM-DD)."}
    criada = store.autorizar_visitante(apto, nome_ok, data_ok)
    return {
        "status": "autorizado" if criada else "ja_autorizado",
        "nome": nome_ok,
        "data": data_ok,
    }


# ---------------------------------------------------------------------------
# Regulamento
# ---------------------------------------------------------------------------


def ler_capitulo_regulamento(numero: int) -> dict[str, Any]:
    """Lê UM capítulo do regulamento interno pelo número (1 a 14).

    Args:
        numero: número do capítulo, conforme o índice das instruções.
    """
    return regulamento.ler_capitulo(int(numero))


def buscar_no_regulamento(termos: str) -> dict[str, Any]:
    """Busca por palavras-chave e devolve os artigos mais relevantes (de um só capítulo).

    Args:
        termos: palavras-chave do assunto (ex.: "piscina horário domingo").
    """
    return regulamento.buscar(termos)
