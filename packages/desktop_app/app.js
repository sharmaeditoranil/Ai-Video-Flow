// AI Video Flow - Desktop Application Logic with Live Backend API

document.addEventListener("DOMContentLoaded", () => {
  // Navigation tabs
  const navItems = document.querySelectorAll(".nav-item");
  const tabPanes = document.querySelectorAll(".tab-pane");

  navItems.forEach((btn) => {
    btn.addEventListener("click", () => {
      const tabId = btn.getAttribute("data-tab");
      navItems.forEach((i) => i.classList.remove("active"));
      tabPanes.forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const targetPane = document.getElementById(`pane-${tabId}`);
      if (targetPane) targetPane.classList.add("active");
      if (tabId === "diagnostics") loadDiagnostics();
      if (tabId === "tools") loadTools();
    });
  });

  // Action Buttons & Elements
  const btnConnectAll = document.getElementById("btn-connect-all");
  const btnQuickConnect = document.getElementById("btn-quick-connect");
  const btnTestConn = document.getElementById("btn-test-connection");
  const btnRunWedding = document.getElementById("btn-run-wedding-ai");
  const weddingConsole = document.getElementById("wedding-console");
  const folderInput = document.getElementById("input-folder-path");
  const audioInput = document.getElementById("input-audio-path");
  const folderInfo = document.getElementById("folder-info");
  const progress = document.getElementById("wedding-progress");

  function appendLog(msg, type = "normal", box = weddingConsole, time = null) {
    if (!box) return;
    const line = document.createElement("div");
    line.className = `console-line ${type}`;
    line.textContent = `[${time || new Date().toLocaleTimeString()}] ${msg}`;
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  async function api(path, body) {
    const opts = body === undefined ? {} : {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    };
    const res = await fetch(path, opts);
    return res.json();
  }

  // Remember last used paths between launches
  const store = {
    get(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }
  };
  if (folderInput) folderInput.value = store.get("avf.folder");
  if (audioInput) audioInput.value = store.get("avf.audio");

  // Update Status Pills and Metrics
  function updateLiveUI(data) {
    if (!data) return;

    // DaVinci Resolve Status
    const resolveStatus = data.resolve || {};
    const resolveLabel = document.querySelector("#status-resolve .status-label");
    const resolveDot = document.querySelector("#status-resolve .status-dot");
    const editionBadge = document.getElementById("resolve-edition-badge");
    const metricProject = document.getElementById("metric-project");
    const metricTimelines = document.getElementById("metric-timelines");

    if (resolveStatus.resolve_running) {
      if (resolveLabel) resolveLabel.textContent = `DaVinci Resolve: Connected (${resolveStatus.version || "Studio/Free"})`;
      if (resolveDot) resolveDot.className = "status-dot dot-green";
      if (editionBadge) editionBadge.textContent = resolveStatus.edition || "Active";
      if (metricProject) metricProject.textContent = resolveStatus.current_project || "No project open";
      if (metricTimelines && resolveStatus.timeline_count !== undefined) metricTimelines.textContent = `${resolveStatus.timeline_count} Timelines`;
    } else {
      if (resolveLabel) resolveLabel.textContent = "DaVinci Resolve: Not Running";
      if (resolveDot) resolveDot.className = "status-dot dot-yellow";
      if (editionBadge) editionBadge.textContent = "Offline";
      if (metricProject) metricProject.textContent = "Open DaVinci Resolve";
    }

    // Adobe Premiere Pro Status
    const premiereStatus = data.premiere || {};
    const premiereLabel = document.querySelector("#status-premiere .status-label");
    const premiereDot = document.querySelector("#status-premiere .status-dot");
    const premiereBadge = document.getElementById("premiere-edition-badge");
    const metricPremiereVer = document.getElementById("metric-premiere-version");
    const metricPremiereStatus = document.getElementById("metric-premiere-status");

    if (premiereStatus.installed) {
      const verText = premiereStatus.version ? ` (${premiereStatus.version})` : "";
      if (premiereLabel) premiereLabel.textContent = premiereStatus.running ? `Premiere Pro: Running${verText}` : `Premiere Pro: Standby${verText}`;
      if (premiereDot) premiereDot.className = `status-dot ${premiereStatus.running ? "dot-green" : "dot-yellow"}`;
      if (premiereBadge) {
        premiereBadge.textContent = premiereStatus.running ? "Running" : "Installed";
        premiereBadge.className = `badge ${premiereStatus.running ? "badge-success" : "badge-premiere"}`;
      }
      if (metricPremiereVer) metricPremiereVer.textContent = premiereStatus.app_name || `Premiere Pro ${premiereStatus.version || ""}`;
      if (metricPremiereStatus) metricPremiereStatus.textContent = premiereStatus.running ? "🟢 Running & Active" : "🟡 Standby (Ready to launch)";
    } else {
      if (premiereLabel) premiereLabel.textContent = "Premiere Pro: Not Found";
      if (premiereDot) premiereDot.className = "status-dot dot-yellow";
      if (premiereBadge) {
        premiereBadge.textContent = "Not Installed";
        premiereBadge.className = "badge badge-warning";
      }
      if (metricPremiereVer) metricPremiereVer.textContent = "Not Detected";
      if (metricPremiereStatus) metricPremiereStatus.textContent = "Install Premiere Pro";
    }

    // Claude Desktop & MCP Status
    const claudeStatus = data.claude || {};
    const claudeLabel = document.querySelector("#status-claude .status-label");
    const claudeDot = document.querySelector("#status-claude .status-dot");
    const mcpLabel = document.querySelector("#status-mcp .status-label");
    const mcpDot = document.querySelector("#status-mcp .status-dot");
    const configured = claudeStatus.claude_desktop && claudeStatus.claude_desktop.configured;

    if (claudeLabel) claudeLabel.textContent = configured ? "Claude AI: Configured" : "Claude AI: Not Connected";
    if (claudeDot) claudeDot.className = `status-dot ${configured ? "dot-green" : "dot-yellow"}`;
    if (mcpLabel) mcpLabel.textContent = configured ? `MCP Server: Active${toolCount ? ` (${toolCount} Tools)` : ""}` : "MCP Server: Press CONNECT ALL";
    if (mcpDot) mcpDot.className = `status-dot ${configured ? "dot-green" : "dot-yellow"}`;
  }

  // Fetch Live Status from Backend
  async function fetchStatus() {
    try {
      updateLiveUI(await api("/api/status"));
    } catch (e) {
      console.log("Backend offline:", e);
    }
  }

  // Handle 1-Click "CONNECT ALL" Action
  async function handleConnectAll() {
    appendLog("Initiating 1-Click 'CONNECT ALL'...", "info");
    try {
      const result = await api("/api/connect", {});
      (result.steps || []).forEach((s) => appendLog(s));
      (result.guidance || []).forEach((g) => appendLog(g, "warning"));
      appendLog(result.success ? "✅ Claude Desktop MCP registered (ai-video-flow-resolve)" : "❌ Claude Desktop registration failed", result.success ? "info" : "error");
      await fetchStatus();
      alert((result.success ? "🎉 AI Video Flow - Setup Complete!\n\n" : "⚠️ Setup incomplete\n\n") +
            "1. Claude Desktop: " + (result.success ? "MCP Registered" : "Failed – see Diagnostics") + "\n" +
            "2. DaVinci Resolve: " + (result.davinci_resolve?.running ? "Connected" : "Open Resolve (External scripting = Local)") + "\n\n" +
            (result.success ? "Restart Claude Desktop, then speak in Hindi or English!" : ""));
    } catch (e) {
      appendLog("Backend not reachable. Close and re-open AI Video Flow.", "error");
    }
  }

  if (btnConnectAll) btnConnectAll.addEventListener("click", handleConnectAll);
  if (btnQuickConnect) btnQuickConnect.addEventListener("click", handleConnectAll);

  if (btnTestConn) {
    btnTestConn.addEventListener("click", async () => {
      appendLog("Pinging DaVinci Resolve...", "info");
      try {
        const status = await api("/api/test-resolve", {});
        appendLog(`Resolve Ping: Running=${status.resolve_running}, Project=${status.current_project || "None"}, Version=${status.version || "Unknown"}`,
                  status.resolve_running ? "info" : "warning");
        updateLiveUI({ resolve: status });
      } catch (e) {
        appendLog("Backend not reachable.", "error");
      }
    });
  }

  const btnLaunchPremiere = document.getElementById("btn-launch-premiere");
  if (btnLaunchPremiere) {
    btnLaunchPremiere.addEventListener("click", async () => {
      appendLog("Launching / focusing Adobe Premiere Pro...", "info");
      try {
        const res = await api("/api/premiere/launch", {});
        appendLog(res.message || (res.success ? "Premiere Pro launched" : res.error), res.success ? "info" : "error");
        await fetchStatus();
      } catch (e) {
        appendLog("Backend error launching Premiere.", "error");
      }
    });
  }

  // ------------------------------------------------------------------
  // Browse buttons (native OS dialogs via backend)
  // ------------------------------------------------------------------
  async function browseInto(input, kind, prompt, btn) {
    if (btn) btn.disabled = true;
    try {
      const res = await api("/api/browse", { kind, prompt });
      if (res.path) {
        input.value = res.path;
        input.dispatchEvent(new Event("change"));
        return res.path;
      }
      if (res.error) alert(res.error);
    } catch (e) {
      alert("Could not open the file dialog. Please paste the path manually.");
    } finally {
      if (btn) btn.disabled = false;
    }
    return null;
  }

  // ------------------------------------------------------------------
  // Footage folder loading
  // ------------------------------------------------------------------
  let loadedFolder = null;

  async function loadFolder() {
    const path = folderInput.value.trim();
    loadedFolder = null;
    if (!path) {
      folderInfo.className = "folder-info err";
      folderInfo.textContent = "Please select a footage folder first.";
      return false;
    }
    folderInfo.className = "folder-info";
    folderInfo.textContent = "Scanning folder...";
    try {
      const res = await api("/api/scan-folder", { path });
      if (!res.success) {
        folderInfo.className = "folder-info err";
        folderInfo.textContent = res.error;
        return false;
      }
      store.set("avf.folder", res.folder);
      folderInput.value = res.folder;
      folderInfo.className = res.video_count ? "folder-info ok" : "folder-info err";
      folderInfo.textContent = `✔ ${res.video_count} video clips, ${res.audio_count} audio files (${res.total_gb} GB) loaded.`;
      if (!res.video_count) {
        folderInfo.textContent = "No video files found in this folder (mp4, mov, mxf, mts, avi, mkv...).";
        return false;
      }
      const ul = document.createElement("ul");
      res.preview.forEach((name) => {
        const li = document.createElement("li");
        li.textContent = name;
        ul.appendChild(li);
      });
      if (res.video_count > res.preview.length) {
        const li = document.createElement("li");
        li.textContent = `...and ${res.video_count - res.preview.length} more`;
        ul.appendChild(li);
      }
      folderInfo.appendChild(ul);
      loadedFolder = res.folder;
      appendLog(`Footage folder loaded: ${res.folder} (${res.video_count} clips)`, "info");
      return true;
    } catch (e) {
      folderInfo.className = "folder-info err";
      folderInfo.textContent = "Backend not reachable. Close and re-open AI Video Flow.";
      return false;
    }
  }

  const btnBrowseFolder = document.getElementById("btn-browse-folder");
  const btnLoadFolder = document.getElementById("btn-load-folder");
  const btnBrowseAudio = document.getElementById("btn-browse-audio");

  if (btnBrowseFolder) {
    btnBrowseFolder.addEventListener("click", async () => {
      if (await browseInto(folderInput, "folder", "Select your raw footage folder", btnBrowseFolder)) loadFolder();
    });
  }
  if (btnLoadFolder) btnLoadFolder.addEventListener("click", loadFolder);
  if (folderInput) {
    folderInput.addEventListener("keydown", (e) => { if (e.key === "Enter") loadFolder(); });
    if (folderInput.value) loadFolder();
  }
  if (btnBrowseAudio) {
    btnBrowseAudio.addEventListener("click", () => browseInto(audioInput, "audio", "Select background music", btnBrowseAudio));
  }
  if (audioInput) audioInput.addEventListener("change", () => store.set("avf.audio", audioInput.value.trim()));

  const btnOpenOutput = document.getElementById("btn-open-output");
  if (btnOpenOutput) btnOpenOutput.addEventListener("click", () => api("/api/open-folder", {}));

  // ------------------------------------------------------------------
  // Background job polling
  // ------------------------------------------------------------------
  function pollJob(jobId, box, onProgress) {
    let shown = 0;
    return new Promise((resolve) => {
      const tick = async () => {
        try {
          const job = await api(`/api/job/${jobId}`);
          (job.logs || []).slice(shown).forEach((l) => appendLog(l.msg, l.level, box, l.t));
          shown = (job.logs || []).length;
          if (onProgress) onProgress(job.progress || 0);
          if (job.status !== "running") return resolve(job);
        } catch (e) { /* retry */ }
        setTimeout(tick, 800);
      };
      tick();
    });
  }

  if (btnRunWedding) {
    btnRunWedding.addEventListener("click", async () => {
      if (!loadedFolder || loadedFolder !== folderInput.value.trim()) {
        if (!(await loadFolder())) return;
      }
      const formats = [];
      if (document.getElementById("chk-teaser").checked) formats.push("teaser");
      if (document.getElementById("chk-highlight").checked) formats.push("highlight");
      if (document.getElementById("chk-fullfilm").checked) formats.push("full_film");
      if (!formats.length) return alert("Select at least one output sequence.");

      const targetNleInput = document.querySelector('input[name="target_nle"]:checked');
      const target_nle = targetNleInput ? targetNleInput.value : "resolve";

      store.set("avf.audio", audioInput.value.trim());
      btnRunWedding.disabled = true;
      progress.classList.add("active");
      const bar = progress.querySelector(".progress-bar");
      bar.style.width = "0%";
      try {
        const { job_id } = await api("/api/run-wedding", { folder: loadedFolder, audio: audioInput.value.trim(), formats, target_nle });
        await pollJob(job_id, weddingConsole, (p) => { bar.style.width = `${p}%`; });
        fetchStatus();
      } catch (e) {
        appendLog("Backend not reachable.", "error");
      } finally {
        btnRunWedding.disabled = false;
      }
    });
  }

  const btnOpenPremiereXml = document.getElementById("btn-open-premiere-xml");
  if (btnOpenPremiereXml) {
    btnOpenPremiereXml.addEventListener("click", async () => {
      try {
        appendLog("Launching Adobe Premiere Pro...", "info");
        const res = await api("/api/premiere/launch", {});
        appendLog(res.message || "Premiere Pro ready.", "info");
        await api("/api/open-folder", { path: "" });
      } catch (e) {
        appendLog("Error launching Premiere: " + e, "error");
      }
    });
  }

  // ------------------------------------------------------------------
  // All AI Tools tab (auto-generated forms from MCP tool schemas)
  // ------------------------------------------------------------------
  let tools = null;
  let toolCount = 0;
  let currentTool = null;
  const toolList = document.getElementById("tool-list");
  const toolForm = document.getElementById("tool-form");
  const toolOutput = document.getElementById("tool-output");
  const btnRunTool = document.getElementById("btn-run-tool");
  const toolSearch = document.getElementById("tool-search");

  const GROUPS = [["resolve_", "DaVinci Resolve"], ["ai_", "AI Engine"], ["hyperframes_", "HyperFrames"]];

  function browseKindFor(key) {
    if (/folder|_dir$|output_dir/.test(key)) return "folder";
    if (/audio|music/.test(key)) return "audio";
    if (/image|reference/.test(key)) return "image";
    if (/drx|lut|cube/.test(key)) return "file";
    if (/path|clip|video/.test(key)) return "video";
    return null;
  }

  function renderToolList() {
    const q = (toolSearch.value || "").toLowerCase();
    toolList.innerHTML = "";
    GROUPS.forEach(([prefix, label]) => {
      const items = tools.filter((t) => t.name.startsWith(prefix) &&
        (!q || t.name.toLowerCase().includes(q) || (t.description || "").toLowerCase().includes(q)));
      if (!items.length) return;
      const h = document.createElement("div");
      h.className = "tool-group";
      h.textContent = `${label} (${items.length})`;
      toolList.appendChild(h);
      items.forEach((t) => {
        const b = document.createElement("button");
        b.className = "tool-item" + (currentTool === t ? " active" : "");
        b.textContent = t.name;
        b.title = t.description || "";
        b.addEventListener("click", () => selectTool(t));
        toolList.appendChild(b);
      });
    });
  }

  function selectTool(t) {
    currentTool = t;
    renderToolList();
    document.getElementById("tool-title").textContent = t.name;
    document.getElementById("tool-desc").textContent = t.description || "";
    toolForm.innerHTML = "";
    const schema = t.inputSchema || {};
    const props = schema.properties || {};
    const required = schema.required || [];

    Object.entries(props).forEach(([key, spec]) => {
      const group = document.createElement("div");
      group.className = "form-group";
      const label = document.createElement("label");
      label.textContent = key + (required.includes(key) ? " *" : "") + (spec.description ? ` — ${spec.description}` : "");
      group.appendChild(label);

      let input;
      if (spec.enum) {
        input = document.createElement("select");
        spec.enum.forEach((v) => {
          const o = document.createElement("option");
          o.value = v;
          o.textContent = v;
          input.appendChild(o);
        });
        if (spec.default !== undefined) input.value = spec.default;
      } else if (spec.type === "boolean") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = spec.default !== undefined ? !!spec.default : false;
      } else if (spec.type === "array" || spec.type === "object" || key === "html_content") {
        input = document.createElement("textarea");
        input.rows = spec.type === "array" ? 4 : 6;
        input.placeholder = spec.type === "array" ? "One value per line" : (spec.type === "object" ? "JSON" : "");
        if (spec.default !== undefined) input.value = spec.type === "object" ? JSON.stringify(spec.default) : spec.default;
      } else {
        input = document.createElement("input");
        input.type = spec.type === "number" || spec.type === "integer" ? "number" : "text";
        if (input.type === "number") input.step = "any";
        if (spec.default !== undefined) input.value = spec.default;
      }
      input.name = key;
      input.dataset.type = spec.enum ? "string" : (spec.type || "string");

      const kind = browseKindFor(key);
      if (kind && input.tagName !== "SELECT" && input.type !== "checkbox" && input.type !== "number") {
        const row = document.createElement("div");
        row.className = "input-action";
        row.appendChild(input);
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-secondary";
        b.textContent = "Browse";
        b.addEventListener("click", async () => {
          const res = await api("/api/browse", { kind, prompt: `Select ${key}` });
          if (!res.path) return;
          if (input.tagName === "TEXTAREA" && spec.type === "array") {
            input.value = (input.value.trim() ? input.value.trim() + "\n" : "") + res.path;
          } else {
            input.value = res.path;
          }
        });
        row.appendChild(b);
        group.appendChild(row);
      } else {
        group.appendChild(input);
      }
      toolForm.appendChild(group);
    });

    if (!Object.keys(props).length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "This tool needs no inputs.";
      toolForm.appendChild(p);
    }
    btnRunTool.disabled = false;
    toolOutput.innerHTML = "";
  }

  function collectArgs() {
    const args = {};
    for (const el of toolForm.elements) {
      if (!el.name) continue;
      const type = el.dataset.type;
      if (type === "boolean") { args[el.name] = el.checked; continue; }
      const raw = el.value.trim();
      if (raw === "") continue;
      if (type === "number" || type === "integer") args[el.name] = Number(raw);
      else if (type === "array") {
        try { args[el.name] = JSON.parse(raw); } catch (e) { args[el.name] = raw.split("\n").map((s) => s.trim()).filter(Boolean); }
      } else if (type === "object") {
        try { args[el.name] = JSON.parse(raw); } catch (e) { throw new Error(`${el.name} must be valid JSON`); }
      } else args[el.name] = raw;
    }
    const missing = ((currentTool.inputSchema || {}).required || []).filter((k) => args[k] === undefined);
    if (missing.length) throw new Error(`Please fill: ${missing.join(", ")}`);
    return args;
  }

  async function loadTools() {
    if (tools) return;
    try {
      const res = await api("/api/tools");
      if (!res.success) {
        toolList.innerHTML = `<div class="muted">${res.error}</div>`;
        return;
      }
      // Deduplicate tools registered twice in the server list
      const seen = new Set();
      tools = res.tools.filter((t) => !seen.has(t.name) && seen.add(t.name));
      toolCount = tools.length;
      const metricTools = document.getElementById("metric-tools");
      if (metricTools) metricTools.textContent = `${toolCount} Tools Active`;
      renderToolList();
    } catch (e) {
      toolList.innerHTML = '<div class="muted">Backend not reachable.</div>';
    }
  }

  if (toolSearch) toolSearch.addEventListener("input", () => tools && renderToolList());

  if (btnRunTool) {
    btnRunTool.addEventListener("click", async () => {
      if (!currentTool) return;
      let args;
      try { args = collectArgs(); } catch (e) { return alert(e.message); }
      btnRunTool.disabled = true;
      toolOutput.innerHTML = "";
      try {
        const { job_id } = await api("/api/tool", { name: currentTool.name, arguments: args });
        const job = await pollJob(job_id, toolOutput);
        const pre = document.createElement("pre");
        pre.className = "console-line";
        pre.style.whiteSpace = "pre-wrap";
        pre.textContent = JSON.stringify(job.result ?? job.error, null, 2);
        toolOutput.appendChild(pre);
        fetchStatus();
      } catch (e) {
        appendLog("Backend not reachable.", "error", toolOutput);
      } finally {
        btnRunTool.disabled = false;
      }
    });
  }

  // ------------------------------------------------------------------
  // Diagnostics tab
  // ------------------------------------------------------------------
  const diagGrid = document.getElementById("diag-grid");

  function diagCard(title, obj) {
    const card = document.createElement("div");
    card.className = "panel-box";
    const h = document.createElement("h3");
    h.textContent = title;
    const pre = document.createElement("pre");
    pre.textContent = JSON.stringify(obj, null, 2);
    card.append(h, pre);
    return card;
  }

  async function loadDiagnostics() {
    if (!diagGrid) return;
    diagGrid.innerHTML = '<div class="muted">Loading diagnostics...</div>';
    try {
      const d = await api("/api/diagnostics");
      diagGrid.innerHTML = "";
      const missing = Object.entries(d.environment.dependencies).filter(([, v]) => !v).map(([k]) => k);
      if (missing.length) {
        diagGrid.appendChild(diagCard("⚠️ Missing AI libraries", {
          missing,
          fix: "Run AI-Video-Flow-Setup again (it installs requirements.txt)"
        }));
      }
      diagGrid.appendChild(diagCard("Environment", d.environment));
      diagGrid.appendChild(diagCard("DaVinci Resolve", d.resolve));
      diagGrid.appendChild(diagCard("Claude Desktop", d.claude));
      diagGrid.appendChild(diagCard("HyperFrames", d.hyperframes));
      updateLiveUI(d);
    } catch (e) {
      diagGrid.innerHTML = '<div class="muted">Backend not reachable.</div>';
    }
  }

  const btnRefreshDiag = document.getElementById("btn-refresh-diag");
  const btnDiagConnect = document.getElementById("btn-diag-connect");
  if (btnRefreshDiag) btnRefreshDiag.addEventListener("click", loadDiagnostics);
  if (btnDiagConnect) btnDiagConnect.addEventListener("click", async () => { await handleConnectAll(); loadDiagnostics(); });

  // ------------------------------------------------------------------
  // Sponsored ads: open landing page in the system browser
  // ------------------------------------------------------------------
  document.querySelectorAll(".qa-ad").forEach((ad) => {
    ad.addEventListener("click", async (e) => {
      e.preventDefault();
      const url = ad.getAttribute("href");
      try {
        const res = await api("/api/open-url", { url });
        if (res.success) return;
      } catch (err) { /* fall through */ }
      window.open(url, "_blank");
    });
  });

  // Poll status initially and every 5 seconds
  loadTools();
  fetchStatus();
  setInterval(fetchStatus, 5000);
});
