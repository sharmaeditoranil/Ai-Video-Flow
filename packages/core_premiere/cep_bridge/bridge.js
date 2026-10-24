/*
 * AI Video Flow bridge for Premiere Pro.
 * The desktop app drops ExtendScript jobs into ~/.ai-video-flow/premiere_queue/<id>.job.json; this
 * extension runs each one inside Premiere and writes <id>.result.json. A heartbeat file tells the
 * app the bridge is alive. Both the hidden service and the panel poll; renaming a job claims it.
 */
(function () {
  var req = (window.cep_node && window.cep_node.require) || window.require;
  var status = document.getElementById("status");
  if (!req || !window.__adobe_cep__) {
    if (status) status.textContent = "This Premiere Pro version does not allow the bridge (Node.js is off).";
    return;
  }
  var fs = req("fs"), path = req("path"), os = req("os");
  var queue = path.join(os.homedir(), ".ai-video-flow", "premiere_queue");
  var busy = false;

  function setStatus(t) { if (status) status.textContent = t; }

  function tick() {
    try {
      if (!fs.existsSync(queue)) fs.mkdirSync(queue, { recursive: true });
      fs.writeFileSync(path.join(queue, "bridge.alive"), String(Date.now()));
      if (busy) return;
      var jobs = fs.readdirSync(queue).filter(function (f) { return /\.job\.json$/.test(f); }).sort();
      if (!jobs.length) { setStatus("Ready - connected to AI Video Flow."); return; }
      var file = path.join(queue, jobs[0]);
      var taken = file + "." + process.pid + "-" + Math.random().toString(36).slice(2) + ".taken";
      try { fs.renameSync(file, taken); } catch (e) { return; }  // another bridge window took it
      var job = JSON.parse(fs.readFileSync(taken, "utf8"));
      fs.unlinkSync(taken);
      busy = true;
      setStatus("Working: " + (job.label || "timeline") + "…");
      window.__adobe_cep__.evalScript(job.jsx, function (result) {
        try { fs.writeFileSync(path.join(queue, job.id + ".result.json"), JSON.stringify({ result: result })); } catch (e) { /* app gone */ }
        busy = false;
        setStatus("Done: " + (job.label || "timeline"));
      });
    } catch (e) {
      busy = false;
      setStatus("Bridge error: " + e);
    }
  }

  tick();
  setInterval(tick, 1000);
})();
