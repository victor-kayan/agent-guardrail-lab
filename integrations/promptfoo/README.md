# Integração opcional com Promptfoo

A implementação principal não depende de Node.js. Esta configuração permite comparar
as três variantes no Promptfoo usando um provider Python e asserts em JavaScript.
Execute na raiz do repositório, depois de instalar o pacote e iniciar o Ollama.

```powershell
$env:PROMPTFOO_PYTHON = (Resolve-Path .\.venv\Scripts\python.exe).Path
$env:PROMPTFOO_DISABLE_REMOTE_GENERATION = 'true'
$env:PROMPTFOO_DISABLE_REDTEAM_REMOTE_GENERATION = 'true'
$env:PROMPTFOO_DISABLE_TELEMETRY = '1'
$env:PROMPTFOO_DISABLE_SHARING = '1'
$env:PROMPTFOO_DISABLE_UPDATE = '1'
$env:PROMPTFOO_SELF_HOSTED = '1'
npm ci --prefix integrations/promptfoo
.\integrations\promptfoo\node_modules\.bin\promptfoo.cmd eval -c integrations/promptfoo/promptfooconfig.yaml --no-cache
```

Use `$env:LAB_MODEL = 'nome-exato-do-modelo'` para selecionar outro modelo instalado
nas três configurações. `provider: scripted` permite
verificar a integração sem inferência, mas não é uma avaliação de IA.
O provider devolve evidências no output antes de limpar o ambiente temporário.
Use o CLI próprio para preservar os bancos e rastros completos de cada execução.

Esta configuração usa casos fixos (`eval`), sem plugins de red team, geração de
ataques remota, `llm-rubric` ou grader pago. Uma execução de cada vez. A preparação
inicial do npm baixa pacotes; não confunda isso com inferência remota.

Falhas de segurança nos modos deliberadamente vulneráveis são resultados esperados
do experimento. O Promptfoo pode terminar com código 1 porque um assert de segurança
falhou; isso difere de um erro de integração. Na validação com a fixture scripted,
o resultado esperado é seis aprovações, seis falhas de segurança e zero erros.
