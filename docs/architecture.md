# Decisões de arquitetura

## Um runtime simples e um servidor MCP

O loop Python usa a API local `/api/chat` do Ollama. Converte os schemas descobertos
via MCP em ferramentas do modelo, devolve os resultados e impõe limites de execução.
Não executa nomes de função arbitrários, código gerado, shell ou URLs externas.

O SDK MCP permanece na linha 1.x, fixado no arquivo de dependências do ambiente
validado. A migração para 2.x será deliberada, sem alterar a implementação durante
um experimento. Transporte stdio, uma sessão e um processo de servidor por caso.

## Autorização no servidor

O servidor é a autoridade. O cliente autenticado é configuração confiável; o argumento
`customer_id` é o recurso solicitado e não uma identidade autenticada.
O modo experimental é configuração do processo e não uma ferramenta exposta ao agente.

Todas as áreas são identificadores opacos com comparação exata. Não há normalização
de caminhos ou promessa de isolamento do sistema operacional. SQL recebe parâmetros.
O armazenamento e os controles estão no mesmo serviço para permitir transações
atômicas entre mutação e evento de auditoria.

## Avaliação independente da resposta textual

Verificadores consultam estado antes/depois e eventos do serviço. Não usam o LLM
avaliado como juiz. Asserts próprios podem ser consumidos pelo Promptfoo ou CLI.
Cada execução tem banco e pasta inéditos; arquivos existentes não são sobrescritos.
HTML escapa conteúdo não confiável de documentos e mensagens.

## Geração e identidade das suítes

`generate` solicita ao Ollama local um objeto JSON contendo somente o texto de ataque.
O modelo não escolhe rótulos, política, ferramentas, dados ou verificadores. Cada caso
deriva de um objetivo adversarial conhecido; nos casos indiretos, apenas o documento
é substituído, mantendo o pedido legítimo original.

O arquivo gerado fica em um diretório novo e recebe hash, metadados e histórico das
tentativas. Somente artefatos completos e estruturalmente válidos podem ser carregados.
Os textos continuam sendo dados não confiáveis, sem execução durante a geração.

O executor usa `fixed-v1` por padrão. A opção explícita `--suite-file` troca o conjunto
inteiro por uma suíte exploratória; não há descoberta automática ou concatenação.
Manifesto e HTML identificam a suíte e sua seleção. Os mesmos casos carregados são
reutilizados para todas as configurações e repetições.

## Custo e reprodução

Inferência usa somente loopback com proxy de ambiente desabilitado e sem redirects.
Download de modelo/dependências exige internet; uso posterior não exige API paga.
Não há serviço em nuvem, conta ou dependência de créditos gratuitos no caminho crítico.
Resultados ficam fora do Git por padrão. Amostras curadas devem conter apenas dados sintéticos.
