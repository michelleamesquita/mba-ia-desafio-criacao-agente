"""Consulta ao regulamento (dados/regulamento.md) por capítulo e por artigo.

O texto inteiro NUNCA vai para o modelo nem para o histórico da sessão: as
tools abaixo devolvem só um capítulo (ou alguns artigos de um único capítulo).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache

from .config import DIR_DADOS

_STOPWORDS = {
    "a", "o", "as", "os", "de", "da", "do", "das", "dos", "em", "na", "no", "nas",
    "nos", "um", "uma", "uns", "umas", "e", "ou", "que", "por", "para", "com",
    "sem", "se", "ao", "aos", "pode", "posso", "podem", "qual", "quais", "como",
    "quando", "onde", "ate", "sao", "ser", "tem", "ha", "meu", "minha", "eu",
    "voce", "condominio", "regulamento", "morador", "moradores", "regra", "regras",
}

_MAX_CARACTERES = 6000


def _sem_acento(texto: str) -> str:
    base = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in base if not unicodedata.combining(c))


def _tokens(texto: str) -> list[str]:
    palavras = re.findall(r"[a-z0-9]+", _sem_acento(texto))
    saida = []
    for p in palavras:
        if p in _STOPWORDS or len(p) < 3:
            continue
        # "stemming" mínimo: plural simples.
        if len(p) > 4 and p.endswith("es"):
            p = p[:-2]
        elif len(p) > 3 and p.endswith("s"):
            p = p[:-1]
        saida.append(p)
    return saida


@dataclass(frozen=True)
class Artigo:
    numero: int
    texto: str


@dataclass(frozen=True)
class Capitulo:
    numero: int
    romano: str
    titulo: str
    artigos: tuple[Artigo, ...]
    texto: str


@lru_cache(maxsize=1)
def capitulos() -> tuple[Capitulo, ...]:
    bruto = (DIR_DADOS / "regulamento.md").read_text(encoding="utf-8")
    resultado: list[Capitulo] = []
    partes = re.split(r"^## (?=Cap[ií]tulo)", bruto, flags=re.MULTILINE)[1:]
    for indice, parte in enumerate(partes, start=1):
        cabecalho, _, corpo = parte.partition("\n")
        m = re.match(r"Cap[ií]tulo\s+([IVXLC]+)\s*:\s*(.+)", cabecalho.strip())
        romano, titulo = (m.group(1), m.group(2).strip()) if m else (str(indice), cabecalho)
        artigos = []
        for bloco in re.split(r"\n(?=\*\*Art\. )", "\n" + corpo.strip()):
            bloco = bloco.strip()
            n = re.match(r"\*\*Art\. (\d+)", bloco)
            if n:
                artigos.append(Artigo(int(n.group(1)), bloco))
        texto = f"Capítulo {romano}: {titulo}\n\n{corpo.strip()}"
        resultado.append(Capitulo(indice, romano, titulo, tuple(artigos), texto))
    return tuple(resultado)


def indice() -> list[dict]:
    """Só números e títulos dos capítulos (sem conteúdo)."""
    return [{"capitulo": c.numero, "titulo": c.titulo} for c in capitulos()]


def ler_capitulo(numero: int) -> dict:
    for c in capitulos():
        if c.numero == numero:
            return {"capitulo": c.numero, "titulo": c.titulo, "texto": c.texto[:_MAX_CARACTERES]}
    return {"erro": f"Capítulo {numero} não existe. Use um número de 1 a {len(capitulos())}."}


def buscar(termos: str, max_artigos: int = 3) -> dict:
    """Busca por palavras-chave e devolve artigos de UM único capítulo."""
    consulta = set(_tokens(termos))
    if not consulta:
        return {"erro": "Informe palavras-chave sobre o assunto."}

    melhor_pontos, melhor = 0.0, None
    for cap in capitulos():
        titulo = set(_tokens(cap.titulo))
        pontuados = []
        for art in cap.artigos:
            palavras = _tokens(art.texto)
            pontos = sum(1 for p in palavras if p in consulta) / (1 + len(palavras) ** 0.3)
            pontuados.append((pontos, art))
        pontuados.sort(key=lambda x: -x[0])
        topo = pontuados[:max_artigos]
        pontos_cap = sum(p for p, _ in topo) + 3.0 * len(titulo & consulta)
        if pontos_cap > melhor_pontos:
            melhor_pontos, melhor = pontos_cap, (cap, topo)

    if melhor is None or melhor_pontos == 0:
        return {"encontrado": False, "mensagem": "Nada encontrado no regulamento para esses termos."}

    cap, topo = melhor
    escolhidos = sorted((a for p, a in topo if p > 0), key=lambda a: a.numero)
    texto = "\n\n".join(a.texto for a in escolhidos)[:_MAX_CARACTERES]
    return {
        "encontrado": True,
        "capitulo": cap.numero,
        "titulo": cap.titulo,
        "artigos": texto,
    }
