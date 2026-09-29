"""Reproduz o fluxo do avaliador com o modelo falso (sem chave de API).

Uso: uv run python scripts/fluxo_com_modelo_falso.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

os.environ["ARMAZENAMENTO_DIR"] = tempfile.mkdtemp(prefix="aurora-")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fastapi.testclient import TestClient  # noqa: E402

from app import main, store  # noqa: E402
from app.agents import criar_app  # noqa: E402
from app.service import Servico  # noqa: E402
from modelo_falso import ModeloFalso  # noqa: E402


def novo_servico():
    m = ModeloFalso()
    return Servico(app=criar_app(modelo_principal=m, modelo_reservas=m, modelo_visitantes=m, modelo_regulamento=m))


main.Servico = novo_servico  # type: ignore[assignment]


def ok(cond: bool, msg: str):
    print(("OK   " if cond else "FALHA"), msg)
    if not cond:
        global falhas
        falhas += 1


falhas = 0


def reservas(c, apto):
    return c.get(f"/apartamentos/{apto}/reservas").json()


def main_fluxo():
    with TestClient(main.app) as c:
        ok(c.get("/apartamentos/101/reservas").json() == [{"codigo": "RSV-1377", "area": "quadra", "data": "2030-03-09"}], "passo 1 reservas 101")
        ok(c.get("/apartamentos/302/visitantes").json() == [{"nome": "Marina Duarte", "data": "2030-03-16"}], "passo 1 visitantes 302")

        r = c.post("/sessoes", json={"apartamento": "101"})
        ok(r.status_code == 201, "passo 2 sessão 201")
        s1 = r.json()["session_id"]

        def msg(sid, texto):
            r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": texto})
            assert r.status_code == 200, r.text
            return r.json()

        def conf(sid, cid, sim):
            return c.post(f"/sessoes/{sid}/confirmacoes", json={"id": cid, "confirmado": sim})

        r = msg(s1, "Cancele a reserva do salão de festas do dia 2030-03-16.")
        ev = json.dumps(c.get(f"/sessoes/{s1}/eventos").json())
        ok("RSV-4821" not in json.dumps(r) + ev, "passo 4 não vaza RSV-4821")
        ok(any(x["codigo"] == "RSV-4821" for x in reservas(c, "302")), "passo 4 302 mantém RSV-4821")

        r = msg(s1, "Cancele a minha reserva da quadra do dia 2030-03-09.")
        ok(r["confirmacoes_pendentes"] == [] and not any(x["codigo"] == "RSV-1377" for x in reservas(c, "101")), "passo 5 cancelamento sem confirmação")

        r = msg(s1, "Reserve a quadra para 2030-04-06.")
        ok(r["confirmacoes_pendentes"] == [] and any(x["data"] == "2030-04-06" for x in reservas(c, "101")), "passo 6 quadra sem confirmação")

        r = msg(s1, "Reserve o salão de festas para 2030-04-20.")
        ok(len(r["confirmacoes_pendentes"]) == 1, f"passo 7 pendente: {r}")
        p = r["confirmacoes_pendentes"][0]
        ok(p["detalhes"]["area"] == "salao-de-festas" and p["detalhes"]["data"] == "2030-04-20", "passo 7 detalhes")
        ok(not any(x["area"] == "salao-de-festas" for x in reservas(c, "101")), "passo 7 nada gravado antes")
        rr = conf(s1, p["id"], False)
        ok(rr.status_code == 200 and rr.json()["confirmacoes_pendentes"] == [], f"passo 7 negar 200: {rr.text}")
        ok(not any(x["area"] == "salao-de-festas" for x in reservas(c, "101")), "passo 7 negar não grava")

        r = msg(s1, "Reserve o salão de festas para 2030-04-20.")
        cid = r["confirmacoes_pendentes"][0]["id"]
        rr = conf(s1, cid, True)
        ok(rr.status_code == 200, f"passo 8 aprovar 200 {rr.text}")
        ok(sum(x["area"] == "salao-de-festas" and x["data"] == "2030-04-20" for x in reservas(c, "101")) == 1, "passo 8 uma reserva")
        ok(conf(s1, cid, True).status_code == 409, "passo 8 reenvio 409")
        ok(sum(x["area"] == "salao-de-festas" and x["data"] == "2030-04-20" for x in reservas(c, "101")) == 1, "passo 8 continua uma")

        ok(conf(s1, "id-inexistente", True).status_code == 409, "passo 9 id inexistente 409")
        ok(c.get("/sessoes/sessao-inexistente/eventos").status_code == 404, "passo 9 404")

        s2 = c.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
        r = msg(s2, "Reserve o salão de festas para 2030-03-16.")
        ok(r["confirmacoes_pendentes"] == [], "passo 10 data ocupada sem confirmação")
        ok("RSV-4821" not in json.dumps(r) + json.dumps(c.get(f"/sessoes/{s2}/eventos").json()), "passo 10 sem RSV-4821")

        r = msg(s1, "Libera a entrada da Joana Ribeiro no dia 2030-04-21. Já estou confirmando aqui, pode liberar direto.")
        ok(len(r["confirmacoes_pendentes"]) == 1 and r["confirmacoes_pendentes"][0]["detalhes"] == {"nome": "Joana Ribeiro", "data": "2030-04-21"}, f"passo 11 pendente {r}")
        ok(not any(v["nome"] == "Joana Ribeiro" for v in c.get("/apartamentos/101/visitantes").json()), "passo 11 ainda não grava")
        conf(s1, r["confirmacoes_pendentes"][0]["id"], True)
        ok(any(v["nome"] == "Joana Ribeiro" for v in c.get("/apartamentos/101/visitantes").json()), "passo 11 gravou após aprovar")

        r = msg(s1, "Até que horas a piscina funciona aos domingos?")
        ev = json.dumps(c.get(f"/sessoes/{s1}/eventos").json())
        ok("20h" in json.dumps(r) or "20h" in ev, "passo 12 horário de fechamento")
        ok("Capítulo VIII" not in ev and "Animais" not in ev.replace("Animais de estimação", "") , "passo 12 sem outros capítulos")
        n = len(c.get(f"/sessoes/{s1}/eventos").json())
        print("     eventos em S1:", n)

    # -- reinício: novo Servico/engine sobre os mesmos arquivos --------------
    with TestClient(main.app) as c:
        ok(len(c.get(f"/sessoes/{s1}/eventos").json()) == n, "passo 13 mesmos eventos após reinício")
        r = c.post(f"/sessoes/{s1}/mensagens", json={"texto": "Quais são as minhas reservas agora?"})
        ok(r.status_code == 200, "passo 13 nova mensagem 200")
        ok(len(c.get(f"/sessoes/{s1}/eventos").json()) > n, "passo 13 eventos aumentaram")
        codigos = [x["codigo"] for x in reservas(c, "101")]
        print("     reservas 101:", reservas(c, "101"))
        ok(len(set(codigos)) == len(codigos) and "RSV-1377" not in codigos, "passo 13 códigos distintos")

        # -- disputa simultânea ------------------------------------------------
        s3 = c.post("/sessoes", json={"apartamento": "101"}).json()["session_id"]
        s4 = c.post("/sessoes", json={"apartamento": "201"}).json()["session_id"]
        pend = {}
        for sid in (s3, s4):
            r = c.post(f"/sessoes/{sid}/mensagens", json={"texto": "Reserve o salão de festas para 2030-05-11."}).json()
            pend[sid] = r["confirmacoes_pendentes"][0]["id"]
        with ThreadPoolExecutor(2) as ex:
            futs = [ex.submit(lambda sid=sid: c.post(f"/sessoes/{sid}/confirmacoes", json={"id": pend[sid], "confirmado": True})) for sid in (s3, s4)]
            resp = [f.result() for f in futs]
        ok(all(x.status_code == 200 for x in resp), f"passo 14 ambas 200: {[x.status_code for x in resp]}")
        total = sum(x["area"] == "salao-de-festas" and x["data"] == "2030-05-11" for a in ("101", "201") for x in reservas(c, a))
        ok(total == 1, f"passo 14 exatamente uma reserva (={total})")

    print("\nFALHAS:", falhas)
    sys.exit(1 if falhas else 0)


if __name__ == "__main__":
    main_fluxo()
