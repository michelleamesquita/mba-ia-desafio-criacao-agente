"""Modelo falso e roteirizado para exercitar o fluxo SEM chamar o Gemini.

Serve só para conferir o encanamento (transferência, confirmação, persistência,
concorrência). O comportamento real do assistente vem do Gemini.
"""

from __future__ import annotations

import re
import uuid
from typing import AsyncGenerator

from google.adk.models import BaseLlm, LlmRequest, LlmResponse
from google.genai import types

AREAS = {"salão": "salao-de-festas", "salao": "salao-de-festas", "churrasqueira": "churrasqueira", "quadra": "quadra"}


def _area(texto: str) -> str:
    for chave, valor in AREAS.items():
        if chave in texto.lower():
            return valor
    return "quadra"


def _data(texto: str) -> str:
    m = re.search(r"\d{4}-\d{2}-\d{2}", texto)
    return m.group(0) if m else "2030-01-01"


def _fc(_nome_fn: str, /, **args) -> LlmResponse:
    part = types.Part(function_call=types.FunctionCall(id=f"fc-{uuid.uuid4().hex[:8]}", name=_nome_fn, args=args))
    return LlmResponse(content=types.Content(role="model", parts=[part]))


def _texto(t: str) -> LlmResponse:
    return LlmResponse(content=types.Content(role="model", parts=[types.Part(text=t)]))


class ModeloFalso(BaseLlm):
    model: str = "falso"

    @classmethod
    def supported_models(cls) -> list[str]:
        return ["falso"]

    async def generate_content_async(
        self, llm_request: LlmRequest, stream: bool = False
    ) -> AsyncGenerator[LlmResponse, None]:
        instr = str(llm_request.config.system_instruction or "")
        contents = llm_request.contents
        ultimo = contents[-1]
        resposta = next((p.function_response for p in ultimo.parts if p.function_response), None)

        # Última fala do morador (texto).
        fala = ""
        for c in reversed(contents):
            if c.role == "user" and any(p.text for p in c.parts):
                texto = "".join(p.text or "" for p in c.parts if p.text)
                if texto.startswith("For context"):
                    continue
                fala = texto
                break

        if resposta and resposta.name != "transfer_to_agent":
            yield _texto(f"[{resposta.name}] {resposta.response}")
            return

        baixa = fala.lower()
        if "assistente virtual" in instr:  # principal
            if "piscina" in baixa or "regulamento" in baixa:
                yield _fc("regulamento", request=fala)
            elif "visitante" in baixa or "libera" in baixa:
                yield _fc("transfer_to_agent", agent_name="visitantes")
            else:
                yield _fc("transfer_to_agent", agent_name="reservas")
        elif "especialista em reservas" in instr:
            if "visitante" in baixa or "libera" in baixa or "piscina" in baixa:
                yield _fc("transfer_to_agent", agent_name="assistente")
            elif "cancel" in baixa:
                yield _fc("cancelar_reserva", area=_area(fala), data=_data(fala))
            elif "reserve" in baixa:
                yield _fc("reservar_area", area=_area(fala), data=_data(fala))
            elif "disponib" in baixa:
                yield _fc("consultar_disponibilidade", area=_area(fala), data=_data(fala))
            else:
                yield _fc("listar_minhas_reservas")
        elif "especialista em visitantes" in instr:
            if not ("visitante" in baixa or "libera" in baixa):
                yield _fc("transfer_to_agent", agent_name="assistente")
                return
            nome = re.search(r"(?:entrada d[ao]|libera a entrada d[ao])\s+([A-Za-zÀ-ú ]+?)\s+no dia", fala)
            yield _fc("autorizar_visitante", nome=nome.group(1) if nome else "Fulano", data=_data(fala))
        else:  # regulamento
            yield _fc("ler_capitulo_regulamento", numero=4)
