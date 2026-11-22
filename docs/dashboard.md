# Painel de investigação local

O painel transforma os artefatos de avaliação em uma navegação de investigação.
O fluxo principal é selecionar experimento, filtrar resultado e abrir uma execução.
Uma tentativa bloqueada, um efeito proibido e um erro de execução permanecem conceitos
separados na interface. Suítes exploratórias recebem identificação própria.

## Arquitetura

`guardrail-lab serve` inicia um `ThreadingHTTPServer` ligado somente a `127.0.0.1`.
Os arquivos HTML, CSS e JavaScript são recursos do pacote Python; não há bundler,
serviços externos ou inferência no caminho de visualização.

O catálogo descobre somente `experiment.json` e `suite.json` sob a raiz configurada.
Os IDs públicos são derivados do caminho relativo. A API resolve IDs pelo catálogo,
verifica que o arquivo continua dentro da raiz e usa a leitura JSON limitada já
utilizada pelo CLI. A raiz deve conter artefatos locais confiáveis; isso não constitui
um sandbox contra processos locais capazes de alterar o sistema de arquivos.

## Rotas

| Rota GET | Função |
|---|---|
| `/api/catalog` | Listar experimentos, suítes válidas e avisos de arquivos inválidos |
| `/api/experiments/<id>` | Ler evidências de um experimento |
| `/api/suites/<id>` | Inspecionar casos e cobertura; `fixed` é a suíte fixa |
| `/api/compare?reference=<id>&current=<id>` | Comparar em memória dois experimentos |

As comparações reutilizam `compare_manifests`. A UI filtra pares compatíveis e o
backend valida novamente cenário, modos, seeds, parâmetros e dados iniciais. Não há
fallback para conjuntos diferentes. Resultados de comparação não são gravados pela UI.

## Acesso e apresentação de dados

- Host, Origin e Sec-Fetch-Site são verificados para reduzir acesso de outras origens
  e ataques de DNS rebinding pelo navegador.
- POST /api/jobs inicia geração ou avaliação com o Ollama em loopback, com validação de origem e limites; não aceita comandos de shell, URLs remotas ou caminhos arbitrários.
- Arquivos arbitrários, bancos e logs não são publicados pelo servidor.
- Textos de documentos e modelos entram no DOM via `textContent` e nós de texto.
- CSP permite scripts e estilos locais, bloqueia enquadramento e desativa objetos.
- JSON possui limite de tamanho; a descoberta limita profundidade e número de artefatos.

O servidor não possui autenticação para múltiplos usuários e destina-se à exploração
local. Não acrescente um proxy que o exponha à rede sem implementar um modelo de acesso.
