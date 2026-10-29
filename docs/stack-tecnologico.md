# STC — Stack tecnológico (v0.1)

> Estado: proposta inicial, em 2026-10-29. Descreve o que se **propõe usar**, não o que já existe. Os requisitos funcionais estão em `docs/spec-funcionalidades.md` e as normas de interface em `docs/SPEC-UI.md`. As versões exatas ficam fixadas em `pyproject.toml` e `frontend/package.json` (fonte normativa); este documento só indica a linha de base.

## 1. Visão geral

```
Raiz (pasta com 100+ aplicações, montada só de leitura)
   │
   ▼
Indexador (CLI + workers) ──► PostgreSQL 17 + pgvector ◄── Motor de consulta (biblioteca Python)
   │   │                         ▲  catálogos (índice),                ▲              ▲
   │   │                         │  chunks, símbolos, relações        │              │
   │   └─► ./catalogos (markdown versionado em Git)                   │              │
   │                                                                  │              │
   └─► LLM (Anthropic) + embeddings (Voyage)          API FastAPI ────┘          Servidor MCP
                                                            │                    (stdio / HTTP)
                                                  Browser ──┴──► Angular (chat)
```

Princípios transversais:

- **Um só motor de consulta.** API do chat e servidor MCP são camadas finas sobre a mesma biblioteca; nenhuma duplica lógica de pesquisa (spec funcional, §2).
- **Factos são determinísticos, interpretação é do LLM.** Deteção de aplicações, símbolos, relações, hashes e localização (ficheiro/linhas) vêm de código e análise estática. O LLM resume, classifica e responde, sempre com evidência verificável.
- **O código das aplicações é só de leitura.** Nunca é alterado nem executado.

## 2. Backend

| Área | Tecnologia | Notas |
|---|---|---|
| Linguagem | **Python ≥ 3.12** | Tipagem estrita; português nos termos de domínio |
| API | **FastAPI** + **Uvicorn** | Streaming das respostas do chat por SSE |
| Modelos e validação | **Pydantic ≥ 2**, **jsonschema** | Saída estruturada do LLM validada por schema antes de ser aceite |
| Análise de código | **tree-sitter** (`tree-sitter` + `tree-sitter-language-pack`) | Chunking por função/classe/módulo e extração de símbolos em todas as linguagens suportadas (IDX-08) |
| Deteção de segredos | **detect-secrets** + regras por nome de ficheiro | Corre antes de qualquer outro processamento (IDX-06, TRV-01) |
| Pesquisa literal | **ripgrep** (processo) | Padrões e texto exato sobre a Raiz atual (QRY-07) |
| Histórico de versões | **git** (CLI, `subprocess`) | Deteção de alterações e versão indexada por aplicação (LIF-02, LIF-08) |
| MCP | **SDK oficial de MCP para Python** (`mcp`) | Transportes stdio e HTTP (streamable) |
| Cliente LLM | **SDK oficial da Anthropic** | Ver §5 |
| Cliente de embeddings | **SDK Voyage** atrás de uma interface própria | Fornecedor substituível (§5) |
| Tarefas em segundo plano | **Workers próprios sobre PostgreSQL** (`FOR UPDATE SKIP LOCKED`) | Sem broker externo (§8) |
| CLI | `stc` (**Typer**) | `indexar`, `reindexar`, `estado`, `catalogo`, `avaliar`, `migrar-db`, `servir-mcp` |
| Empacotamento | **hatchling**, ambientes com **uv** | Pacote `stc` em `src/` |
| Qualidade | **pytest**, **ruff**, **mypy**, **httpx** (cliente de teste) | Ver §7 |
| Logs | **structlog** (JSON) | Nunca regista código fonte nem segredos |

## 3. Dados

- **PostgreSQL 17** (`pgvector/pgvector:pg17`) como única base de dados.
- **pgvector em uso**: vetores dos chunks de código e dos catálogos (índice HNSW). Cada vetor guarda modelo e versão do embedding; mudar de modelo implica reindexar.
- **Pesquisa lexical**: `tsvector` (texto) e **pg_trgm** (identificadores e fragmentos de nomes). A pesquisa híbrida combina vetorial + lexical com *reciprocal rank fusion* e reranking (§5).
- **Grafo de relações em tabelas** (aplicação → aplicação, com tipo e evidência) consultado com CTEs recursivas para dependências transitivas (QRY-03). Não se usa base de dados de grafos.
- **Tabelas principais**: `aplicacoes`, `ficheiros`, `chunks`, `simbolos`, `relacoes`, `catalogos`, `execucoes_indexacao`, `tarefas`, `conversas`, `mensagens`, `feedback`.
- **Dois papéis de base de dados**: o dono do esquema (migrações) e `stc_app` (API e workers, sem poder de dono).
- **Migrações SQL** numeradas em `src/stc/db/migracoes/`, aplicadas por `stc migrar-db`. **Driver**: `psycopg 3` (`binary`, `pool`).
- **Catálogos e mapa global como markdown** em `./catalogos/<aplicacao>.md` e `./catalogos/_mapa-global.md`, num repositório Git próprio. É a fonte de verdade: a base de dados guarda apenas o índice. As correções manuais ficam em blocos marcados que a regeneração preserva (CAT-04, CAT-05).
- **Volumes**: a Raiz é montada só de leitura; `./catalogos` e `./dados` (caches, relatórios de indexação) são de escrita.

## 4. Pipeline de indexação

1. **Deteção**: percorre a Raiz, aplica exclusões e identifica aplicações por ficheiros marcadores (`package.json`, `pom.xml`, `*.csproj`, `pyproject.toml`, `go.mod`, …), com correções manuais em `stc.toml` (IDX-02, IDX-03).
2. **Higiene**: exclusões de ruído e deteção de segredos; o que for excluído fica registado sem o valor.
3. **Decomposição**: tree-sitter gera chunks e símbolos; ficheiros de linguagens sem gramática entram como texto e são sinalizados (IDX-04).
4. **Incremental**: SHA-256 por ficheiro mais `git diff` entre versões; só se reprocessam ficheiros alterados e as aplicações afetadas (LIF-01 a LIF-04).
5. **Embeddings**: só dos chunks novos ou alterados, em lotes.
6. **Relações**: extraídas por análise estática e heurísticas (URLs e endpoints, filas e tópicos, ligações a bases de dados, bibliotecas internas, configuração) e depois confirmadas pelo LLM com evidência (CAT-07).
7. **Catálogo por aplicação**: LLM com saída estruturada e contexto montado a partir de símbolos, manifestos, esquemas e pontos de entrada; cada afirmação leva evidência e grau de confiança (CAT-01 a CAT-03).
8. **Mapa global**: construído a partir dos catálogos e da tabela de relações.

As aplicações processam-se em paralelo, com limite de concorrência e de pedidos por minuto ao LLM. Uma falha numa aplicação não interrompe as restantes (LIF-06). A estimativa de custo antes de uma indexação completa (LIF-07) usa o número de tokens dos chunks.

## 5. Inteligência artificial

| Uso | Fornecedor | Notas |
|---|---|---|
| Geração de catálogos e mapa global | **API da Anthropic** (Claude) | Saída estruturada validada; prompt caching e **Batch API** na geração em massa para reduzir custo |
| Chat | **API da Anthropic** (Claude) com *tool use* | Mesmo conjunto de ferramentas do MCP; ciclo do agente no servidor; respostas em streaming |
| Embeddings de código e de catálogos | **Voyage AI** (modelo de código), atrás de uma interface | Alternativas via configuração: OpenAI ou modelo local |
| Reranking | **Voyage AI** (reranker) | Opcional por configuração; aplicado à lista híbrida |

- Os **modelos são configuráveis** por variável de ambiente (`STC_LLM_MODELO_CATALOGO`, `STC_LLM_MODELO_CHAT`, `STC_EMBEDDINGS_MODELO`), nunca fixados em código.
- **Que dados saem da infraestrutura**: excertos de código e metadados de aplicações vão ao LLM e ao fornecedor de embeddings. Nunca saem segredos (IDX-06). Exclusões por aplicação em `stc.toml` (TRV-02).
- Ver §8 sobre a opção de modelos locais para aplicações sensíveis.

## 6. Frontend (chat)

Segue `docs/SPEC-UI.md`; este quadro fixa apenas as bibliotecas.

| Área | Tecnologia |
|---|---|
| Framework | **Angular** (componentes *standalone*, **signals**, `OnPush`), TypeScript |
| Estilos | **Tailwind CSS** com os tokens da SPEC-UI (`class-variance-authority`, `clsx`, `tailwind-merge`) |
| Componentes | **SpartanNG** (`brain` e `helm`, sobre `@angular/cdk`) |
| Ícones | **ng-icons** com **Lucide** |
| i18n | **Transloco** (`public/i18n/pt-PT.json`) |
| Tipografia | **DM Sans** e **IBM Plex Mono** (`@fontsource`) |
| Markdown das respostas | **marked** + **DOMPurify** (HTML sempre sanitizado) |
| Código nas citações | **Shiki** (realce de sintaxe) |
| Streaming | **SSE** com `fetch` e `ReadableStream` |
| Build e testes | **Angular CLI / `@angular/build`**, **Vitest** + **jsdom**, **Prettier**, npm |

## 7. Qualidade e avaliação

- **pytest** com *fixtures* de aplicações pequenas em várias linguagens (o conjunto de teste do indexador, sem código real do cliente).
- **Avaliação de qualidade (TRV-08)**: `stc avaliar` corre um conjunto de perguntas de referência em `avaliacao/perguntas.yaml` (pergunta, aplicações esperadas, evidências esperadas) e mede:
  - *recall* da pesquisa (a evidência esperada aparece no top-k);
  - correção da resposta, com critério objetivo sempre que possível;
  - taxa de respostas sem evidência.
- Os resultados de cada execução ficam guardados para comparar alterações a chunking, embeddings, reranking ou prompts.
- **Frontend**: Vitest para componentes; fluxo do chat coberto por teste de ponta a ponta com a API simulada.
- **CI**: ruff, mypy, pytest e Vitest em cada *push*.

## 8. Infraestrutura e deploy

| Área | Tecnologia |
|---|---|
| Contentores | **Docker** + **Docker Compose**. Serviços: `db`, `migrar`, `api`, `worker`, `mcp`, `web` |
| Imagem do backend | `python:3.12-slim` com `git` e `ripgrep`, utilizador sem privilégios, *healthcheck* em `/api/saude` |
| Imagem do frontend | Build em Node LTS, servido por **nginx** sem privilégios (sistema de ficheiros só de leitura) |
| MCP | Transporte **stdio** para uso local (Claude Code, Claude Desktop, Cursor) e **HTTP** com token para uso partilhado |
| Rede | API, workers e base de dados só na rede interna; `web` e `mcp` (HTTP) expostos atrás do proxy |
| Segredos | `.env` por `env_file` em tempo de execução; nunca no repositório nem nas imagens |
| Código | Git, GitHub (`jnascimentogcc/stc`), ramo `develop` |

O proxy reverso e o alojamento ficam por decidir (§9).

## 9. Variáveis de ambiente principais

| Variável | Para quê |
|---|---|
| `STC_RAIZ` | Pasta com as aplicações (montada só de leitura) |
| `STC_CATALOGOS`, `STC_DADOS` | Pastas dos catálogos e dos dados de trabalho |
| `STC_DB_URL`, `STC_DB_URL_MIGRACAO` | Ligação da API/workers e das migrações |
| `POSTGRES_PASSWORD`, `STC_APP_PASSWORD` | Palavras-passe da base de dados |
| `ANTHROPIC_API_KEY`, `STC_LLM_MODELO_CATALOGO`, `STC_LLM_MODELO_CHAT` | LLM |
| `VOYAGE_API_KEY`, `STC_EMBEDDINGS_MODELO`, `STC_RERANK_MODELO` | Embeddings e reranking |
| `STC_CONCORRENCIA`, `STC_LLM_PEDIDOS_MIN` | Paralelismo e limite de débito |
| `STC_MCP_TOKEN` | Autenticação do MCP em HTTP |
| `STC_AMBIENTE`, `STC_URL_PUBLICA` | Modo de execução e endereço público |

Modelo completo e comentado em `.env.example`.

## 10. Segurança (resumo)

Raiz montada só de leitura; segredos excluídos antes de qualquer indexação ou chamada externa e nunca devolvidos em respostas; HTML das respostas sanitizado; MCP em HTTP com token; contentores sem privilégios; papel da base de dados sem poder de dono; logs sem código nem segredos. Autenticação de utilizadores e controlo de acessos por aplicação estão por definir (§11).

## 11. Decisões e pendências

- **Sem fila de tarefas externa**: workers sobre PostgreSQL chegam para a escala prevista; se o volume crescer, avaliar um broker.
- **Sem base de dados de grafos**: relações em tabelas com CTEs recursivas. Reavaliar se as consultas de impacto ficarem lentas.
- **Autenticação do chat**: por definir. Hipótese de arranque: instalação de utilizador único atrás do proxy; depois sessão por cookie, como nos outros projetos.
- **Controlo de acessos por aplicação**: fora de âmbito na v0.1 (spec funcional, §6).
- **Fornecedor de embeddings e de LLM**: escolhidos por qualidade em código; decidir se aplicações sensíveis exigem modelos locais ou fornecedor na UE.
- **Análise estática mais profunda** (SCIP/LSIF, grafo de chamadas): candidata a fase 2 para aumentar a precisão das relações.
- **Alojamento e proxy**: por decidir (na máquina local, servidor próprio ou cloud).
- **Capacidade**: a indexação completa tem picos de CPU e de chamadas ao LLM; dimensionar após o piloto com 3–5 aplicações.
