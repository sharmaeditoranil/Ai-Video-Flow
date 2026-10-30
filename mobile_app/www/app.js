// AI Video Flow Reels - Mobile App Frontend Logic

(() => {
  const $ = (id) => document.getElementById(id);

  let selectedStyle = "auto_ref";
  let selectedDur = 30;
  let uploadedClips = [];
  let loadedFolderName = "";
  let uploadCancelToken = 0;
  let uploadedRef = "";
  let uploadedAudio = "";
  let licenseState = { is_premium: false };

  // Tab navigation
  function switchTab(tab) {
    document.querySelectorAll(".m-nav-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tab));
    document.querySelectorAll(".m-pane").forEach((p) => p.classList.toggle("active", p.id === `pane-${tab}`));
    if (tab === "pricing") loadPlans();
  }
  document.querySelectorAll("[data-tab]").forEach((el) => {
    el.addEventListener("click", () => switchTab(el.dataset.tab));
  });

  // Style dropdown selection
  const styleSelect = $("sel-m-style");
  if (styleSelect) {
    styleSelect.addEventListener("change", () => {
      selectedStyle = styleSelect.value || "auto_ref";
    });
  }

  // Duration pills
  document.querySelectorAll("#dur-pills button").forEach((btn) => {
    btn.addEventListener("click", () => {
      selectedDur = Number(btn.dataset.dur) || 30;
      document.querySelectorAll("#dur-pills button").forEach((b) => b.classList.toggle("active", b === btn));
    });
  });

  // File uploads (Gallery -> Mobile Backend)
  async function uploadSingleFile(file, kind) {
    const res = await fetch("/api/mobile/upload", {
      method: "POST",
      headers: {
        "X-File-Name": encodeURIComponent(file.name),
        "X-Upload-Kind": kind,
      },
      body: file,
    });
    return res.json();
  }

  $("btn-pick-ref").addEventListener("click", () => $("file-ref").click());
  $("file-ref").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    $("ref-status").textContent = `Uploading reference: ${file.name}…`;
    const r = await uploadSingleFile(file, "ref");
    if (r.success) {
      uploadedRef = r.path;
      $("ref-status").className = "hint ok";
      $("ref-status").textContent = `Reference loaded: ${file.name}`;
      $("btn-clear-ref").hidden = false;
    }
  });
  $("btn-clear-ref").addEventListener("click", () => {
    uploadedRef = "";
    $("file-ref").value = "";
    $("btn-clear-ref").hidden = true;
    $("ref-status").className = "hint";
    $("ref-status").textContent = "Clones cut rhythm, pacing, 3D color LUT and audio beats from any reel.";
  });

  $("btn-pick-clips").addEventListener("click", () => $("file-clips").click());
  if ($("btn-pick-folder")) {
    $("btn-pick-folder").addEventListener("click", () => $("file-folder").click());
  }

  function renderClipChips(customMessage = "") {
    const listBox = $("clips-list");
    const srcBar = $("loaded-source-bar");
    const srcLbl = $("loaded-source-label");
    if (!listBox) return;
    if (!uploadedClips.length) {
      listBox.hidden = true;
      listBox.innerHTML = "";
      if (srcBar) srcBar.hidden = true;
      $("btn-pick-clips").textContent = "+ Select video clips";
      $("clips-status").className = "hint";
      $("clips-status").textContent = customMessage || "Phone: Long-press a video in Gallery to select multiple clips, or tap '+ Select video clips' again to add more.";
      return;
    }
    listBox.hidden = false;
    listBox.innerHTML = "";
    if (srcBar && srcLbl) {
      srcBar.hidden = false;
      const folderPart = loadedFolderName ? `Folder "${loadedFolderName}" · ` : "";
      srcLbl.innerHTML = `<b>✓ Loaded:</b> ${folderPart}${uploadedClips.length} video clip${uploadedClips.length === 1 ? "" : "s"}`;
    }
    $("btn-pick-clips").textContent = `+ Add more clips (${uploadedClips.length} selected)`;
    $("clips-status").className = "hint ok";
    $("clips-status").textContent = `${uploadedClips.length} video clip${uploadedClips.length === 1 ? "" : "s"} ready. Tap '✕ Remove' anytime to switch to another folder or project.`;

    uploadedClips.forEach((item, idx) => {
      const chip = document.createElement("div");
      chip.className = "clip-chip";
      const nameSpan = document.createElement("span");
      nameSpan.textContent = `${idx + 1}. ${item.name}`;
      const delBtn = document.createElement("button");
      delBtn.type = "button";
      delBtn.textContent = "✕";
      delBtn.title = "Remove clip";
      delBtn.addEventListener("click", () => {
        uploadedClips.splice(idx, 1);
        if (!uploadedClips.length) loadedFolderName = "";
        renderClipChips();
      });
      chip.append(nameSpan, delBtn);
      listBox.appendChild(chip);
    });
  }

  async function handleClipFilesSelected(fileList, isFolder = false) {
    const videoRe = /\.(mp4|mov|mxf|mts|m2ts|avi|mkv|m4v|webm|3gp)$/i;
    const files = Array.from(fileList || []).filter(
      (f) => (f.type && f.type.startsWith("video/")) || videoRe.test(f.name || "")
    );
    if (!files.length) {
      if (isFolder) {
        $("clips-status").className = "hint";
        $("clips-status").textContent = "No video files (.mp4, .mov, .mkv, .avi) found in the selected folder.";
      }
      return;
    }
    if (isFolder) {
      // Replacing previous folder when user picks a folder
      uploadedClips = [];
      const rel = files[0].webkitRelativePath || "";
      loadedFolderName = rel.includes("/") ? rel.split("/")[0] : "Selected Folder";
    }
    const myToken = ++uploadCancelToken;
    $("clips-status").className = "hint";
    for (let i = 0; i < files.length; i++) {
      if (myToken !== uploadCancelToken) return;
      $("clips-status").textContent = `Loading clip ${i + 1} of ${files.length}: ${files[i].name}… (Tap '✕ Remove' to cancel)`;
      const r = await uploadSingleFile(files[i], "clips");
      if (myToken !== uploadCancelToken) return;
      if (r.success && !uploadedClips.some((x) => x.name === files[i].name && x.size === files[i].size)) {
        uploadedClips.push({ path: r.path, name: files[i].name, size: files[i].size });
      }
    }
    renderClipChips();
  }

  $("file-clips").addEventListener("change", async (e) => {
    await handleClipFilesSelected(e.target.files, false);
    e.target.value = "";
  });

  if ($("file-folder")) {
    $("file-folder").addEventListener("change", async (e) => {
      await handleClipFilesSelected(e.target.files, true);
      e.target.value = "";
    });
  }

  async function clearLoadedFootage(msg = "✓ Loaded folder & video clips removed. Select a new folder or video clips for your next project.") {
    uploadCancelToken++;
    uploadedClips = [];
    loadedFolderName = "";
    $("file-clips").value = "";
    if ($("file-folder")) $("file-folder").value = "";
    renderClipChips(msg);
    try {
      await fetch("/api/mobile/clear-uploads", { method: "POST" });
    } catch (e) {}
  }

  $("btn-clear-clips").addEventListener("click", () => clearLoadedFootage());
  if ($("btn-remove-source-bar")) {
    $("btn-remove-source-bar").addEventListener("click", () => clearLoadedFootage());
  }
  if ($("btn-reset-project")) {
    $("btn-reset-project").addEventListener("click", async () => {
      await clearLoadedFootage("✓ Project reset! Choose new video clips or folder to start a fresh project.");
      uploadedRef = "";
      $("file-ref").value = "";
      $("btn-clear-ref").hidden = true;
      $("ref-status").className = "hint";
      $("ref-status").textContent = "Clones cut rhythm, pacing, 3D color LUT and audio beats from any reel.";
      uploadedAudio = "";
      $("file-audio").value = "";
      $("btn-pick-audio").textContent = "Choose audio track";
      $("btn-clear-audio").hidden = true;
      $("m-progress").hidden = true;
      $("m-console").hidden = true;
      $("m-console").innerHTML = "";
    });
  }

  $("btn-pick-audio").addEventListener("click", () => $("file-audio").click());
  $("file-audio").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    $("btn-pick-audio").textContent = `Uploading ${file.name}…`;
    const r = await uploadSingleFile(file, "audio");
    if (r.success) {
      uploadedAudio = r.path;
      $("btn-pick-audio").textContent = `Audio: ${file.name}`;
      $("btn-clear-audio").hidden = false;
    }
  });
  $("btn-clear-audio").addEventListener("click", () => {
    uploadedAudio = "";
    $("file-audio").value = "";
    $("btn-pick-audio").textContent = "Choose audio track";
    $("btn-clear-audio").hidden = true;
  });

  function logLine(msg, level = "normal") {
    const box = $("m-console");
    box.hidden = false;
    const div = document.createElement("div");
    div.className = `ln ${level}`;
    div.textContent = msg;
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  function openTrialEndedModal() {
    if ($("trial-ended-modal")) {
      $("trial-ended-modal").hidden = false;
    }
  }

  if ($("te-close")) {
    $("te-close").addEventListener("click", () => { $("trial-ended-modal").hidden = true; });
  }
  if ($("te-later-btn")) {
    $("te-later-btn").addEventListener("click", () => { $("trial-ended-modal").hidden = true; });
  }
  if ($("te-buy-btn")) {
    $("te-buy-btn").addEventListener("click", () => {
      $("trial-ended-modal").hidden = true;
      switchTab("pricing");
    });
  }
  if ($("te-key-btn")) {
    $("te-key-btn").addEventListener("click", () => {
      $("trial-ended-modal").hidden = true;
      switchTab("profile");
      setTimeout(() => { if ($("m-lic-key")) $("m-lic-key").focus(); }, 120);
    });
  }

  // Build Reel
  async function startReelBuild() {
    if (!uploadedClips.length) {
      alert("Please select your raw video clips in Step 1 first.");
      return;
    }
    if (!licenseState.is_premium && !licenseState.registered) {
      $("reg-modal").hidden = false;
      setTimeout(() => { if ($("rm-name")) $("rm-name").focus(); }, 100);
      return;
    }
    if (!licenseState.is_premium && licenseState.free_left_today === 0) {
      openTrialEndedModal();
      return;
    }
    const btn = $("btn-build-reel");
    btn.disabled = true;
    btn.textContent = "Rendering 9:16 Reel…";
    $("m-progress").hidden = false;
    $("m-bar").style.width = "5%";
    $("m-console").innerHTML = "";
    $("m-console").hidden = false;

    try {
      const res = await fetch("/api/mobile/build-reel", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          clips: uploadedClips.map((c) => (typeof c === "string" ? c : c.path)),
          ref_video: uploadedRef,
          audio: uploadedAudio,
          reel_style: selectedStyle,
          duration: selectedDur,
          depth_text: $("m-depth-text").value.trim(),
          caption_text: $("m-captions").value.trim(),
          watermark_text: $("m-watermark").value.trim(),
          enable_broll: $("chk-m-broll").checked,
          use_ref_audio: $("chk-use-ref-audio").checked,
          smart_ai: $("chk-m-smart-ai").checked,
          instructions: $("m-instructions").value.trim(),
        }),
      }).then((r) => r.json());

      if (!res.success) {
        if (res.needs_registration) {
          btn.disabled = false;
          btn.textContent = "Render 9:16 Reel (.MP4)";
          $("reg-modal").hidden = false;
          return;
        }
        if (res.limit) {
          btn.disabled = false;
          btn.textContent = "Render 9:16 Reel (.MP4)";
          await loadStatus();
          openTrialEndedModal();
          return;
        }
        throw new Error(res.error || "Could not start Reel build.");
      }

      let shown = 0;
      const poll = async () => {
        const job = await fetch(`/api/mobile/job/${res.job_id}`).then((r) => r.json());
        (job.logs || []).slice(shown).forEach((l) => logLine(`${l.t} · ${l.msg}`, l.level));
        shown = (job.logs || []).length;
        $("m-bar").style.width = `${job.progress || 0}%`;
        if (job.status === "running") {
          setTimeout(poll, 600);
          return;
        }
        btn.disabled = false;
        btn.textContent = "Render 9:16 Reel (.MP4)";
        if (job.status === "done" && job.result) {
          showRenderedReel(job.result);
          await loadStatus();
          switchTab("reels");
        } else {
          logLine(job.error || "Reel build failed", "error");
        }
      };
      poll();
    } catch (e) {
      logLine(e.message, "error");
      btn.disabled = false;
      btn.textContent = "Render 9:16 Reel (.MP4)";
    }
  }
  $("btn-build-reel").addEventListener("click", startReelBuild);

  function showRenderedReel(reel) {
    const player = $("reel-player");
    $("player-empty").style.display = "none";
    player.style.display = "block";
    player.src = reel.mp4_url;
    player.load();
    player.play().catch(() => {});

    $("reel-actions").hidden = false;
    $("btn-dl-mp4").href = reel.mp4_url;
    $("btn-dl-mp4").setAttribute("download", reel.name || "AI_Reel.mp4");
    if (reel.xml_url) {
      $("btn-dl-xml").hidden = false;
      $("btn-dl-xml").href = reel.xml_url;
    } else {
      $("btn-dl-xml").hidden = true;
    }
  }

  function renderRecentReels(list) {
    const box = $("recent-reels-list");
    if (!list || !list.length) return;
    box.innerHTML = "";
    list.forEach((r) => {
      const row = document.createElement("div");
      row.className = "recent-item";
      row.innerHTML = `
        <div>
          <b style="font-size:12.5px">${r.name}</b>
          <div class="muted" style="font-size:11px">${r.duration}s · ${r.clip_count} cuts + ${r.broll_count} B-roll</div>
        </div>
      `;
      const playBtn = document.createElement("button");
      playBtn.className = "btn";
      playBtn.style.padding = "6px 12px";
      playBtn.textContent = "Play";
      playBtn.addEventListener("click", () => showRenderedReel(r));
      row.appendChild(playBtn);
      box.appendChild(row);
    });
  }

  // Status & License (PC Subscription Free Unlock + Free Trial 2 Edits / Day)
  async function loadStatus() {
    try {
      const d = await fetch("/api/mobile/status").then((r) => r.json());
      const l = d.license || {};
      licenseState = l;
      const topBtn = $("top-plan-btn");
      const name = l.client_name ? l.client_name : "Guest";
      const initial = (name.trim()[0] || "G").toUpperCase();
      topBtn.classList.toggle("premium", !!l.is_premium);
      if ($("top-chip-avatar")) $("top-chip-avatar").textContent = initial;
      if ($("top-chip-name")) $("top-chip-name").textContent = name;
      const freeLimit = l.free_limit ?? 2;
      const freeLeft = l.free_left_today ?? freeLimit;
      if ($("top-chip-sub")) {
        $("top-chip-sub").textContent = l.is_premium
          ? (l.plan_name || "Pro Plan")
          : `Free · ${freeLeft}/${freeLimit} today`;
      }
      $("pc-free-banner").hidden = !!l.is_premium;

      $("m-pf-name").textContent = name;
      $("m-pf-avatar").textContent = initial;
      $("m-pf-plan").textContent = l.is_premium
        ? `${l.plan_name} (PC + Mobile Active)`
        : `Free Edition · ${freeLimit} edits per day (${freeLeft} left today)`;
      $("m-pf-credits").textContent = l.is_premium
        ? (l.unlimited ? "Unlimited Smart AI Reels" : `${l.credits_remaining} Smart AI credits left`)
        : (l.client_phone ? `WhatsApp: ${l.client_phone} · ${l.client_email || ""}` : "Register profile below to unlock Free Trial");
      $("m-device-id").textContent = l.machine_id || "…";

      const prof = l.user_profile || {};
      if ($("m-reg-name") && !$("m-reg-name").value && (prof.name || l.client_name)) $("m-reg-name").value = prof.name || l.client_name || "";
      if ($("m-reg-phone") && !$("m-reg-phone").value && (prof.phone || l.client_phone)) $("m-reg-phone").value = prof.phone || l.client_phone || "";
      if ($("m-reg-email") && !$("m-reg-email").value && (prof.email || l.client_email)) $("m-reg-email").value = prof.email || l.client_email || "";
      if ($("m-reg-studio") && !$("m-reg-studio").value && (prof.studio || l.client_studio)) $("m-reg-studio").value = prof.studio || l.client_studio || "";
      if ($("m-reg-badge")) {
        $("m-reg-badge").textContent = l.registered ? "✓ 2 Edits / Day Active" : "Required for Free Trial";
      }

      // Mandatory Startup Registration Gate: keep open until user registers or activates a license key
      if ($("reg-modal")) {
        const unlocked = !!(l.is_premium || l.registered);
        $("reg-modal").hidden = unlocked;
        if (!unlocked && (d.pc_license_key_detected || l.license_key) && $("rm-lic-key") && !$("rm-lic-key").value) {
          $("rm-lic-key").value = l.license_key || d.pc_license_key_detected;
        }
      }

      if ((l.license_key || d.pc_license_key_detected) && !$("m-lic-key").value) {
        $("m-lic-key").value = l.license_key || d.pc_license_key_detected;
      }
      if (d.recent_reels && d.recent_reels.length) {
        renderRecentReels(d.recent_reels);
      }
    } catch (e) {}
  }

  async function submitProfileRegistration(name, phone, email, studio) {
    return fetch("/api/mobile/profile/register", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, phone, email, studio }),
    }).then((r) => r.json());
  }

  if ($("btn-m-save-profile")) {
    $("btn-m-save-profile").addEventListener("click", async () => {
      const name = $("m-reg-name").value.trim();
      const phone = $("m-reg-phone").value.trim();
      const email = $("m-reg-email").value.trim();
      const studio = $("m-reg-studio").value.trim();
      const msgEl = $("m-reg-msg");
      msgEl.hidden = true;
      $("btn-m-save-profile").disabled = true;
      try {
        const r = await submitProfileRegistration(name, phone, email, studio);
        msgEl.hidden = false;
        if (r.success) {
          msgEl.className = "hint ok";
          msgEl.textContent = r.message || "✓ Profile saved! 2 Free Edits / Day unlocked.";
          await loadStatus();
        } else {
          msgEl.className = "hint";
          msgEl.style.color = "#fca5a5";
          msgEl.textContent = r.error || "Please fill Name, WhatsApp Number and Email.";
        }
      } finally {
        $("btn-m-save-profile").disabled = false;
      }
    });
  }

  if ($("reg-modal-form")) {
    $("reg-modal-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const name = $("rm-name").value.trim();
      const phone = $("rm-phone").value.trim();
      const email = $("rm-email").value.trim();
      const studio = $("rm-studio").value.trim();
      const errBox = $("rm-error");
      const btn = $("rm-submit-btn");
      errBox.hidden = true;
      btn.disabled = true;
      try {
        const r = await submitProfileRegistration(name, phone, email, studio);
        if (r.success) {
          $("reg-modal").hidden = true;
          await loadStatus();
          if (uploadedClips.length) startReelBuild();
        } else {
          errBox.textContent = r.error || "Registration failed.";
          errBox.hidden = false;
        }
      } catch (err) {
        errBox.textContent = err.message;
        errBox.hidden = false;
      } finally {
        btn.disabled = false;
      }
    });
  }

  if ($("rm-activate-btn")) {
    $("rm-activate-btn").addEventListener("click", async () => {
      const key = ($("rm-lic-key") ? $("rm-lic-key").value : "").trim();
      const errBox = $("rm-error");
      if (!key) {
        errBox.textContent = "Please enter your AVF License Key.";
        errBox.hidden = false;
        return;
      }
      errBox.hidden = true;
      $("rm-activate-btn").disabled = true;
      try {
        const r = await fetch("/api/mobile/license/activate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ license_key: key }),
        }).then((res) => res.json());
        if (r.success) {
          $("reg-modal").hidden = true;
          await loadStatus();
        } else {
          errBox.textContent = r.error || "License activation failed.";
          errBox.hidden = false;
        }
      } catch (err) {
        errBox.textContent = err.message;
        errBox.hidden = false;
      } finally {
        $("rm-activate-btn").disabled = false;
      }
    });
  }

  $("btn-m-activate").addEventListener("click", async () => {
    const key = $("m-lic-key").value.trim();
    if (!key) return alert("Apni AVF License Key enter karein.");
    const r = await fetch("/api/mobile/license/activate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ license_key: key }),
    }).then((res) => res.json());
    alert(r.message || r.error || "Done");
    loadStatus();
  });

  $("btn-m-sync-pc").addEventListener("click", async () => {
    const r = await fetch("/api/mobile/license/sync-pc", { method: "POST" }).then((res) => res.json());
    if (r.license_key) $("m-lic-key").value = r.license_key;
    alert(r.message || r.error || "Synced!");
    loadStatus();
  });

  $("btn-m-remove-lic").addEventListener("click", async () => {
    if (!confirm("Remove license from Mobile App?")) return;
    await fetch("/api/mobile/license/deactivate", { method: "POST" });
    $("m-lic-key").value = "";
    loadStatus();
  });

  // In-App Mobile Purchase & Instant Auto-Activation
  let activeCheckoutPlan = null;
  let activeBuyUrl = "https://license.aivideoflow.in/index.php/buy";

  function openCheckoutModal(plan, buyUrl) {
    activeCheckoutPlan = plan;
    activeBuyUrl = buyUrl || activeBuyUrl;
    $("co-plan-name").textContent = plan.name;
    $("co-plan-price").textContent = `₹${Number(plan.price_inr).toLocaleString("en-IN")} · Includes 1 Mobile + 1 PC/Mac (${plan.validity_days} days)`;
    $("co-result").hidden = true;
    $("co-result").innerHTML = "";
    $("co-submit-btn").disabled = false;
    $("co-submit-btn").textContent = "Pay with UPI / Card / NetBanking";
    $("checkout-modal").hidden = false;
  }

  if ($("co-close")) {
    $("co-close").addEventListener("click", () => {
      $("checkout-modal").hidden = true;
    });
  }

  if ($("co-web-btn")) {
    $("co-web-btn").addEventListener("click", () => {
      if (!activeCheckoutPlan) return;
      window.open(`${activeBuyUrl}?plan=${activeCheckoutPlan.id}`, "_blank");
    });
  }

  if ($("co-form")) {
    $("co-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!activeCheckoutPlan) return;
      const name = $("co-name").value.trim();
      const email = $("co-email").value.trim();
      const phone = $("co-phone").value.trim();
      const btn = $("co-submit-btn");
      const resBox = $("co-result");

      btn.disabled = true;
      btn.textContent = "Creating secure order…";
      resBox.hidden = true;

      try {
        const order = await fetch("/api/mobile/pay/order", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ plan_id: activeCheckoutPlan.id, name, email, phone }),
        }).then((r) => r.json());

        if (!order.success) {
          resBox.hidden = false;
          resBox.innerHTML = `<div style="color:#fca5a5;margin-bottom:8px">${order.error || "Online checkout unavailable right now."}</div>
            <button type="button" class="btn btn-gold btn-block" id="co-fallback-open">Continue on Checkout Page →</button>`;
          const fb = $("co-fallback-open");
          if (fb) fb.onclick = () => window.open(`${activeBuyUrl}?plan=${activeCheckoutPlan.id}`, "_blank");
          btn.disabled = false;
          btn.textContent = "Pay with UPI / Card / NetBanking";
          return;
        }

        if (typeof window.Razorpay !== "function") {
          window.open(`${activeBuyUrl}?plan=${activeCheckoutPlan.id}`, "_blank");
          btn.disabled = false;
          btn.textContent = "Pay with UPI / Card / NetBanking";
          return;
        }

        const rzp = new window.Razorpay({
          key: order.key_id,
          order_id: order.order_id,
          amount: order.amount,
          currency: "INR",
          name: "AI Video Flow",
          description: order.plan,
          prefill: { name: order.name, email: order.email, contact: order.phone },
          theme: { color: "#7c5cff" },
          handler: async (resp) => {
            resBox.hidden = false;
            resBox.textContent = "Verifying payment & activating your Mobile App…";
            const verify = await fetch("/api/mobile/pay/verify", {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(resp),
            }).then((r) => r.json());

            if (verify.success && verify.license_key) {
              $("m-lic-key").value = verify.license_key;
              await loadStatus();
              resBox.innerHTML = `
                <div style="color:#22c55e;font-weight:600">✓ Payment successful &amp; Mobile App activated!</div>
                <div class="co-key">${verify.license_key}</div>
                <div class="muted" style="font-size:11.5px;margin-bottom:8px">This same key also unlocks your Windows / Mac Desktop App for free.</div>
                <button type="button" class="btn btn-gold btn-block" id="co-copy-key">Copy License Key</button>
              `;
              const cp = $("co-copy-key");
              if (cp) {
                cp.onclick = () => {
                  navigator.clipboard.writeText(verify.license_key);
                  cp.textContent = "Copied!";
                };
              }
            } else {
              resBox.innerHTML = `<div style="color:#fca5a5">${verify.error || "Verification pending. Check your email for your license key."}</div>`;
            }
          },
        });
        rzp.open();
      } catch (err) {
        resBox.hidden = false;
        resBox.textContent = err.message || "Payment error.";
      } finally {
        btn.disabled = false;
        btn.textContent = "Pay with UPI / Card / NetBanking";
      }
    });
  }

  // Pricing (100% matched with PC)
  document.querySelectorAll("#m-price-tabs button").forEach((b) => {
    b.addEventListener("click", () => {
      const g = b.dataset.pg;
      document.querySelectorAll("#m-price-tabs button").forEach((x) => x.classList.toggle("active", x === b));
      $("m-plans-local").hidden = g !== "local";
      $("m-plans-ai").hidden = g !== "ai";
    });
  });

  async function loadPlans() {
    const localBox = $("m-plans-local");
    const aiBox = $("m-plans-ai");
    try {
      const data = await fetch("/api/mobile/plans").then((r) => r.json());
      const buyUrl = data.buy_url || "https://license.aivideoflow.in/index.php/buy";
      const plans = data.plans && data.plans.length ? data.plans : [
        { id: 1, name: "AI Editing · Monthly", kind: "local", price_inr: 399, validity_days: 30, badge: "" },
        { id: 2, name: "AI Editing · Yearly", kind: "local", price_inr: 2499, mrp_inr: 4788, validity_days: 365, badge: "Best value" },
        { id: 3, name: "Smart AI · 5 Weddings / Reels Pack", kind: "ai", credits: 5, price_inr: 999, validity_days: 365, badge: "" },
        { id: 4, name: "Smart AI · 11 Weddings / Reels Pack", kind: "ai", credits: 11, price_inr: 1999, mrp_inr: 2200, validity_days: 365, badge: "Most popular" },
        { id: 5, name: "Smart AI · 30 Weddings / Reels Pack", kind: "ai", credits: 30, price_inr: 4999, mrp_inr: 6000, validity_days: 365, badge: "Best value" },
        { id: 6, name: "Smart AI Unlimited · Yearly", kind: "ai", unlimited: 1, price_inr: 14999, mrp_inr: 29999, validity_days: 365, badge: "50% off" },
      ];
      localBox.innerHTML = "";
      aiBox.innerHTML = "";
      plans.forEach((p) => {
        const card = document.createElement("div");
        card.className = "m-plan-card";
        card.innerHTML = `
          <div style="display:flex;justify-content:space-between;align-items:center">
            <b>${p.name}</b>
            ${p.badge ? `<span class="pill-gold">${p.badge}</span>` : ""}
          </div>
          <div class="p-price">₹${Number(p.price_inr).toLocaleString("en-IN")}</div>
          <div class="muted" style="font-size:11.5px;margin-bottom:10px">
            Includes 1 PC/Mac + 1 Mobile device · ${p.validity_days} days
          </div>
          <button class="btn btn-primary btn-block">Buy now</button>
        `;
        card.querySelector("button").addEventListener("click", () => {
          openCheckoutModal(p, buyUrl);
        });
        (p.kind === "local" ? localBox : aiBox).appendChild(card);
      });
    } catch (e) {}
  }

  // Automatic Over-The-Air (OTA) App Update Checker
  async function checkMobileUpdate(manual = false) {
    try {
      const u = await fetch("/api/mobile/update-check").then((r) => r.json());
      if (!u || !u.success) return;
      if ($("m-ver-sub")) $("m-ver-sub").textContent = `v${u.current} · AI editing assistant`;
      if ($("m-cur-ver-pill")) $("m-cur-ver-pill").textContent = `v${u.current}`;

      if (u.update_available) {
        const dismissed = localStorage.getItem("avf_mobile_dismissed_ver");
        if (manual || dismissed !== u.latest) {
          $("m-update-title").textContent = `Update available · v${u.latest}`;
          $("m-update-notes").textContent = u.notes || "A new version is ready to install on your phone.";
          $("m-update-dl").href = u.download_url;
          $("m-update-banner").hidden = false;
        }
        if ($("m-update-status-text")) {
          $("m-update-status-text").innerHTML = `<span style="color:var(--gold);font-weight:600">New version v${u.latest} is available!</span> ${u.notes || ""}`;
        }
        if ($("btn-m-install-update")) {
          $("btn-m-install-update").href = u.download_url;
          $("btn-m-install-update").hidden = false;
        }
      } else {
        $("m-update-banner").hidden = true;
        if ($("btn-m-install-update")) $("btn-m-install-update").hidden = true;
        if (manual && $("m-update-status-text")) {
          $("m-update-status-text").innerHTML = `<span style="color:var(--green)">✓ You are using the latest version (v${u.current}).</span>`;
        }
      }
    } catch (e) {}
  }

  if ($("m-update-close")) {
    $("m-update-close").addEventListener("click", () => {
      $("m-update-banner").hidden = true;
      const title = $("m-update-title").textContent || "";
      const ver = title.split("v").pop().trim();
      if (ver) {
        try { localStorage.setItem("avf_mobile_dismissed_ver", ver); } catch (e) {}
      }
    });
  }

  if ($("btn-m-check-update")) {
    $("btn-m-check-update").addEventListener("click", () => checkMobileUpdate(true));
  }

  loadStatus();
  checkMobileUpdate(false);
  setInterval(() => checkMobileUpdate(false), 300000);
})();
