"""
GERAR / ENRIQUECER QUESTOES

Adaptado de podcast/generate_podcast.py (mesmo padrao de chamada ao Gemini com
responseSchema + retries). Aqui NAO gera audio: so mexe em data/questoes.json.

REGRA DO PROJETO: o banco (data/questoes.json) e formado SOMENTE por questoes
de provas anteriores reais, com gabarito oficial. Questoes novas se adicionam a
mao (ou via scripts/importar_prova.py) a partir do caderno oficial + gabarito
definitivo da banca.

O que este script faz por execucao:

1. BACKFILL DE EXPLICACOES (principal)
   Para questoes com "explicacao" vazia, pede ao Gemini uma explicacao didatica
   do gabarito OFICIAL. A IA nunca decide/muda o gabarito - ele vem do JSON.

2. QUESTOES DE TREINO (DESLIGADO por padrao: QUESTOES_TREINO_POR_RUN = 0)
   Questoes autorais de IA. So use se quiser um modo de treino conceitual
   separado; ficam marcadas com "origem": "treino-ia".

Segredo necessario: GEMINI_API_KEY
"""

import datetime
import json
import os
import re
import sys
import unicodedata
from pathlib import Path

import requests

RAIZ = Path(__file__).resolve().parent.parent
ARQ_QUESTOES = RAIZ / "data" / "questoes.json"
ARQ_ESTADO = RAIZ / "data" / "questoes_state.json"

MODELO_GEMINI = "gemini-flash-latest"

# limites por execucao (nao estourar a cota gratuita)
MAX_EXPLICACOES_POR_RUN = 12

# 0 = desligado. O projeto usa SOMENTE questoes de provas anteriores; a IA
# entra apenas para explicar o gabarito oficial (nunca para inventar questao
# ou decidir gabarito). Deixe em 0 salvo se quiser questoes autorais de treino.
QUESTOES_TREINO_POR_RUN = 0
DISCIPLINAS_TREINO = [
    "Direito Constitucional", "Direito Administrativo", "Informática",
    "Raciocínio lógico", "Português", "Atualidades",
]


# ==========================================================
# UTIL (mesma logica do projeto podcast)
# ==========================================================

def normalizar_texto(texto):
    texto = unicodedata.normalize("NFD", str(texto).strip().lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def normalizar_gabarito(valor):
    v = normalizar_texto(valor)
    if v in ("c", "certo", "correto", "verdadeiro"):
        return "CERTO"
    if v in ("e", "errado", "incorreto", "falso"):
        return "ERRADO"
    raise ValueError(f"Gabarito invalido: {valor}")


def limpar_json_gemini(texto):
    texto = str(texto).strip()
    if texto.startswith("```"):
        linhas = texto.splitlines()
        if linhas and linhas[0].startswith("```"):
            linhas = linhas[1:]
        if linhas and linhas[-1].strip() == "```":
            linhas = linhas[:-1]
        texto = "\n".join(linhas)
    return texto.strip()


def chamar_gemini(prompt, response_schema, max_output_tokens=6000, temperature=0.5):
    api_key = os.environ["GEMINI_API_KEY"]
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{MODELO_GEMINI}:generateContent?key={api_key}"
    )
    corpo = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": temperature,
            "maxOutputTokens": max_output_tokens,
            "responseMimeType": "application/json",
            "responseSchema": response_schema,
        },
    }
    resp = requests.post(url, json=corpo, timeout=180)
    resp.raise_for_status()
    dados = resp.json()
    cand = (dados.get("candidates") or [{}])[0]
    if cand.get("finishReason") == "MAX_TOKENS":
        raise RuntimeError("MAX_TOKENS")
    partes = cand.get("content", {}).get("parts", [])
    if not partes or not partes[0].get("text"):
        raise RuntimeError("Resposta vazia do Gemini")
    return json.loads(limpar_json_gemini(partes[0]["text"]))


# ==========================================================
# ARQUIVOS
# ==========================================================

def carregar_questoes():
    doc = json.loads(ARQ_QUESTOES.read_text(encoding="utf-8"))
    doc.setdefault("questoes", [])
    return doc


def salvar_questoes(doc):
    doc["atualizado_em"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc["disciplinas"] = sorted({q["disciplina"] for q in doc["questoes"]})
    ARQ_QUESTOES.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def carregar_estado():
    if ARQ_ESTADO.exists():
        try:
            return json.loads(ARQ_ESTADO.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"ultimo_indice_treino": -1, "ultima_execucao": None, "gerados": 0}


def salvar_estado(estado):
    estado["ultima_execucao"] = datetime.datetime.now(
        datetime.timezone.utc
    ).isoformat()
    ARQ_ESTADO.write_text(
        json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ==========================================================
# 1) BACKFILL DE EXPLICACOES
# ==========================================================

SCHEMA_EXPLICACOES = {
    "type": "OBJECT",
    "properties": {
        "explicacoes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "explicacao": {"type": "STRING"},
                },
                "required": ["id", "explicacao"],
            },
        }
    },
    "required": ["explicacoes"],
}


def backfill_explicacoes(doc):
    pendentes = [q for q in doc["questoes"] if not str(q.get("explicacao", "")).strip()]
    if not pendentes:
        print("Nenhuma explicacao pendente.")
        return 0
    lote = pendentes[:MAX_EXPLICACOES_POR_RUN]
    print(f"Gerando explicacao para {len(lote)} questoes...")

    blocos = []
    for q in lote:
        blocos.append(
            f"ID: {q['id']}\n"
            f"DISCIPLINA: {q['disciplina']}\n"
            f"BANCA/ANO: {q.get('banca', '')} {q.get('ano', '')}\n"
            f"QUESTAO: {q['questao']}\n"
            f"GABARITO OFICIAL: {q['gabarito']}\n"
        )
    prompt = f"""
Voce esta preparando a correcao didatica de questoes de concurso de certo/errado.

REGRA MAIS IMPORTANTE: o campo GABARITO OFICIAL e FIXO. Voce NAO pode mudar,
corrigir ou contestar o gabarito. Sua funcao e SO explicar o raciocinio que leva
ao gabarito oficial informado.

Para cada questao, escreva de 80 a 150 palavras: explique o conceito, aponte a
pegadinha e diga qual raciocinio o candidato deveria usar. Nao invente numero de
artigo, nao invente jurisprudencia, nao invente dados. Se uma norma pode ter
mudado, nao trate a regra antiga como situacao atual.

QUESTOES:

{chr(10).join(blocos)}

Retorne somente JSON: {{"explicacoes": [{{"id": "...", "explicacao": "..."}}]}}
"""
    ultimo_erro = None
    for tentativa in range(1, 4):
        try:
            resp = chamar_gemini(prompt, SCHEMA_EXPLICACOES, temperature=0.4)
            mapa = {
                str(it["id"]).strip(): str(it["explicacao"]).strip()
                for it in resp.get("explicacoes", [])
                if it.get("id") and it.get("explicacao")
            }
            aplicadas = 0
            for q in lote:
                if q["id"] in mapa:
                    q["explicacao"] = mapa[q["id"]]
                    aplicadas += 1
            print(f"  {aplicadas} explicacoes aplicadas.")
            return aplicadas
        except Exception as erro:
            ultimo_erro = erro
            print(f"  tentativa {tentativa} falhou: {erro}")
    raise RuntimeError(f"Falha no backfill de explicacoes: {ultimo_erro}")


# ==========================================================
# 2) QUESTOES DE TREINO (AUTORAIS)
# ==========================================================

SCHEMA_TREINO = {
    "type": "OBJECT",
    "properties": {
        "questoes": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "questao": {"type": "STRING"},
                    "gabarito": {"type": "STRING", "enum": ["CERTO", "ERRADO"]},
                    "explicacao": {"type": "STRING"},
                    "topico": {"type": "STRING"},
                },
                "required": ["questao", "gabarito", "explicacao", "topico"],
            },
        }
    },
    "required": ["questoes"],
}


def gerar_treino(doc, estado):
    if QUESTOES_TREINO_POR_RUN <= 0:
        return 0
    idx = (estado.get("ultimo_indice_treino", -1) + 1) % len(DISCIPLINAS_TREINO)
    disciplina = DISCIPLINAS_TREINO[idx]
    estado["ultimo_indice_treino"] = idx
    print(f"Gerando {QUESTOES_TREINO_POR_RUN} questoes de treino de {disciplina}...")

    prompt = f"""
Crie {QUESTOES_TREINO_POR_RUN} questoes AUTORAIS de concurso publico, no estilo
Cebraspe de CERTO/ERRADO, sobre "{disciplina}".

Regras:
- foco CONCEITUAL; nao cite numero de artigo, sumula ou julgado especifico;
- nada de datas/estatisticas inventadas;
- misture questoes com gabarito CERTO e ERRADO;
- cada enunciado com 1 a 3 frases;
- a explicacao (60 a 120 palavras) deve justificar o gabarito e mostrar a pegadinha;
- "topico" = subtema curto dentro da disciplina.

Retorne somente JSON:
{{"questoes": [{{"questao": "...", "gabarito": "CERTO", "explicacao": "...", "topico": "..."}}]}}
"""
    resp = None
    for tentativa in range(1, 4):
        try:
            resp = chamar_gemini(
                prompt, SCHEMA_TREINO,
                max_output_tokens=8192, temperature=0.7,
            )
            if resp.get("questoes"):
                break
        except Exception as erro:
            print(f"  tentativa {tentativa} falhou: {erro}")
    if not resp or not resp.get("questoes"):
        print("  nenhuma questao de treino retornada nesta execucao.")
        return 0

    existentes = {normalizar_texto(q["questao"])[:120] for q in doc["questoes"]}
    ano = datetime.date.today().year
    seq = sum(1 for q in doc["questoes"] if q.get("origem") == "treino-ia")
    novas = 0
    for item in resp.get("questoes", []):
        texto = str(item.get("questao", "")).strip()
        if not texto or normalizar_texto(texto)[:120] in existentes:
            continue
        try:
            gab = normalizar_gabarito(item.get("gabarito"))
        except ValueError:
            continue
        seq += 1
        doc["questoes"].append({
            "id": f"TREINO-{ano}-{seq:04d}",
            "concurso": "Treino",
            "orgao": "",
            "ano": ano,
            "banca": "Autoral (IA)",
            "disciplina": disciplina,
            "tipo": "certo_errado",
            "topico": str(item.get("topico", "")).strip(),
            "item_original": "",
            "questao": texto,
            "gabarito": gab,
            "explicacao": str(item.get("explicacao", "")).strip(),
            "origem": "treino-ia",
            "fonte": "Questao autoral gerada por IA para treino conceitual.",
        })
        novas += 1
    print(f"  {novas} questoes de treino adicionadas.")
    return novas


# ==========================================================
# MAIN
# ==========================================================

def main():
    if "GEMINI_API_KEY" not in os.environ:
        print("ERRO: defina GEMINI_API_KEY.")
        sys.exit(1)

    doc = carregar_questoes()
    estado = carregar_estado()
    total_antes = len(doc["questoes"])

    mudou = 0
    mudou += backfill_explicacoes(doc)
    mudou += gerar_treino(doc, estado)

    if mudou == 0:
        print("Nada mudou nesta execucao.")
        return

    salvar_questoes(doc)
    estado["gerados"] = estado.get("gerados", 0) + mudou
    salvar_estado(estado)
    print(
        f"\nOK. Questoes: {total_antes} -> {len(doc['questoes'])}. "
        f"Alteracoes nesta run: {mudou}."
    )


if __name__ == "__main__":
    main()
