"""API HTTP (FastAPI) do assistente do Residencial Aurora."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import store
from .service import ConfirmacaoInexistente, Servico, SessaoNaoEncontrada

logger = logging.getLogger("aurora")


class NovaSessao(BaseModel):
    apartamento: str = Field(min_length=1)


class NovaMensagem(BaseModel):
    texto: str = Field(min_length=1)


class RespostaConfirmacao(BaseModel):
    id: str
    confirmado: bool


@asynccontextmanager
async def lifespan(app: FastAPI):
    store.inicializar()  # cria o esquema; semeia só na primeira vez
    app.state.servico = Servico()
    yield
    await app.state.servico.fechar()


app = FastAPI(title="Assistente do Residencial Aurora", lifespan=lifespan)


def _servico() -> Servico:
    return app.state.servico


@app.exception_handler(SessaoNaoEncontrada)
async def _sessao_404(_, exc: SessaoNaoEncontrada):
    return JSONResponse(status_code=404, content={"detail": "Sessão não encontrada."})


@app.exception_handler(ConfirmacaoInexistente)
async def _confirmacao_409(_, exc: ConfirmacaoInexistente):
    return JSONResponse(
        status_code=409,
        content={"detail": "Não há confirmação pendente com esse id nesta sessão."},
    )


@app.exception_handler(Exception)
async def _erro_interno(_, exc: Exception):
    logger.exception("Falha ao executar o agente")
    return JSONResponse(
        status_code=503,
        content={"detail": "Falha ao consultar o modelo. Tente novamente em instantes."},
    )


# -- conversa -----------------------------------------------------------------


@app.post("/sessoes", status_code=201)
async def criar_sessao(corpo: NovaSessao):
    apartamento = corpo.apartamento.strip()
    if not store.apartamento_existe(apartamento):
        raise HTTPException(status_code=422, detail="Apartamento não encontrado.")
    return {"session_id": await _servico().criar_sessao(apartamento)}


@app.post("/sessoes/{session_id}/mensagens")
async def enviar_mensagem(session_id: str, corpo: NovaMensagem):
    return await _servico().enviar_mensagem(session_id, corpo.texto)


@app.post("/sessoes/{session_id}/confirmacoes")
async def responder_confirmacao(session_id: str, corpo: RespostaConfirmacao):
    return await _servico().responder_confirmacao(session_id, corpo.id, corpo.confirmado)


@app.get("/sessoes/{session_id}/eventos")
async def listar_eventos(session_id: str):
    return await _servico().eventos(session_id)


# -- verificação (leitura direta, sem passar pelo modelo) ---------------------


@app.get("/apartamentos/{apartamento}/reservas")
async def reservas_do_apartamento(apartamento: str):
    return store.listar_reservas(apartamento)


@app.get("/apartamentos/{apartamento}/visitantes")
async def visitantes_do_apartamento(apartamento: str):
    return store.listar_visitantes(apartamento)
