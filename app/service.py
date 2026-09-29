"""Ponte entre a API e o Runner do ADK.

Aqui moram as decisões de runtime:

* sessões persistidas em SQLite (``DatabaseSessionService``) => sobrevivem ao reinício;
* o apartamento entra no ``state`` da sessão UMA vez, em ``criar_sessao``;
* confirmações pendentes são derivadas dos eventos gravados na sessão, e a
  resposta do morador volta ao ADK como ``FunctionResponse`` de
  ``adk_request_confirmation`` (o mesmo que o adk web faz).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

from google.adk.apps import App
from google.adk.events import Event
from google.adk.runners import Runner
from google.adk.sessions import DatabaseSessionService
from google.genai import types

from . import store
from .agents import criar_app
from .config import DB_SESSOES, DIR_ARMAZENAMENTO, NOME_APP

CONFIRMACAO_FN = "adk_request_confirmation"


class SessaoNaoEncontrada(Exception):
    pass


class ConfirmacaoInexistente(Exception):
    pass


def user_id_do_apartamento(apartamento: str) -> str:
    return f"apto-{apartamento}"


def _texto_do_evento(evento: Event) -> str:
    if evento.author == "user" or not evento.content or not evento.content.parts:
        return ""
    if getattr(evento, "partial", False):
        return ""
    return "".join(p.text for p in evento.content.parts if p.text and not p.thought)


def _detalhes(nome_tool: str, args: dict[str, Any]) -> dict[str, Any]:
    """Detalhes legíveis do que será executado, normalizados pelo sistema."""
    if nome_tool == "reservar_area":
        area = store.resolver_area(args.get("area"))
        return {
            "area": area["id"] if area else args.get("area"),
            "data": store.validar_data(args.get("data")) or args.get("data"),
            "taxa": float(area["taxa"]) if area else None,
        }
    if nome_tool == "autorizar_visitante":
        return {
            "nome": " ".join(str(args.get("nome", "")).split()),
            "data": store.validar_data(args.get("data")) or args.get("data"),
        }
    return dict(args)


def pendencias(eventos: list[Event]) -> list[dict[str, Any]]:
    """Confirmações pedidas (adk_request_confirmation) e ainda sem resposta."""
    respondidas: set[str] = set()
    for ev in eventos:
        for fr in ev.get_function_responses():
            if fr.id:
                respondidas.add(fr.id)
    saida: list[dict[str, Any]] = []
    for ev in eventos:
        for fc in ev.get_function_calls():
            if fc.name != CONFIRMACAO_FN or not fc.id or fc.id in respondidas:
                continue
            original = (fc.args or {}).get("originalFunctionCall") or {}
            nome = original.get("name", "")
            saida.append(
                {
                    "id": fc.id,
                    "acao": nome,
                    "detalhes": _detalhes(nome, original.get("args") or {}),
                }
            )
    return saida


class Servico:
    def __init__(self, app: App | None = None, sessoes: DatabaseSessionService | None = None):
        DIR_ARMAZENAMENTO.mkdir(parents=True, exist_ok=True)
        self.app = app or criar_app()
        self.sessoes = sessoes or DatabaseSessionService(f"sqlite+aiosqlite:///{DB_SESSOES}")
        self.runner = Runner(app=self.app, session_service=self.sessoes)
        # Uma execução por vez em cada sessão (o ADK detecta sessão "velha").
        self._travas: dict[str, asyncio.Lock] = {}

    def _trava(self, session_id: str) -> asyncio.Lock:
        return self._travas.setdefault(session_id, asyncio.Lock())

    # -- sessões -----------------------------------------------------------

    async def criar_sessao(self, apartamento: str) -> str:
        session_id = str(uuid.uuid4())
        await self.sessoes.create_session(
            app_name=NOME_APP,
            user_id=user_id_do_apartamento(apartamento),
            session_id=session_id,
            # Único lugar em que o apartamento é definido. As tools leem daqui.
            state={"apartamento": apartamento},
        )
        store.registrar_sessao(session_id, apartamento)
        return session_id

    async def _obter(self, session_id: str):
        apartamento = store.apartamento_da_sessao(session_id)
        if apartamento is None:
            raise SessaoNaoEncontrada(session_id)
        sessao = await self.sessoes.get_session(
            app_name=NOME_APP,
            user_id=user_id_do_apartamento(apartamento),
            session_id=session_id,
        )
        if sessao is None:
            raise SessaoNaoEncontrada(session_id)
        return sessao

    async def eventos(self, session_id: str) -> list[dict[str, Any]]:
        sessao = await self._obter(session_id)
        return [e.model_dump(mode="json", by_alias=True, exclude_none=True) for e in sessao.events]

    # -- conversa ----------------------------------------------------------

    async def _rodar(self, sessao, mensagem: types.Content) -> dict[str, Any]:
        """Executa o Runner e monta a resposta da API. Chamar com a trava da sessão."""
        textos: list[str] = []
        async for ev in self.runner.run_async(
            user_id=sessao.user_id, session_id=sessao.id, new_message=mensagem
        ):
            texto = _texto_do_evento(ev).strip()
            if texto:
                textos.append(texto)
        atual = await self._obter(sessao.id)
        return {"resposta": "\n".join(textos), "confirmacoes_pendentes": pendencias(atual.events)}

    async def enviar_mensagem(self, session_id: str, texto: str) -> dict[str, Any]:
        async with self._trava(session_id):
            sessao = await self._obter(session_id)
            pendentes = pendencias(sessao.events)
            if pendentes:
                # Nada executa sem confirmação: enquanto houver pendência, a
                # conversa espera a resposta pela rota de confirmações.
                return {
                    "resposta": (
                        "Há uma confirmação pendente. Aprove ou negue pela rota de "
                        "confirmações antes de continuar."
                    ),
                    "confirmacoes_pendentes": pendentes,
                }
            conteudo = types.Content(role="user", parts=[types.Part(text=texto)])
            return await self._rodar(sessao, conteudo)

    async def responder_confirmacao(
        self, session_id: str, confirmacao_id: str, confirmado: bool
    ) -> dict[str, Any]:
        async with self._trava(session_id):
            sessao = await self._obter(session_id)
            if confirmacao_id not in {p["id"] for p in pendencias(sessao.events)}:
                raise ConfirmacaoInexistente(confirmacao_id)
            resposta = types.Content(
                role="user",
                parts=[
                    types.Part(
                        function_response=types.FunctionResponse(
                            id=confirmacao_id,
                            name=CONFIRMACAO_FN,
                            response={"confirmed": bool(confirmado)},
                        )
                    )
                ],
            )
            return await self._rodar(sessao, resposta)

    async def fechar(self) -> None:
        await self.runner.close()
