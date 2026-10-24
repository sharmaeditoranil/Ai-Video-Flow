// AI Video Flow - dashboard logic

(() => {
  // ------------------------------------------------------------------
  // Session token: the launcher puts it in the URL fragment (#t=...)
  // ------------------------------------------------------------------
  const TOKEN_KEY = "avf.session";
  const START_TAB = (location.hash.match(/tab=(\w+)/) || [])[1];
  const START_MODE = (location.hash.match(/mode=(\w+)/) || [])[1];
  function readToken() {
    const m = location.hash.match(/t=([\w-]+)/);
    if (m) {
      try { sessionStorage.setItem(TOKEN_KEY, m[1]); } catch (e) { /* ignore */ }
      history.replaceState(null, "", location.pathname);
      return m[1];
    }
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
  }
  const TOKEN = readToken();

  async function api(path, body) {
    const opts = { headers: { "X-AVF-Token": TOKEN } };
    if (body !== undefined) {
      opts.method = "POST";
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    if (res.status === 403) throw new Error("This window is not authorised. Close it and open AI Video Flow again.");
    return res.json();
  }

  const $ = (id) => document.getElementById(id);
  const store = {
    get(k) { try { return localStorage.getItem(k) || ""; } catch (e) { return ""; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } },
  };
  const fmtDate = (ts) => ts ? new Date(ts * 1000).toLocaleDateString("en-IN", { day: "2-digit", month: "short", year: "numeric" }) : "—";

  // ------------------------------------------------------------------
  // Navigation
  // ------------------------------------------------------------------
  const PAGES = {
    home: ["Home", "Your editors, footage and AI in one place"],
    studio: ["AI Studio", "Turn a folder of raw clips into ready timelines"],
    assistant: ["AI Assistant", "Tell Smart AI what to do in your editor — Hindi, Hinglish or English"],
    tools: ["Tools", "Run any editing function by hand"],
    profile: ["My Profile", "Your account, plan and license"],
    pricing: ["Pricing", "AI Editing and Smart AI Editing plans"],
    settings: ["Settings", "Claude Desktop connection and diagnostics"],
  };

  function go(tab) {
    document.querySelectorAll(".nav-item").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${tab}`));
    $("page-title").textContent = PAGES[tab][0];
    $("page-sub").textContent = PAGES[tab][1];
    if (tab === "settings") loadDiagnostics();
    if (tab === "tools") loadTools();
    if (tab === "pricing") loadPlans();
  }
  document.querySelectorAll(".nav-item").forEach((b) => b.addEventListener("click", () => go(b.dataset.tab)));
  document.addEventListener("click", (e) => {
    const g = e.target.closest("[data-goto]");
    if (g) go(g.dataset.goto);
  });

  // Ads and external links open in the normal browser
  document.querySelectorAll(".qa-ad").forEach((a) => a.addEventListener("click", (e) => {
    e.preventDefault();
    openUrl(a.getAttribute("href"));
  }));
  async function openUrl(url) {
    try { if ((await api("/api/open-url", { url })).success) return; } catch (e) { /* fall through */ }
    window.open(url, "_blank");
  }

  // ------------------------------------------------------------------
  // Logging helpers
  // ------------------------------------------------------------------
  function log(box, msg, level = "normal", time = null) {
    const line = document.createElement("div");
    line.className = `line ${level}`;
    const t = document.createElement("span");
    t.className = "t";
    t.textContent = time || new Date().toLocaleTimeString("en-IN", { hour12: false });
    line.append(t, document.createTextNode(msg));
    box.appendChild(line);
    box.scrollTop = box.scrollHeight;
  }

  function pollJob(jobId, onLog, onProgress) {
    let shown = 0;
    return new Promise((resolve) => {
      const tick = async () => {
        try {
          const job = await api(`/api/job/${jobId}`);
          (job.logs || []).slice(shown).forEach((l) => onLog(l));
          shown = (job.logs || []).length;
          if (onProgress) onProgress(job.progress || 0);
          if (job.status !== "running") return resolve(job);
        } catch (e) { /* retry */ }
        setTimeout(tick, 700);
      };
      tick();
    });
  }

  // ------------------------------------------------------------------
  // Status (top bar, home cards, settings)
  // ------------------------------------------------------------------
  function setStatus(id, state, title) {
    const el = $(id);
    el.className = `status ${state}`;
    el.title = title;
  }
  function setBadge(id, text, state) {
    const el = $(id);
    el.textContent = text;
    el.className = `badge ${state || ""}`;
  }

  function renderStatus(data) {
    const r = data.resolve || {};
    const p = data.premiere || {};
    const c = (data.claude || {}).claude_desktop || {};

    setStatus("st-resolve", r.resolve_running ? "ok" : "warn", r.resolve_running ? `DaVinci Resolve ${r.version || ""} connected` : "DaVinci Resolve is not running");
    setBadge("home-resolve-badge", r.resolve_running ? "Connected" : "Not running", r.resolve_running ? "ok" : "warn");
    $("home-resolve-project").textContent = r.resolve_running ? (r.current_project || "No project open") : "Open DaVinci Resolve";
    $("home-resolve-timelines").textContent = r.resolve_running ? String(r.timeline_count ?? 0) : "—";
    $("set-resolve").textContent = r.resolve_running ? `Connected ${r.version || ""}` : "Not running";

    const pText = p.installed ? (p.running ? "Running" : "Installed") : "Not installed";
    setStatus("st-premiere", p.running ? "ok" : (p.installed ? "warn" : ""), `Premiere Pro: ${pText}`);
    setBadge("home-premiere-badge", pText, p.running ? "ok" : "warn");
    $("home-premiere-version").textContent = p.installed ? (p.app_name || p.version || "Installed") : "—";
    $("set-premiere").textContent = pText;

    setStatus("st-claude", c.configured ? "ok" : "warn", c.configured ? "Claude Desktop is connected" : "Press Connect to link Claude Desktop");
    setBadge("home-claude-badge", c.configured ? "Connected" : "Not connected", c.configured ? "ok" : "warn");
    $("set-claude").textContent = c.configured ? "Connected" : "Not connected";
  }

  async function fetchStatus() {
    try { renderStatus(await api("/api/status")); } catch (e) { /* backend restarting */ }
  }

  async function connectAll() {
    const btns = [$("btn-connect-2")];
    btns.forEach((b) => b.disabled = true);
    try {
      const r = await api("/api/connect", {});
      await fetchStatus();
      const lines = [r.success ? "Claude Desktop is connected to AI Video Flow." : "Could not update the Claude Desktop settings."];
      (r.guidance || []).forEach((g) => lines.push("• " + g));
      alert(lines.join("\n"));
    } catch (e) {
      alert(e.message);
    } finally {
      btns.forEach((b) => b.disabled = false);
    }
  }
  $("btn-connect-2").addEventListener("click", connectAll);
  $("btn-test-resolve").addEventListener("click", async () => {
    const r = await api("/api/test-resolve", {});
    renderStatus({ resolve: r });
    alert(r.resolve_running ? `DaVinci Resolve connected · Project: ${r.current_project || "none"}` :
      "DaVinci Resolve is not reachable. Open it and set Preferences → System → General → External scripting using: Local.");
  });

  // ------------------------------------------------------------------
  // License
  // ------------------------------------------------------------------
  let license = { is_premium: false };

  function renderLicense(l) {
    license = l;
    const name = l.is_premium && l.client_name ? l.client_name : "Guest";
    const initial = (name.trim()[0] || "?").toUpperCase();
    $("chip-name").textContent = name;
    $("plan-chip").textContent = "My Profile";
    const creditsText = !l.is_premium ? "" : (l.plan_type === "local" ? "Unlimited editing" : (l.unlimited ? "Unlimited Smart AI" : `${l.credits_remaining} Smart AI weddings left`));
    $("chip-credits").textContent = l.is_premium ? creditsText : (l.free_left_today === 0 ? "Free · today's project used" : "Free · 1 project per day");
    $("chip-avatar").textContent = initial;
    document.querySelector(".side-profile").classList.toggle("premium", !!l.is_premium);
    $("pf-avatar").textContent = initial;
    $("pf-name").textContent = name;
    $("pf-email").textContent = l.is_premium ? (l.client_email || "—") : "Activate a license to see your account";
    $("pf-plan").textContent = l.is_premium ? l.plan_name : "Free Edition";
    $("pf-plan").classList.toggle("premium", !!l.is_premium);
    $("lic-credits").textContent = !l.ai_enabled ? "—" : (l.unlimited ? "Unlimited" : String(l.credits_remaining));
    $("lic-expiry").textContent = l.is_premium ? fmtDate(l.expires_at) : "—";
    $("lic-machine").textContent = l.machine_id || "…";
    if (l.license_key && !$("lic-key").value) $("lic-key").value = l.license_key;
    const err = $("lic-error");
    err.hidden = !l.error;
    err.textContent = l.error || "";

    $("chat-lock").hidden = !!l.has_credits;
    const review = $("chk-ai-review");
    review.disabled = !l.has_credits;
    if (!l.has_credits) review.checked = false;
    $("ai-review-note").textContent = l.has_credits
      ? `Smart AI looks at every candidate shot and picks the most emotional moments. Uses 1 wedding credit (${l.unlimited ? "unlimited plan" : l.credits_remaining + " left"}).`
      : (l.ai_enabled ? "No Smart AI weddings left — add a Smart AI pack to continue."
        : "Needs a Smart AI pack (from ₹999). See Pricing.");

    const quota = $("quota");
    if (l.is_premium) {
      quota.hidden = true;
    } else {
      quota.hidden = false;
      quota.innerHTML = "";
      const left = l.free_left_today ?? 1;
      const txt = document.createElement("span");
      txt.textContent = left > 0 ? "Free plan · 1 project per day (today's project is still available)." : "Today's free project is used. It resets tomorrow.";
      const go = document.createElement("button");
      go.className = "link";
      go.dataset.goto = "pricing";
      go.textContent = "Go unlimited from ₹399/month →";
      quota.append(txt, " ", go);
    }
  }

  async function fetchLicense() {
    try { renderLicense(await api("/api/license/status")); } catch (e) { /* ignore */ }
  }

  $("btn-activate").addEventListener("click", async () => {
    const key = $("lic-key").value.trim();
    if (!key) return alert("Paste your license key first.");
    $("btn-activate").disabled = true;
    try {
      const r = await api("/api/license/activate", { license_key: key });
      if (r.success) {
        await fetchLicense();
        alert(`License activated on this computer: ${r.status.plan_name}.`);
      } else {
        alert(r.error || "Activation failed.");
      }
    } finally {
      $("btn-activate").disabled = false;
    }
  });
  $("btn-refresh-lic").addEventListener("click", async () => {
    const r = await api("/api/license/refresh", {});
    await fetchLicense();
    if (!r.success && r.error) alert(r.error);
  });
  $("btn-remove-lic").addEventListener("click", async () => {
    if (!confirm("Remove the license from this computer?")) return;
    const r = await api("/api/license/deactivate", {});
    $("lic-key").value = "";
    await fetchLicense();
    alert(r.message);
  });
  $("btn-copy-machine").addEventListener("click", () => {
    navigator.clipboard.writeText($("lic-machine").textContent);
    $("btn-copy-machine").textContent = "Copied";
    setTimeout(() => $("btn-copy-machine").textContent = "Copy", 1500);
  });
  // ------------------------------------------------------------------
  // Pricing
  // ------------------------------------------------------------------
  function planCard(p, buyUrl) {
    const price = Number(p.price_inr), mrp = Number(p.mrp_inr || 0);
    const save = mrp > price ? Math.round(100 - (price * 100) / mrp) : 0;
    const local = p.kind === "local";
    let unit, perks;
    if (local) {
      unit = p.validity_days >= 300 ? `₹${Math.round(price / 12)}/month, billed yearly` : "per month";
      perks = ["No daily limit — unlimited projects", "All workflows & editors", `${p.validity_days} days · 1 computer`];
    } else if (+p.unlimited) {
      unit = "Unlimited Smart AI weddings";
      perks = ["Unlimited Smart AI shot review", "Smart AI Assistant", "Unlimited editing · 1 year"];
    } else {
      unit = `₹${Math.round(price / Math.max(1, p.credits))} per wedding`;
      perks = [`${p.credits} Smart AI weddings`, "Smart AI Assistant", `Unlimited editing · ${p.validity_days} days`];
    }
    const card = document.createElement("div");
    card.className = "plan card" + (p.badge ? " popular" : "");
    const tag = p.badge || (save ? `Save ${save}%` : "");
    if (tag) { const t = document.createElement("span"); t.className = "plan-tag"; t.textContent = tag; card.appendChild(t); }
    const h = document.createElement("h3"); h.textContent = p.name; card.appendChild(h);
    const pr = document.createElement("div"); pr.className = "price"; pr.textContent = `₹${price.toLocaleString("en-IN")}`;
    if (save) { const st = document.createElement("s"); st.textContent = `₹${mrp.toLocaleString("en-IN")}`; pr.appendChild(st); }
    card.appendChild(pr);
    const per = document.createElement("div"); per.className = "per"; per.textContent = unit + (save && p.badge ? ` · save ${save}%` : ""); card.appendChild(per);
    const ul = document.createElement("ul"); ul.className = "perks";
    perks.forEach((x) => { const li = document.createElement("li"); li.textContent = x; ul.appendChild(li); });
    card.appendChild(ul);
    const current = license.is_premium && license.plan_name === p.name;
    const b = document.createElement("button");
    b.className = "btn btn-block " + (p.badge ? "btn-gold" : "btn-primary");
    b.textContent = current ? "Renew" : "Buy now";
    b.addEventListener("click", () => openUrl(`${buyUrl}?plan=${p.id}`));
    card.appendChild(b);
    return card;
  }

  function showPriceGroup(g) {
    document.querySelectorAll("#price-switch button").forEach((b) => b.classList.toggle("active", b.dataset.group === g));
    $("group-local").hidden = g !== "local";
    $("group-ai").hidden = g !== "ai";
  }
  document.querySelectorAll("#price-switch button").forEach((b) => b.addEventListener("click", () => showPriceGroup(b.dataset.group)));

  async function loadPlans() {
    const localBox = $("plans-local"), aiBox = $("plans-ai"), note = $("plans-note");
    let data;
    try { data = await api("/api/plans"); } catch (e) { data = { success: false, plans: [] }; }
    const buyUrl = data.buy_url || license.buy_url || "https://flow.aiphotoflow.in/index.php/buy";
    localBox.querySelectorAll(".plan:not(.free-plan)").forEach((c) => c.remove());
    aiBox.innerHTML = "";
    const freeBtn = localBox.querySelector(".free-plan button");
    freeBtn.textContent = license.is_premium ? "Free plan" : "Your current plan";
    if (!data.success || !data.plans.length) {
      aiBox.innerHTML = '<div class="muted">Plans could not be loaded. Check your internet connection.</div>';
      note.textContent = "";
      return;
    }
    data.plans.forEach((p) => (p.kind === "local" ? localBox : aiBox).appendChild(planCard(p, buyUrl)));
    note.textContent = data.payments_enabled ? "After paying, paste the license key in My Profile → License and press Activate."
      : "Online payment is opening soon — contact us on WhatsApp to buy a plan.";
  }

  // ------------------------------------------------------------------
  // AI Studio
  // ------------------------------------------------------------------
  const OUTPUTS = {
    wedding: [["teaser", "01 Teaser — 60 s"], ["highlight", "02 Cinematic Highlight — about 4 min"], ["full_film", "03 Full Wedding Film — chronological"]],
    reels: [["teaser", "Instagram Reel — 45 s, 9:16"], ["highlight", "Shorts Teaser — 30 s, 9:16"]],
    youtube: [["highlight", "Main edit — about 6 min"], ["full_film", "Shorts highlights — 60 s, 9:16"]],
    music_video: [["highlight", "Music video cut — about 3 min, on the beat"], ["full_film", "Mood montage — 60 s"]],
    podcast: [["clean", "01 Clean edit — silences and dead air removed"], ["shorts", "3 vertical shorts — the liveliest 45 s moments, 9:16"]],
    tutorial: [["clean", "01 Clean edit — pauses and dead air removed"], ["chapters", "YouTube chapters file + chapter markers"]],
  };
  const LONG_MODES = ["podcast", "tutorial"];
  let cutLevel = store.get("avf.cut") || "normal";
  let mode = START_MODE || store.get("avf.mode") || "wedding";
  let nle = store.get("avf.nle") || "resolve";
  let loadedFolder = null;
  const consoleBox = $("console");

  function renderOutputs() {
    $("outputs").innerHTML = "";
    OUTPUTS[mode].forEach(([key, label]) => {
      const l = document.createElement("label");
      const cb = document.createElement("input");
      cb.type = "checkbox";
      cb.checked = true;
      cb.value = key;
      l.append(cb, document.createTextNode(label));
      $("outputs").appendChild(l);
    });
  }

  function setMode(m) {
    mode = OUTPUTS[m] ? m : "wedding";
    store.set("avf.mode", mode);
    document.querySelectorAll(".mode").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    const long = LONG_MODES.includes(mode);
    document.querySelectorAll(".short-only").forEach((el) => el.hidden = long);
    document.querySelectorAll(".long-only").forEach((el) => el.hidden = !long);
    $("lbl-folder").textContent = long ? "Folder with the long recording(s)" : "Raw footage folder";
    $("step2-title").textContent = long ? "Recording" : "Footage & music";
    $("in-folder").placeholder = long ? "/Volumes/Drive/Podcast_Episode_12" : "/Volumes/Drive/Wedding_Rohit_Priya";
    if (!loadedFolder) $("folder-info").textContent = long
      ? "Put the full podcast / class recording here. Several files are joined in name order."
      : "Choose the folder with your raw clips (subfolders are included).";
    renderOutputs();
  }
  document.querySelectorAll(".mode").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));
  setMode(mode);

  function setCut(v) {
    cutLevel = v === "tight" ? "tight" : "normal";
    store.set("avf.cut", cutLevel);
    document.querySelectorAll("#cut-level button").forEach((b) => b.classList.toggle("active", b.dataset.cut === cutLevel));
  }
  document.querySelectorAll("#cut-level button").forEach((b) => b.addEventListener("click", () => setCut(b.dataset.cut)));
  setCut(cutLevel);

  function setNle(v) {
    nle = v;
    store.set("avf.nle", v);
    document.querySelectorAll("#nle button").forEach((b) => b.classList.toggle("active", b.dataset.nle === v));
  }
  document.querySelectorAll("#nle button").forEach((b) => b.addEventListener("click", () => setNle(b.dataset.nle)));
  setNle(nle);

  async function browseInto(input, kind, prompt) {
    const r = await api("/api/browse", { kind, prompt });
    if (r.path) { input.value = r.path; return r.path; }
    if (r.error) alert(r.error);
    return null;
  }

  async function loadFolder() {
    const path = $("in-folder").value.trim();
    const info = $("folder-info");
    loadedFolder = null;
    if (!path) { info.className = "hint error"; info.textContent = "Choose your footage folder first."; return false; }
    info.className = "hint";
    info.textContent = "Scanning…";
    const r = await api("/api/scan-folder", { path });
    if (!r.success) { info.className = "hint error"; info.textContent = r.error; return false; }
    if (!r.video_count) { info.className = "hint error"; info.textContent = "No video files found in this folder (mp4, mov, mxf, mts, avi, mkv…)."; return false; }
    $("in-folder").value = r.folder;
    store.set("avf.folder", r.folder);
    info.className = "hint ok";
    info.textContent = `✓ ${r.video_count} video clips · ${r.total_gb} GB${r.audio_count ? ` · ${r.audio_count} audio files` : ""}`;
    loadedFolder = r.folder;
    return true;
  }

  $("in-folder").value = store.get("avf.folder");
  $("in-audio").value = store.get("avf.audio");
  $("in-ref").value = store.get("avf.ref");
  if ($("in-folder").value) loadFolder();

  $("btn-browse-folder").addEventListener("click", async () => {
    if (await browseInto($("in-folder"), "folder", "Select your raw footage folder")) loadFolder();
  });
  $("btn-load-folder").addEventListener("click", loadFolder);
  $("in-folder").addEventListener("keydown", (e) => { if (e.key === "Enter") loadFolder(); });
  $("btn-browse-audio").addEventListener("click", () => browseInto($("in-audio"), "audio", "Select the song"));
  $("btn-browse-ref").addEventListener("click", () => browseInto($("in-ref"), "video", "Select a reference video"));

  $("btn-run").addEventListener("click", async () => {
    if (!loadedFolder || loadedFolder !== $("in-folder").value.trim()) {
      if (!(await loadFolder())) return;
    }
    const formats = [...$("outputs").querySelectorAll("input:checked")].map((c) => c.value);
    if (!formats.length) return alert("Tick at least one timeline.");
    const audio = $("in-audio").value.trim();
    const ref = $("in-ref").value.trim();
    store.set("avf.audio", audio);
    store.set("avf.ref", ref);

    const btn = $("btn-run");
    const prog = $("progress");
    const bar = prog.querySelector(".bar");
    btn.disabled = true;
    btn.textContent = "Working…";
    prog.classList.add("active");
    bar.style.width = "0%";
    consoleBox.innerHTML = "";
    try {
      const started = await api("/api/run-wedding", {
        folder: loadedFolder, audio, formats, target_nle: nle, mode, ref_video: ref,
        instructions: $("in-notes").value.trim(),
        tier: $("chk-ai-review").checked && !LONG_MODES.includes(mode) ? "premium" : "free",
        cut_level: cutLevel,
      });
      if (!started.success) {
        log(consoleBox, started.error, "warning");
        if (started.limit && confirm(started.error + "\n\nOpen the plans now?")) go("pricing");
        return;
      }
      const job_id = started.job_id;
      const job = await pollJob(job_id, (l) => log(consoleBox, l.msg, l.level, l.t), (p) => bar.style.width = `${p}%`);
      if (job.status === "error") log(consoleBox, "Stopped: " + (job.error || "unknown error"), "error");
      fetchStatus();
      fetchLicense();
    } catch (e) {
      log(consoleBox, e.message, "error");
    } finally {
      btn.disabled = false;
      btn.textContent = "Build timelines";
    }
  });

  $("btn-open-output").addEventListener("click", () => api("/api/open-folder", {}));

  $("btn-send-again").addEventListener("click", async () => {
    const btn = $("btn-send-again");
    btn.disabled = true;
    try {
      if (nle === "resolve" || nle === "both") {
        log(consoleBox, "Sending the last edit plan to DaVinci Resolve…", "info");
        const r = await api("/api/resolve/send", {});
        log(consoleBox, r.success ? r.message : r.error, r.success ? "info" : "warning");
      }
      if (nle === "edius" || nle === "both") {
        const r = await api("/api/edius/export", {});
        log(consoleBox, r.success ? `${r.message} In EDIUS: File → Import Project → FCP XML.` : r.error, r.success ? "info" : "warning");
        if (r.success) api("/api/open-folder", { path: r.edius_dir || "" });
      }
      if (nle === "premiere" || nle === "both") {
        const r = await api("/api/premiere/launch", {});
        log(consoleBox, (r.message || r.error || "Premiere Pro opened.") + " Import the XML from the output folder.", r.success ? "info" : "warning");
      }
    } catch (e) {
      log(consoleBox, e.message, "error");
    } finally {
      btn.disabled = false;
    }
  });

  // ------------------------------------------------------------------
  // AI Assistant
  // ------------------------------------------------------------------
  const chatLog = $("chat-log");
  function addMsg(text, cls) {
    const d = document.createElement("div");
    d.className = `msg ${cls}`;
    d.textContent = text;
    chatLog.appendChild(d);
    chatLog.scrollTop = chatLog.scrollHeight;
    return d;
  }

  async function sendChat(text) {
    text = text.trim();
    if (!text) return;
    $("chat-text").value = "";
    addMsg(text, "user");
    $("chat-send").disabled = true;
    const thinking = addMsg("Thinking…", "tool");
    try {
      const r = await api("/api/assistant/send", { text });
      if (!r.success) throw new Error(r.error);
      const job = await pollJob(r.job_id, (l) => addMsg("⚙ " + l.msg, "tool"));
      thinking.remove();
      if (job.status === "error") addMsg(job.error || "Something went wrong.", "err");
      else addMsg(job.result.reply, "bot");
      fetchStatus();
    } catch (e) {
      thinking.remove();
      addMsg(e.message, "err");
    } finally {
      $("chat-send").disabled = false;
      $("chat-text").focus();
    }
  }

  $("chat-form").addEventListener("submit", (e) => { e.preventDefault(); sendChat($("chat-text").value); });
  $("chat-text").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChat($("chat-text").value); }
  });
  document.querySelectorAll(".chip").forEach((c) => c.addEventListener("click", () => sendChat(c.textContent)));
  $("chat-reset").addEventListener("click", async () => {
    await api("/api/assistant/reset", {});
    chatLog.innerHTML = "";
    addMsg("New conversation. Kya karna hai?", "bot");
  });

  // ------------------------------------------------------------------
  // Tools (forms generated from the tool schemas)
  // ------------------------------------------------------------------
  let tools = null;
  let currentTool = null;
  const GROUPS = [["resolve_", "DaVinci Resolve"], ["premiere_", "Premiere Pro"], ["edius_", "EDIUS"], ["ai_", "AI engine"], ["hyperframes_", "Titles & motion"]];

  function browseKindFor(key) {
    if (/folder|_dir$/.test(key)) return "folder";
    if (/audio|music/.test(key)) return "audio";
    if (/image|reference/.test(key)) return "image";
    if (/drx|lut|cube|xml/.test(key)) return "file";
    if (/path|clip|video/.test(key)) return "video";
    return null;
  }

  function renderToolList() {
    const q = $("tool-search").value.toLowerCase();
    const list = $("tool-list");
    list.innerHTML = "";
    const groups = GROUPS.map(([prefix, label]) => [label, tools.filter((t) => t.name.startsWith(prefix))]);
    const other = tools.filter((t) => !GROUPS.some(([p]) => t.name.startsWith(p)));
    if (other.length) groups.push(["Other", other]);
    groups.forEach(([label, items]) => {
      items = items.filter((t) => !q || t.name.includes(q) || (t.description || "").toLowerCase().includes(q));
      if (!items.length) return;
      const h = document.createElement("div");
      h.className = "tool-group";
      h.textContent = `${label} · ${items.length}`;
      list.appendChild(h);
      items.forEach((t) => {
        const b = document.createElement("button");
        b.className = "tool-item" + (currentTool === t ? " active" : "");
        b.textContent = t.name;
        b.title = t.description || "";
        b.addEventListener("click", () => selectTool(t));
        list.appendChild(b);
      });
    });
  }

  function selectTool(t) {
    currentTool = t;
    renderToolList();
    $("tool-title").textContent = t.name;
    $("tool-desc").textContent = t.description || "";
    const form = $("tool-form");
    form.innerHTML = "";
    const schema = t.inputSchema || {};
    const props = schema.properties || {};
    const required = schema.required || [];

    Object.entries(props).forEach(([key, spec]) => {
      const label = document.createElement("label");
      label.className = "lbl";
      label.textContent = key + (required.includes(key) ? " *" : "") + (spec.description ? ` — ${spec.description}` : "");
      form.appendChild(label);

      let input;
      if (spec.enum) {
        input = document.createElement("select");
        spec.enum.forEach((v) => { const o = document.createElement("option"); o.value = v; o.textContent = v; input.appendChild(o); });
        if (spec.default !== undefined) input.value = spec.default;
      } else if (spec.type === "boolean") {
        input = document.createElement("input");
        input.type = "checkbox";
        input.checked = !!spec.default;
      } else if (spec.type === "array" || spec.type === "object") {
        input = document.createElement("textarea");
        input.rows = 3;
        input.placeholder = spec.type === "array" ? "One value per line" : "JSON";
      } else {
        input = document.createElement("input");
        input.type = "text";
        if (spec.default !== undefined) input.value = spec.default;
      }
      input.name = key;
      input.dataset.type = spec.enum ? "string" : (spec.type || "string");

      const kind = browseKindFor(key);
      if (kind && input.tagName !== "SELECT" && input.type !== "checkbox" && !["number", "integer"].includes(spec.type)) {
        const row = document.createElement("div");
        row.className = "input-row";
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn";
        b.textContent = "Browse";
        b.addEventListener("click", async () => {
          const r = await api("/api/browse", { kind, prompt: `Select ${key}` });
          if (!r.path) return;
          input.value = spec.type === "array" && input.value.trim() ? `${input.value.trim()}\n${r.path}` : r.path;
        });
        row.append(input, b);
        form.appendChild(row);
      } else {
        form.appendChild(input);
      }
    });
    if (!Object.keys(props).length) {
      const p = document.createElement("p");
      p.className = "muted";
      p.textContent = "This tool needs no inputs.";
      form.appendChild(p);
    }
    $("btn-run-tool").disabled = false;
    $("tool-output").innerHTML = "";
  }

  function collectArgs() {
    const args = {};
    for (const el of $("tool-form").elements) {
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
      const r = await api("/api/tools");
      if (!r.success) { $("tool-list").innerHTML = ""; $("tool-list").textContent = r.error; return; }
      const seen = new Set();
      tools = r.tools.filter((t) => !seen.has(t.name) && seen.add(t.name));
      $("home-tool-count").textContent = `${tools.length} tools`;
      renderToolList();
    } catch (e) {
      $("tool-list").textContent = e.message;
    }
  }
  $("tool-search").addEventListener("input", () => tools && renderToolList());

  $("btn-run-tool").addEventListener("click", async () => {
    if (!currentTool) return;
    let args;
    try { args = collectArgs(); } catch (e) { return alert(e.message); }
    const out = $("tool-output");
    out.innerHTML = "";
    $("btn-run-tool").disabled = true;
    try {
      const { job_id } = await api("/api/tool", { name: currentTool.name, arguments: args });
      const job = await pollJob(job_id, (l) => log(out, l.msg, l.level, l.t));
      log(out, JSON.stringify(job.result ?? job.error, null, 2), job.status === "error" ? "error" : "normal");
      fetchStatus();
    } catch (e) {
      log(out, e.message, "error");
    } finally {
      $("btn-run-tool").disabled = false;
    }
  });

  // ------------------------------------------------------------------
  // Diagnostics
  // ------------------------------------------------------------------
  async function loadDiagnostics() {
    const box = $("diag");
    try {
      const d = await api("/api/diagnostics");
      renderStatus(d);
      box.innerHTML = "";
      const env = d.environment;
      const items = [["App", `v1.1 · ${env.os}`], ["Python", env.python], ["Output folder", env.output_dir]];
      Object.entries(env.dependencies).forEach(([k, v]) => items.push([k, v || "missing"]));
      items.forEach(([k, v]) => {
        const it = document.createElement("div");
        it.className = "item";
        const a = document.createElement("span"); a.className = "muted"; a.textContent = k;
        const b = document.createElement("span"); b.textContent = v;
        if (v === "missing") b.style.color = "var(--red)";
        it.append(a, b);
        box.appendChild(it);
      });
    } catch (e) {
      box.textContent = e.message;
    }
  }
  $("btn-diag").addEventListener("click", loadDiagnostics);

  // ------------------------------------------------------------------
  // Start
  // ------------------------------------------------------------------
  if (START_TAB && PAGES[START_TAB]) go(START_TAB);
  fetchStatus();
  fetchLicense();
  loadTools();
  setInterval(fetchStatus, 6000);
  setInterval(fetchLicense, 60000);
})();
