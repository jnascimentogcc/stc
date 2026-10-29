# STC — Especificação Funcional (v0.1)

> Estado: rascunho inicial. Âmbito: funcionalidades. Fora deste documento: stack tecnológico e especificação de UI (definidos em specs separadas).

## 1. Objetivo

Permitir compreender e interrogar um conjunto grande (100+) de aplicações **sem documentação**, a partir apenas do seu código fonte. O sistema lê uma pasta que contém as aplicações, constrói conhecimento estruturado sobre elas e responde a perguntas **técnicas** e **funcionais**, sempre com referência à origem da resposta.

## 2. Componentes

| Componente | Responsabilidade |
|---|---|
| **Indexador** | Lê a pasta, deteta as aplicações, extrai conhecimento e mantém-no atualizado. |
| **Motor de consulta** | Núcleo partilhado que pesquisa e devolve conhecimento e evidência. Existe uma única vez. |
| **Chat** | Conversação em linguagem natural sobre o conhecimento, sobre o motor de consulta. |
| **MCP** | Expõe as capacidades do motor de consulta a clientes MCP externos. |

Chat e MCP são clientes do mesmo motor; não duplicam lógica de pesquisa.

## 3. Conceitos

- **Raiz**: pasta indicada pelo utilizador que contém as aplicações.
- **Aplicação**: unidade lógica independente dentro da raiz (ex.: serviço, módulo, projeto).
- **Catálogo**: documento estruturado gerado por aplicação.
- **Mapa global**: visão transversal derivada de todos os catálogos.
- **Evidência**: referência verificável à origem de uma afirmação (aplicação, ficheiro, intervalo de linhas).

## 4. Requisitos funcionais

### 4.1 Indexador — leitura e deteção

| ID | Requisito |
|---|---|
| IDX-01 | Receber como entrada o caminho de uma raiz e percorrê-la recursivamente. |
| IDX-02 | Detetar automaticamente as aplicações dentro da raiz com base em marcadores de projeto (manifestos, ficheiros de build, solução, etc.), independentemente da linguagem. |
| IDX-03 | Permitir ao utilizador corrigir a deteção: incluir, excluir, juntar ou dividir aplicações, e atribuir-lhes um nome. |
| IDX-04 | Suportar aplicações em múltiplas linguagens e tecnologias; ficheiros de linguagens não suportadas são tratados como texto e sinalizados. |
| IDX-05 | Ignorar por omissão dependências de terceiros, artefactos de build, binários, ficheiros gerados e lockfiles, com lista de exclusões configurável. |
| IDX-06 | Detetar e excluir segredos (credenciais, chaves, tokens, ficheiros de ambiente) antes de qualquer processamento; reportar o que foi excluído sem expor o valor. |
| IDX-07 | Registar por ficheiro: aplicação, caminho, linguagem, tamanho, estado e identificador de conteúdo. |
| IDX-08 | Decompor o código em unidades com significado (funções, classes, módulos) e não em blocos de tamanho fixo, preservando localização (ficheiro e linhas). |
| IDX-09 | Ficheiros de configuração, esquemas de base de dados, definições de API, scripts e documentação existente (README, comentários) são também fonte de conhecimento. |

### 4.2 Indexador — conhecimento extraído

| ID | Requisito |
|---|---|
| CAT-01 | Gerar um catálogo por aplicação com, no mínimo: propósito funcional, stack e versões, estrutura de módulos, entidades e modelo de dados, endpoints/interfaces expostas, interfaces consumidas, integrações externas, configuração e variáveis de ambiente, fluxos principais, e pontos de entrada. |
| CAT-02 | Cada afirmação do catálogo deve ter evidência associada. |
| CAT-03 | Marcar no catálogo o grau de confiança e as lacunas (o que não foi possível determinar), em vez de inferir sem base. |
| CAT-04 | Os catálogos são guardados em formato legível e editável por humanos, e versionáveis. |
| CAT-05 | Correções manuais num catálogo são preservadas em regenerações futuras e prevalecem sobre o conteúdo gerado. |
| CAT-06 | Gerar o mapa global: lista de aplicações, relações entre aplicações (quem chama/depende de quem), bases de dados e serviços partilhados, tecnologias comuns e aplicações sem relações. |
| CAT-07 | Detetar relações entre aplicações a partir de evidência (chamadas a endpoints, mensageria, bases de dados partilhadas, bibliotecas internas comuns, configuração). |
| CAT-08 | Permitir uma descrição funcional/de negócio por aplicação, em linguagem não técnica. |

### 4.3 Indexador — ciclo de vida

| ID | Requisito |
|---|---|
| LIF-01 | Indexação completa inicial e indexação **incremental**: reprocessar apenas o que mudou. |
| LIF-02 | Detetar alterações por comparação de conteúdo e, quando disponível, por histórico de versões. |
| LIF-03 | Quando uma aplicação muda, atualizar o catálogo dessa aplicação e as partes afetadas do mapa global. |
| LIF-04 | Ficheiros removidos ou movidos são removidos/atualizados no conhecimento. |
| LIF-05 | Permitir reindexar uma aplicação específica ou toda a raiz a pedido. |
| LIF-06 | Reportar progresso, erros por ficheiro/aplicação e resumo final; uma falha numa aplicação não interrompe as restantes. |
| LIF-07 | Estimar e reportar volume e custo de processamento antes de uma indexação completa. |
| LIF-08 | Registar a que versão do código corresponde cada conhecimento, para detetar respostas desatualizadas. |

### 4.4 Motor de consulta

| ID | Requisito |
|---|---|
| QRY-01 | Listar aplicações com filtros (tecnologia, nome, relação com outra aplicação). |
| QRY-02 | Devolver o catálogo de uma aplicação, total ou por secção. |
| QRY-03 | Devolver o mapa global e as dependências diretas e transitivas de uma aplicação (a montante e a jusante). |
| QRY-04 | Pesquisar código por significado e por termo exato, combinados, com possibilidade de limitar a uma ou mais aplicações, linguagens ou caminhos. |
| QRY-05 | Pesquisar nos catálogos (conhecimento funcional) separadamente do código. |
| QRY-06 | Ler um ficheiro ou intervalo de linhas, respeitando as exclusões de segredos. |
| QRY-07 | Pesquisar por padrão/texto literal no código (equivalente a grep) e por símbolo (definições e utilizações). |
| QRY-08 | Responder a perguntas transversais (ex.: "que aplicações usam X?") usando o mapa global antes de descer ao código. |
| QRY-09 | Todos os resultados incluem evidência (aplicação, ficheiro, linhas) e a versão do código indexado. |
| QRY-10 | Indicar explicitamente quando não há informação suficiente, em vez de produzir resposta sem suporte. |
| QRY-11 | Ordenar resultados por relevância e permitir paginação. |

### 4.5 Chat

| ID | Requisito |
|---|---|
| CHT-01 | Aceitar perguntas em linguagem natural (técnicas, funcionais e de negócio) sobre uma ou várias aplicações. |
| CHT-02 | Decidir sozinho que capacidades do motor usar (catálogo, mapa, pesquisa, leitura) e iterar até ter evidência suficiente. |
| CHT-03 | Cada resposta cita as fontes (aplicação, ficheiro, linhas) que a sustentam. |
| CHT-04 | Distinguir na resposta o que está **comprovado no código** do que é **inferido**, e admitir quando não sabe. |
| CHT-05 | Manter o contexto da conversa (perguntas de seguimento, referências a respostas anteriores). |
| CHT-06 | Permitir restringir uma conversa a um âmbito: uma aplicação, um conjunto de aplicações ou a raiz toda. |
| CHT-07 | Adaptar o nível da resposta ao perfil pedido (técnico ou funcional/negócio). |
| CHT-08 | Guardar, retomar e apagar conversas. |
| CHT-09 | Permitir ao utilizador sinalizar uma resposta como incorreta ou incompleta, com correção opcional. |
| CHT-10 | Uma correção sinalizada pode ser proposta como alteração ao catálogo da aplicação (sujeita a aprovação). |
| CHT-11 | Indicar quando o conhecimento usado pode estar desatualizado face ao código. |

### 4.6 MCP

| ID | Requisito |
|---|---|
| MCP-01 | Expor como ferramentas as capacidades do motor: listar aplicações, obter catálogo, obter mapa global, obter dependências, pesquisar catálogos, pesquisar código, pesquisar por padrão/símbolo, ler ficheiro, estado da indexação. |
| MCP-02 | Expor catálogos e mapa global também como recursos consultáveis. |
| MCP-03 | Cada ferramenta tem descrição e parâmetros claros o suficiente para um modelo escolher e usar corretamente. |
| MCP-04 | As respostas das ferramentas incluem evidência e versão, tal como no motor. |
| MCP-05 | Operações do MCP são apenas de leitura sobre o código; qualquer operação que altere conhecimento (ex.: reindexar, aprovar correção) é separada e explícita. |
| MCP-06 | Limitar o tamanho das respostas e paginar, para não saturar o contexto do cliente. |
| MCP-07 | Devolver erros estruturados e compreensíveis (aplicação inexistente, indexação em curso, sem resultados). |

## 5. Requisitos transversais

| ID | Requisito |
|---|---|
| TRV-01 | **Segurança**: segredos nunca são indexados, enviados a serviços externos, nem devolvidos em respostas. |
| TRV-02 | **Controlo de dados**: o utilizador sabe que partes do código são enviadas a serviços externos e pode definir exclusões por aplicação. |
| TRV-03 | **Configuração**: raiz, exclusões, perfis por aplicação e parâmetros de processamento definidos em configuração explícita. |
| TRV-04 | **Idempotência**: repetir uma indexação sem alterações no código não altera o resultado. |
| TRV-05 | **Rastreabilidade**: registo do que foi indexado, quando, com que versão e com que resultado. |
| TRV-06 | **Escala**: suportar 100+ aplicações com tempos de resposta interativos nas consultas. |
| TRV-07 | **Idioma**: catálogos e respostas em português por omissão, com identificadores e termos técnicos preservados. |
| TRV-08 | **Avaliação**: suporte a um conjunto de perguntas de referência com respostas esperadas, executável após cada alteração relevante, para medir qualidade das respostas e da pesquisa. |

## 6. Fora de âmbito (v0.1)

- Stack tecnológico e escolhas de implementação.
- Especificação de interface de utilizador.
- Alteração de código das aplicações (o sistema é só de leitura).
- Execução das aplicações ou análise dinâmica.
- Controlo de acessos por utilizador/aplicação (a confirmar).

## 7. Critérios de aceitação (iniciais)

1. Dada uma raiz com 100+ aplicações, o indexador deteta as aplicações e gera um catálogo por cada uma, sem intervenção, com lista de lacunas.
2. Uma segunda indexação sem alterações não reprocessa nada; uma alteração a um ficheiro atualiza apenas a aplicação afetada.
3. Uma pergunta transversal ("que aplicações comunicam com a aplicação X?") é respondida a partir do mapa global, com evidência.
4. Uma pergunta de detalhe ("onde é validado o campo Y?") devolve ficheiro e linhas corretos.
5. Uma pergunta sem resposta possível no código resulta numa resposta que o admite, sem invenção.
6. A mesma pergunta feita no chat e através do MCP produz evidência equivalente.
7. Nenhum segredo presente no código aparece em índices, catálogos ou respostas.

## 8. Questões em aberto

1. Como se define "aplicação" em casos limite (monorepos, bibliotecas partilhadas, múltiplos módulos)?
2. Há necessidade de controlo de acessos por aplicação ou por utilizador?
3. Os catálogos corrigidos à mão ficam na raiz, num repositório próprio ou noutro local?
4. Qual a frequência esperada de alteração do código (determina a exigência de atualização incremental)?
5. Quais são as 20–30 perguntas reais que servirão de conjunto de avaliação?
6. O chat deve poder responder só com base em catálogos quando o código não estiver disponível?
