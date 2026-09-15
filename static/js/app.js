/* QMS — SO Fulfilment frontend */
(function () {
  "use strict";

  var POLL_MS = 5000;       // quiet auto-refresh (staff can also hit the refresh button)
  var COOLDOWN_MS = 2500;   // ignore the same barcode re-read within this window
  var FLASH_MS = 12000;     // how long a card stays highlighted after it changes

  // Database statuses -> words a shop-floor operator recognises instantly.
  var STATUS_LABEL = {
    scanned: "WAITING",
    assigned: "CLAIMED",
    delivered: "DELIVERED",
    cancelled: "CANCELLED",
  };

  var ROLE_TABS = {
    scanner: [{ k: "scan", i: "📷", t: "Scan" },
              { k: "mine", i: "📋", t: "My Scans" }],
    pos:     [{ k: "unclaimed", i: "🎯", t: "Claim SO" },
              { k: "mine", i: "📋", t: "My SOs" }],
    backstore: [{ k: "todeliver", i: "🚚", t: "To Deliver" },
                { k: "done", i: "✅", t: "Completed" }],
    manager: [{ k: "today", i: "📋", t: "All" },
              { k: "stats", i: "📊", t: "Stats" },
              { k: "setup", i: "⚙", t: "Setup" }],
  };

  var TITLES = {
    mine: "My Scans",
    unclaimed: "Unclaimed SOs",
    todeliver: "To Deliver to POS",
    done: "Completed",
    today: "All SOs Today",
  };

  var EMPTY = {
    mine: "Nothing yet.<br>Scan a customer barcode to start.",
    unclaimed: "No SOs waiting.<br>Every SO has been claimed.",
    todeliver: "Nothing to deliver.<br>All caught up.",
    done: "No completed SOs yet today.",
    today: "No SOs today yet.",
  };

  var state = {
    token: localStorage.getItem("qms_token") || null,
    staff: null, store: null, posCount: 4,
    tab: "scan", prevTab: "scan",
    scanning: false, reader: null, track: null, torchOn: false,
    lastCode: null, lastCodeAt: 0, submitting: false,
    prevStatus: {}, seenIds: {}, flashUntil: {},
    pollTimer: null, net: null,
  };

  function $(id) { return document.getElementById(id); }
  function show(el, yes) { el.classList.toggle("hidden", !yes); }

  function toast(msg, kind, ms) {
    var t = $("toast");
    t.textContent = msg;
    t.className = "toast" + (kind ? " " + kind : "");
    clearTimeout(t._h);
    t._h = setTimeout(function () { t.classList.add("hidden"); }, ms || 3200);
  }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function beep(freq, dur) {
    try {
      var Ctx = window.AudioContext || window.webkitAudioContext;
      if (!Ctx) return;
      if (!state._ac) state._ac = new Ctx();
      var ac = state._ac;
      if (ac.state === "suspended") ac.resume();
      var o = ac.createOscillator(), g = ac.createGain();
      o.connect(g); g.connect(ac.destination);
      o.frequency.value = freq || 900; o.type = "square";
      g.gain.setValueAtTime(0.09, ac.currentTime);
      g.gain.exponentialRampToValueAtTime(0.0001, ac.currentTime + (dur || 0.14));
      o.start(); o.stop(ac.currentTime + (dur || 0.14));
    } catch (e) {}
  }

  function buzz(ms) { if (navigator.vibrate) { try { navigator.vibrate(ms || 60); } catch (e) {} } }

  async function api(method, path, body) {
    var res = await fetch(path, {
      method: method,
      headers: { "Authorization": "Bearer " + (state.token || ""), "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : null,
    });
    if (res.status === 401) { doLogout(true); throw new Error("Session expired — pick your role again"); }
    var data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) throw new Error((data && data.detail) || ("HTTP " + res.status));
    return data;
  }

  async function pub(path) {
    var res = await fetch(path);
    if (!res.ok) throw new Error("HTTP " + res.status);
    return res.json();
  }

  // --------------------------------------------------------- offline outbox
  var IDB_NAME = "qms", IDB_STORE = "outbox";

  function idb() {
    return new Promise(function (resolve, reject) {
      var r = indexedDB.open(IDB_NAME, 1);
      r.onupgradeneeded = function () {
        var db = r.result;
        if (!db.objectStoreNames.contains(IDB_STORE)) {
          db.createObjectStore(IDB_STORE, { keyPath: "id", autoIncrement: true });
        }
      };
      r.onsuccess = function () { resolve(r.result); };
      r.onerror = function () { reject(r.error); };
    });
  }

  async function outboxAdd(so) {
    var db = await idb();
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).add({ so_number: so, at: new Date().toISOString() });
      tx.oncomplete = function () { resolve(true); };
      tx.onerror = function () { reject(tx.error); };
    });
  }

  async function outboxAll() {
    var db = await idb();
    return new Promise(function (resolve, reject) {
      var tx = db.transaction(IDB_STORE, "readonly");
      var rq = tx.objectStore(IDB_STORE).getAll();
      rq.onsuccess = function () { resolve(rq.result || []); };
      rq.onerror = function () { reject(rq.error); };
    });
  }

  async function outboxDelete(id) {
    var db = await idb();
    return new Promise(function (resolve) {
      var tx = db.transaction(IDB_STORE, "readwrite");
      tx.objectStore(IDB_STORE).delete(id);
      tx.oncomplete = function () { resolve(true); };
    });
  }

  async function outboxCount() { try { return (await outboxAll()).length; } catch (e) { return 0; } }

  async function refreshBars() {
    var n = await outboxCount();
    var bar = $("offlineBar");
    if (bar) {
      $("outboxCount").textContent = n;
      show(bar, (!navigator.onLine || n > 0) && !!state.token);
    }
    var dot = $("liveDot");
    if (dot) dot.classList.toggle("off", !navigator.onLine || n > 0);

    // Camera needs a secure context. Say so plainly instead of failing silently.
    var insecure = $("insecureBar");
    if (insecure) {
      var needCam = state.staff && state.staff.role === "scanner";
      var bad = needCam && !window.isSecureContext;
      if (bad && state.net) $("insecureUrl").textContent = state.net.localhost_url;
      show(insecure, !!bad);
    }
  }

  async function flushOutbox() {
    if (!state.token || !navigator.onLine) { refreshBars(); return; }
    var items = await outboxAll();
    if (!items.length) { refreshBars(); return; }
    var ok = 0;
    for (var i = 0; i < items.length; i++) {
      try {
        await api("POST", "/api/v1/requests/scan", { so_number: items[i].so_number, note: "offline-capture" });
        await outboxDelete(items[i].id);
        ok++;
      } catch (e) { break; }
    }
    await refreshBars();
    if (ok) toast(ok + " offline scan(s) sent", "ok");
  }

  // ----------------------------------------------------------------- login
  async function loadNetwork() {
    try { state.net = await pub("/api/network"); } catch (e) { state.net = null; }
    if (state.net) state.posCount = state.net.pos_count || state.posCount;
  }

  function buildRoleGrid() {
    var g = $("roleGrid");
    var html = '<button class="role-btn scanner" data-role="scanner">SCANNER' +
               '<span class="rsub">Scan SO</span></button>';
    for (var n = 1; n <= state.posCount; n++) {
      html += '<button class="role-btn pos" data-role="pos" data-pos="' + n + '">P' + n +
              '<span class="rsub">POS ' + n + '</span></button>';
    }
    html += '<button class="role-btn backstore" data-role="backstore">BACKSTORE' +
            '<span class="rsub">Deliver to POS</span></button>';
    g.innerHTML = html;

    g.querySelectorAll(".role-btn").forEach(function (b) {
      b.addEventListener("click", function () {
        doLogin(b.dataset.role, b.dataset.pos ? parseInt(b.dataset.pos, 10) : null);
      });
    });
    $("setupLink").onclick = function () { openSetup("login"); };
    $("managerLink").onclick = function () { doLogin("manager", null); };
    $("loginStore").textContent = state.net && state.net.store_name
      ? state.net.store_name : "SO Fulfilment";
  }

  async function doLogin(role, posNumber) {
    show($("loginErr"), false);
    try {
      var body = { role: role };
      if (posNumber) body.pos_number = posNumber;
      var res = await api("POST", "/api/login", body);
      state.token = res.token;
      state.staff = res.staff;
      state.store = res.store;
      state.posCount = res.pos_count || state.posCount;
      localStorage.setItem("qms_token", res.token);
      localStorage.setItem("qms_staff", JSON.stringify({ staff: res.staff, store: res.store }));
      enterApp();
    } catch (e) {
      $("loginErr").textContent = e.message;
      show($("loginErr"), true);
      buzz(120);
    }
  }

  function doLogout(silent) {
    stopCamera(); stopPolling();
    if (!silent && state.token) {
      fetch("/api/logout", { method: "POST", headers: { Authorization: "Bearer " + state.token } })
        .catch(function () {});
    }
    state.token = null; state.staff = null;
    state.prevStatus = {}; state.seenIds = {}; state.flashUntil = {};
    localStorage.removeItem("qms_token");
    localStorage.removeItem("qms_staff");
    show($("tabbar"), false);
    document.querySelectorAll(".screen").forEach(function (s) { s.classList.add("hidden"); });
    show($("screen-login"), true);
    document.title = "QMS — SO Fulfilment";
    buildRoleGrid();
    refreshBars();
    if (!silent) toast("Signed out — pick a role");
  }

  async function restoreSession() {
    var cached = localStorage.getItem("qms_staff");
    if (cached) {
      try { var c = JSON.parse(cached); state.staff = c.staff; state.store = c.store; } catch (e) {}
    }
    if (!state.token || !state.staff) { show($("screen-login"), true); buildRoleGrid(); refreshBars(); return; }
    try {
      var m = await api("GET", "/api/me");
      state.staff = m.staff; state.store = m.store; state.posCount = m.pos_count;
      enterApp();
    } catch (e) {
      show($("screen-login"), true);
      buildRoleGrid();
      refreshBars();
    }
  }

  function roleLabel() {
    var s = state.staff;
    if (!s) return "";
    if (s.role === "pos") return "POS " + s.pos_number;
    return s.name || s.role.toUpperCase();
  }

  function enterApp() {
    show($("screen-login"), false);
    show($("tabbar"), true);
    show($("screen-setup"), false);
    $("scanWho").textContent = roleLabel() + " · " + state.store.name;
    $("boardWho").textContent = roleLabel() + " · " + state.store.name;
    $("setupStore").textContent = state.store.name;

    var tabs = ROLE_TABS[state.staff.role] || ROLE_TABS.manager;
    $("tabbar").innerHTML = tabs.map(function (t) {
      return '<button class="tab" data-tab="' + t.k + '"><span>' + t.i + "</span>" + esc(t.t) + "</button>";
    }).join("");
    $("tabbar").querySelectorAll(".tab").forEach(function (b) {
      b.addEventListener("click", function () { go(b.dataset.tab); });
    });
    go(tabs[0].k);
    refreshBars();
    flushOutbox();
  }

  // ------------------------------------------------------------ navigation
  var VIEW_OF_TAB = {
    scan: null, mine: "mine", unclaimed: "unclaimed",
    todeliver: "todeliver", done: "today", today: "today",
    stats: null, setup: null,
  };

  function go(tab) {
    if (tab !== "setup") state.prevTab = tab;
    state.tab = tab;
    if (tab !== "scan") stopCamera();

    var target = tab === "stats" ? "stats" : tab === "setup" ? "setup" : tab === "scan" ? "scan" : "board";
    ["scan", "board", "stats", "setup"].forEach(function (n) { show($("screen-" + n), n === target); });
    $("tabbar").querySelectorAll(".tab").forEach(function (b) {
      b.classList.toggle("active", b.dataset.tab === tab);
    });

    if (tab === "scan") { stopPolling(); refreshBars(); return; }
    if (tab === "stats") { stopPolling(); loadStats(); return; }
    if (tab === "setup") { stopPolling(); openSetup("app"); return; }

    setBoardTitle(tab);
    loadBoard();
    startPolling();
  }

  function setBoardTitle(tab) {
    var t = TITLES[tab] || "Board";
    if (tab === "mine" && state.staff.role === "pos") t = "My SOs";
    if (tab === "unclaimed" && state.staff.role === "pos") t = "Claim for POS " + state.staff.pos_number;
    $("boardTitle").textContent = t;
    var lbl = { mine: "Mine", unclaimed: "Unclaimed", todeliver: "To Deliver", done: "Completed", today: "Open" };
    $("bOpenLbl").textContent = lbl[tab] || "Open";
  }

  function startPolling() {
    stopPolling();
    state.pollTimer = setInterval(function () {
      var t = state.tab;
      if (navigator.onLine && t !== "scan" && t !== "stats" && t !== "setup") loadBoard(true);
    }, POLL_MS);
  }

  function stopPolling() { if (state.pollTimer) { clearInterval(state.pollTimer); state.pollTimer = null; } }

  // ----------------------------------------------------------------- setup
  async function openSetup(from) {
    show($("screen-login"), false);
    $("setupStore").textContent = state.store ? state.store.name : "—";
    state._setupFrom = from;

    await loadNetwork();
    var net = state.net;
    if (!net) { toast("Cannot read server address", "bad"); return; }

    $("addrLocal").textContent = net.localhost_url;
    $("noteLocal").textContent = net.localhost_note;
    $("qrLocal").src = "/setup/qr.png?u=" + encodeURIComponent(net.localhost_url) + "&size=200";

    var lan = net.lan_urls || [];
    // prefer a normal private LAN address over a tailscale/VPN one
    var best = lan.filter(function (u) {
      return /\/\/192\.168\.|^http:\/\/10\.|^http:\/\/172\.(1[6-9]|2\d|3[01])\./.test(u);
    });
    var pick = (best.length ? best : lan)[0] || "";
    $("addrLan").innerHTML = lan.length
      ? lan.map(function (u) {
          return '<div class="addr-row"><b>' + esc(u) + "</b>" +
                 (u === pick ? ' <span class="tag">recommended</span>' : "") + "</div>";
        }).join("")
      : '<div class="dim">No network address found</div>';
    $("noteLan").textContent = net.lan_note;
    if (pick) {
      show($("qrLan").parentNode, true);
      $("qrLan").src = "/setup/qr.png?u=" + encodeURIComponent(pick) + "&size=200";
    } else {
      show($("qrLan").parentNode, false);
    }

    $("abStore").textContent = net.store_code;
    $("abVersion").textContent = "v" + net.version;
    $("abTime").textContent = new Date(net.time).toLocaleString();
    $("abPos").textContent = net.pos_count;

    // POS management is manager-only
    var isMgr = state.staff && state.staff.role === "manager";
    show($("posBox"), !!isMgr);
    if (isMgr) await loadPos();
    else $("posList").innerHTML = "";

    refreshBars();
  }

  async function loadPos() {
    try {
      var d = await api("GET", "/api/v1/pos");
      state.posCount = d.pos_count;
      $("abPos").textContent = d.pos_count;
      $("posList").innerHTML = d.counters.map(function (c) {
        return '<div class="addr-row"><b>' + esc(c.label) + "</b>" +
          '<span class="dim">' + c.today_count + " SO today</span></div>";
      }).join("");
      $("posAdd").disabled = d.pos_count >= d.max_pos;
      $("posRemove").disabled = d.pos_count <= 1;
    } catch (e) { toast(e.message, "bad"); }
  }

  function wireSetup() {
    $("setupClose").addEventListener("click", function () {
      if (state.token && state.staff) go(state.prevTab || "scan");
      else { show($("screen-setup"), false); show($("screen-login"), true); }
    });
    $("posAdd").addEventListener("click", async function () {
      try {
        var r = await api("POST", "/api/v1/pos/add");
        toast("POS " + r.added + " added", "ok");
        await loadPos();
      } catch (e) { toast(e.message, "bad", 6000); }
    });
    $("posRemove").addEventListener("click", async function () {
      try {
        var r = await api("POST", "/api/v1/pos/remove");
        toast("POS " + r.removed + " switched off", "warn");
        await loadPos();
      } catch (e) { toast(e.message, "bad", 6000); }
    });
  }

  // --------------------------------------------------------------- camera
  function camHint(m) { $("camHint").textContent = m; }

  async function startCamera() {
    if (state.scanning) return;
    if (!window.isSecureContext) {
      camHint("Camera needs HTTPS — use Manual Entry, or open on the server itself");
      toast("Camera unavailable without HTTPS", "bad", 6000);
      return;
    }
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      camHint("Camera not available — use Manual Entry");
      toast("Camera not available", "bad");
      return;
    }
    if (!window.ZXing) { camHint("Scanner library failed to load — use Manual Entry"); return; }
    try {
      camHint("Starting camera…");
      var hints = new Map();
      hints.set(ZXing.DecodeHintType.TRY_HARDER, true);
      hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [
        ZXing.BarcodeFormat.CODE_128, ZXing.BarcodeFormat.CODE_39,
        ZXing.BarcodeFormat.EAN_13, ZXing.BarcodeFormat.EAN_8,
        ZXing.BarcodeFormat.UPC_A, ZXing.BarcodeFormat.UPC_E,
        ZXing.BarcodeFormat.ITF, ZXing.BarcodeFormat.CODABAR,
        ZXing.BarcodeFormat.QR_CODE, ZXing.BarcodeFormat.DATA_MATRIX,
      ]);
      state.reader = new ZXing.BrowserMultiFormatReader(hints, { delayBetweenScanAttempts: 120 });
      state.scanning = true;
      show($("camStart"), false); show($("camStop"), true);
      camHint("Point the camera at the SO barcode");

      await state.reader.decodeFromConstraints(
        { video: { facingMode: { ideal: "environment" }, width: { ideal: 1280 }, height: { ideal: 720 } } },
        $("video"),
        function (result) {
          if (!result) return;
          var text = result.getText();
          if (!text) return;
          var t = Date.now();
          if (text === state.lastCode && t - state.lastCodeAt < COOLDOWN_MS) return;
          state.lastCode = text; state.lastCodeAt = t;
          handleScan(text);
        }
      );
      var st = $("video").srcObject;
      var track = st && st.getVideoTracks ? st.getVideoTracks()[0] : null;
      if (track) {
        state.track = track;
        var caps = {};
        try { caps = track.getCapabilities ? track.getCapabilities() : {}; } catch (e) {}
        show($("torchBtn"), !!caps.torch);
      }
    } catch (e) {
      state.scanning = false;
      show($("camStart"), true); show($("camStop"), false);
      var m = String((e && e.name) || e);
      if (m.indexOf("NotAllowed") >= 0) camHint("Camera permission denied — allow it in your browser settings");
      else if (m.indexOf("NotFound") >= 0) camHint("No camera found on this device");
      else camHint("Camera failed: " + m + " — use Manual Entry");
      toast("Could not open the camera", "bad");
    }
  }

  function stopCamera() {
    try { if (state.reader) state.reader.reset(); } catch (e) {}
    if ($("video").srcObject) {
      $("video").srcObject.getTracks().forEach(function (t) { t.stop(); });
      $("video").srcObject = null;
    }
    state.reader = null; state.scanning = false; state.track = null; state.torchOn = false;
    show($("camStart"), true); show($("camStop"), false); show($("torchBtn"), false);
    camHint("Camera stopped");
  }

  async function toggleTorch() {
    if (!state.track) return;
    try {
      state.torchOn = !state.torchOn;
      await state.track.applyConstraints({ advanced: [{ torch: state.torchOn }] });
      $("torchBtn").textContent = state.torchOn ? "🔦 Flash On" : "🔦 Flash";
    } catch (e) { toast("Flash not supported", "bad"); }
  }

  // ------------------------------------------------------------------ scan
  async function handleScan(soNumber) {
    if (state.submitting) return;
    state.submitting = true;
    buzz(60);
    try {
      var r = await api("POST", "/api/v1/requests/scan", { so_number: soNumber });
      renderResult(r);
      beep(r.duplicate ? 500 : 1000, r.duplicate ? 0.10 : 0.16);
      buzz(r.duplicate ? 40 : 90);
    } catch (e) {
      if (!navigator.onLine) {
        await outboxAdd(soNumber); await refreshBars();
        beep(700, 0.12);
        renderResult({ ref_no: "PENDING", so_number: soNumber, offline: true });
        toast("Offline — scan saved", "warn");
      } else { toast(e.message, "bad", 5000); beep(300, 0.25); }
    } finally { setTimeout(function () { state.submitting = false; }, 600); }
  }

  function renderResult(r) {
    var el = $("lastResult"), cls = "result", lbl, note;
    if (r.offline) {
      cls += " dup"; lbl = "OFFLINE — SAVED"; note = "It will be sent as soon as you are back online";
    } else if (r.duplicate) {
      cls += " dup"; lbl = "ALREADY SCANNED"; note = r.duplicate_reason || "This SO is already in progress";
    } else if (r.format_ok === false) {
      cls += " badfmt"; lbl = "SCANNED — UNUSUAL FORMAT";
      note = "This does not match the normal SO format (MACSO26-xxxxxxxx). Check the barcode.";
    } else {
      cls += " new"; lbl = "SCANNED ✓"; note = "A POS can now claim this SO";
    }

    el.className = cls;
    el.innerHTML =
      '<div class="result-lbl">' + esc(lbl) + "</div>" +
      '<div class="result-no">' + esc(r.ref_no) + "</div>" +
      '<div class="result-bc">' + esc(r.so_number) + "</div>" +
      '<div class="result-note">' + esc(note) + "</div>";
    show(el, true);
    if (!r.duplicate && !r.offline) setTimeout(function () { show($("lastResult"), false); }, 9000);
  }

  // ----------------------------------------------------------------- board
  function minsLabel(m) {
    if (m == null) return "—";
    if (m < 1) return "<1 min";
    return Math.round(m) + " min";
  }

  function fmtTime(s) {
    if (!s) return "-";
    var d = new Date(s);
    if (isNaN(d)) return "-";
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  }

  async function loadBoard(silent) {
    var view = VIEW_OF_TAB[state.tab];
    if (!view) return;
    try {
      var q = await api("GET", "/api/v1/requests?view=" + view);
      var rows = q.requests || [];
      if (state.tab === "done") rows = rows.filter(function (r) { return r.status === "delivered"; });

      $("bOpen").textContent = state.tab === "done" ? q.delivered_count
        : state.tab === "mine" ? rows.filter(function (r) {
            return r.status === "scanned" || r.status === "assigned"; }).length
        : q.open_count;
      $("bDone").textContent = q.delivered_count;
      $("bAttention").textContent = q.attention_count;

      if (q.attention_count > 0) {
        $("attentionMsg").textContent = q.attention_count + " SO(s) need attention — see the red cards";
        $("attentionBar").className = "breach-bar";
      } else {
        $("attentionBar").className = "breach-bar hidden";
      }

      var pend = rows.filter(function (r) {
        return r.status !== "delivered" && r.status !== "cancelled";
      }).length;
      if (state.staff.role === "pos") document.title = (pend ? pend + " SO — " : "") + "QMS P" + state.staff.pos_number;
      else if (state.staff.role === "backstore") document.title = (pend ? pend + " to deliver — " : "") + "QMS";
      else document.title = "QMS — SO Fulfilment";

      renderBoard(rows);
    } catch (e) { if (!silent) toast(e.message, "bad", 5000); }
  }

  function renderBoard(rows) {
    var el = $("boardList");
    var role = state.staff.role;

    if (!rows.length) {
      el.innerHTML = '<div class="empty">' + (EMPTY[state.tab] || "Nothing here.") + "</div>";
      return;
    }

    var sorted = rows.slice().sort(function (a, b) {
      if (state.tab === "done" || state.tab === "today" || state.tab === "mine") {
        return (b.seq || 0) - (a.seq || 0);
      }
      var ra = a.status === "scanned", rb = b.status === "scanned";
      if (state.tab === "todeliver" && ra !== rb) return ra ? 1 : -1;  // claimed work first
      return (a.seq || 0) - (b.seq || 0);                             // otherwise FIFO
    });

    var t0 = Date.now();
    sorted.forEach(function (r) {
      if (state.seenIds[r.id] && state.prevStatus[r.id] && state.prevStatus[r.id] !== r.status) {
        state.flashUntil[r.id] = t0 + FLASH_MS;
      }
      state.prevStatus[r.id] = r.status;
      state.seenIds[r.id] = true;
      if (state.flashUntil[r.id] && state.flashUntil[r.id] < t0) delete state.flashUntil[r.id];
    });

    el.innerHTML = sorted.map(function (r) { return card(r, role); }).join("");
  }

  function card(r, role) {
    var open = r.status === "scanned" || r.status === "assigned";
    var timeVal, timeCls;
    if (open) { timeVal = minsLabel(r.elapsed_minutes); timeCls = r.attention ? "over" : "ok"; }
    else if (r.status === "delivered") { timeVal = "✓ " + minsLabel(r.total_minutes); timeCls = "dimmed"; }
    else { timeVal = "cancelled"; timeCls = "dimmed"; }

    var meta = "Scanned " + fmtTime(r.scanned_at) + " · " + esc(r.scanned_by_name || "-");
    if (r.claimed_at) {
      meta += "<br>POS " + r.pos_number + " claimed " + fmtTime(r.claimed_at) +
              " · " + esc(r.claimed_by_name || "-");
    }
    if (r.delivered_at) {
      meta += "<br>Delivered " + fmtTime(r.delivered_at) + " · " + esc(r.delivered_by_name || "-");
    }

    // the line the backstore actually reads
    var dest = "";
    if (r.pos_number) {
      dest = '<div class="so-dest">→ DELIVER TO POS ' + r.pos_number + "</div>";
    } else if (r.status === "scanned") {
      dest = '<div class="so-dest none">→ no POS has claimed this yet</div>';
    } else if (r.status === "delivered") {
      dest = '<div class="so-dest none">→ delivered without a POS</div>';
    }

    var attn = "";
    if (r.attention && r.status !== "cancelled") {
      attn = '<div class="attn">⚠ ' + esc(r.attention_reason) + "</div>";
    }
    if (r.format_ok === false) {
      attn += '<div class="fmt">⚠ Not a normal SO format (MACSO26-xxxxxxxx)</div>';
    }

    var actions = "";
    if (open) {
      var btns = [];
      if (role === "pos" && r.status === "scanned") {
        btns.push('<button class="btn btn-claim btn-big" data-act="claim" data-id="' + r.id +
                  '">CLAIM FOR POS ' + (state.staff.pos_number || "") + "</button>");
      }
      if (role === "backstore" && r.status === "assigned") {
        btns.push('<button class="btn btn-done btn-big" data-act="deliver" data-id="' + r.id +
                  '">✓ DELIVERED TO POS ' + r.pos_number + "</button>");
      }
      if (role === "backstore" && r.status === "scanned") {
        btns.push('<button class="btn btn-dark btn-big" data-act="deliver" data-id="' + r.id +
                  '">Mark Delivered (no POS)</button>');
      }
      if (role === "pos" && r.status === "assigned" && r.pos_number === state.staff.pos_number) {
        btns.push('<button class="btn btn-dark" data-act="release" data-id="' + r.id +
                  '">Release</button>');
      }
      if (role === "scanner" || role === "manager") {
        btns.push('<button class="btn btn-dark" data-act="cancel" data-id="' + r.id + '">Cancel</button>');
      }
      if (btns.length) actions = '<div class="so-actions">' + btns.join("") + "</div>";
    }

    return '<div class="so ' + r.status + (r.attention ? " attention" : "") +
      (state.flashUntil[r.id] ? " flash" : "") + '">' +
      '<div class="so-head">' +
        '<div class="so-ref">' + esc(r.ref_no) + "</div>" +
        '<div><span class="badge ' + r.status + '">' + (STATUS_LABEL[r.status] || r.status) + "</span> " +
        (r.pos_number ? '<span class="badge pos">P' + r.pos_number + "</span> " : "") +
        '<span class="so-time ' + timeCls + '">' + timeVal + "</span></div>" +
      "</div>" +
      '<div class="so-num">' + esc(r.so_number) + "</div>" +
      dest + attn +
      '<div class="so-meta">' + meta + "</div>" + actions +
      "</div>";
  }

  async function act(id, what) {
    try {
      var body = what === "cancel" ? { reason: "cancelled from app" } : null;
      var r = await api("POST", "/api/v1/requests/" + id + "/" + what, body);
      buzz(60);
      if (what === "claim") { toast("Claimed for POS " + r.pos_number + " ✓", "ok"); beep(1000, 0.12); }
      else if (what === "deliver") {
        toast(r.warning ? "Delivered — " + r.warning : "Marked delivered ✓", r.warning ? "warn" : "ok", 5000);
        beep(1000, 0.14);
      } else if (what === "release") toast("SO released back to the pool", "warn");
      else toast("Done", "ok");
      loadBoard();
    } catch (e) {
      toast(e.message, "bad", 6000);
      beep(300, 0.25);
      loadBoard(true);
    }
  }

  // ----------------------------------------------------------------- stats
  async function loadStats() {
    try {
      var s = await api("GET", "/api/v1/stats/today");
      $("statsDay").textContent = s.day;
      var posRows = Object.keys(s.by_pos).sort().map(function (k) {
        return '<div class="bar-row"><span>' + esc(k) + "</span><b>" + s.by_pos[k] + "</b></div>";
      }).join("") || '<div class="bar-row dim">Nothing yet</div>';
      var scRows = Object.keys(s.by_scanner).map(function (k) {
        return '<div class="bar-row"><span>' + esc(k) + "</span><b>" + s.by_scanner[k] + "</b></div>";
      }).join("") || '<div class="bar-row dim">Nothing yet</div>';

      $("statsBody").innerHTML =
        box("Total SOs", s.total) +
        box("Completed", s.delivered, "var(--ok)") +
        box("Open", s.scanned + s.assigned, "var(--warn)") +
        box("Attention", s.attention, "var(--bad)") +
        box("Avg wait for POS", s.avg_wait_pos + "m") +
        box("Avg POS → delivered", s.avg_wait_deliver + "m") +
        box("Avg total", s.avg_total + "m") +
        box("Slowest", s.max_total + "m", "var(--bad)") +
        box("Within 5 min", s.under_5, "var(--ok)") +
        box("Over SLA", s.over_sla, "var(--bad)") +
        box("Delivered w/o POS", s.no_pos_delivered, s.no_pos_delivered ? "var(--bad)" : "var(--dim)") +
        box("Cancelled", s.cancelled) +
        '<div class="sbox wide"><div class="sbox-l">SOs by POS</div>' + posRows + "</div>" +
        '<div class="sbox wide"><div class="sbox-l">Scans by Scanner</div>' + scRows + "</div>";
      $("exportLink").href = "/api/v1/stats/export.csv";
    } catch (e) { toast(e.message, "bad", 5000); }
  }

  function box(lbl, val, color) {
    return '<div class="sbox"><div class="sbox-v"' + (color ? ' style="color:' + color + '"' : "") +
      ">" + esc(val) + '</div><div class="sbox-l">' + esc(lbl) + "</div></div>";
  }

  // ------------------------------------------------------------------ wire
  function wire() {
    $("camStart").addEventListener("click", startCamera);
    $("camStop").addEventListener("click", stopCamera);
    $("torchBtn").addEventListener("click", toggleTorch);

    $("manualToggle").addEventListener("click", function () {
      var w = $("manualWrap");
      show(w, w.classList.contains("hidden"));
      if (!w.classList.contains("hidden")) $("manualCode").focus();
    });
    $("manualSubmit").addEventListener("click", function () {
      var v = ($("manualCode").value || "").trim();
      if (!v) return;
      $("manualCode").value = "";
      handleScan(v);
    });
    $("manualCode").addEventListener("keydown", function (e) {
      if (e.key === "Enter") $("manualSubmit").click();
    });

    document.querySelectorAll(".switch-role").forEach(function (b) {
      b.addEventListener("click", function () { doLogout(false); });
    });

    $("refreshBoard").addEventListener("click", function () { loadBoard(); });

    $("boardList").addEventListener("click", function (e) {
      var btn = e.target.closest("button[data-act]");
      if (btn) act(btn.dataset.id, btn.dataset.act);
    });

    window.addEventListener("online", function () { refreshBars(); flushOutbox(); loadBoard(true); });
    window.addEventListener("offline", refreshBars);
    document.addEventListener("visibilitychange", function () {
      if (document.visibilityState === "visible") {
        refreshBars(); flushOutbox();
        var t = state.tab;
        if (t && t !== "scan" && t !== "stats" && t !== "setup") loadBoard(true);
      }
    });

    wireSetup();
  }

  function boot() {
    wire();
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/service-worker.js").catch(function () {});
    }
    loadNetwork().then(function () { restoreSession(); });
    setInterval(function () { if (state.token) refreshBars(); }, 15000);
  }

  document.addEventListener("DOMContentLoaded", boot);
  window.QMS = state;
})();
