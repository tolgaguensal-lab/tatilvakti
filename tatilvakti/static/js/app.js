/* tatilvakti – progressive enhancement. Every page also works without this file. */
(function () {
  "use strict";

  var S = {};
  try { S = JSON.parse(document.getElementById("tv-strings").textContent); } catch (e) { S = {}; }
  var $ = function (sel, root) { return (root || document).querySelector(sel); };
  var $$ = function (sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); };
  var fmt = function (str, vars) { return String(str || "").replace(/\{(\w+)\}/g, function (_, k) { return vars[k] != null ? vars[k] : ""; }); };
  // Wert für einen Attribut-Selektor ['…"' + q(v) + '"']: Fremdwerte (URL, Speicher, API) dürfen
  // querySelector nie werfen lassen, sonst fällt das ganze Skript aus
  var q = function (value) {
    value = String(value);
    if (window.CSS && CSS.escape) return CSS.escape(value);
    return value.replace(/[^\w-]/g, function (ch) { return "\\" + ch.charCodeAt(0).toString(16) + " "; });
  };

  // ---------------------------------------------------------- local storage
  // Only per-device conveniences. Never required for the page to work.
  var store = {
    get: function (key, fallback) {
      try { var v = window.localStorage.getItem("tv." + key); return v == null ? fallback : JSON.parse(v); } catch (e) { return fallback; }
    },
    set: function (key, value) {
      try { window.localStorage.setItem("tv." + key, JSON.stringify(value)); } catch (e) { /* private mode */ }
    },
    remove: function (key) {
      try { window.localStorage.removeItem("tv." + key); } catch (e) { /* private mode */ }
    }
  };
  // Besuchszähler und Zeitpunkt des letzten Aufrufs gibt es nicht mehr (Datensparsamkeit,
  // § 25 TDDDG): Werte älterer Versionen beim Laden entfernen
  store.remove("visits");
  store.remove("seen_at");

  $$(".js-hide").forEach(function (el) { el.hidden = true; });
  $$(".chips__item.is-active").forEach(function (chip) {
    var bar = chip.parentNode;
    bar.scrollLeft = chip.offsetLeft - (bar.clientWidth - chip.offsetWidth) / 2;
  });

  // ---------------------------------------------------------- relative times
  function ago(ts) {
    var diff = Math.max(0, Math.floor(Date.now() / 1000) - ts);
    if (diff < 60) return S.ago_now;
    if (diff < 3600) return fmt(S.ago_min, { n: Math.floor(diff / 60) });
    if (diff < 48 * 3600) return fmt(S.ago_h, { n: Math.floor(diff / 3600) });
    return fmt(S.ago_d, { n: Math.floor(diff / 86400) });
  }
  function refreshTimes() {
    $$("time[data-ts]").forEach(function (el) { el.textContent = ago(parseInt(el.getAttribute("data-ts"), 10)); });
  }
  refreshTimes();
  setInterval(refreshTimes, 30000);

  // ---------------------------------------------------------- offline banner
  var banner = $("[data-offline-banner]");
  function onlineState() { if (banner) banner.hidden = navigator.onLine !== false; }
  window.addEventListener("online", onlineState);
  window.addEventListener("offline", onlineState);
  onlineState();

  // ---------------------------------------------------------- preferences
  // Bundesland nur als Kürzel aus zwei Buchstaben übernehmen (?land= kommt aus geteilten Links)
  function stateCode(value) {
    var code = typeof value === "string" ? value.toUpperCase() : "";
    return /^[A-Z]{2}$/.test(code) ? code : "";
  }
  var params = new URLSearchParams(window.location.search);
  var urlState = stateCode(params.get("land"));
  var state = urlState || stateCode(store.get("state", ""));
  var mode = store.get("mode", "car");

  function applyState(code) {
    $$("[data-per-state]").forEach(function (el) {
      var key = el.getAttribute("data-per-state");
      el.hidden = code ? key !== code : key !== "none";
    });
    if (code && !$('[data-per-state="' + q(code) + '"]')) {
      var none = $('[data-per-state="none"]');
      if (none) none.hidden = false;
    }
    $$("[data-tl-state]").forEach(function (el) {
      el.classList.toggle("is-you", el.getAttribute("data-tl-state") === code);
    });
    $$("[data-state-link]").forEach(function (a) {
      var url = new URL(a.getAttribute("href"), window.location.href);
      if (code) url.searchParams.set("land", code); else url.searchParams.delete("land");
      a.setAttribute("href", url.pathname + url.search);
    });
    $$('select[data-pref="state"]').forEach(function (sel) { sel.value = code || ""; });
  }
  function applyMode(m) {
    document.body.classList.toggle("mode-plane", m === "plane");
    $$('[data-pref="mode"] input').forEach(function (inp) { inp.checked = inp.value === m; });
  }
  applyState(state);
  applyMode(mode);

  $$('select[data-pref="state"]').forEach(function (sel) {
    sel.addEventListener("change", function () {
      state = sel.value;
      store.set("state", state);
      applyState(state);
      showA2hs();
      if (window.history && window.history.replaceState && $(".tl")) {
        var url = new URL(window.location.href);
        if (state) url.searchParams.set("land", state); else url.searchParams.delete("land");
        window.history.replaceState(null, "", url.pathname + url.search);
      }
    });
  });
  $$('[data-pref="mode"] input').forEach(function (inp) {
    inp.addEventListener("change", function () { mode = inp.value; store.set("mode", mode); applyMode(mode); });
  });
  $$("form[data-prefs]").forEach(function (form) {
    form.addEventListener("submit", function (ev) { ev.preventDefault(); });
  });

  // ---------------------------------------------------------- share
  // WhatsApp-Knopf ist ein normaler wa.me-Link. Das Teilen-Menü des Geräts bekommt einen eigenen
  // Knopf, sichtbar nur, wo es navigator.share gibt (Abbrechen durch den Nutzer ist kein Fehler).
  if (navigator.share) {
    $$("[data-share]").forEach(function (btn) {
      btn.hidden = false;
      btn.addEventListener("click", function () {
        navigator.share({ text: btn.getAttribute("data-share-text"), url: btn.getAttribute("data-share-url") }).catch(function () {});
      });
    });
  }

  // ---------------------------------------------------------- letzte Meldung (Übergang, Richtung)
  // Nur Übergang und Richtung der letzten Meldung: Die Startseite zeigt diesen Übergang zuerst,
  // das Formular schlägt die Richtung vor. Werte aus dem Speicher nur in bekannter Form übernehmen.
  var DIRECTIONS = ["to_tr", "to_de"];
  var lastReport = (function () {
    var v = store.get("report_pref", null) || {};
    return {
      cid: typeof v.cid === "string" && /^[a-z0-9-]{1,40}$/.test(v.cid) ? v.cid : "",
      direction: DIRECTIONS.indexOf(v.direction) !== -1 ? v.direction : ""
    };
  })();
  var picker = $("[data-picker]");
  var picked = lastReport.cid && picker && $('[data-pick="' + q(lastReport.cid) + '"]', picker);
  if (picked) {
    picked.hidden = false;
    picker.insertBefore(picked, picker.firstChild);
    var lastLabel = $(".picker__last", picked);
    if (lastLabel) lastLabel.hidden = false;
  }

  // ---------------------------------------------------------- checklist
  var checks = store.get("checks", {});
  $$("input[data-check]").forEach(function (box) {
    var id = box.getAttribute("data-check");
    box.checked = !!checks[id];
    box.addEventListener("change", function () { checks[id] = box.checked; store.set("checks", checks); });
  });

  // ---------------------------------------------------------- border status
  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text != null) node.textContent = text;
    return node;
  }
  function renderStatus(box, st) {
    var compact = box.classList.contains("status--compact");
    box.className = "status status--" + st.level + (compact ? " status--compact" : "");
    box.setAttribute("data-live", st.state === "live" ? "1" : "0");
    var body = $(".status__body", box);
    body.textContent = "";
    var main = st.state === "live" ? S["b_bucket_" + st.bucket]
      : (compact ? S.b_level_none : (st.last_at ? S.b_no_reports : S.b_no_reports_ever));
    body.appendChild(el("span", "status__main", main));
    var meta = el("span", "status__meta");
    if (compact) {
      if (st.state === "live") {
        meta.appendChild(el("span", "status__level", S["b_level_" + st.level]));
        meta.appendChild(document.createTextNode(" · " + st.count + "× · "));
        var t = el("time", null, ago(st.last_at));
        t.setAttribute("data-ts", st.last_at);
        meta.appendChild(t);
      }
      body.appendChild(meta);
      return;
    }
    if (st.state === "live") {
      meta.appendChild(el("span", "status__level", S["b_level_" + st.level]));
      var key = st.count === 1 ? "b_reports_1" : "b_reports_n";
      meta.appendChild(document.createTextNode(" · " + fmt(S[key], { n: st.count, h: Math.round(st.window_min / 60) })));
    }
    if (st.last_at) {
      if (st.state === "live") meta.appendChild(document.createTextNode(" · "));
      var last = el("span", "status__last");
      var parts = String(S.b_last_report).split("{ago}");
      last.appendChild(document.createTextNode(parts[0] || ""));
      var time = el("time", null, ago(st.last_at));
      time.setAttribute("data-ts", st.last_at);
      time.setAttribute("datetime", new Date(st.last_at * 1000).toISOString());
      last.appendChild(time);
      last.appendChild(document.createTextNode(parts[1] || ""));
      meta.appendChild(last);
    }
    body.appendChild(meta);
  }
  function applyCrossing(c) {
    Object.keys(c.directions).forEach(function (dir) {
      var st = c.directions[dir];
      $$('[data-status][data-cid="' + q(c.id) + '"][data-dir="' + q(dir) + '"]').forEach(function (box) { renderStatus(box, st); });
      $$('[data-node][data-cid="' + q(c.id) + '"]').forEach(function (node) { node.setAttribute("data-l-" + dir, st.level); });
    });
  }
  // Leerzustand nachziehen: Übergang ohne aktuelle Meldung (data-dirs) bzw. ganze Liste (data-live-list)
  function updateEmpty() {
    $$("[data-dirs]").forEach(function (box) {
      box.classList.toggle("is-empty", !$('[data-status][data-live="1"]', box));
    });
    $$("[data-live-list]").forEach(function (list) {
      list.classList.toggle("is-empty", !$("[data-dirs]:not(.is-empty)", list));
    });
  }
  function refreshBorders() {
    if (!$("[data-status]") || document.hidden || navigator.onLine === false) return;
    fetch("/api/v1/borders", { headers: { Accept: "application/json" } })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (data) { if (data && data.crossings) { data.crossings.forEach(applyCrossing); updateEmpty(); } })
      .catch(function () {});
  }
  if ($("[data-status]")) {
    setInterval(refreshBorders, 90000);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) refreshBorders(); });
  }

  var dirSwitch = $("[data-dir-switch]");
  var map = $("[data-map]");
  if (dirSwitch && map) {
    dirSwitch.hidden = false;
    $$("button", dirSwitch).forEach(function (btn) {
      btn.addEventListener("click", function () {
        map.setAttribute("data-dir", btn.getAttribute("data-dir"));
        $$("button", dirSwitch).forEach(function (b) {
          var on = b === btn;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
        });
      });
    });
  }

  // ---------------------------------------------------------- reports (+ offline queue)
  var MAX_AGE = 90 * 60;
  var reportMsg = $("[data-report-msg]");
  function say(kind, text) {
    if (!reportMsg) return;
    reportMsg.className = "flash flash--" + kind;
    reportMsg.textContent = text;
  }
  function postReport(item) {
    return fetch("/api/v1/borders/" + encodeURIComponent(item.cid) + "/reports", {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ direction: item.direction, bucket: item.bucket, observed_at: item.observed_at })
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) { return { status: r.status, data: data }; });
    });
  }
  // Angenommene Meldung (201, direkt oder aus der Warteschlange): Übergang und Richtung merken –
  // die Startseite zeigt ihn zuerst, das Formular schlägt die Richtung vor – und danach den
  // Hinweis zum Startbildschirm anbieten. Abgelehnte Meldungen zählen nicht.
  function reported(item) {
    lastReport = { cid: item.cid, direction: item.direction };
    store.set("report_pref", lastReport);
    showA2hs();
  }
  // Nachliefern: bei Netzfehler oder 5xx zurück in die Warteschlange, bei 4xx verwerfen
  // (zu alt, Limit, ungültig – ein neuer Versuch ändert daran nichts)
  var flushing = false;
  function flushQueue() {
    var queue = store.get("queue", []);
    if (flushing || !queue.length || navigator.onLine === false) return;
    var now = Math.floor(Date.now() / 1000);
    queue = queue.filter(function (q) { return now - q.observed_at < MAX_AGE; });
    store.set("queue", []);
    flushing = true;
    var delivered = 0;
    var requeue = function (item) { var rest = store.get("queue", []); rest.push(item); store.set("queue", rest); };
    Promise.all(queue.map(function (item) {
      return postReport(item).then(function (res) {
        if (res.status === 201) {
          delivered++;
          reported(item);
          if (res.data && res.data.crossing) { applyCrossing(res.data.crossing); updateEmpty(); }
        } else if (res.status >= 500 || res.status === 0) {
          requeue(item);
        }
      }, function () { requeue(item); });
    })).then(function () {
      flushing = false;
      if (delivered) say("ok", S.b_report_delivered);
    });
  }
  window.addEventListener("online", flushQueue);
  document.addEventListener("visibilitychange", function () { if (!document.hidden) flushQueue(); });
  setInterval(flushQueue, 60000);
  flushQueue();

  $$("form[data-report]").forEach(function (form) {
    // Richtung der letzten Meldung vorschlagen, sonst nichts vorbelegen. Hat der Browser eine Auswahl
    // wiederhergestellt (Zurück-Taste), bleibt sie.
    var lastDir = lastReport.direction && form.querySelector('input[name="direction"][value="' + q(lastReport.direction) + '"]');
    if (lastDir && !form.querySelector('input[name="direction"]:checked')) lastDir.checked = true;
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var dir = form.querySelector('input[name="direction"]:checked');
      var bucket = form.querySelector('input[name="bucket"]:checked');
      var hp = form.querySelector('input[name="website"]');
      if (!dir || !bucket) { form.reportValidity && form.reportValidity(); return; }
      if (hp && hp.value) { say("ok", S.b_report_thanks); return; }
      var item = { cid: form.getAttribute("data-cid"), direction: dir.value, bucket: parseInt(bucket.value, 10), observed_at: Math.floor(Date.now() / 1000) };
      var button = form.querySelector('button[type="submit"]');
      if (button) button.disabled = true;
      function done() { if (button) button.disabled = false; }
      var queueIt = function () {
        var queue = store.get("queue", []); queue.push(item); store.set("queue", queue);
        say("queued", S.b_report_queued); bucket.checked = false; done();
      };
      if (navigator.onLine === false) { queueIt(); return; }
      postReport(item).then(function (res) {
        done();
        if (res.status === 201) {
          say("ok", S.b_report_thanks);
          bucket.checked = false;
          reported(item);
          if (res.data && res.data.crossing) { applyCrossing(res.data.crossing); updateEmpty(); }
        } else if (res.status === 429) {
          // crossing_busy: the crossing-wide cap is full – affects everyone, not just this client
          say("error", res.data && res.data.detail === "crossing_busy" ? S.b_report_busy : S.b_report_ratelimited);
        } else if (res.status === 422) {
          say("error", S.b_report_stale);
        } else if (res.status >= 500 || res.status === 0) {
          queueIt();
        } else {
          say("error", S.b_report_error);
        }
      }).catch(queueIt);
    });
  });

  // ---------------------------------------------------------- customs search
  function fold(text) {
    return String(text || "").replace(/ı/g, "i").replace(/İ/g, "i").replace(/ß/g, "ss").toLowerCase()
      .normalize("NFKD").replace(/[̀-ͯ]/g, "");
  }
  var tools = $("[data-customs-tools]");
  if (tools) {
    tools.hidden = false;
    // Sticky-Toolbar: Sprungziele landen darunter (scroll-margin in app.css). Den Tastaturfokus
    // (z. B. Shift+Tab zurück in die Liste) schieben wir selbst darunter, denn scroll-margin beachten
    // Browser beim Fokussieren nicht. Sofort und noch einmal im nächsten Frame, falls der Browser
    // erst danach scrollt. Dasselbe nach einem Sprung, für Browser ohne scroll-margin (Safari < 14.1).
    document.documentElement.classList.add("has-toolbar");
    var later = window.requestAnimationFrame || setTimeout;
    var behindTools = function (el) { return !!el && !tools.contains(el) && !!(tools.compareDocumentPosition(el) & 4); };
    var uncover = function (el) {
      var bottom = tools.getBoundingClientRect().bottom;
      var top = el.getBoundingClientRect().top;
      if (top < bottom) window.scrollBy(0, top - bottom - 14);
    };
    document.addEventListener("focusin", function (ev) {
      var target = ev.target;
      if (!target.getBoundingClientRect || !behindTools(target)) return;
      uncover(target);
      later(function () { uncover(target); });
    });
    var uncoverHash = function () {
      var target = window.location.hash.length > 1 && document.getElementById(window.location.hash.slice(1));
      if (behindTools(target)) later(function () { uncover(target); });
    };
    window.addEventListener("hashchange", uncoverHash);
    window.addEventListener("load", uncoverHash);
    var input = $("[data-customs-search]", tools);
    var empty = $("[data-customs-empty]");
    var dirFilter = "all";
    var filter = function () {
      var terms = fold(input.value).split(/\s+/).filter(Boolean);
      var visible = 0;
      $$("[data-customs-section]").forEach(function (section) {
        var dirOk = dirFilter === "all" || section.getAttribute("data-customs-section") === dirFilter;
        var shown = 0;
        $$("[data-rule]", section).forEach(function (rule) {
          var hay = rule.getAttribute("data-search");
          var ok = dirOk && terms.every(function (t) { return hay.indexOf(t) !== -1; });
          rule.hidden = !ok;
          if (ok) shown++;
        });
        section.hidden = shown === 0;
        visible += shown;
      });
      if (empty) empty.hidden = visible !== 0;
    };
    input.addEventListener("input", filter);
    $$("[data-customs-dir]", tools).forEach(function (btn) {
      btn.addEventListener("click", function () {
        dirFilter = btn.getAttribute("data-customs-dir");
        $$("[data-customs-dir]", tools).forEach(function (b) {
          var on = b === btn;
          b.classList.toggle("is-active", on);
          b.setAttribute("aria-pressed", on ? "true" : "false");
        });
        filter();
      });
    });
  }

  // ---------------------------------------------------------- Startbildschirm-Hinweis
  // Nur außerhalb der installierten App und erst, wenn die Person ein Bundesland gewählt oder
  // erfolgreich eine Wartezeit gemeldet hat – ohne Besuchszähler. Die Karte gibt es nur auf
  // Startseite, Grenz-Übersicht und Übergangsseiten (base.html).
  // Chromium: eigener Button (beforeinstallprompt), iOS: Kurzanleitung über „Teilen“.
  function isStandalone() {
    var mm = window.matchMedia;
    return navigator.standalone === true || !!(mm && ["standalone", "fullscreen", "minimal-ui"].some(function (m) {
      return mm("(display-mode: " + m + ")").matches;
    }));
  }
  var a2hs = $("[data-a2hs]");
  var installEvent = null;
  var isIos = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);

  function a2hsWanted() {
    return !!a2hs && !isStandalone() && !store.get("a2hs_off", false)
      && (!!stateCode(store.get("state", "")) || !!lastReport.cid);
  }
  function showA2hs() {
    var ios = !installEvent && isIos;
    if (!a2hsWanted() || !(installEvent || ios)) return;
    $("[data-a2hs-install]", a2hs).hidden = !installEvent;
    $("[data-a2hs-ios]", a2hs).hidden = !ios;
    a2hs.hidden = false;
    document.documentElement.classList.add("has-a2hs");
  }
  function hideA2hs(remember) {
    if (!a2hs) return;
    a2hs.hidden = true;
    document.documentElement.classList.remove("has-a2hs");
    if (remember) store.set("a2hs_off", true);
  }
  // Speicher dauerhaft machen – nur in der installierten App, dort ohne Rückfrage an den Nutzer.
  // Mehrfaches Anfragen schadet nicht; ist er schon dauerhaft, passiert nichts.
  function persistStorage() {
    var st = navigator.storage;
    if (!st || !st.persist || !st.persisted) return;
    st.persisted().then(function (done) { return done || st.persist(); }).catch(function () {});
  }

  window.addEventListener("beforeinstallprompt", function (ev) {
    installEvent = ev;
    // Chromes eigene Infoleiste nur unterdrücken, wenn stattdessen unsere Karte erscheint
    if (a2hsWanted()) { ev.preventDefault(); showA2hs(); }
  });
  window.addEventListener("appinstalled", function () {
    installEvent = null;
    hideA2hs(true);
    persistStorage();
  });
  if (a2hs) {
    $("[data-a2hs-close]", a2hs).addEventListener("click", function () { hideA2hs(true); });
    $("[data-a2hs-install]", a2hs).addEventListener("click", function () {
      var ev = installEvent;
      installEvent = null;
      if (!ev) { hideA2hs(false); return; }
      // Abgelehnt oder installiert: nicht noch einmal fragen
      Promise.resolve().then(function () { return ev.prompt(); })
        .then(function () { return ev.userChoice; })
        .then(function () { hideA2hs(true); }, function () { hideA2hs(false); });
    });
    // Niedrige Bildschirme: die iOS-Schritte erst auf „So geht's“ (app.css blendet sie sonst aus)
    var how = $("[data-a2hs-how]", a2hs);
    how.addEventListener("click", function () {
      var open = !a2hs.classList.contains("is-open");
      a2hs.classList.toggle("is-open", open);
      how.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", function (ev) {
      if ((ev.key === "Escape" || ev.key === "Esc") && !a2hs.hidden) hideA2hs(true);
    });
    // Die Karte liegt fest über der Tabbar: Was den Tastaturfokus bekommt, schieben wir darüber
    // (scroll-padding-bottom in app.css wirkt nur bei Sprungzielen, nicht beim Fokussieren)
    var raise = function (el) {
      if (a2hs.hidden) return;
      var top = a2hs.getBoundingClientRect().top;
      var r = el.getBoundingClientRect();
      // Noch ganz unterhalb des Fensters: erst scrollt der Browser, dann der zweite Aufruf
      if (r.bottom > top && r.top < window.innerHeight) window.scrollBy(0, r.bottom - top + 14);
    };
    var later = window.requestAnimationFrame || setTimeout;
    document.addEventListener("focusin", function (ev) {
      var target = ev.target;
      if (!target.getBoundingClientRect || a2hs.contains(target) || target.closest(".tabbar, .topbar, .skip")) return;
      raise(target);
      later(function () { raise(target); });
    });
    showA2hs();
  }
  if (isStandalone()) persistStorage();

  // ---------------------------------------------------------- service worker
  if ("serviceWorker" in navigator && window.location.protocol !== "file:") {
    window.addEventListener("load", function () {
      navigator.serviceWorker.register("/sw.js").catch(function () {});
    });
  }
})();
