# Estuda Aí — plataforma de estudos para concursos

Site estático (um `index.html`) publicado na Vercel. Reúne num lugar só:

| Módulo | O que faz | Fonte dos dados |
|---|---|---|
| 🗂️ Concursos | lista de concursos abertos/previstos com filtro por UF, status, nível | `data/concursos.json` (Action diária lê RSS) |
| 📄 Provas & gabaritos | links oficiais das bancas + resolver provas reais do ENEM | `data/provas.json`, `data/bancas.json`, API `enem.dev` |
| ✍️ Banco de questões | resolve questões certo/errado, salva acertos/erros, revisão | `data/questoes.json` |
| ⏱️ Simulado | sessão cronometrada (banco + ENEM), placar por disciplina | `data/questoes.json` + API `enem.dev` |
| 🧭 Trilhas de estudo | checklist de tópicos por disciplina, com progresso | `data/trilhas.json` |
| 📰 Editais & notícias | itens recentes marcados como "edital" | `data/concursos.json` |
| 🔌 Fontes & APIs | catálogo das APIs/feeds usados e candidatos futuros | `data/fontes-apis.json` |

Todo o progresso do usuário fica em `localStorage` (`estuda_ai_v1`). Não há login nem backend.

## Rodar local

```bash
python -m http.server 8000
# abre http://localhost:8000
```

## Automação (GitHub Actions)

- **`atualizar-concursos.yml`** — 2x/dia. Roda `scripts/atualizar_concursos.py`, que lê os feeds
  RSS do *Concursos no Brasil* e do *JC Concursos*, monta `data/concursos.json` e commita.
  Não precisa de segredo.
- **`gerar-questoes.yml`** — seg/qua/sex. Roda `scripts/gerar_questoes.py`, que usa o Gemini para
  (1) preencher explicações das questões que estão sem e (2) gerar algumas questões conceituais de
  treino. **O gabarito oficial nunca é decidido/alterado pela IA.** Precisa do segredo
  `GEMINI_API_KEY` (mesma chave gratuita do Google AI Studio usada no projeto `podcast`).

Cada commit das Actions dispara um redeploy automático na Vercel.

### Seed de questões

`data/questoes.json` foi iniciado com 60 questões da prova PRF 2021 (Cebraspe), reformuladas para
estudo preservando o item e o gabarito oficial definitivo. O campo `explicacao` começa vazio e é
preenchido pela Action.

## Deploy na Vercel

1. `git push` para `github.com/Welllimaoliveira/estudos`.
2. Na Vercel: **Add New Project → Import** esse repo. Framework Preset: **Other**. Build Command:
   vazio. Output Directory: `.` (raiz). Deploy.
3. Em **Settings → Environment Variables** não é preciso nada para o site.
4. No GitHub, **Settings → Secrets and variables → Actions → New repository secret**:
   `GEMINI_API_KEY` = sua chave do Google AI Studio.
5. Rode as duas Actions uma vez pela aba **Actions → Run workflow** para popular os dados.

## Nota sobre commits manuais

Ao commitar à mão neste repo, confira antes:

```bash
git config user.email   # deve ser wellinson25@hotmail.com
git config user.name    # deve ser Welllimaoliveira
```

É essa identidade que está ligada à conta GitHub que a Vercel usa para autorizar o deploy.
