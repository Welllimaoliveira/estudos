"""
ATUALIZAR CONCURSOS

Le feeds RSS publicos de concursos e gera data/concursos.json para o site.

- Nao usa nenhum segredo.
- Se um feed cair, mantem os itens do outro e nao apaga o JSON anterior.
- Roda 1x/dia pela GitHub Action atualizar-concursos.yml.

Formato de saida (data/concursos.json):

{
  "atualizado_em": "2026-09-02T09:00:00+00:00",
  "fontes": [ {"nome": "...", "url": "..."} ],
  "concursos": [
    {
      "id": "<hash do link>",
      "titulo": "Concurso SEFAZ (SC) abre 50 vagas...",
      "orgao": "SEFAZ (SC)",
      "uf": "SC",
      "status": "aberto" | "previsto",
      "nivel": "superior" | "medio" | "fundamental" | "",
      "vagas": 50 | null,
      "salario": "R$ 25.337,61" | "",
      "resumo": "texto curto",
      "link": "https://...",
      "fonte": "Concursos no Brasil",
      "data": "2026-09-01T22:06:45+00:00",
      "tem_edital": true
    }
  ]
}
"""

import datetime
import hashlib
import html
import json
import re
import sys
import unicodedata
from pathlib import Path

import feedparser

RAIZ = Path(__file__).resolve().parent.parent
ARQUIVO_SAIDA = RAIZ / "data" / "concursos.json"

FEEDS = [
    {
        "nome": "Concursos no Brasil",
        "url": "https://www.concursosnobrasil.com.br/concursos/feed/",
    },
    {
        "nome": "JC Concursos",
        "url": "https://www.jcconcursos.com.br/feed/",
    },
]

MAX_ITENS = 120

# so entram itens que parecem concurso publico
PADRAO_CONCURSO = re.compile(
    r"concurs|edital|processo seletiv|sele[cç][aã]o|vagas?|prova|"
    r"convoca|nomea|homologa|banca|inscri[cç]|servidor|guarda|"
    r"prefeitura|c[aâ]mara|tribunal|pol[ií]cia|bombeir",
    re.IGNORECASE,
)

# Nome do estado (como vem na <category>) -> sigla
UFS = {
    "acre": "AC", "alagoas": "AL", "amapa": "AP", "amazonas": "AM",
    "bahia": "BA", "ceara": "CE", "distrito federal": "DF",
    "espirito santo": "ES", "goias": "GO", "maranhao": "MA",
    "mato grosso": "MT", "mato grosso do sul": "MS", "minas gerais": "MG",
    "para": "PA", "paraiba": "PB", "parana": "PR", "pernambuco": "PE",
    "piaui": "PI", "rio de janeiro": "RJ", "rio grande do norte": "RN",
    "rio grande do sul": "RS", "rondonia": "RO", "roraima": "RR",
    "santa catarina": "SC", "sao paulo": "SP", "sergipe": "SE",
    "tocantins": "TO",
}
UF_SIGLAS = set(UFS.values())


def sem_acento(texto):
    texto = unicodedata.normalize("NFD", texto or "")
    return "".join(c for c in texto if unicodedata.category(c) != "Mn")


def limpar_html(texto):
    texto = re.sub(r"<[^>]+>", " ", texto or "")
    texto = html.unescape(texto)
    texto = re.sub(r"\s+", " ", texto).strip()
    # o feed anexa "O post ... apareceu primeiro em ..." - cortamos
    texto = re.split(r"O post\s", texto)[0].strip()
    return texto


def detectar_uf(entry):
    # 1) tags/categorias
    tags = []
    for t in getattr(entry, "tags", []) or []:
        termo = getattr(t, "term", "") or ""
        tags.append(termo)
        chave = sem_acento(termo).lower().strip()
        if chave in UFS:
            return UFS[chave]
        if termo.strip().upper() in UF_SIGLAS:
            return termo.strip().upper()
    # 2) slug da URL: .../concursos/sc/2026/09/01/...
    m = re.search(r"/concursos/([a-z]{2})/\d{4}/", entry.get("link", ""))
    if m and m.group(1).upper() in UF_SIGLAS:
        return m.group(1).upper()
    # 3) sigla entre parenteses no titulo: "Concurso SEFAZ (SC) ..."
    m = re.search(r"\(([A-Z]{2})\)", entry.get("title", ""))
    if m and m.group(1) in UF_SIGLAS:
        return m.group(1)
    return ""


def detectar_status(entry):
    for t in getattr(entry, "tags", []) or []:
        termo = sem_acento(getattr(t, "term", "") or "").lower()
        if "previsto" in termo:
            return "previsto"
        if "encerrado" in termo or "resultado" in termo:
            return "encerrado"
    titulo = sem_acento(entry.get("title", "")).lower()
    if "previst" in titulo or "autorizad" in titulo or "sera realizado" in titulo:
        return "previsto"
    return "aberto"


def detectar_nivel(texto):
    t = sem_acento(texto).lower()
    tem_sup = "superior" in t
    tem_med = "nivel medio" in t or "ensino medio" in t or "medio completo" in t
    tem_fund = "fundamental" in t
    if tem_sup and (tem_med or tem_fund):
        return "todos"
    if tem_sup:
        return "superior"
    if tem_med:
        return "medio"
    if tem_fund:
        return "fundamental"
    return ""


def detectar_vagas(texto):
    m = re.search(r"(\d[\d.\s]{0,6})\s+vagas?", texto, re.IGNORECASE)
    if not m:
        return None
    try:
        return int(re.sub(r"[.\s]", "", m.group(1)))
    except ValueError:
        return None


def detectar_salario(texto):
    m = re.search(
        r"R\$\s*\d[\d.]*(?:,\d{2})?(?:\s*(?:mil|milh\w+))?",
        texto,
        re.IGNORECASE,
    )
    if not m:
        return ""
    return re.sub(r"\s+", " ", m.group(0)).strip(" .,")


def detectar_orgao(titulo):
    t = titulo.strip()
    for corte in [" abre", " libera", " divulga", " publica", " tem ",
                  " confirma", " autoriza", " prorroga", " retifica",
                  " oferece", " com inscri", " abrira", " ter ", ":", " - "]:
        idx = t.lower().find(corte)
        if idx > 3:
            t = t[:idx]
            break
    t = re.sub(r"^(Concurso|Processo Seletivo|Edital)\s+(d[aeo]\s+)?", "",
               t, flags=re.IGNORECASE)
    t = re.sub(r"^(d[aeo]|em|no|na)\s+", "", t, flags=re.IGNORECASE)
    return t.strip(" .:-") or titulo.strip()


def data_iso(entry):
    for campo in ("published_parsed", "updated_parsed"):
        val = entry.get(campo)
        if val:
            return datetime.datetime(*val[:6], tzinfo=datetime.timezone.utc).isoformat()
    return ""


def processar_feed(feed_cfg):
    print(f"Lendo: {feed_cfg['nome']} ({feed_cfg['url']})")
    d = feedparser.parse(feed_cfg["url"])
    if getattr(d, "bozo", 0) and not d.entries:
        print(f"  AVISO: feed sem itens ({getattr(d, 'bozo_exception', '')})")
        return []
    itens = []
    for entry in d.entries:
        titulo = limpar_html(entry.get("title", ""))
        link = entry.get("link", "").strip()
        if not titulo or not link:
            continue
        resumo = limpar_html(entry.get("summary", ""))
        base_texto = f"{titulo}. {resumo}"
        if not PADRAO_CONCURSO.search(base_texto):
            continue  # descarta propaganda / noticia solta
        itens.append({
            "id": hashlib.sha1(link.encode("utf-8")).hexdigest()[:12],
            "titulo": titulo,
            "orgao": detectar_orgao(titulo),
            "uf": detectar_uf(entry),
            "status": detectar_status(entry),
            "nivel": detectar_nivel(base_texto),
            "vagas": detectar_vagas(base_texto),
            "salario": detectar_salario(base_texto),
            "resumo": resumo[:400],
            "link": link,
            "fonte": feed_cfg["nome"],
            "data": data_iso(entry),
            "tem_edital": bool(re.search(r"edital", base_texto, re.IGNORECASE)),
        })
    print(f"  {len(itens)} itens")
    return itens


def carregar_anterior():
    if ARQUIVO_SAIDA.exists():
        try:
            return json.loads(ARQUIVO_SAIDA.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"concursos": []}


def main():
    anterior = carregar_anterior()
    coletados = []
    falhas = 0
    for feed_cfg in FEEDS:
        try:
            coletados.extend(processar_feed(feed_cfg))
        except Exception as erro:
            falhas += 1
            print(f"  ERRO em {feed_cfg['nome']}: {erro}")

    if not coletados:
        print("Nenhum item novo coletado. Mantendo JSON anterior.")
        if falhas == len(FEEDS):
            sys.exit(0)
        return

    # merge com o anterior (mantem historico recente), dedup por link
    por_link = {}
    for item in anterior.get("concursos", []):
        if item.get("link"):
            por_link[item["link"]] = item
    for item in coletados:
        por_link[item["link"]] = item  # novo sobrescreve

    concursos = list(por_link.values())
    concursos.sort(key=lambda x: x.get("data", ""), reverse=True)
    concursos = concursos[:MAX_ITENS]

    doc = {
        "atualizado_em": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "fontes": FEEDS,
        "total": len(concursos),
        "concursos": concursos,
    }
    ARQUIVO_SAIDA.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nOK: {len(concursos)} concursos gravados em {ARQUIVO_SAIDA}")


if __name__ == "__main__":
    main()
