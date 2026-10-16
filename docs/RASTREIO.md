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
