"use strict";

const $ = (selector) => document.querySelector(selector);
const state = { view: "explore", catalog: null, selected: null, data: new Map(),
  reference: null, current: null, comparison: null, busy: false };
const modeNames = { baseline: "Sem proteção específica", prompt_only: "Somente prompt", enforced: "Prompt + autorização" };
const verdictNames = { pass: "Sem violação", fail: "Violação", inconclusive: "Inconclusivo" };
const ruleNames = { customer_boundary: "Limite entre clientes", deletion_forbidden: "Exclusão proibida", report_area_boundary: "Destino do relatório" };
const changeNames = { security_regression: "Nova violação", security_improvement: "Violação não observada novamente", utility_regression: "Perda de utilidade", utility_improvement: "Tarefa passou a concluir", execution_regression: "Nova falha de execução" };
const number = (value) => Number(value || 0).toLocaleString("pt-BR");
const pretty = (value) => JSON.stringify(value, null, 2);

// All artifact and model content enters the DOM as text, never HTML.
function h(tag, attrs = {}, ...children) {
  const element = document.createElement(tag);
  for (const child of children.flat(Infinity)) {
    if (child !== null && child !== undefined && child !== false) {
      element.append(child instanceof Node ? child : document.createTextNode(String(child)));
    }
  }
  for (const [key, value] of Object.entries(attrs)) {
    if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (key === "value" || key === "disabled") element[key] = value;
    else element.setAttribute(key, String(value));
  }
  return element;
}
const badge = (text, type = "") => h("span", { class: `badge ${type}` }, text);
const jsonBlock = (value) => h("pre", {}, pretty(value));
function notice(message = "", error = false) {
  $("#notice").replaceChildren(...(message ? [h("div", { class: `notice ${error ? "error" : ""}` }, message)] : []));
}
async function api(path) {
  const response = await fetch(path, { cache: "no-store", credentials: "same-origin" });
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || "Não foi possível carregar os dados.");
  return body;
}
function failure(error) { notice(error.message || String(error), true); }
function title(heading, description) {
  $("#page-title").textContent = heading;
  $("#page-description").textContent = description;
}
function panel(heading, body, right = null) {
  return h("section", { class: "panel" }, h("div", { class: "panel-heading" }, h("h2", {}, heading), right), body);
}
function empty(heading, description, command = null) {
  return h("div", { class: "panel empty" }, h("strong", {}, heading), h("p", {}, description), command && h("code", {}, command));
}
function selection(label, value, entries, callback) {
  return h("div", { class: "field" }, h("label", { class: "label" }, label,
    h("select", { "aria-label": label, value, onchange: (event) => callback(event.target.value) },
      entries.map(([id, text]) => h("option", { value: id }, text)))));
}
function metric(label, value, note, type = "") {
  return h("div", { class: "metric" }, h("div", { class: "metric-label" }, label),
    h("div", { class: `metric-value ${type}` }, value), h("div", { class: "metric-note" }, note));
}
async function refresh() {
  const button = $("#refresh");
  button.disabled = true;
  try {
    state.catalog = await api("/api/catalog");
    state.data.clear();
    if (!state.catalog.experiments.some((item) => item.id === state.selected)) {
      state.selected = state.catalog.experiments[0]?.id || null;
    }
    render();
  } catch (error) { failure(error); }
  finally { button.disabled = false; }
}
function render() {
  notice();
  document.querySelectorAll("[data-view]").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === state.view);
    button.setAttribute("aria-current", button.dataset.view === state.view ? "page" : "false");
  });
  if (!state.catalog) return;
  if (state.catalog.warnings.length || state.catalog.truncated) {
    const text = `${state.catalog.warnings.length} arquivo(s) não puderam ser carregados.`;
    $("#notice").replaceChildren(h("details", { class: "notice" }, h("summary", {},
      state.catalog.truncated ? "O catálogo atingiu o limite de arquivos. Escolha uma pasta mais específica." : text),
      state.catalog.warnings.map((warning) => h("p", {}, `${warning.file}: ${warning.error}`))));
  }
  if (state.view === "explore") renderExplore();
  else if (state.view === "compare") renderCompare();
  else renderSuites();
}
async function getExperiment(id) {
  if (!state.data.has(id)) state.data.set(id, await api(`/api/experiments/${encodeURIComponent(id)}`));
  return state.data.get(id);
}
function renderExplore() {
  title("O que seu agente fez?", "Explore chamadas, decisões e efeitos reais de cada execução.");
  const experiments = state.catalog.experiments;
  if (!experiments.length) {
    $("#content").replaceChildren(empty("Nenhum experimento salvo", "Execute uma avaliação pelo CLI e atualize o painel. Para explorar os resultados incluídos no repositório, use:", "guardrail-lab serve --root examples/results"));
    return;
  }
  const item = experiments.find((entry) => entry.id === state.selected) || experiments[0];
  state.selected = item.id;
  const heading = h("div", { class: "selector-row" }, selection("Experimento", item.id,
    experiments.map((entry) => [entry.id, `${entry.name} · ${entry.runs} execuções`]),
    (id) => { state.selected = id; renderExplore(); }),
    h("div", { class: "selector-info" }, badge(item.provider === "scripted" ? "FIXTURE · SEM IA" : "OLLAMA LOCAL", item.provider === "scripted" ? "warn" : "good")));
  const results = h("div", { "aria-live": "polite" }, h("p", { class: "empty" }, "Carregando evidências…"));
  $("#content").replaceChildren(heading, results);
  getExperiment(item.id).then((manifest) => {
    if (state.view !== "explore" || state.selected !== item.id) return;
    renderResults(manifest, results);
  }).catch(failure);
}
function renderResults(manifest, target) {
  const rows = manifest.results || [];
  const legitimate = rows.filter((row) => row.scenario.kind === "legitimate");
  const violations = rows.filter((row) => row.evaluation.security === "fail").length;
  const blocked = rows.reduce((total, row) => total + row.evaluation.blocked_event_ids.length, 0);
  const incomplete = rows.filter((row) => row.status !== "completed").length;
  const succeeded = legitimate.filter((row) => row.evaluation.task_success === true).length;
  const label = manifest.suite?.kind === "generated" ? "Suíte exploratória gerada" : manifest.suite?.kind === "fixed" ? "Avaliação fixa · fixed-v1" : "Experimento histórico";
  const metrics = h("div", { class: "metrics" },
    metric("Execuções avaliadas", number(rows.length), `${number(incomplete)} incompletas ou com erro`),
    metric("Violações efetivas", number(violations), "Casos com acesso ou efeito proibido", violations ? "bad-text" : "good-text"),
    metric("Chamadas bloqueadas", number(blocked), "Negadas pelo controle no servidor"),
    metric("Tarefas legítimas", legitimate.length ? `${succeeded}/${legitimate.length}` : "—", legitimate.length ? "Concluídas segundo os critérios da suíte" : "Este conjunto contém apenas ataques"));
  const modes = [...new Set(rows.map((row) => row.mode))];
  const summaries = h("div", { class: "summary-grid" }, modes.map((mode) => {
    const group = rows.filter((row) => row.mode === mode);
    const failed = group.filter((row) => row.evaluation.security === "fail").length;
    return h("div", { class: "mode-summary" }, h("div", { class: "row space-between" },
      h("h3", {}, modeNames[mode] || mode), badge(`${failed}/${group.length} com violação`, failed ? "bad" : "good")),
      h("progress", { max: group.length || 1, value: failed, "aria-label": `${modeNames[mode]}: ${failed} violações em ${group.length} execuções` }),
      h("p", {}, "Contagem observada nos cenários executados."));
  }));
  const context = h("div", { class: "row small muted" }, badge(label, manifest.suite?.kind === "generated" ? "warn" : ""),
    manifest.provider === "scripted" ? "Roteiro determinístico; não mede robustez de um modelo." : (manifest.model || "Modelo não informado"));
  const body = h("tbody");
  const count = h("div", { class: "table-count", role: "status" });
  const filters = { search: "", mode: "all", verdict: "all" };
  function updateTable() {
    const visible = rows.filter((row) =>
      row.scenario.id.toLowerCase().includes(filters.search.toLowerCase()) &&
      (filters.mode === "all" || row.mode === filters.mode) &&
      (filters.verdict === "all" || row.evaluation.security === filters.verdict));
    body.replaceChildren(...visible.map((row) => h("tr", {},
      h("td", {}, h("span", { class: "case-name" }, row.scenario.id),
        h("div", { class: "case-sub" }, `${row.scenario.kind === "legitimate" ? "Tarefa legítima" : "Adversarial"} · seed ${row.seed}`)),
      h("td", {}, modeNames[row.mode] || row.mode),
      h("td", {}, badge(verdictNames[row.evaluation.security] || row.evaluation.security,
        row.evaluation.security === "fail" ? "bad" : row.evaluation.security === "pass" ? "good" : "warn")),
      h("td", {}, `${row.evaluation.attempt_event_ids.length} / ${row.evaluation.blocked_event_ids.length}`),
      h("td", {}, `${Number(row.elapsed_seconds || 0).toLocaleString("pt-BR", { maximumFractionDigits: 2 })} s`),
      h("td", {}, h("button", { class: "investigate", onclick: () => openCase(row), "aria-label": `Investigar ${row.scenario.id} em ${row.mode}` }, "Investigar ↗")))));
    if (!visible.length) body.append(h("tr", {}, h("td", { colspan: 6, class: "empty" }, "Nenhum caso corresponde aos filtros.")));
    count.textContent = `${visible.length} de ${rows.length} execuções · Tentativas / bloqueios contam chamadas, não casos.`;
  }
  const controls = h("div", { class: "filters" },
    h("input", { type: "search", placeholder: "Buscar cenário…", "aria-label": "Buscar cenário", oninput: (event) => { filters.search = event.target.value; updateTable(); } }),
    h("select", { "aria-label": "Filtrar proteção", onchange: (event) => { filters.mode = event.target.value; updateTable(); } },
      h("option", { value: "all" }, "Todas as proteções"), modes.map((mode) => h("option", { value: mode }, modeNames[mode] || mode))),
    h("select", { "aria-label": "Filtrar resultado", onchange: (event) => { filters.verdict = event.target.value; updateTable(); } },
      h("option", { value: "all" }, "Todos os resultados"), Object.entries(verdictNames).map(([value, text]) => h("option", { value }, text))));
  const table = h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, ["Cenário", "Proteção", "Resultado", "Tentativas / bloqueios", "Tempo", ""].map((text) => h("th", { scope: "col" }, text)))), body));
  target.replaceChildren(metrics, panel("Proteção por configuração", summaries),
    panel("Execuções e evidências", h("div", {}, controls, table, count), context),
    h("details", { class: "panel panel-body" }, h("summary", {}, "Identidade da suíte e parâmetros do experimento"),
      jsonBlock({ suite: manifest.suite || "Sem identidade registrada", parameters: manifest.parameters, provenance: manifest.provenance })));
  updateTable();
}
function openCase(row) {
  const dialog = $("#case-dialog");
  $("#case-title").textContent = row.scenario.id;
  const intro = h("div", { class: "row" }, badge(modeNames[row.mode] || row.mode),
    badge(verdictNames[row.evaluation.security] || row.evaluation.security,
      row.evaluation.security === "fail" ? "bad" : row.evaluation.security === "pass" ? "good" : "warn"),
    h("span", { class: "small muted" }, `seed ${row.seed} · ${row.status}`));
  const content = h("div");
  const views = {
    "Linha do tempo": () => {
      const events = row.events || [];
      const list = h("div", { class: "timeline" });
      if (row.evaluation.violations.length) list.append(h("div", { class: "notice error" },
        "Violação observada: ", row.evaluation.violations.map((entry) => ruleNames[entry.rule] || entry.rule).join(" · ")));
      if (row.error) list.append(h("div", { class: "notice error" }, row.error));
      if (!events.length) list.append(h("p", { class: "muted small" }, "Nenhuma ferramenta foi chamada. Consulte a resposta do agente abaixo."));
      for (const event of events) {
        const blocked = event.decision === "deny";
        list.append(h("article", { class: `event ${blocked ? "blocked" : ""}` },
          h("div", { class: "event-heading" }, h("span", { class: "event-tool" }, `${String(event.id).padStart(2, "0")}  ${event.tool}`),
            badge(blocked ? "Bloqueada" : event.executed ? "Executada" : "Sem efeito", blocked ? "warn" : "good")),
          h("p", {}, `Decisão: ${event.decision} · Motivo: ${ruleNames[event.reason] || event.reason}`),
          h("details", {}, h("summary", {}, "Argumentos e retorno"), jsonBlock({ arguments: event.arguments, result: event.result }))));
      }
      const answer = [...(row.trace?.messages || [])].reverse().find((message) => message.role === "assistant" && message.content);
      if (answer) list.append(h("div", {}, h("h3", {}, "Resposta final do agente"), h("div", { class: "text-content" }, answer.content)));
      return list;
    },
    "Tarefa e documento": () => h("div", { class: "stack" },
      h("section", {}, h("h3", {}, "Solicitação do usuário"), h("div", { class: "text-content" }, row.scenario.prompt)),
      h("section", {}, h("h3", {}, "Documento externo · dado não confiável"), h("div", { class: "text-content" }, row.scenario.document))),
    "Estado dos dados": () => h("div", {}, h("h3", {}, "Antes"), jsonBlock(row.before), h("h3", {}, "Depois"), jsonBlock(row.after)),
    "Rastro completo": () => jsonBlock(row)
  };
  const tabs = h("div", { class: "tabs", role: "group", "aria-label": "Seções da investigação" });
  for (const [name, draw] of Object.entries(views)) {
    const button = h("button", { class: "tab", "aria-pressed": false, onclick: () => {
      [...tabs.children].forEach((child) => { child.classList.remove("active"); child.setAttribute("aria-pressed", "false"); });
      button.classList.add("active"); button.setAttribute("aria-pressed", "true"); content.replaceChildren(draw());
    } }, name);
    tabs.append(button);
  }
  $("#case-content").replaceChildren(h("div", { class: "dialog-body" }, intro, tabs, content));
  tabs.firstChild.click();
  if (!dialog.open) dialog.showModal();
}
function renderCompare() {
  title("O que mudou entre execuções?", "Compare os mesmos testes e identifique novas violações, perda de utilidade e erros.");
  const choices = state.catalog.experiments.filter((item) => item.compatibility);
  if (!choices.length) {
    $("#content").replaceChildren(empty("Nenhum experimento comparável", "São necessários resultados completos, com a mesma suíte, seleção, seeds e parâmetros."));
    return;
  }
  if (!choices.some((item) => item.id === state.reference)) {
    const newest = choices[0];
    state.reference = choices.find((item) => item.id !== newest.id && item.compatibility === newest.compatibility)?.id || newest.id;
  }
  const reference = choices.find((item) => item.id === state.reference);
  const compatible = choices.filter((item) => item.id !== reference.id && item.compatibility === reference.compatibility);
  if (!compatible.some((item) => item.id === state.current)) state.current = compatible[0]?.id || "";
  const result = h("div", { class: "comparison-result", "aria-live": "polite" });
  const form = h("div", { class: "comparison-form" },
    selection("Execução de referência", state.reference, choices.map((item) => [item.id, item.name]),
      (id) => { state.reference = id; state.comparison = null; renderCompare(); }),
    selection("Execução a avaliar · compatíveis", state.current, compatible.length ? compatible.map((item) => [item.id, item.name]) : [["", "Nenhuma outra execução compatível"]],
      (id) => { state.current = id; state.comparison = null; result.replaceChildren(); }),
    h("button", { class: "button primary", disabled: state.busy || !state.current, onclick: async (event) => {
      const ref = state.reference, current = state.current;
      state.busy = true; event.target.disabled = true; result.replaceChildren(h("p", { class: "muted small" }, "Comparando evidências…"));
      try {
        const comparison = await api(`/api/compare?reference=${encodeURIComponent(ref)}&current=${encodeURIComponent(current)}`);
        state.comparison = { reference: ref, current, value: comparison };
        if (state.view === "compare" && state.reference === ref && state.current === current) drawComparison(comparison, result, current);
      } catch (error) { result.replaceChildren(h("div", { class: "notice error" }, error.message)); }
      finally {
        state.busy = false;
        if (state.view === "compare" && !event.target.isConnected) renderCompare();
        else event.target.disabled = !state.current;
      }
    } }, state.busy ? "Comparando…" : "Comparar execuções →"));
  const explanation = compatible.length ?
    "A lista mostra apenas execuções com os mesmos cenários, modos, seeds, dados iniciais e parâmetros. O servidor valida a compatibilidade novamente." :
    "Salve outra execução da mesma suíte e com os mesmos parâmetros para habilitar a comparação.";
  $("#content").replaceChildren(panel("Selecione as execuções", h("div", { class: "panel-body" }, form, h("p", { class: "muted small" }, explanation))), result);
  if (state.comparison?.reference === state.reference && state.comparison.current === state.current) drawComparison(state.comparison.value, result, state.current);
}
function drawComparison(comparison, target, currentId) {
  const summary = h("div", { class: `regression-summary ${comparison.regression_runs ? "has-regressions" : ""}` },
    h("h2", {}, comparison.regression_runs ? `${comparison.regression_runs} execução(ões) com regressão` : "Nenhuma regressão observada"),
    h("p", {}, `${comparison.paired_runs} pares avaliados. Contagens descritivas dos mesmos testes; não demonstram segurança geral.`));
  const body = h("tbody");
  for (const [mode, item] of Object.entries(comparison.summaries)) {
    body.append(h("tr", {}, h("td", {}, modeNames[mode] || mode),
      h("td", {}, `${item.baseline.violations} → ${item.candidate.violations}`),
      h("td", {}, badge(`${item.delta.violations > 0 ? "+" : ""}${item.delta.violations}`, item.delta.violations > 0 ? "bad" : item.delta.violations < 0 ? "good" : "")),
      h("td", {}, item.candidate.legitimate_runs ? `${item.candidate.legitimate_successes}/${item.candidate.legitimate_runs}` : "Sem tarefas legítimas"),
      h("td", {}, `${item.baseline.incomplete_runs} → ${item.candidate.incomplete_runs}`)));
  }
  const changes = h("div", { class: "panel-body stack" });
  if (!comparison.changed_runs.length) changes.append(h("p", { class: "muted small" }, "Nenhuma mudança nos critérios de regressão e melhoria avaliados."));
  comparison.changed_runs.forEach((change) => changes.append(h("div", { class: "row space-between" },
    h("div", {}, h("div", { class: "case-name" }, change.scenario_id), h("p", { class: "small muted" },
      `${modeNames[change.mode]} · seed ${change.seed} · ${change.changes.map((name) => changeNames[name] || name).join(" · ")}`)),
    h("button", { class: "button subtle", onclick: async () => {
      try {
        const manifest = await getExperiment(currentId);
        const row = manifest.results.find((entry) => entry.scenario.id === change.scenario_id && entry.mode === change.mode && entry.seed === change.seed);
        if (row) openCase(row);
      } catch (error) { failure(error); }
    } }, "Investigar"))));
  target.replaceChildren(summary,
    panel("Resultado por configuração", h("div", { class: "table-wrap" }, h("table", {},
      h("thead", {}, h("tr", {}, ["Proteção", "Violações · antes → depois", "Delta", "Tarefas legítimas · depois", "Erros · antes → depois"].map((text) => h("th", { scope: "col" }, text)))), body))),
    panel("Mudanças por execução", changes),
    h("details", { class: "panel panel-body" }, h("summary", {}, "Parâmetros e rastreabilidade da comparação"), jsonBlock(comparison)));
}
function renderSuites() {
  title("Quais limites estão sendo testados?", "Inspecione a cobertura, as tarefas e os documentos sem executar o agente.");
  const grid = h("div", { class: "suite-grid" }, state.catalog.suites.map((suite) => h("article", { class: "suite-card" },
    badge(suite.kind === "fixed" ? "AVALIAÇÃO FIXA" : "EXPLORATÓRIA", suite.kind === "fixed" ? "good" : "warn"),
    h("h2", {}, suite.name), h("p", {}, `${suite.case_count} casos · ${suite.kind === "fixed" ? "Referência estável para comparação" : "Variações adversariais geradas por IA"}`),
    h("button", { class: "button subtle", onclick: async () => {
      try { showSuite(await api(`/api/suites/${encodeURIComponent(suite.id)}`)); }
      catch (error) { failure(error); }
    } }, "Inspecionar casos →"))));
  $("#content").replaceChildren(grid, h("div", { class: "notice" }, "A suíte fixa mantém os mesmos 12 casos. Suítes geradas são exploratórias e seus totais não devem ser somados aos da avaliação fixa."), h("div", { id: "suite-details" }));
}
function showSuite(suite) {
  const content = h("div", { class: "panel-body" }, h("p", { class: "mono muted" }, `SHA256 ${suite.suite.sha256}`),
    h("h3", {}, "Cobertura por objetivo e canal"), jsonBlock(suite.coverage));
  for (const row of suite.texts) {
    content.append(h("details", {}, h("summary", {}, `${row.id} · ${row.goal}`),
      h("h3", {}, "Solicitação do usuário"), h("p", { class: "text-content" }, row.prompt),
      h("h3", {}, "Documento externo · dado não confiável"), h("p", { class: "text-content" }, row.document)));
  }
  $("#suite-details").replaceChildren(panel(`${suite.suite.id} · ${suite.case_count} casos`, content));
  $("#suite-details").scrollIntoView({ block: "start" });
}
document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", () => {
  state.view = button.dataset.view; render(); window.scrollTo({top: 0});
}));
$(".brand").addEventListener("click", (event) => { event.preventDefault(); state.view = "explore"; render(); window.scrollTo({top: 0}); });
$("#refresh").addEventListener("click", () => { state.comparison = null; refresh(); });
$("#close-dialog").addEventListener("click", () => $("#case-dialog").close());
refresh();

const jobsPanel = h("section", {class:"panel panel-body"}, h("h2", {}, "Executar com Ollama local"));
const modelInput = h("input", {value:"qwen3:4b-instruct-2507-q4_K_M", "aria-label":"Modelo Ollama"});
const countInput = h("input", {type:"number",min:1,max:24,value:6,"aria-label":"Quantidade de testes"});
const repetitionsInput = h("input", {type:"number",min:1,max:3,value:1,"aria-label":"Repetições"});
const suiteInput = h("select", {"aria-label":"Suíte para execução"},h("option", {value:"fixed"},"Avaliação fixa · 12 casos"));
const jobStatus = h("div", {role:"status", "aria-live":"polite"});
let jobActive = false;
async function startJob(action) {
  try {
    const response = await fetch("/api/jobs", {method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({action,model:modelInput.value,count:Number(countInput.value),
        repetitions:Number(repetitionsInput.value),suite_id:suiteInput.value})});
    const body = await response.json();
    if (!response.ok) throw new Error(body.error);
    jobActive = true; jobStatus.textContent = "Trabalho iniciado…";
    pollJobs();
  } catch(error) { jobStatus.textContent = error.message; }
}
const generateButton = h("button", {class:"button",onclick:()=>startJob("generate")},"Gerar testes");
const evaluateButton = h("button", {class:"button primary",onclick:()=>startJob("evaluate")},"Executar avaliação");
jobsPanel.append(h("p",{class:"muted small"},"Um trabalho por vez. Avaliações usam a suíte fixa por padrão; a geração salva uma suíte exploratória separada."),
 h("div", {class:"stack"},h("label",{},"Modelo local",modelInput),h("label",{},"Novos testes (1–24)",countInput),
 h("label",{},"Repetições (1–3)",repetitionsInput),h("label",{},"Suíte",suiteInput),
 h("div",{class:"row"},generateButton,evaluateButton),jobStatus));
$("main").append(jobsPanel);
async function pollJobs() {
  try {
    const jobs = await api("/api/jobs");
    const job = jobs[jobs.length-1];
    const wasActive = jobActive;
    jobActive = Boolean(job && job.status === "running");
    generateButton.disabled = evaluateButton.disabled = jobActive;
    if (job) jobStatus.replaceChildren(h("p",{},`${job.action} · ${job.status} · ${job.completed}/${job.total}`),
      h("progress",{max:job.total,value:job.completed,"aria-label":"Progresso do trabalho"}),h("p",{},job.message));
    if (wasActive && !jobActive) await refresh();
    if (state.catalog && !jobActive) {
      const selected = suiteInput.value;
      suiteInput.replaceChildren(...state.catalog.suites.map(suite=>h("option",{value:suite.id},suite.name)));
      if ([...suiteInput.options].some(option=>option.value===selected)) suiteInput.value=selected;
    }
  } catch(error) { jobStatus.textContent=error.message; }
}
pollJobs();
setInterval(pollJobs,2000);
