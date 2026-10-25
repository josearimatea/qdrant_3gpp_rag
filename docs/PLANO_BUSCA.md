# Plano — melhorar a busca no corpus 3GPP (metadado, busca híbrida, modelo novo)

Escrito em 15/out/2026. O que já foi feito está em `docs/RASTREIO.md`; **registre lá
cada passo concluído** (data, o que mudou, números antes/depois, commit).

## 0. Contexto

O `qdrant_3gpp_rag` é o pacote de busca compartilhado por quatro repositórios irmãos
(todos em `workspace/`):

| repositório | como usa |
|---|---|
| `openapi_rulesbank` | `retrieve`/`index_segments` (busca na spec, no corpus `3gpp_rel18_all`/`3gpp_rel18_28`, e constrói a `openapi_reference`) |
| `openapi_generator` | corpus `3gpp_rel18_28`, com **factory próprio** (`rag/qdrant_factory.py`) |
| `openapi_analysis` | o juiz (camada 3): `retrieve` em `3gpp_rel18_28_28532` e `openapi_reference` |
| `openapi_chatbotUI` | `infrastructure/qdrant.py` |

Coleções no Qdrant (`localhost:6333`, container `qdrant-local`):

| coleção | conteúdo | estado em 15/out |
|---|---|---|
| `3gpp_rel18_28_28532` | só a TS 28.532 (i00) | **reindexada por seção** — 605 pedaços |
| `3gpp_rel18_28` | Rel-18, série 28 (38 specs) | **reindexada por seção** — 6.096 pedaços |
| `3gpp_rel18_all` | Rel-18, todas as séries (920 specs) | **VAZIA/INCOMPLETA** — a reindexação foi interrompida de propósito, para ser feita uma vez só, com tudo deste plano |
| `openapi_reference` | referência OpenAPI 3.0.1 | antiga (MiniLM); construída pelo rulesbank |

Fonte do corpus: `workspace/../Dataset/TSpec-LLM/3GPP-clean` (`config.paths.DOCS_DIR`).
**Indexar só o que está nessa pasta** (a TS 28.532 lá é a i00).

## 1. Regras que valem para tudo

- ~~**Nenhum anexo**, nunca~~ — **revogado em 25/out/2026** (ver `docs/RASTREIO.md`): os
  anexos guardam definições que outras specs referenciam (o `NotificationHeader` só existe
  no Annex C da TS 28.623). Este pacote indexa **tudo**, cada pedaço marcado com `annex`;
  que anexos uma tarefa pode ler (ex.: não ler o OpenAPI pronto da spec que se está
  gerando ou julgando) é decisão de cada repositório consumidor.
- Código, comentários e prompts em inglês; documentos em português.
- Commits com `[TAG]` no assunto (`[FEAT]`, `[FIX]`, `[TEST]`, `[DOCS]`), **sem**
  linha `Co-Authored-By`. Push só quando o usuário pedir.
- O `uv.lock` deste repo já estava modificado antes deste trabalho: **não commitar**
  essa alteração por engano. Rodar com `uv run --frozen` para o uv não reescrevê-lo
  (se uma dependência nova for adicionada de propósito, aí sim o lock muda — avise).
- Não use `git stash` para comparar coisas (já deu problema com o `uv.lock`).
- Para parar um processo em segundo plano, não use `pkill -f` com um padrão que case
  com o próprio comando do shell.
- Enquanto uma coleção estiver sendo reindexada, os irmãos que a usam não devem rodar.

## 2. O que fazer

### 2.1 Metadado mais rico (no chunker `spec_sections.py`)

Cada pedaço já tem `release, series, spec, clause, title, path, annex, part, parts,
chunking`. Acrescentar:

- `clause_ancestors`: lista com a cláusula e todos os prefixos
  (`["12", "12.3", "12.3.1", …, "12.3.1.2.3.2.1.3.1"]`). Um filtro exato
  (`MatchValue`) em `"12.3"` pega a cláusula e tudo abaixo dela — é o filtro de escopo
  por serviço que hoje o `openapi_analysis` faz depois da busca.
- `defines`: o que a seção define, lido do título no padrão 3GPP. Casos reais vistos:
  `Type NotifyThresholdCrossing`, `Type: ReplaceHttpUrl` (TS 29.571),
  `Enumeration PerfNotificationTypes`, `Notification notifyThresholdCrossing`,
  `Operation "establishStreamingConnection"`, `Resource "/notificationSink"` (com
  aspas escapadas `\"` no markdown). Guardar o nome limpo (sem aspas/barras).
- `mentions`: os nomes definidos **na mesma spec** (o conjunto dos `defines` dela)
  que a seção cita no texto — o "grafo leve": permite "traga a definição de cada tipo
  que esta seção usa" com um filtro `defines = X`. Calcular depois de cortar a spec
  inteira (precisa do conjunto de definições). Limitar o tamanho da lista.
- Criar **índices de payload** (keyword) no Qdrant para `metadata.spec`,
  `metadata.clause`, `metadata.clause_ancestors`, `metadata.defines`,
  `metadata.mentions` (o payload fica em `metadata.*` — ver `_payload_key` no
  `retriever.py`). Fazer isso na criação da coleção (`indexer.index_segments`).
- Acrescentar `clause`, `clause_ancestors`, `defines` ao esquema do self-query
  (`self_query.DEFAULT_METADATA_FIELD_INFO`), com descrições boas, para os agentes
  poderem filtrar por eles.

Testes: `defines` dos títulos acima; `clause_ancestors` de uma cláusula com sufixo de
letra (`12.1.1.4.1a.7`); `mentions` só com nomes definidos na mesma spec.

### 2.2 Busca híbrida (denso + esparso BM25)

Hoje só existe o vetor denso `text-dense`; o nome `text-sparse` é reservado no
`factory.py`, mas nada gera o vetor esparso (`fastembed` nem está instalado).

- Adicionar a dependência `fastembed` (modelo esparso `Qdrant/bm25`) — isto muda o
  `uv.lock` de propósito.
- `indexer.index_segments`: criar a coleção com o vetor denso **e** o esparso
  `text-sparse` (com modificador IDF) e gravar os dois.
- `factory.get_vector_store` / `retriever.retrieve`: **detectar** se a coleção tem o
  vetor esparso (pela configuração da coleção) e, se tiver, buscar em modo híbrido
  (`langchain_qdrant.RetrievalMode.HYBRID`, fusão RRF); se não tiver, continuar como
  hoje. Ninguém dos irmãos deve precisar passar `sparse_vector_name`.
- Motivo, medido no juiz: o vetor denso é ruim com identificadores exatos
  (`NotifyEvent`, `notifyThresholdCrossing`, `4xx/5xx`); o BM25 acerta esses termos.

### 2.3 Modelo de embedding novo: `BAAI/bge-base-en-v1.5`

- 768 dimensões, lê 512 tokens (o `all-MiniLM-L6-v2` atual lê ~256 e ignora o resto
  do pedaço), forte em texto técnico em inglês. Usar o prefixo de consulta recomendado
  pelo modelo para as **perguntas** (não para os pedaços):
  `"Represent this sentence for searching relevant passages: "`
  (no `HuggingFaceEmbeddings`, via `query_encode_kwargs={"prompt": ...}`).
- Ponto único da verdade: `qdrant_3gpp_rag` exporta o modelo e a dimensão
  (`DEFAULT_EMBEDDING_MODEL`, `DEFAULT_EMBEDDING_DIM`, e a função que monta os
  embeddings com o prefixo). **Os irmãos passam a importar daqui** em vez de fixar o
  seu — hoje o MiniLM está fixo em:
  - `qdrant_3gpp_rag/src/qdrant_3gpp_rag/factory.py` e `indexer.py`
  - `openapi_rulesbank/src/openapi_rulesbank/config/settings.py` (`EMBEDDING_MODEL`,
    `EMBEDDING_DIM`) e `tools/rag_tools.py`
  - `openapi_generator/src/openapi_generator/config/settings.py` e o factory próprio
    `rag/qdrant_factory.py` (cria coleções temporárias por execução)
  - `openapi_analysis/src/openapi_analysis/config/judge_settings.py`
  - `openapi_chatbotUI/src/app/infrastructure/qdrant.py`
- Se um irmão ficar com o modelo antigo, a busca dele quebra (384 ≠ 768 dimensões).
  Depois da troca, procure de novo por `MiniLM` e `384` nos cinco repos.
- As coleções **temporárias** que o rulesbank e o generator criam por execução usam o
  modelo do próprio repo — passam a usar o daqui automaticamente com a importação.

### 2.4 Reindexar (por último, uma vez só, em segundo plano, GPU)

Com 2.1–2.3 prontos e testados:

```
cd qdrant_3gpp_rag
uv run --frozen python scripts/index_3gpp_all.py --docs-dir ../../Dataset/TSpec-LLM/3GPP-clean \
  --collection 3gpp_rel18_28_28532 --release Rel-18 --series 28_series --spec 28532-i00 \
  --device cuda --output-dir output --force-chunks
# depois 3gpp_rel18_28 (--series 28_series, sem --spec) e por fim
# 3gpp_rel18_all (só --release Rel-18) — esta é pesada (920 specs)
```

- Comece pela `3gpp_rel18_28_28532` (rápida) e valide (2.5) antes das outras.
- A `openapi_reference` também precisa ser refeita com o modelo novo: ela é construída
  pelo rulesbank (`openapi_rulesbank/src/openapi_rulesbank/tools/rag_tools.py`,
  `index_openapi_reference(force)`, coleção `OPENAPI_COLLECTION_NAME`).
- Registrar no rastreio: número de pedaços de cada coleção, tempo, modelo.

### 2.5 Como validar

Casos reais que falhavam (TS 28.532, coleção `3gpp_rel18_28_28532`):

| pergunta | deve trazer |
|---|---|
| `HTTP POST response status codes 204 4XX 5XX` com `clause_ancestors = 12.3` | a seção `12.3.1.2.3.2.1.3.1 POST` inteira, com a linha `Error-Response 4xx/5xx` |
| `NotifyThresholdCrossing` | `12.3.1.2.4.2.1 Type NotifyThresholdCrossing`, com `hysteresis` e `monitorGranularityPeriod` (M) |
| `NotifyEvent` (escopo `12.1`) | a definição do tipo (ou, com `defines`, o filtro direto) |
| `HTTP GET` com escopo `12.1` | `12.1.1.3.2.1.3.2 HTTP GET` com a sua tabela de respostas (`4xx/5xx`) |

Depois, no `openapi_analysis`: rodar `uv run openapi-analysis all` e comparar o
relatório novo do PerfMnS com o anterior (`data/outputs/reports/PerfMnS/generator/`) —
os 5 valores "uncertain" do PerfMnS vinham de busca falha (tabela de respostas e
tabela do NotifyThresholdCrossing que não chegavam ao juiz).

### 2.6 Depois, no `openapi_analysis` (outro repo — combinar com o usuário)

A busca do juiz (`src/openapi_analysis/judge/retrieval.py`, `spec_index.py`) reconstrói
anexo e cláusula a partir dos pedaços antigos (`annex_cut`, `chunk_clauses`,
`split_by_heading`). Com a cláusula no metadado e sem anexo no índice, isso pode sair:
filtrar por `clause_ancestors` no próprio Qdrant e usar `defines` para buscar a
definição do assunto da parte. Também remover `scripts/index_specs.py` (criava a
coleção própria `analysis_spec_*`, já apagada).

## 3. Fora deste plano (decidido)

- **Knowledge graph completo** (Neo4j etc.): não por ora. `defines`/`mentions` dão os
  "saltos" de um grafo por filtro, sem banco novo; só reavaliar se não bastar.
- **Reranking** (cross-encoder sobre os 50 primeiros): opcional, depois — não exige
  reindexar.
- Cláusulas "Void" e documentos OpenAPI fora de anexo (ex.: TS 28.312, cláusula 7.2.3):
  decidido deixar como estão.
