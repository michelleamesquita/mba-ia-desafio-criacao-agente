"""Configuração central: caminhos, variáveis de ambiente e modelos."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.adk.models import Gemini
from google.genai import types

RAIZ = Path(__file__).resolve().parent.parent
load_dotenv(RAIZ / ".env")

# O ADK/genai usa a API do Google AI Studio (chave), não o Vertex AI.
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

DIR_DADOS = RAIZ / "dados"
DIR_ARMAZENAMENTO = Path(os.getenv("ARMAZENAMENTO_DIR") or RAIZ / "armazenamento")

# Dados do condomínio (reservas, visitantes, vínculo sessão -> apartamento).
DB_CONDOMINIO = DIR_ARMAZENAMENTO / "condominio.db"
# Sessões e eventos do ADK (DatabaseSessionService).
DB_SESSOES = DIR_ARMAZENAMENTO / "sessoes.db"

NOME_APP = "aurora"

# Modelos: variáveis de ambiente em branco caem no padrão abaixo.
MODELO_PADRAO = "gemini-flash-lite-latest"


def modelo(variavel: str) -> Gemini:
    """Gemini com nova tentativa em 429/503 (limites de taxa do plano gratuito)."""
    nome = os.getenv(variavel) or os.getenv("MODELO_PADRAO") or MODELO_PADRAO
    return Gemini(
        model=nome,
        retry_options=types.HttpRetryOptions(
            attempts=8,
            initial_delay=5.0,
            max_delay=60.0,
            exp_base=2.0,
            http_status_codes=[429, 500, 503],
        ),
    )
