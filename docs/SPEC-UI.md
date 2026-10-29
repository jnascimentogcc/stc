# SPEC de UI Global

## 1. Propósito

Este documento é a fonte normativa para o design system e para os padrões globais de experiência do utilizador. Aplica-se a todas as interfaces do projeto, independentemente do domínio funcional.

As páginas, os fluxos, os dados, as permissões e as regras de negócio de cada funcionalidade devem ser definidos em feature specs separadas. Em caso de conflito, esta SPEC prevalece nas decisões globais de UI; a feature spec prevalece apenas no comportamento específico da funcionalidade.

### Contexto tecnológico

- Frontend: Angular, Tailwind CSS e SpartanNG.
- Usar versões estáveis e suportadas, fixadas na configuração do projeto e não neste documento.
- Reutilizar primeiro os componentes e padrões oferecidos pelo SpartanNG e pela biblioteca partilhada do projeto.
- Usar Tailwind e design tokens para composição visual; evitar valores arbitrários quando existir um token equivalente.
- Manter componentes de domínio separados dos componentes primitivos e reutilizáveis.

## 2. Princípios de experiência

- **Clareza:** cada ecrã deve comunicar propósito, estado e ação principal sem depender de ornamentação.
- **Consistência:** padrões equivalentes devem ter aparência e comportamento equivalentes.
- **Controlo:** ações importantes devem apresentar consequências, progresso e resultado de forma explícita.
- **Rastreabilidade:** estados, timestamps, identificadores e atores devem ser legíveis quando forem relevantes para a tarefa.
- **Acessibilidade:** a interface deve funcionar com teclado, tecnologias assistivas, zoom e preferências de movimento reduzido.
- **Divulgação progressiva:** apresentar primeiro a informação necessária e revelar detalhe adicional sob pedido.
- **Recuperação:** erros devem explicar o problema em linguagem clara e oferecer uma ação segura quando possível.

Evitar estética promocional, gradientes decorativos, ilustrações sem função, cores saturadas sem significado, excesso de sombras e superfícies excessivamente arredondadas.

## 3. Identidade visual

### 3.1 Paleta base

| Nome | Hex | Uso principal |
|---|---:|---|
| Pearl Bush | `#ECE6DA` | Fundo claro e contexto neutro |
| Foggy Gray | `#CACEB5` | Superfícies secundárias e estados subtis |
| Nandor | `#4E615B` | Marca, ação primária e estados positivos |
| Ebony Clay | `#2F3543` | Texto forte e superfícies escuras |
| Ebony Clay Dark | `#1F2532` | Fundo escuro e contraste máximo |

As cores de estado são semânticas. Nunca usar apenas a cor para transmitir significado; combinar cor com texto, ícone ou padrão visual.

### 3.2 Tokens normativos

```css
:root {
  color-scheme: light;

  /* Palette */
  --pearl-bush: #ece6da;
  --foggy-gray: #caceb5;
  --nandor: #4e615b;
  --ebony-clay: #2f3543;
  --ebony-clay-dark: #1f2532;

  /* Surfaces */
  --bg: #ece6da;
  --bg-2: #e4ddcf;
  --surface: #f8f5ee;
  --surface-2: #f1ece2;
  --surface-muted: #caceb5;

  /* Borders */
  --border: rgba(47, 53, 67, 0.14);
  --border-2: rgba(47, 53, 67, 0.24);
  --border-strong: rgba(47, 53, 67, 0.36);

  /* Text */
  --text: #1f2532;
  --text-2: #4e615b;
  --text-3: rgba(31, 37, 50, 0.58);
  --text-inverse: #ece6da;

  /* Brand and actions */
  --accent: #4e615b;
  --accent-hover: #43544f;
  --accent-soft: rgba(78, 97, 91, 0.12);
  --accent-soft-2: rgba(78, 97, 91, 0.18);

  /* Feedback */
  --success: #4e615b;
  --success-bg: rgba(78, 97, 91, 0.12);
  --success-border: rgba(78, 97, 91, 0.28);
  --warning: #b7791f;
  --warning-bg: rgba(183, 121, 31, 0.13);
  --warning-border: rgba(183, 121, 31, 0.30);
  --danger: #b94a3a;
  --danger-bg: rgba(185, 74, 58, 0.12);
  --danger-border: rgba(185, 74, 58, 0.30);
  --info: #3f647c;
  --info-bg: rgba(63, 100, 124, 0.12);
  --info-border: rgba(63, 100, 124, 0.30);

  /* Typography */
  --sans: "DM Sans", "Inter", system-ui, sans-serif;
  --mono: "IBM Plex Mono", "SFMono-Regular", Consolas, monospace;

  /* Spacing */
  --space-1: 4px;
  --space-2: 8px;
  --space-3: 12px;
  --space-4: 16px;
  --space-5: 20px;
  --space-6: 24px;
  --space-8: 32px;
  --space-10: 40px;
  --space-12: 48px;

  /* Shape and depth */
  --radius-sm: 4px;
  --radius: 6px;
  --radius-lg: 10px;
  --radius-xl: 14px;
  --shadow: 0 1px 3px rgba(31, 37, 50, 0.08), 0 1px 2px rgba(31, 37, 50, 0.05);
  --shadow-md: 0 8px 24px rgba(31, 37, 50, 0.12), 0 2px 6px rgba(31, 37, 50, 0.07);

  /* Layout and motion */
  --content-max: 1360px;
  --header-height: 56px;
  --transition: 180ms ease;
}

[data-theme="dark"] {
  color-scheme: dark;
  --bg: #1f2532;
  --bg-2: #252c3a;
  --surface: #2f3543;
  --surface-2: #363d4d;
  --surface-muted: rgba(202, 206, 181, 0.12);
  --border: rgba(236, 230, 218, 0.12);
  --border-2: rgba(236, 230, 218, 0.20);
  --border-strong: rgba(236, 230, 218, 0.34);
  --text: #ece6da;
  --text-2: #caceb5;
  --text-3: rgba(236, 230, 218, 0.58);
  --text-inverse: #1f2532;
  --accent: #caceb5;
  --accent-hover: #d8dbc7;
  --accent-soft: rgba(202, 206, 181, 0.13);
  --accent-soft-2: rgba(202, 206, 181, 0.20);
  --success: #caceb5;
  --success-bg: rgba(202, 206, 181, 0.13);
  --success-border: rgba(202, 206, 181, 0.30);
  --warning: #d49a3a;
  --warning-bg: rgba(212, 154, 58, 0.14);
  --warning-border: rgba(212, 154, 58, 0.32);
  --danger: #e06a5b;
  --danger-bg: rgba(224, 106, 91, 0.14);
  --danger-border: rgba(224, 106, 91, 0.34);
  --info: #8eaec1;
  --info-bg: rgba(142, 174, 193, 0.14);
  --info-border: rgba(142, 174, 193, 0.34);
  --shadow: 0 1px 3px rgba(0, 0, 0, 0.32), 0 1px 2px rgba(0, 0, 0, 0.22);
  --shadow-md: 0 10px 30px rgba(0, 0, 0, 0.42), 0 2px 8px rgba(0, 0, 0, 0.24);
}
```

Implementações podem mapear estes tokens para Tailwind, mas os nomes semânticos e valores devem permanecer como fonte de verdade.

## 4. Tipografia e conteúdo

- Base: `14px`, `line-height` entre `1.5` e `1.6` para texto corrido.
- Interface e conteúdo: `var(--sans)`.
- IDs, timestamps, código, métricas e dados tabulares: `var(--mono)`.
- Usar uma escala tipográfica limitada e hierárquica; títulos não devem depender apenas de tamanho ou cor.
- Labels devem ser curtas, específicas e próximas do controlo correspondente.
- Evitar texto totalmente em maiúsculas, exceto em labels curtas, badges e cabeçalhos compactos.
- Mensagens devem indicar o que aconteceu e, quando aplicável, o que o utilizador pode fazer a seguir.

## 5. Base global e layout

- Aplicar `box-sizing: border-box` globalmente e remover margens padrão do `body`.
- O `body` usa fundo `var(--bg)`, texto `var(--text)`, `min-height: 100vh` e antialiasing.
- O conteúdo principal tem largura máxima `var(--content-max)`, margem automática e padding horizontal de 16px; a partir de 640px, usar 24px.
- Usar grid ou flexbox de forma previsível, mantendo a ordem visual coerente com a ordem do DOM.
- O cabeçalho de página inclui título, descrição opcional e ações contextuais; em ecrãs estreitos, as ações podem empilhar sem perder hierarquia.
- Navegação persistente deve indicar claramente o item ativo e permanecer acessível por teclado.
- Scrollbars podem ser discretas, mas não devem ficar invisíveis quando forem necessárias para compreender overflow.
- Respeitar safe areas em dispositivos móveis quando existirem elementos fixos.

## 6. Componentes globais

### 6.1 Botões

Variantes permitidas: `primary`, `secondary`, `ghost` e `destructive`.

- Deve existir no máximo uma ação primária por grupo de ações.
- Todos os botões implementam estados default, hover, active, focus-visible, disabled e loading.
- Botões destrutivos usam tokens `danger` e exigem confirmação quando a consequência não for facilmente reversível.
- Botões apenas com ícone precisam de nome acessível e tooltip quando o significado não for universal.
- O estado loading mantém a largura do botão e impede submissões duplicadas.

### 6.2 Formulários

- Cada campo tem label visível, salvo quando existir alternativa igualmente clara e acessível.
- Texto auxiliar e erro devem estar associados ao campo programaticamente.
- Estados obrigatórios: default, hover, focus-visible, disabled, readonly, valid e invalid.
- Validar no momento adequado: evitar erros antes da primeira interação, salvo quando a submissão exigir validação completa.
- Preservar os dados introduzidos após falhas recuperáveis.
- Campos obrigatórios devem ser identificados por texto ou símbolo explicado, nunca apenas por cor.
- Agrupar opções relacionadas com `fieldset` e `legend` quando aplicável.

### 6.3 Cards

- Usar cards apenas para agrupar conteúdo e ações relacionados.
- Fundo `var(--surface)`, borda `var(--border)`, raio `var(--radius-lg)` e sombra `var(--shadow)` quando necessária.
- Evitar cards aninhados sem uma distinção semântica clara.

### 6.4 Tabelas e listas de dados

- Usar tabelas para dados comparáveis por colunas; usar listas ou cards quando a comparação tabular não for necessária.
- Cabeçalhos devem ser programaticamente associados às células.
- Alinhar números de forma consistente e usar fonte monoespaçada para identificadores e métricas.
- Oferecer scroll horizontal em ecrãs estreitos ou uma representação responsiva definida na feature spec.
- Paginação, ordenação, filtros e seleção devem manter estado e feedback visíveis.
- Estados vazio, loading e erro ocupam a região da tabela sem alterar inesperadamente o layout.

### 6.5 Badges e estados

- Variantes: `neutral`, `success`, `warning`, `danger` e `info`.
- Combinar texto com cor; ícones são opcionais e nunca substituem o label.
- Usar badges para estado ou categorização curta, não para ações.

### 6.6 Modais, dialogs e drawers

- Usar modal apenas quando a tarefa exigir atenção imediata ou confirmação contextual.
- O foco entra no dialog, permanece contido enquanto aberto e retorna ao elemento de origem ao fechar.
- Deve existir título acessível, ação de fechar e comportamento definido para `Escape`.
- Não fechar por clique exterior quando isso puder causar perda de dados ou confirmar implicitamente uma ação.
- Em mobile, drawers ou dialogs full-screen podem substituir modais estreitos quando melhorarem a tarefa.

### 6.7 Tooltips, menus e popovers

- Conteúdo essencial nunca deve existir apenas num tooltip.
- Elementos acionáveis devem funcionar por teclado e toque, não apenas por hover.
- Menus implementam gestão de foco, teclas direcionais e fecho com `Escape`.

## 7. Feedback e estados de interface

Cada superfície assíncrona deve definir, quando aplicável:

- **Loading:** progresso visível, sem bloquear regiões não relacionadas; usar skeleton apenas quando representar a estrutura real.
- **Empty:** explicar por que não existem dados e oferecer uma ação quando houver uma ação relevante.
- **Success:** confirmar a conclusão sem interromper desnecessariamente o fluxo.
- **Warning:** explicar risco ou condição incompleta antes da ação consequente.
- **Error:** apresentar mensagem clara, preservar contexto e oferecer retry ou recuperação quando possível.
- **Disabled:** explicar a indisponibilidade quando ela não for óbvia.

Toasts servem para feedback transitório e não devem conter informação necessária para concluir uma tarefa. Erros persistentes devem permanecer junto da região afetada.

## 8. Responsividade

Breakpoints de referência, alinhados com Tailwind:

| Nome | Largura mínima | Intenção |
|---|---:|---|
| Base | `0px` | Mobile-first |
| `sm` | `640px` | Telemóveis largos e tablets pequenos |
| `md` | `768px` | Tablets |
| `lg` | `1024px` | Desktop |
| `xl` | `1280px` | Desktop largo |
| `2xl` | `1536px` | Ecrãs muito largos |

- Implementar mobile-first e validar conteúdo real, zoom e strings longas.
- Nenhuma ação essencial pode depender exclusivamente de hover.
- Alvos interativos devem ter, sempre que possível, pelo menos 44×44 CSS pixels.
- Tabelas, código e conteúdo largo devem tratar overflow sem comprimir texto até ficar ilegível.
- Não esconder conteúdo essencial apenas para ajustar o layout; reorganizar ou oferecer divulgação progressiva.

## 9. Acessibilidade

Objetivo mínimo: WCAG 2.2 nível AA.

- Usar HTML semântico antes de ARIA.
- Garantir navegação completa por teclado, ordem de foco lógica e indicador `focus-visible` claramente perceptível.
- Texto normal deve atingir contraste mínimo de 4.5:1; texto grande e elementos gráficos essenciais, 3:1.
- Controlos devem possuir nome, função, valor e estado acessíveis.
- Alterações assíncronas importantes devem ser anunciadas sem mover o foco indevidamente.
- Imagens informativas precisam de texto alternativo; imagens decorativas devem ser ignoradas por tecnologias assistivas.
- Respeitar `prefers-reduced-motion`; animações não essenciais devem ser removidas ou reduzidas.
- Suportar zoom até 200% sem perda de conteúdo ou funcionalidade.
- Erros devem ser identificados por texto e associados aos campos correspondentes.

## 10. Internacionalização

- Todo o texto de interface deve usar o mecanismo de i18n do projeto; não colocar strings finais diretamente em templates ou componentes.
- Não concatenar fragmentos de frases traduzíveis.
- Suportar expansão de texto, pluralização, género gramatical quando aplicável e formatos locais de data, hora, número e moeda.
- Não assumir comprimentos fixos para labels, botões ou valores.
- Separar idioma da interface, locale de formatação e timezone.
- Preparar layout e ícones direcionais para idiomas RTL caso estes sejam incluídos pelos requisitos do produto.

## 11. Temas e preferências

- Os temas claro e escuro devem usar os mesmos tokens semânticos e manter hierarquia equivalente.
- A estratégia de seleção e persistência do tema deve ser definida pelo projeto; o estado inicial deve respeitar a preferência do sistema quando não existir escolha guardada.
- Mudanças de tema não podem causar perda de estado nem flash prolongado de tema incorreto.
- Componentes de terceiros devem ser tematizados com os mesmos tokens.

## 12. Segurança de apresentação

- Conteúdo HTML proveniente de fontes externas deve ser sanitizado antes de entrar no DOM.
- Links externos abertos numa nova janela devem usar `rel="noopener noreferrer"`.
- Não expor stack traces, segredos, tokens ou detalhes internos em mensagens de erro.
- Conteúdo potencialmente sensível deve respeitar as regras de visibilidade definidas na feature spec.

## 13. Critérios globais de aceitação

Uma implementação de UI só está concluída quando:

1. Usa componentes SpartanNG ou componentes partilhados antes de criar equivalentes novos.
2. Usa tokens de design e não introduz cores, tipografia ou espaçamentos arbitrários sem justificação.
3. Implementa os estados relevantes de loading, empty, success, warning, error e disabled.
4. Funciona nos breakpoints aplicáveis e sem overflow não intencional.
5. É operável por teclado e mantém foco visível e previsível.
6. Cumpre WCAG 2.2 AA nos fluxos abrangidos.
7. Usa o mecanismo de i18n e suporta conteúdo expandido e formatos locais.
8. Funciona nos temas claro e escuro quando ambos forem disponibilizados.
9. Inclui testes proporcionais ao risco e validação visual nos tamanhos relevantes.
10. Documenta qualquer divergência aprovada em relação a esta SPEC.

## 14. Relação com outros artefactos

- **Feature spec:** define páginas, fluxos, dados, permissões, conteúdo e critérios específicos.
- **Design tokens:** materializam os valores normativos desta SPEC em formatos consumíveis pelo código.
- **Biblioteca de componentes:** implementa os padrões reutilizáveis.
- **UI engineering skill:** define o procedimento usado por agentes para implementar UI.
- **UI review skill:** define o procedimento usado por agentes para validar conformidade.

Nenhum skill deve duplicar ou substituir as decisões normativas deste documento.
