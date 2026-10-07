/* AcellusAuto dashboard - polls the bot API, no dependencies. */
(function () {
  "use strict";
  var TOKEN = "";
  var tokenAsked = false;
  function loadToken() {
    try {
      var m = (location.hash || "").match(/token=([^&]+)/);
      if (m) {
        TOKEN = decodeURIComponent(m[1]);
        try { localStorage.setItem("aa_token", TOKEN); } catch (e) {}
      } else {
        TOKEN = localStorage.getItem("aa_token") || "";
      }
    } catch (e) { TOKEN = ""; }
  }
  loadToken();

  function askToken() {
    if (tokenAsked) return;
    tokenAsked = true;
    var bar = document.getElementById("tokenbar");
    bar.style.display = "flex";
    document.getElementById("tokensave").addEventListener("click", function () {
      var v = document.getElementById("tokeninput").value.trim();
      if (!v) return;
      TOKEN = v;
      try { localStorage.setItem("aa_token", v); } catch (e) {}
      bar.style.display = "none";
      tokenAsked = false;
      refreshStatus(); refreshLog(); refreshShot();
    });
  }

  function api(path, opts) {
    var url = path + (TOKEN ? (path.indexOf("?") >= 0 ? "&" : "?") + "token=" + encodeURIComponent(TOKEN) : "");
    return fetch(url, opts).then(function (r) {
      if (r.status === 403) { askToken(); throw new Error("http 403 (need token)"); }
      if (!r.ok) throw new Error("http " + r.status);
      var ct = r.headers.get("content-type") || "";
      return ct.indexOf("json") >= 0 ? r.json() : r.text();
    });
  }

  function $(id) { return document.getElementById(id); }

  function fmtUptime(s) {
    s = Math.max(0, Math.floor(s || 0));
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60), sec = s % 60;
    function p(n) { return (n < 10 ? "0" : "") + n; }
    return h > 0 ? h + ":" + p(m) + ":" + p(sec) : m + ":" + p(sec);
  }

  function setStatus(st) {
    var el = $("status");
    el.className = "status " + (st || "idle").toLowerCase();
    $("statusText").textContent = st || "?";
  }

  function refreshStatus() {
    api("/api/status").then(function (d) {
      setStatus(d.status);
      $("build").textContent = d.build ? "[" + d.build + "]" : "";
      $("activity").textContent = d.activity || "—";
      $("cUptime").textContent = fmtUptime(d.uptime_s);
      $("cAnswered").textContent = d.stats.answered;
      $("cVideos").textContent = d.stats.videos;
      $("cSkips").textContent = d.stats.skips;
      $("cSubmits").textContent = d.stats.submits;
      $("cStrikes").textContent = d.stats.strikes + "/" + d.strike_limit;
      $("cClass").textContent = d.rotation.active || "(none)";
      $("cBudget").textContent = d.rotation.budget_left || "—";
      $("cLast").textContent = d.stats.last_answer || "—";
      $("cfg").textContent = "model=" + d.config.model + " · input=" + d.config.input_mode +
        " · rotation=" + (d.config.rotation_enabled ? ("on " + d.config.minutes_per_class + "m") : "off");
      var pu = $("puburl");
      if (d.public_url) {
        pu.innerHTML = "";
        var a = document.createElement("a");
        a.href = d.public_url;
        a.textContent = d.public_url;
        a.style.color = "#7dd3fc";
        pu.appendChild(document.createTextNode("public: "));
        pu.appendChild(a);
      } else { pu.textContent = ""; }
    }).catch(function () { setStatus("error"); });
  }

  function refreshLog() {
    api("/api/log?n=80").then(function (d) {
      var el = $("log");
      el.textContent = (d.lines || []).join("\n");
      el.scrollTop = el.scrollHeight;
    }).catch(function () {});
  }

  function refreshShot() {
    var img = $("shot");
    img.src = "/api/shot" + (TOKEN ? "?token=" + encodeURIComponent(TOKEN) + "&" : "?") + "t=" + Date.now();
  }

  function post(path, btn) {
    if (btn) btn.disabled = true;
    api(path, { method: "POST" }).then(refreshStatus).catch(function () {}).then(function () {
      if (btn) btn.disabled = false;
    });
  }

  $("btnStart").addEventListener("click", function () { post("/api/start", this); });
  $("btnPause").addEventListener("click", function () { post("/api/pause", this); });
  $("shot").addEventListener("error", function () {
    $("shotHint").textContent = "no capture yet";
  });

  refreshStatus(); refreshLog(); refreshShot();
  setInterval(refreshStatus, 2000);
  setInterval(refreshLog, 4000);
  setInterval(refreshShot, 3000);
})();
