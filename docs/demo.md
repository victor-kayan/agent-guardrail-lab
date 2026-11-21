# Demonstração do laboratório — três minutos

1. **Problema (30 s):** um agente pode seguir instruções externas e propor ações
   fora do escopo. Instruções no prompt e autorização na execução são camadas diferentes.
2. **Ambiente (30 s):** dois clientes fictícios, três regras e quatro ferramentas MCP.
   Mostre o diagrama e a identidade da sessão definida fora do modelo.
3. **Experimento (60 s):** abra um relatório obtido com Ollama. Mostre a chamada,
   a decisão e o efeito para um caso. Se o modelo resistiu ao ataque, diga isso;
   use a fixture scripted separada para demonstrar o bloqueio determinístico.
4. **Utilidade (30 s):** mostre uma tarefa legítima concluída com autorização ligada.
5. **Conclusão (30 s):** descreva o que foi observado, a variabilidade e os limites.

Nunca apresente a fixture scripted como comportamento de um modelo. Não invente
uma falha para tornar a apresentação interessante: resistência ao ataque também é resultado.

## Exploração com novos casos

Use `guardrail-lab generate` para criar variações adversariais com o Ollama local.
Inspecione os textos em `suite.json` e execute com `run --suite-file`. O relatório
identifica o conjunto como exploratório. Para comparar com os resultados históricos,
use o comando `run` sem `--suite-file`, que preserva os 12 casos da suíte `fixed-v1`.
