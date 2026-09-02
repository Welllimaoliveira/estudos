"""
IMPORTAR PROVA ANTERIOR

Junta questoes de uma prova real ao data/questoes.json, com validacao e dedup.

Uso:
    python scripts/importar_prova.py data/_import/petrobras-2023-eng-junior.json

O arquivo de entrada e um JSON:

{
  "concurso": "Petrobras",
  "orgao": "Petrobras",
  "banca": "Cesgranrio",
  "ano": 2023,
  "cargo": "Engenheiro(a) Junior",
  "fonte": "Caderno oficial Cesgranrio + gabarito definitivo",
  "questoes": [
    {
      "disciplina": "Língua Portuguesa",
      "tipo": "multipla",                          // ou "certo_errado"
      "item_original": 12,
      "enunciado": "texto de apoio (opcional)",
      "questao": "comando da questao",
      "alternativas": ["...", "...", "...", "...", "..."],   // so p/ multipla
      "gabarito": "C",                             // letra (multipla) ou CERTO/ERRADO
      "topico": "Coesao textual"                   // opcional
    }
  ]
}

IMPORTANTE: gabarito vem SEMPRE do gabarito oficial definitivo da banca.
Nao invente questao nem resposta. Reformular o enunciado para estudo e ok,
desde que preserve o conteudo cobrado, o numero do item e o gabarito.
"""

import datetime
import json
import re
import sys
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
ARQ_QUESTOES = RAIZ / "data" / "questoes.json"

LETRAS = ["A", "B", "C", "D", "E"]


def slug(texto):
    texto = unicodedata.normalize("NFD", str(texto)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", texto.lower()).strip("-")


def norm(texto):
    texto = unicodedata.normalize("NFD", str(texto).lower())
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def normalizar_gabarito(valor, tipo, n_alt):
    v = norm(valor).strip().upper()
    if tipo == "certo_errado":
        if v in ("C", "CERTO", "CORRETO", "VERDADEIRO"):
            return "CERTO"
        if v in ("E", "ERRADO", "INCORRETO", "FALSO"):
            return "ERRADO"
        raise ValueError(f"gabarito certo/errado invalido: {valor!r}")
    # multipla
    if v in LETRAS[:n_alt]:
        return v
    raise ValueError(f"gabarito de multipla invalido: {valor!r} (esperado A..{LETRAS[n_alt-1]})")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    entrada = Path(sys.argv[1])
    prova = json.loads(entrada.read_text(encoding="utf-8"))

    doc = json.loads(ARQ_QUESTOES.read_text(encoding="utf-8"))
    doc.setdefault("questoes", [])
    existentes_txt = {norm(q["questao"])[:140] for q in doc["questoes"]}
    ids = {q["id"] for q in doc["questoes"]}

    concurso = prova["concurso"]
    banca = prova.get("banca", "")
    ano = prova["ano"]
    base_fonte = prova.get("fonte", f"{banca} {ano}")
    pref = f"{slug(concurso)}-{ano}".upper()

    novas, ignoradas = 0, 0
    for item in prova["questoes"]:
        tipo = item.get("tipo", "multipla")
        texto = str(item.get("questao", "")).strip()
        if not texto:
            ignoradas += 1
            continue
        if norm(texto)[:140] in existentes_txt:
            print(f"  ja existe, pulando: {texto[:60]}...")
            ignoradas += 1
            continue

        alternativas = item.get("alternativas") or []
        if tipo == "multipla" and len(alternativas) < 2:
            print(f"  multipla sem alternativas, pulando item {item.get('item_original')}")
            ignoradas += 1
            continue

        try:
            gab = normalizar_gabarito(item["gabarito"], tipo, len(alternativas))
        except (KeyError, ValueError) as e:
            print(f"  gabarito invalido no item {item.get('item_original')}: {e}")
            ignoradas += 1
            continue

        item_orig = item.get("item_original", "")
        qid = f"{pref}-{item_orig or (len(doc['questoes'])+novas+1)}"
        while qid in ids:
            qid += "b"
        ids.add(qid)

        registro = {
            "id": qid,
            "concurso": concurso,
            "orgao": prova.get("orgao", concurso),
            "cargo": prova.get("cargo", ""),
            "ano": ano,
            "banca": banca,
            "disciplina": item["disciplina"],
            "tipo": tipo,
            "topico": item.get("topico", ""),
            "item_original": item_orig,
            "enunciado": item.get("enunciado", ""),
            "questao": texto,
            "gabarito": gab,
            "explicacao": item.get("explicacao", ""),
            "fonte": item.get("fonte", base_fonte),
        }
        if tipo == "multipla":
            registro["alternativas"] = [str(a).strip() for a in alternativas]

        doc["questoes"].append(registro)
        existentes_txt.add(norm(texto)[:140])
        novas += 1

    doc["atualizado_em"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    doc["disciplinas"] = sorted({q["disciplina"] for q in doc["questoes"]})
    doc["concursos"] = sorted({q["concurso"] for q in doc["questoes"] if q.get("concurso")})
    ARQ_QUESTOES.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"\nOK: +{novas} questoes ({ignoradas} ignoradas). "
          f"Total agora: {len(doc['questoes'])}.")
    print("As explicacoes vazias serao preenchidas pelo workflow gerar-questoes.")


if __name__ == "__main__":
    main()
