# Rastreio — o corpus 3GPP e a busca

Caderno de bordo do `qdrant_3gpp_rag`: o que mudou, por quê, e o que se mediu.
**Acrescente no fim; não reescreva entradas antigas.** O que falta fazer está em
`docs/PLANO_BUSCA.md`.

---

## 15/out/2026 — chunking por seção, sem anexo

**O problema, achado pelo juiz do `openapi_analysis`.** O juiz julgava diferenças do
YAML gerado lendo trechos da spec recuperados do corpus, e decidia "incerto" em casos
que a spec responde. No PerfMnS: a tabela de respostas do `POST /notificationSink`
(`Error-Response 4xx/5xx — M`) e a tabela do `Type NotifyThresholdCrossing` (com
`hysteresis` e `monitorGranularityPeriod`, ambos M) nunca chegavam inteiras.

**A causa: o corpus não estava cortado por seção.** O código convertia o markdown em
HTML e de volta em texto (`_flatten_markdown`), o que apagava os `#` dos títulos; o
divisor por título (só níveis 1–3) ficava sem onde cortar, e o divisor por tamanho
fazia janelas de 2000 caracteres. Medido nos caches: 81% (`3gpp_rel18_28`) e 94%
(`3gpp_rel18_all`) dos pedaços tinham ~2000 caracteres; na TS 28.532, só 103 de 647
começavam num título. Um pedaço terminava uma cláusula e começava a seguinte (o pedaço
176 trazia o fim do HTTP GET e o começo do HTTP PATCH), tabelas eram partidas ao meio,
tabelas em grade viravam texto picado. As três coleções incluíam os anexos. A
`3gpp_rel18_28_28532` antiga tinha sido montada por outro caminho (marcadores
`[Extracted Table: N]`, `chunk_index`), não por este código.

**A correção** (commits `415913b`, `8c6966a`):

- `spec_sections.chunk_spec_sections`: um pedaço por cláusula, cortando em todos os
  níveis de título (ATX até nove `#`, e setext); um título sem texto próprio não é
  pedaço, mas entra no caminho dos filhos — cada pedaço começa com os dois títulos-pai
  mais próximos (`Resource "/notificationSink" › HTTP methods`); só uma seção acima de
  4000 caracteres é dividida (por tipo YAML, depois por parágrafo), cada parte
  repetindo o título como "(part i/n)". Metadado: `clause, title, path, annex, part,
  parts, chunking="sections-v1"`.
- Fora do índice: o sumário ("Contents"), a capa e **todos os anexos** (a partir do
  primeiro "Annex X"). `include_annexes=True` existe para quem precisar.
- `index_3gpp_all`: o markdown fica markdown (só caem os atributos `{#id .H6}`); filtro
  `--spec`. `index_segments(..., chunk_size=None)` indexa os segmentos como vierem (o
  padrão continua recortando — a `openapi_reference` do rulesbank não muda).
- Impacto: o commit anterior `e5a3359` tinha tornado buscáveis os tipos YAML dos
  anexos; sem anexos, eles saem do corpus (coerente com a regra de nunca usar anexo).

**Medido.** Seções reais da TS 28.532: mediana 256 caracteres, 226 títulos sem texto
próprio, só 59 de 696 acima de 4000. Em três specs de séries diferentes: 23501-i30 →
1119 pedaços, 29571-i30 → 495, 28532-i00 → 605; 100% com cláusula nas specs (um
documento de análise de teste da série 38, sem títulos numerados, fica com título e
caminho, sem cláusula). Série 28 inteira: de 6.249 pedaços / 10,4 M caracteres (com
anexos) para 6.096 / 7,8 M (sem anexos, −25%); mediana de 1.967 para 697 caracteres —
o número de pedaços quase não muda porque as seções são, em geral, menores que 2000;
4.814 são seções inteiras e 1.282 são partes de 247 seções longas (a maior: "Attribute
properties" da TS 28.541, 149 partes). 45 testes.

**Reindexado** (fonte `Dataset/TSpec-LLM/3GPP-clean`, GPU): `3gpp_rel18_28_28532` →
605 pedaços; `3gpp_rel18_28` → 6.096. A `3gpp_rel18_all` foi iniciada e **interrompida
de propósito**: será reindexada uma vez só, com o metadado, a busca híbrida e o modelo
novo (`docs/PLANO_BUSCA.md`). **Até lá ela está incompleta** — rulesbank e generator
não devem rodar contra ela.

**Limpeza do Qdrant.** Apagadas as coleções temporárias deixadas por execuções dos
irmãos (`spec_tmp_98fe32cbe32d`, `spec_tmp_df3c6374b9e2`, `rules_tmp_66934d24cdce`) e
a `analysis_spec_28532_i20` (coleção própria do `openapi_analysis`, já sem uso).
Ficaram: `3gpp_rel18_28_28532`, `3gpp_rel18_28`, `3gpp_rel18_all`, `openapi_reference`.

**Decidido deixar como está:** cláusulas "Void"; documentos OpenAPI fora de anexo
(TS 28.312, cláusula 7.2.3, 51 partes). A série 38 vem do dataset dividida em vários
arquivos por spec (`38101-1-i30_s00-05.md` …), cada um com o seu `spec` no metadado.

---

## 15/out/2026 — metadado por seção: `clause_ancestors`, `defines`, `mentions` (plano 2.1)

**O que mudou.** Cada pedaço (`chunking="sections-v2"`) ganhou três campos, todos
calculados por regra, sem lista por spec — valem para qualquer release:

- `clause_ancestors`: a cláusula e todos os prefixos (`12.1.1.4.1a.7` → `12`, `12.1`,
  …, `12.1.1.4.1a`, `12.1.1.4.1a.7`). Filtro exato em `12.3` = a cláusula e tudo abaixo.
- `defines`: o nome definido, lido do título no padrão 3GPP. Três formas: com
  dois-pontos (`Type: ReplaceHttpUrl`, `Resource: Individual SM Context (Document)` →
  sem o parêntese final), entre aspas (`Resource \"/notificationSink\"` →
  `/notificationSink`) e sem nada (`Type NotifyThresholdCrossing`) — nesta última, só
  vale nome com **forma de código** (minúscula seguida de maiúscula, ou dígito, `_`,
  `-`, `/`). Assim "Resource allocation", "Operation Points", "Notification PUSH"
  não viram definição. Custo aceito: `Type Scope`, `Operation subscribe` (palavras
  comuns, repetidas em várias cláusulas) ficam sem `defines`.
- `mentions`: os nomes definidos na mesma spec que o corpo da seção cita (não o
  prefixo com os títulos-pai), na ordem em que aparecem, sem os da própria seção, no
  máximo 30. Só nomes com forma de código são procurados no texto.

Índices de payload (keyword) em `metadata.spec`, `clause`, `clause_ancestors`,
`defines`, `mentions`, criados com a coleção (`index_segments`). Self-query: campos
`clause`, `clause_ancestors`, `defines` com descrição.

**Medido.** Rel-18 inteiro (920 specs, sem anexos): a primeira versão da regra dava
4.185 `defines`, com ~60–70 falsos (palavra comum depois de Notification/Operation/
Resource, quase todos fora das séries 28/29); com a regra da forma de código, 4.087
(3.352 com dois-pontos, 706 sem nada, 29 entre aspas), nenhum sem forma de código.
TS 28.532: 605 pedaços (igual), 154 com `defines`, 145 com `mentions`, 1 no limite de
30. Ex.: o `POST` de `/notificationSink` (12.3.1.2.3.2.1.3.1) tem `mentions =
[NotifyThresholdCrossing, notifyThresholdCrossing]` — o salto para a tabela do tipo.

**Testes:** 45 → 68. Um deles roda a regra sobre as séries 28 e 29 do corpus (se
presente) e falha se algum `defines` sem dois-pontos/aspas não tiver forma de código —
é o que se roda numa release nova (~50 s).

As coleções ainda não têm esses campos: entram na reindexação única (plano 2.4).

Commit: `a6dad60`.

**Conferência (18/out).** Cada `defines` do Rel-18 conferido contra o corpo da
própria seção: 3.130 de 4.087 repetem o nome lá (2.254 na tabela "Definition of type
X"). Os outros são definições certas que não repetem o nome (títulos-pai de recurso/
operação, com corpo vazio — nem viram pedaço; tipos `…Rm`, que citam o tipo base;
operações como `createMOI`). Único erro achado: `Type: Void` → `Void` (cláusula
removida); a regra agora rejeita "Void".

---

## 18/out/2026 — busca híbrida: denso + BM25 (plano 2.2)

**O que mudou.**
- Dependência nova `fastembed` (modelo esparso `Qdrant/bm25`). Saíram do
  `pyproject.toml` `beautifulsoup4` e `markdown`, sem uso desde que o markdown deixou
  de passar por HTML (`8c6966a`); a alteração antiga do `uv.lock` era só a
  sincronização desses dois e deixou de existir. O lock commitado difere do anterior
  só pelo `fastembed` e suas dependências (onnxruntime, py-rust-stemmers, mmh3 …).
- `index_segments(..., hybrid=True)`: a coleção é criada com o vetor denso
  `text-dense` **e** o esparso `text-sparse` (IDF aplicado pelo Qdrant), e os dois são
  gravados. `index_document` (coleções temporárias) continua só densa.
- `factory.get_vector_store` / `retrieve`: o padrão agora é `AUTO` — lê a
  configuração da coleção e, se ela tem `text-sparse`, busca em modo híbrido (as duas
  listas juntadas pela posição, RRF); senão, só denso, como antes. Os irmãos não
  precisam passar nada. `None` força só denso. O BM25 só é carregado quando uma
  coleção híbrida é usada.

**Por quê.** O denso busca por significado e confunde nomes parecidos (GET/PUT,
`NotifyThresholdCrossing`/`PerfNotificationTypes`); o BM25 conta as palavras exatas,
com mais peso para as raras (`hysteresis`, `monitorGranularityPeriod`). O híbrido junta
as duas listas.

**Medido** (TS 28.532, coleção temporária, modelo ainda MiniLM, mesma coleção
consultada nos dois modos; posição do pedaço esperado entre os 10 primeiros):

| pergunta | filtro | só denso | híbrido |
|---|---|---|---|
| `HTTP POST response status codes 204 4XX 5XX` | `clause_ancestors=12.3` | 1 | 1 |
| `NotifyThresholdCrossing` | — | 4 | 3 |
| `HTTP GET` | `clause_ancestors=12.1` | 2 (HTTP PUT em 1º) | 1 |
| `notifyThresholdCrossing hysteresis monitorGranularityPeriod` | — | 10 | 2 |

`NotifyEvent` com escopo `12.1`: nenhum dos dois acha a definição — não há tipo
`NotifyEvent` definido em 12.1 na 28.532 (a notificação `notifyEvent` é a 11.1.1.10);
reavaliar com o modelo novo. O "grafo leve" funciona: os `mentions` do POST
(`NotifyThresholdCrossing`, `notifyThresholdCrossing`) levam, só por filtro
`defines`, a `12.3.1.2.4.2.1 Type NotifyThresholdCrossing` e a
`12.3.1.2.2.2 Notification notifyThresholdCrossing`. Indexar os 605 pedaços com os
dois vetores levou 4 s (GPU).

**Testes:** 68 → 73 (factory: coleção só densa → denso; com `text-sparse` → híbrido;
`None` não carrega o BM25. indexer: coleção criada com o esparso IDF; `hybrid=False`).

Commit: `db46962`.

---

## 18/out/2026 — modelo novo `BAAI/bge-base-en-v1.5`, neste pacote (plano 2.3, 1ª parte)

**O que mudou.** O `factory.py` é a fonte única do modelo para os cinco repositórios:
`DEFAULT_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"` (substitui o
`all-MiniLM-L6-v2`), `DEFAULT_EMBEDDING_DIM = 768` (era 384, no `indexer.py`) e
`build_embeddings()`, que monta o modelo com vetores normalizados e o prefixo
"Represent this sentence for searching relevant passages: " **só nas perguntas** (não
nos pedaços). `DEFAULT_DEVICE` passa a ser a GPU quando existe, senão a CPU (era CPU
fixo); vale também para `index_3gpp_all` e o `--device` do script. Exportados no
`__init__` para os irmãos importarem.

**Medido** (TS 28.532, coleção temporária híbrida; posição do pedaço esperado):

| pergunta | MiniLM só denso | MiniLM híbrido | bge só denso | bge híbrido |
|---|---|---|---|---|
| POST 204 4XX 5XX (escopo 12.3) | 1 | 1 | 1 | 1 |
| `NotifyThresholdCrossing` | 4 | 3 | 4 | 3 |
| HTTP GET (escopo 12.1) | 2 | 1 | 1 | 1 |
| `notifyThresholdCrossing hysteresis monitorGranularityPeriod` | 10 | 2 | 3 | 2 |

Vetor de 768 dimensões; a mesma frase como pergunta e como pedaço dá vetores
diferentes (cosseno 0,925), sinal de que o prefixo entra só na pergunta. `NotifyEvent`
(escopo 12.1) continua sem resultado: não há esse tipo definido em 12.1 na 28.532.

**Testes:** 73 → 76 (prefixo só na pergunta; modelo sem prefixo conhecido; GPU com
fallback para CPU). Falta: os irmãos importarem daqui; reindexar (as coleções atuais
são de 384 dimensões e passam a dar erro de dimensão com o modelo novo até lá).

Commit: `00c4951`.

**Import leve (18/out).** Importar o pacote leva ~6 s (carrega o torch, via
`langchain_huggingface`) — sempre foi assim, uma vez por execução, não por busca. Para os
irmãos lerem o nome do modelo nas configurações sem pagar isso (e sem quebrar o
`openapi_analysis`, onde o pacote é opcional), o modelo, a dimensão e o prefixo foram
para `qdrant_3gpp_rag/embedding_config.py`, que não importa nada pesado, e o `__init__`
passou a carregar cada nome só no primeiro uso. `from qdrant_3gpp_rag.embedding_config
import DEFAULT_EMBEDDING_MODEL`: 0,01 s, sem torch. Os nomes exportados não mudaram.

Commit: `f01722a`.

---

## 18/out/2026 — os irmãos passam a usar tudo daqui (plano 2.3, 2ª parte)

**Regra.** Modelo, cliente do Qdrant, vector store e busca vêm só deste pacote; nenhum
irmão fixa modelo nem monta o seu factory. As configurações deles leem o modelo de
`qdrant_3gpp_rag.embedding_config` (leve, sem torch).

| repositório | o que mudou | commit |
|---|---|---|
| `openapi_rulesbank` | `EMBEDDING_MODEL`/`EMBEDDING_DIM` daqui; a `openapi_reference` largou cliente, modelo e vector store próprios: usa o `QdrantFactory` e é construída por `index_segments` (híbrida). `uv.lock` segue as dependências deste pacote | `6ac2a99` (branch `feature/ref`) |
| `openapi_analysis` | `judge_settings`: modelo e dimensão daqui, protegidos por `try` como o `dotenv` (o pacote só vem com o extra `[judge]`); `EMBEDDING_DEVICE = "cpu"` mantido. 139 testes | `0625c20` |
| `openapi_generator` | `qdrant-3gpp-rag` como dependência local; `rag/qdrant_factory.py` embrulha o `QdrantFactory`; `get_relevant_chunks` usa o `retrieve` daqui; `tools/rag_tools.py` e `rag/spec_index.py` usam o factory. Corrigido de passagem: o filtro local não usava o prefixo `metadata.` (nunca casaria; nenhum chamador passava filtro) | `7316ef4` |
| `openapi_chatbotUI` | `qdrant-3gpp-rag` como dependência local; `infrastructure/qdrant.py` devolve o `QdrantFactory` daqui, criado uma vez (antes o modelo era recarregado a cada pergunta); `retriever.py` usa o `retrieve` daqui (mesmo defeito do filtro, corrigido). **Removidos**, por defasados: a ingestão (`src/app/ingest`, os `.pkl` de `files/chunks`, `TSPEC_DATA`/`CHUNKS_FILE`), `retriever_hard.py`, `self_query.py` e as dependências só deles (`langchain-classic`, `lark`, `beautifulsoup4`, `markdown`). Coleção de produção: `3gpp` (não existia) → `3gpp_rel18_all` | `044ee72` |

**Neste pacote:** removido `tests/test_rag_3gpp_rel18.ipynb`, defasado (MiniLM fixo,
busca sem vetor nomeado, `text-davinci-003`).

**Conferido.** Busca por `MiniLM` e `384` nos cinco repositórios (código e
configuração): sobram só dois comentários que explicam a troca e um "384" que é número
de linha numa listagem de teste do rulesbank. Provas de ponta a ponta com o modelo novo,
em coleções temporárias apagadas no fim: generator (coleção de spec por execução, 768
dimensões, busca e remoção) e chatbotUI (filtro `clause_ancestors` casando pelo prefixo
`metadata.`, factory único). Tempo de import das configurações: generator 0,6 s,
analysis 0,02 s, chatbotUI 0,02 s, todos sem torch.

**Pendente.** O `qdrant-client` do generator está na 1.18.0 e o servidor na 1.16.3 (o
cliente avisa incompatibilidade); o lock do generator não é versionado, então não dá
para saber se já era assim. Até a reindexação (2.4), as coleções continuam com 384
dimensões e qualquer irmão que buscar nelas recebe erro de dimensão — não rodar os
irmãos até lá.
