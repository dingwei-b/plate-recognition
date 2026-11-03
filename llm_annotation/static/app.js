const state = { items: [], index: 0, busy: false, poller: null };

const el = Object.fromEntries([
  "modelName", "counts", "prompt", "savePrompt", "batchLabel", "batchStatus",
  "filter", "search", "previous", "position", "next", "workspace", "emptyState",
  "plateImage", "imageMeta", "llmState", "llmPlate", "llmTime", "runLlm",
  "copyLlm", "humanState", "humanPlate", "humanStatus", "saveNext", "message",
  "batchDialog", "cancelBatch", "confirmBatch", "backBrowse"
].map(id => [id, document.getElementById(id)]));

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function notify(text, error = false) {
  el.message.textContent = text;
  el.message.className = `message visible${error ? " error" : ""}`;
  clearTimeout(notify.timer);
  notify.timer = setTimeout(() => { el.message.className = "message"; }, 3500);
}

function updateCounts(counts) {
  el.counts.textContent = `${counts.confirmed} confirmed · ${counts.llm_labeled} LLM labeled · ${counts.total} total`;
}

function current() { return state.items[state.index] || null; }

function render() {
  const item = current();
  el.workspace.hidden = !item;
  el.emptyState.hidden = Boolean(item);
  el.position.textContent = item ? `${state.index + 1} / ${state.items.length}` : "0 / 0";
  el.previous.disabled = !item || state.index === 0;
  el.next.disabled = !item || state.index >= state.items.length - 1;
  if (!item) return;

  el.plateImage.src = `${item.image_url}?v=${encodeURIComponent(item.sha256 || "")}`;
  el.imageMeta.textContent = `${item.relative_path} · ${item.width}×${item.height}`;
  el.llmState.textContent = item.llm_state || "Not labeled";
  el.llmPlate.textContent = item.llm_plate || "Not labeled";
  el.runLlm.textContent = item.llm_state ? "Run again" : "Run LLM";
  el.llmTime.textContent = item.llm_updated_at ? `Updated ${new Date(item.llm_updated_at).toLocaleString()}` : "";
  el.humanState.value = item.human_state || item.llm_state || "";
  el.humanPlate.value = item.human_plate || item.llm_plate || "";
  const confirmed = item.human_status === "confirmed";
  el.humanStatus.textContent = confirmed ? "Confirmed" : "Pending";
  el.humanStatus.className = `status-badge${confirmed ? " confirmed" : ""}`;
  el.copyLlm.disabled = !item.llm_state;
  setBusy(false);
}

function setBusy(busy) {
  state.busy = busy;
  el.runLlm.disabled = busy || !current();
  el.saveNext.disabled = busy || !current();
  el.copyLlm.disabled = busy || !current()?.llm_state;
}

async function loadItems(keepId = null) {
  const query = new URLSearchParams({ status: el.filter.value, search: el.search.value });
  const data = await api(`/api/items?${query}`);
  state.items = data.items;
  const found = keepId ? state.items.findIndex(item => item.id === keepId) : -1;
  state.index = found >= 0 ? found : Math.min(state.index, Math.max(0, state.items.length - 1));
  updateCounts(data.counts);
  render();
}

async function runLlm() {
  const item = current();
  if (!item || state.busy) return;
  setBusy(true);
  el.runLlm.textContent = "Running…";
  try {
    const data = await api(`/api/items/${item.id}/label`, { method: "POST" });
    item.llm_state = data.llm.state;
    item.llm_plate = data.llm.plate;
    if (item.human_status !== "confirmed") {
      item.human_state = data.llm.state;
      item.human_plate = data.llm.plate;
    }
    updateCounts(data.counts);
    render();
    notify("LLM result saved");
  } catch (error) {
    notify(error.message, true);
  } finally {
    el.runLlm.textContent = item.llm_state ? "Run again" : "Run LLM";
    setBusy(false);
  }
}

async function saveHuman(moveNext = true) {
  const item = current();
  if (!item || state.busy) return;
  setBusy(true);
  try {
    const data = await api(`/api/items/${item.id}/human`, {
      method: "PATCH",
      body: JSON.stringify({ state: el.humanState.value, plate: el.humanPlate.value })
    });
    item.human_state = data.state;
    item.human_plate = data.plate;
    item.human_status = data.status;
    updateCounts(data.counts);
    if (moveNext && state.index < state.items.length - 1) state.index += 1;
    render();
    notify("Human annotation saved");
  } catch (error) {
    notify(error.message, true);
  } finally {
    setBusy(false);
  }
}

async function savePrompt() {
  try {
    await api("/api/prompt", { method: "PUT", body: JSON.stringify({ prompt: el.prompt.value }) });
    notify("Prompt saved");
  } catch (error) { notify(error.message, true); }
}

function copyLlm() {
  const item = current();
  if (!item?.llm_state) return;
  el.humanState.value = item.llm_state;
  el.humanPlate.value = item.llm_plate;
}

async function startBatch() {
  try {
    const job = await api("/api/batch", { method: "POST" });
    showBatch(job);
    if (job.running && !state.poller) state.poller = setInterval(pollBatch, 1500);
  } catch (error) { notify(error.message, true); }
}

function showBatch(job) {
  if (!job.running && job.total === 0) {
    el.batchStatus.textContent = "";
  } else if (job.running) {
    el.batchStatus.textContent = `Batch labeling: ${job.completed}/${job.total} complete, ${job.failed} failed${job.current ? ` · ${job.current}` : ""}`;
  } else {
    el.batchStatus.textContent = `Batch complete: ${job.completed}/${job.total}, ${job.failed} failed${job.last_error ? ` · Last error: ${job.last_error}` : ""}`;
  }
  el.batchLabel.disabled = job.running;
}

async function pollBatch() {
  try {
    const job = await api("/api/batch");
    showBatch(job);
    if (!job.running) {
      clearInterval(state.poller);
      state.poller = null;
      await loadItems(current()?.id);
    }
  } catch (error) { notify(error.message, true); }
}

async function initialize() {
  try {
    const config = await api("/api/config");
    el.modelName.textContent = `${config.model}${config.api_configured ? "" : " · API key missing"}`;
    el.prompt.value = config.prompt;
    updateCounts(config.counts);
    const pageParams = new URLSearchParams(window.location.search);
    const requestedId = Number(pageParams.get("image"));
    if (Number.isInteger(requestedId) && requestedId > 0) {
      const returnPath = pageParams.get("return");
      el.backBrowse.href = returnPath?.startsWith("/browse") ? returnPath : "/browse";
      el.backBrowse.hidden = false;
    }
    await loadItems(Number.isInteger(requestedId) && requestedId > 0 ? requestedId : null);
    await pollBatch();
  } catch (error) { notify(error.message, true); }
}

el.previous.addEventListener("click", () => { if (state.index > 0) { state.index -= 1; render(); } });
el.next.addEventListener("click", () => { if (state.index < state.items.length - 1) { state.index += 1; render(); } });
el.runLlm.addEventListener("click", runLlm);
el.copyLlm.addEventListener("click", copyLlm);
el.saveNext.addEventListener("click", () => saveHuman(true));
el.savePrompt.addEventListener("click", savePrompt);
el.batchLabel.addEventListener("click", () => el.batchDialog.showModal());
el.cancelBatch.addEventListener("click", () => el.batchDialog.close());
el.confirmBatch.addEventListener("click", () => {
  el.batchDialog.close();
  startBatch();
});
el.filter.addEventListener("change", () => { state.index = 0; loadItems(); });
let searchTimer;
el.search.addEventListener("input", () => {
  clearTimeout(searchTimer);
  searchTimer = setTimeout(() => { state.index = 0; loadItems(); }, 250);
});
document.addEventListener("keydown", event => {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
    event.preventDefault();
    saveHuman(true);
  }
});

initialize();
