/*!
 * CosmaNeura Sovereign — drop-in dashboard panel (reference implementation).
 *
 * A collapsed right-rail button that expands into the Sovereign chat panel.
 * No framework, no dependencies, styles self-injected and prefixed (.cnp-).
 * The HOST owns auth, context, and persistence — the panel never scrapes the
 * page. Served by the Sovereign at GET /embed/v1/panel.js.
 *
 * Minimal mount (two functions is all a host must implement):
 *
 *   CosmaNeuraPanel.mount({
 *     sovereignUrl: "https://sovereign.example.com",
 *     getAuthToken: async () => currentUserJwt,          // null when auth disabled (dev)
 *     getContext:   async () => ({                       // whatever is on screen now
 *       clientId: "abc123",                              // keys the thread
 *       record_ref: client.athenaRecordId,                 // host-open synthetic record
 *       journey_digest: client.journeyDigest,            // from POST /digest, see docs
 *       prior_sessions: latestNote,                      // most recent note/transcript
 *       terminology: settings.terminology,               // "client/therapist" etc.
 *       care_model: client.careModel,
 *       // optional per-view: case_context, labs, patient_metrics,
 *       // calorie_target, note_template, patient_profile, intake_summary
 *     }),
 *     onInsert: (text, resp) => note.insert(text),       // optional action hooks
 *     onSave:   (text, resp, clientId) => visits.savePrep(clientId, text),
 *     loadHistory: async (clientId, {signal}) => [...],  // optional persistence
 *     saveHistory: async (clientId, history) => {},
 *     shiftContent: true,   // default: reflow the host page instead of covering it
 *     fabBottom: "18px",    // launcher offset — raise it above an existing
 *     fabRight: "18px",     //   floating control (e.g. a "Report a Bug" pill)
 *   });
 */
(function () {
  "use strict";
  if (window.CosmaNeuraPanel) return;

  var CSS = [
    ".cnp-root{--bg:#0f1620;--panel:#16202c;--panel2:#1d2a38;--line:#27384a;--ink:#eaf1f8;",
    "--muted:#93a6ba;--accent:#c9a24b;--accent2:#6fb1d6;--ok:#8fc7a5;",
    "font:14px/1.5 -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:var(--ink)}",
    ".cnp-fab{position:fixed;right:18px;bottom:18px;z-index:99998;width:52px;height:52px;",
    "border-radius:14px;border:1px solid var(--line);cursor:pointer;font-weight:800;font-size:20px;",
    "background:linear-gradient(135deg,var(--accent),#9c7a2f);color:#1a130a;box-shadow:0 6px 22px rgba(0,0,0,.4)}",
    ".cnp-rail{position:fixed;top:0;right:0;bottom:0;z-index:99999;width:400px;max-width:96vw;",
    "display:none;flex-direction:column;background:var(--bg);border-left:1px solid var(--line);",
    "box-shadow:-12px 0 34px rgba(0,0,0,.45)}",
    ".cnp-rail.cnp-open{display:flex}",
    ".cnp-head{display:flex;align-items:center;gap:10px;padding:13px 15px;border-bottom:1px solid var(--line)}",
    ".cnp-logo{width:28px;height:28px;border-radius:8px;background:linear-gradient(135deg,var(--accent),#9c7a2f);",
    "display:grid;place-items:center;font-weight:800;color:#1a130a;font-size:13px}",
    ".cnp-title{font-weight:700;font-size:14px}.cnp-sub{font-size:11px;color:var(--muted)}",
    ".cnp-sandbox{display:inline-block;margin-top:3px;padding:1px 7px;border-radius:999px;",
    "border:1px solid #d8b75d;color:#ffe6a0;background:#3b3015;font-size:9px;font-weight:800;",
    "letter-spacing:.45px;text-transform:uppercase}",
    ".cnp-sandbox[hidden]{display:none}",
    ".cnp-x{margin-left:auto;background:none;border:none;color:var(--muted);font-size:19px;cursor:pointer}",
    ".cnp-thread{flex:1;overflow-y:auto;padding:14px}",
    ".cnp-msg{margin:10px 0;display:flex}.cnp-msg.cnp-u{justify-content:flex-end}",
    ".cnp-b{max-width:88%;padding:10px 13px;border-radius:12px;background:var(--panel);",
    "border:1px solid var(--line);font-size:13.5px}",
    ".cnp-u .cnp-b{background:#214a63;border-color:#2b5c7a}",
    ".cnp-b p{margin:.35em 0}",
    ".cnp-b.cnp-emg{border-color:#d9534f;background:#341c1c}",
    ".cnp-emg-h{color:#ff9d94;font-weight:700;font-size:11px;text-transform:uppercase;",
    "letter-spacing:.5px;margin-bottom:5px}",
    ".cnp-badge{display:inline-block;font-size:9.5px;text-transform:uppercase;letter-spacing:.6px;",
    "color:var(--accent);border:1px solid var(--line);border-radius:999px;padding:1px 7px;margin-bottom:6px}",
    ".cnp-cite{font-size:10.5px;color:var(--muted);margin-top:7px;border-top:1px solid var(--line);padding-top:6px}",
    ".cnp-trust{font-size:10.5px;color:var(--ok);margin-top:6px}",
    ".cnp-calc{margin-top:8px;background:#14231c;border:1px solid #2d4a3a;border-radius:9px;",
    "padding:7px 10px;font-size:11.5px}",
    ".cnp-calc-t{color:var(--ok);font-size:9.5px;text-transform:uppercase;letter-spacing:.5px;margin-bottom:4px}",
    ".cnp-chart{margin-top:8px;background:#111a26;border:1px solid var(--panel-line,#27384a);",
    "border-radius:9px;padding:9px 10px 6px}",
    ".cnp-chart-t{color:#93a6ba;font-size:9.5px;text-transform:uppercase;letter-spacing:.5px;",
    "margin:0 0 4px 2px}",
    ".cnp-chart svg{display:block;width:100%;height:auto}",
    ".cnp-tip{position:fixed;z-index:100000;background:#0c1420;border:1px solid #34506e;",
    "border-radius:8px;padding:6px 9px;font-size:11px;color:#e9eff7;pointer-events:none;",
    "box-shadow:0 4px 14px rgba(0,0,0,.5);display:none;max-width:220px}",
    ".cnp-acts{margin-top:8px;display:flex;gap:6px;flex-wrap:wrap}",
    ".cnp-act{font-size:11.5px;background:var(--panel2);border:1px solid var(--line);color:var(--ink);",
    "border-radius:7px;padding:4px 9px;cursor:pointer}.cnp-act:hover{border-color:var(--accent2)}",
    ".cnp-chips{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px 8px}",
    ".cnp-chip{font-size:11.5px;color:var(--muted);background:var(--panel);border:1px solid var(--line);",
    "border-radius:999px;padding:4px 10px;cursor:pointer}.cnp-chip:hover{color:var(--ink);border-color:var(--accent2)}",
    ".cnp-foot{border-top:1px solid var(--line);padding:10px 14px}",
    ".cnp-row{display:flex;gap:7px;align-items:flex-end}",
    ".cnp-in{flex:1;resize:none;background:var(--panel);border:1px solid var(--line);color:var(--ink);",
    "border-radius:10px;padding:9px 11px;font:inherit;font-size:13.5px;max-height:110px;outline:none}",
    ".cnp-in:focus{border-color:var(--accent2)}",
    ".cnp-send{background:var(--accent);color:#1a130a;border:none;border-radius:10px;",
    "padding:9px 13px;font-weight:700;cursor:pointer}.cnp-send:disabled{opacity:.5}",
    ".cnp-fmt{background:var(--panel);border:1px solid var(--line);color:var(--ink);",
    "border-radius:9px;padding:8px;font:inherit;font-size:12.5px}",
    ".cnp-welcome-disc{font-size:10px;color:var(--muted);margin-top:6px;text-align:center}",
    ".cnp-welcome-disc[hidden],.cnp-chips[hidden]{display:none}",
    ".cnp-typing span{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--muted);",
    "margin-right:3px;animation:cnpb 1s infinite}.cnp-typing span:nth-child(2){animation-delay:.2s}",
    ".cnp-typing span:nth-child(3){animation-delay:.4s}",
    "@keyframes cnpb{0%,60%,100%{opacity:.3}30%{opacity:1}}",
  ].join("");

  // Budget for context payloads: stay well inside the Sovereign's 256 KB
  // body cap. Prefer the journey digest + latest note over raw transcripts;
  // when the host passes arrays, keep newest-first until the budget is spent.
  var CONTEXT_CHAR_BUDGET = 60000;

  // Escapes quotes too — escaped text can land inside HTML attributes, where
  // an unescaped quote is an XSS breakout.
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;",
               '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function md(t) {
    var lines = esc(t).split("\n"), html = "", inList = false, m;
    for (var i = 0; i < lines.length; i++) {
      var ln = lines[i].replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
      if (/^###?\s+/.test(ln)) { if (inList) { html += "</ul>"; inList = false; } html += "<p><b>" + ln.replace(/^###?\s+/, "") + "</b></p>"; }
      else if ((m = ln.match(/^\s*[-*]\s+(.*)/))) { if (!inList) { html += "<ul>"; inList = true; } html += "<li>" + m[1] + "</li>"; }
      else if (ln.trim() === "") { if (inList) { html += "</ul>"; inList = false; } }
      else { if (inList) { html += "</ul>"; inList = false; } html += "<p>" + ln + "</p>"; }
    }
    if (inList) html += "</ul>";
    return html;
  }

  // The verified-calculations box is the trust centerpiece for clinicians, so
  // it must read like clinical values, not like a JSON dump. Known keys get
  // formatted; anything new falls back to a readable key: value line.
  function num(n) {
    return typeof n === "number" ? n.toLocaleString("en-US") : String(n);
  }

  function formatComputed(comp) {
    var out = [];
    if (comp.bmi != null) out.push("BMI: " + comp.bmi);
    if (comp.bmr_kcal_day != null)
      out.push("Resting energy (BMR): " + num(comp.bmr_kcal_day) + " kcal/day");
    var t = comp.calorie_target;
    if (t && t.min != null)
      out.push("Calorie target: " + num(t.min) +
               (t.max != null && t.max !== t.min ? "–" + num(t.max) : "") +
               " kcal/day" + (t.source ? " (" + t.source + ")" : ""));
    var m = comp.macros_at_target_midpoint;
    if (m && m.calories != null)
      out.push("Macros at " + num(m.calories) + " kcal: " + num(m.protein_g) +
               " g protein · " + num(m.carbs_g) + " g carbs · " + num(m.fat_g) +
               " g fat" + (m.split ? " (" + m.split + " split)" : ""));
    Object.keys(comp).forEach(function (k) {
      if (["bmi", "bmr_kcal_day", "calorie_target",
           "macros_at_target_midpoint"].indexOf(k) === -1) {
        var v = comp[k];
        out.push(k.replace(/_/g, " ") + ": " +
                 (typeof v === "object" ? JSON.stringify(v) : v));
      }
    });
    return out;
  }

  // ---- analyte trend chart -------------------------------------------------
  // Single series over time: 2px line, dot markers with a 2px surface ring,
  // emphasized endpoint with a direct value label, clinical reference range as
  // a subtle band, per-point hover tooltips. No legend — the title names the
  // one series. Line color #3b9ce6 validated against the dark surface.
  var LINE = "#3b9ce6", SURFACE = "#16202c";

  function fdate(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return String(iso || "").slice(0, 10);
    return (d.getMonth() + 1) + "/" + d.getDate() + "/" + String(d.getFullYear()).slice(2);
  }

  function parseRange(txt) {
    var m = /(-?\d+(?:\.\d+)?)\s*[-–]\s*(-?\d+(?:\.\d+)?)/.exec(String(txt || ""));
    return m ? [parseFloat(m[1]), parseFloat(m[2])] : null;
  }

  function trendChart(trend, tipHost, ownedTips, listen) {
    var pts = (trend.points || []).map(function (p) {
      var v = parseFloat(p.value), t = Date.parse(p.observed_at || "");
      return isNaN(v) ? null : { v: v, t: t, p: p };
    }).filter(Boolean);
    if (pts.length < 2) return null;
    var timed = pts.every(function (d) { return !isNaN(d.t); });

    var W = 320, H = 96, mL = 8, mR = 44, mT = 10, mB = 18;
    var iw = W - mL - mR, ih = H - mT - mB;
    var last = pts[pts.length - 1];
    var rng = parseRange(last.p.reference_range);
    var vals = pts.map(function (d) { return d.v; });
    var lo = Math.min.apply(null, vals.concat(rng ? [rng[0]] : []));
    var hi = Math.max.apply(null, vals.concat(rng ? [rng[1]] : []));
    var pad = (hi - lo) * 0.15 || 1; lo -= pad; hi += pad;
    var x = function (i, d) {
      if (timed) {
        var t0 = pts[0].t, t1 = last.t;
        return mL + (t1 === t0 ? iw / 2 : (d.t - t0) / (t1 - t0) * iw);
      }
      return mL + (pts.length === 1 ? iw / 2 : i / (pts.length - 1) * iw);
    };
    var y = function (v) { return mT + (hi - v) / (hi - lo) * ih; };

    var ns = "http://www.w3.org/2000/svg";
    var box = document.createElement("div"); box.className = "cnp-chart";
    var title = document.createElement("div"); title.className = "cnp-chart-t";
    title.textContent = (last.p.analyte_name || trend.analyte_query || "Trend") +
      (last.p.unit ? " (" + last.p.unit + ")" : "") +
      (rng ? " · ref " + last.p.reference_range : "");
    box.appendChild(title);
    var svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 " + W + " " + H);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", title.textContent + ", " + pts.length + " points");

    function el(tag, attrs) {
      var e = document.createElementNS(ns, tag);
      Object.keys(attrs).forEach(function (k) { e.setAttribute(k, attrs[k]); });
      svg.appendChild(e); return e;
    }
    if (rng) {
      el("rect", { x: mL, width: iw, y: y(rng[1]),
        height: Math.max(2, y(rng[0]) - y(rng[1])),
        fill: "rgba(143,199,165,.10)", stroke: "rgba(143,199,165,.22)",
        "stroke-width": .5, rx: 2 });
    }
    el("path", {
      d: pts.map(function (d, i) {
        return (i ? "L" : "M") + x(i, d).toFixed(1) + " " + y(d.v).toFixed(1);
      }).join(" "),
      fill: "none", stroke: LINE, "stroke-width": 2,
      "stroke-linecap": "round", "stroke-linejoin": "round" });
    pts.forEach(function (d, i) {
      var isLast = i === pts.length - 1;
      el("circle", { cx: x(i, d), cy: y(d.v), r: isLast ? 4.5 : 3,
        fill: LINE, stroke: SURFACE, "stroke-width": 2 });
    });
    // Direct label on the emphasized endpoint — text tokens, not series color.
    el("text", { x: Math.min(x(pts.length - 1, last) + 8, W - 2), y: y(last.v) + 3.5,
      fill: "#e9eff7", "font-size": 11, "font-weight": 700 }).textContent =
      last.v + (last.p.unit ? " " + last.p.unit : "");
    var ax = el("text", { x: mL, y: H - 4, fill: "#93a6ba", "font-size": 9 });
    ax.textContent = fdate(pts[0].p.observed_at);
    var ax2 = el("text", { x: mL + iw, y: H - 4, fill: "#93a6ba",
      "font-size": 9, "text-anchor": "end" });
    ax2.textContent = fdate(last.p.observed_at);

    // Hover layer: oversized hit targets, one shared tooltip.
    var tip = document.createElement("div"); tip.className = "cnp-tip";
    tipHost.appendChild(tip); ownedTips.push(tip);
    pts.forEach(function (d, i) {
      var hit = el("circle", { cx: x(i, d), cy: y(d.v), r: 11,
        fill: "transparent", cursor: "pointer" });
      listen(hit, "mousemove", function (ev) {
        tip.textContent = fdate(d.p.observed_at) + " · " + d.v +
          (d.p.unit ? " " + d.p.unit : "") +
          (d.p.abnormal_flag ? " (" + d.p.abnormal_flag + ")" : "") +
          (d.p.reference_range ? " · ref " + d.p.reference_range : "");
        tip.style.display = "block";
        tip.style.left = Math.min(ev.clientX + 12, window.innerWidth - 230) + "px";
        tip.style.top = (ev.clientY + 12) + "px";
      }, "record");
      listen(hit, "mouseleave", function () { tip.style.display = "none"; }, "record");
    });
    box.appendChild(svg);
    return box;
  }

  function budgetSessions(prior) {
    if (prior == null) return "";
    var parts = Array.isArray(prior) ? prior.slice() : [String(prior)];
    parts.reverse(); // newest first
    var out = [], used = 0;
    for (var i = 0; i < parts.length; i++) {
      var p = String(parts[i]);
      if (used + p.length > CONTEXT_CHAR_BUDGET) {
        p = p.slice(0, Math.max(0, CONTEXT_CHAR_BUDGET - used));
        if (p) out.push(p + "\n[truncated for size]");
        break;
      }
      out.push(p); used += p.length;
    }
    out.reverse();
    return out.join("\n\n--- session break ---\n\n");
  }

  function mount(opts) {
    opts = opts || {};
    var url = (opts.sovereignUrl || "").replace(/\/$/, "");
    var state = {
      open: false, busy: false, switching: false, destroyed: false,
      clientId: null, history: [], persona: "patient", generation: 0,
      welcomePromise: null, welcomeController: null, welcomeRenderGeneration: null,
      recordController: null, chatController: null,
    };
    var ownedTips = [];
    var listeners = [];

    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var root = document.createElement("div");
    root.className = "cnp-root";
    root.innerHTML =
      '<button class="cnp-fab" title="CosmaNeura assistant">C</button>' +
      '<div class="cnp-rail" role="complementary" aria-label="CosmaNeura assistant">' +
      '  <div class="cnp-head"><div class="cnp-logo">C</div>' +
      '    <div><div class="cnp-title">CosmaNeura</div>' +
      '    <div class="cnp-sub">Loading assistant…</div>' +
      '    <div class="cnp-sandbox" hidden></div></div>' +
      '    <button class="cnp-x" title="Close">×</button></div>' +
      '  <div class="cnp-thread"></div>' +
      '  <div class="cnp-chips"></div>' +
      '  <div class="cnp-foot"><div class="cnp-row">' +
      '    <select class="cnp-fmt"><option value="chat">Chat</option>' +
      '      <option value="note">Note text</option><option value="document">Document</option></select>' +
      '    <textarea class="cnp-in" rows="1" placeholder="Ask a question…"></textarea>' +
      '    <button class="cnp-send">Send</button></div></div></div>';
    (opts.target || document.body).appendChild(root);

    // Hosts often already own the bottom-right corner (support widgets, bug
    // reporters). Let them place the launcher instead of colliding with it.
    if (opts.fabBottom) root.querySelector(".cnp-fab").style.bottom = opts.fabBottom;
    if (opts.fabRight) root.querySelector(".cnp-fab").style.right = opts.fabRight;

    var fab = root.querySelector(".cnp-fab"), rail = root.querySelector(".cnp-rail"),
        thread = root.querySelector(".cnp-thread"), chips = root.querySelector(".cnp-chips"),
        input = root.querySelector(".cnp-in"), send = root.querySelector(".cnp-send"),
        fmt = root.querySelector(".cnp-fmt"), sandboxBadge = root.querySelector(".cnp-sandbox"),
        sub = root.querySelector(".cnp-sub");

    function bubble(cls) {
      var w = document.createElement("div"); w.className = "cnp-msg " + (cls === "u" ? "cnp-u" : "");
      var b = document.createElement("div"); b.className = "cnp-b";
      w.appendChild(b); thread.appendChild(w); thread.scrollTop = thread.scrollHeight;
      return b;
    }

    function currentGeneration(generation) {
      return !state.destroyed && generation === state.generation;
    }

    function abort(controller) {
      if (controller) controller.abort();
    }

    function clearOwnedTips() {
      ownedTips.forEach(function (tip) { tip.remove(); });
      ownedTips.length = 0;
    }

    function listen(target, event, handler, scope) {
      target.addEventListener(event, handler);
      listeners.push({
        target: target, event: event, handler: handler, scope: scope || "mount",
      });
    }

    function clearEventHandlers(scope) {
      var retained = [];
      listeners.forEach(function (item) {
        if (!scope || item.scope === scope) {
          item.target.removeEventListener(item.event, item.handler);
        } else {
          retained.push(item);
        }
      });
      listeners = retained;
      // Dynamic chips and response actions use property handlers. Revoke them
      // too, even if a host retained a reference to the detached root.
      if (!scope) {
        root.querySelectorAll("*").forEach(function (node) { node.onclick = null; });
      }
    }

    function syncControls() {
      var disabled = state.destroyed || state.busy || state.switching;
      input.disabled = disabled; fmt.disabled = disabled; send.disabled = disabled;
    }

    function setSensitiveChrome(sensitive) {
      root.querySelectorAll(".cnp-welcome-disc").forEach(function (notice) {
        notice.hidden = sensitive;
      });
      chips.hidden = sensitive;
    }

    function welcomeData() {
      if (!state.welcomePromise) {
        state.welcomeController = new AbortController();
        state.welcomePromise = fetch(url + "/welcome", {
          signal: state.welcomeController.signal,
        }).then(function (r) {
          if (!r.ok) throw new Error("welcome unavailable");
          return r.json();
        }).catch(function (error) {
          state.welcomePromise = null;
          if (error.name !== "AbortError") throw error;
          throw error;
        });
      }
      return state.welcomePromise;
    }

    function applyWelcomeUX(w) {
      state.persona = w.persona === "provider" ? "provider" : "patient";
      sub.textContent = state.persona === "provider"
        ? "Faithful clinical assistant" : "Faithful health assistant";
      input.placeholder = state.persona === "provider"
        ? "Ask about the open record…" : "Ask a health or values question…";
    }

    function applyEnvironmentLabel(w) {
      var hasLabsPath = (w.paths || []).some(function (path) {
        return path.intent === "lab_review";
      });
      if (w.labs_tier === "sandbox" && w.labs_label) {
        sandboxBadge.textContent = w.labs_label;
        sandboxBadge.hidden = false;
      } else if ((w.persona === "patient" || w.persona === "provider") &&
                 !hasLabsPath && !w.labs_tier && !w.labs_label) {
        sandboxBadge.textContent = "";
        sandboxBadge.hidden = true;
      } else {
        // Unknown or inconsistent lab-capable bootstrap data must never make
        // the environment look production-safe.
        sandboxBadge.textContent = "Environment not verified";
        sandboxBadge.hidden = false;
      }
    }

    // Welcome is mount-resolved by the server: the browser sends no persona.
    // The response drives tone and the persistent sandbox badge even when a
    // restored record history means no welcome bubble is rendered.
    // Fail closed while bootstrap is pending or unavailable. A successful
    // public welcome hides it; a sandbox welcome replaces it with the exact
    // synthetic-data label.
    sandboxBadge.textContent = "Environment not verified";
    sandboxBadge.hidden = false;
    welcomeData().then(function (w) {
      if (state.destroyed) return;
      applyWelcomeUX(w); applyEnvironmentLabel(w);
    }).catch(function () {});

    // Record-scoped threads. A monotonically increasing generation plus an
    // AbortController protects against both cooperative and non-cooperative
    // history loaders. A late A load can never paint after record B is active.
    async function loadThreadFor(id) {
      if (state.destroyed) return;
      var recordId = id == null ? null : String(id);
      var generation = ++state.generation;
      abort(state.chatController); abort(state.recordController);
      state.chatController = null;
      var recordController = new AbortController();
      state.recordController = recordController;
      state.clientId = recordId;
      state.history = [];
      state.welcomeRenderGeneration = null;
      state.busy = false;
      state.switching = true;
      clearEventHandlers("record");
      input.value = ""; input.style.height = "auto";
      thread.innerHTML = ""; chips.innerHTML = "";
      setSensitiveChrome(false); clearOwnedTips(); syncControls();

      try {
        var loaded = opts.loadHistory
          ? await opts.loadHistory(recordId, { signal: recordController.signal })
          : [];
        if (!currentGeneration(generation)) return null;
        state.history = Array.isArray(loaded) ? loaded : [];
        // Restored turns render as text; citations and actions belong to the
        // live response and are not replayed.
        state.history.forEach(function (m) {
          var b = bubble(m.role === "user" ? "u" : "a");
          if (m.role === "user") b.textContent = m.content || "";
          else b.innerHTML = md(m.content || "");
        });
      } catch (error) {
        if (currentGeneration(generation) && error.name !== "AbortError") {
          bubble("a").innerHTML = "<p>Conversation history is unavailable.</p>";
        }
      } finally {
        if (currentGeneration(generation)) {
          if (state.recordController === recordController) state.recordController = null;
          state.switching = false;
          syncControls();
          if (!state.history.length && state.open) await loadWelcome(generation);
        }
      }
      return currentGeneration(generation) && !state.switching ? generation : null;
    }

    async function loadWelcome(generation) {
      if (state.welcomeRenderGeneration === generation) return;
      state.welcomeRenderGeneration = generation;
      try {
        var w = await welcomeData();
        if (!currentGeneration(generation)) return;
        applyWelcomeUX(w); applyEnvironmentLabel(w); setSensitiveChrome(false);
        var b = bubble("a");
        b.innerHTML = "<p>" + esc(w.greeting) + "</p><p><b>" + esc(w.emergency_note) + "</b></p>";
        var d = document.createElement("div"); d.className = "cnp-cite cnp-welcome-disc";
        d.textContent = w.disclaimer; b.appendChild(d);
        chips.innerHTML = "";
        (w.paths || []).forEach(function (p) {
          var c = document.createElement("div"); c.className = "cnp-chip"; c.textContent = p.label;
          c.onclick = function () { input.value = p.message; input.focus(); };
          chips.appendChild(c);
        });
      } catch (error) {
        if (currentGeneration(generation) && error.name !== "AbortError") {
          state.welcomeRenderGeneration = null;
          bubble("a").innerHTML = "<p>Assistant unavailable — check the Sovereign service.</p>";
        }
      }
    }

    function render(b, d, recordId) {
      var html = "";
      setSensitiveChrome(d.intent === "sensitive_disclosure");
      if (d.intent === "emergency") { b.classList.add("cnp-emg"); html += '<div class="cnp-emg-h">⚠ This may be an emergency</div>'; }
      else if (d.intent) html += '<span class="cnp-badge">' + esc(d.intent.replace(/_/g, " ")) + "</span>";
      if (d.structured && d.structured.tier === "sandbox" && d.structured.sandbox_label)
        html += '<div class="cnp-sandbox">' + esc(d.structured.sandbox_label) + "</div>";
      html += md(d.answer || "");
      b.innerHTML = html;
      var cites = (d.citations || []).map(function (c) { return c.source; }).filter(Boolean);
      if (cites.length) {
        var e = document.createElement("div"); e.className = "cnp-cite";
        e.textContent = "Sources: " + Array.from(new Set(cites)).join(" · "); b.appendChild(e);
      }
      if (d.doctrinal_review) {
        var t = document.createElement("div"); t.className = "cnp-trust";
        t.textContent = d.doctrinal_review.conforms
          ? "✓ Reviewed for conformity with Catholic teaching"
          : "✓ Revised in keeping with Catholic teaching";
        b.appendChild(t);
      }
      var comp = d.structured && d.structured.computed;
      if (comp && Object.keys(comp).length) {
        var c2 = document.createElement("div"); c2.className = "cnp-calc";
        c2.innerHTML = '<div class="cnp-calc-t">✓ Verified calculations (computed, not generated)</div>';
        formatComputed(comp).forEach(function (line) {
          var row = document.createElement("div");
          row.textContent = line;
          c2.appendChild(row);
        });
        b.appendChild(c2);
      }
      var trend = d.structured && d.structured.trend;
      if (trend && trend.points && trend.points.length >= 2) {
        var chart = trendChart(trend, root, ownedTips, listen);
        if (chart) b.appendChild(chart);
      }
      var bar = document.createElement("div"); bar.className = "cnp-acts";
      var ofmt = d.structured && d.structured.output_format;
      function act(label, fn) {
        var x = document.createElement("button"); x.className = "cnp-act";
        x.textContent = label; x.onclick = fn; bar.appendChild(x);
      }
      if (ofmt === "note" || ofmt === "document")
        act("Copy", function () { navigator.clipboard.writeText(d.answer || ""); });
      if (ofmt === "document")
        act("Download .md", function () {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([d.answer || ""], { type: "text/markdown" }));
          a.download = "cosmaneura-" + (d.intent || "output") + ".md"; a.click();
        });
      if (opts.onInsert && (ofmt === "note" || (d.structured && d.structured.feature === "visit_prep")))
        act("Insert into note", function () { opts.onInsert(d.answer || "", d, recordId); });
      if (opts.onSave && d.structured && d.structured.feature)
        act("Save", function () { opts.onSave(d.answer || "", d, recordId); });
      if (bar.children.length) b.appendChild(bar);
    }

    async function ask() {
      var text = input.value.trim();
      if (!text || state.busy || state.switching || state.destroyed) return;
      var generation = state.generation;
      var controller = null;
      var b = null;
      state.busy = true; syncControls();
      input.value = ""; input.style.height = "auto";

      var ctx = {};
      try {
        // Persona is a response-derived presentation hint, never a mount claim.
        // Waiting for welcome here also makes the first chat deterministic when
        // the user submits before the bootstrap request has completed.
        var welcome = await welcomeData();
        if (!currentGeneration(generation)) return;
        applyWelcomeUX(welcome); applyEnvironmentLabel(welcome);

        ctx = opts.getContext ? (await opts.getContext()) || {} : {};
        if (!currentGeneration(generation)) return;
        // Resolve the record BEFORE painting this turn. loadThreadFor advances
        // the generation and aborts any older record work.
        var contextRecordId = ctx.clientId == null ? null : String(ctx.clientId);
        if (contextRecordId && contextRecordId !== state.clientId) {
          state.busy = false; syncControls();
          var loadedGeneration = await loadThreadFor(contextRecordId);
          // Bind to this exact switch, not merely the same record id. A host
          // may issue a second setRecord(B) while this B load is pending.
          if (loadedGeneration == null ||
              !currentGeneration(loadedGeneration) || state.switching ||
              state.clientId !== contextRecordId) return;
          generation = loadedGeneration;
          state.busy = true; syncControls();
        }
        if (!currentGeneration(generation)) return;

        // Bind every side effect to the record/generation that originated the
        // turn. Never read state.clientId again when saving this response.
        var recordId = state.clientId;
        var requestPersona = state.persona;
        var historyForRequest = state.history.slice(-20);
        var meta = { output_format: fmt.value };
        ["journey_digest", "care_model", "terminology", "case_context", "labs",
         "patient_metrics", "calorie_target", "macro_split", "note_template",
         "patient_profile", "intake_summary",
         "record_ref", "analyte"].forEach(function (k) {
          if (ctx[k] != null && ctx[k] !== "") meta[k] = ctx[k];
        });
        // Temporary host compatibility: normalize the old open-record key to
        // the contract's canonical name. Never forward a patient display name.
        if (meta.record_ref == null && ctx.patient_ref != null && ctx.patient_ref !== "")
          meta.record_ref = ctx.patient_ref;
        var prior = budgetSessions(ctx.prior_sessions);
        if (prior) meta.prior_sessions = prior;

        var headers = { "content-type": "application/json" };
        var token = opts.getAuthToken ? await opts.getAuthToken() : null;
        if (!currentGeneration(generation)) return;
        if (token) headers["authorization"] = "Bearer " + token;

        bubble("u").textContent = text;
        b = bubble("a");
        b.innerHTML = '<div class="cnp-typing"><span></span><span></span><span></span></div>';
        controller = new AbortController();
        state.chatController = controller;
        var r = await fetch(url + "/chat", {
          method: "POST", headers: headers,
          signal: controller.signal,
          body: JSON.stringify({
            persona: requestPersona, message: text,
            history: historyForRequest, metadata: meta,
            session_id: recordId ? String(recordId).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64) || null : null,
          }),
        });
        if (!currentGeneration(generation) || state.chatController !== controller) return;
        if (!r.ok) {
          b.innerHTML = "<p>That request couldn’t be completed (" + r.status + "). " +
                        (r.status === 401 ? "Please sign in again." : "Please try again.") + "</p>";
        } else {
          var d = await r.json();
          if (!currentGeneration(generation) || state.chatController !== controller) return;
          render(b, d, recordId);
          state.history.push({ role: "user", content: text },
                             { role: "assistant", content: d.answer || "" });
          var historySnapshot = state.history.slice();
          if (opts.saveHistory && recordId) {
            // Defer invocation too: a synchronous host persistence failure must
            // never replace an already-rendered successful assistant response.
            Promise.resolve().then(function () {
              return opts.saveHistory(recordId, historySnapshot);
            }).catch(function () {});
          }
        }
      } catch (error) {
        if (currentGeneration(generation) && error.name !== "AbortError") {
          if (!b) b = bubble("a");
          b.innerHTML = "<p>Couldn’t reach the assistant. Is the Sovereign service up?</p>";
        }
      } finally {
        if (currentGeneration(generation) &&
            (!controller || state.chatController === controller)) {
          if (state.chatController === controller) state.chatController = null;
          state.busy = false; syncControls();
          thread.scrollTop = thread.scrollHeight; input.focus();
        }
      }
    }

    // Reflow the host instead of covering it: a clinician reading a note while
    // the assistant is open must still see the note (that is the whole point of
    // "insert into note"). Restores the host's own inline value on close, and
    // skips the shift on narrow screens where the rail is nearly full-width.
    var savedPad = null;
    function setOpen(open) {
      if (state.destroyed) return;
      state.open = open;
      rail.classList.toggle("cnp-open", open);
      var canShift = opts.shiftContent !== false && window.innerWidth >= 900;
      if (open && canShift) {
        if (savedPad === null) savedPad = document.body.style.paddingRight || "";
        document.body.style.paddingRight = rail.offsetWidth + "px";
      } else if (savedPad !== null) {
        document.body.style.paddingRight = savedPad;
        savedPad = null;
      }
      if (open && !thread.children.length && !state.switching) {
        loadWelcome(state.generation);
      }
      if (open) input.focus();
    }

    function destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      state.generation += 1;
      abort(state.chatController); abort(state.recordController);
      abort(state.welcomeController);
      state.chatController = null; state.recordController = null;
      state.welcomeController = null;
      state.history = []; state.clientId = null; state.welcomePromise = null;
      if (savedPad !== null) {
        document.body.style.paddingRight = savedPad;
        savedPad = null;
      }
      clearOwnedTips();
      clearEventHandlers();
      root.innerHTML = "";
      root.remove(); style.remove();
    }

    fab.onclick = function () { setOpen(!state.open); };
    root.querySelector(".cnp-x").onclick = function () { setOpen(false); };
    send.onclick = ask;
    listen(input, "keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); }
    });
    listen(input, "input", function () {
      input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 110) + "px";
    });

    return {
      open: function () { setOpen(true); },
      close: function () { setOpen(false); },
      // Call whenever the open record changes — clears the thread, reloads
      // that record's history, and re-renders it. Prevents one patient's
      // conversation from being displayed against another's chart.
      setRecord: function (id) { return loadThreadFor(id); },
      destroy: destroy,
      root: root,
    };
  }

  window.CosmaNeuraPanel = { mount: mount };
})();
