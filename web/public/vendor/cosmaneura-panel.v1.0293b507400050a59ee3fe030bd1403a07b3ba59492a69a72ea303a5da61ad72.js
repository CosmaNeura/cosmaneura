/*!
 * CosmaNeura Sovereign — drop-in dashboard panel (reference implementation).
 *
 * A collapsed right-rail button that expands into the Sovereign chat panel.
 * No framework, no dependencies, styles self-injected and prefixed (.cnp-).
 * The host owns identity, authority, and any governed conversation history.
 * The panel receives a short-lived bearer and presentation hints; it never
 * trusts browser role hints or scrapes the page. Served by the Sovereign at
 * GET /embed/v1/panel.js.
 *
 * Minimal governed mount:
 *
 *   CosmaNeuraPanel.mount({
 *     sovereignUrl: "https://sovereign.example.com",
 *     embedVersion: "1.0",
 *     // The configured host returns its short-lived, audience-bound token.
 *     // For the direct Clerk pilot this is the cosmaneura-sovereign template.
 *     getAuthToken: async () => currentBearerToken,
 *     getContext: async () => ({
 *       clientId: openRecord.threadKey,
 *       thread_ref: openRecord.assistantThreadRef,
 *       context_snapshot_ref: openRecord.assistantSnapshotRef,
 *       digest_ref: openRecord.journeyDigestRef,
 *     }),
 *     onHumanContact: action => host.openHumanContactPath(action.action_id),
 *     loadHistory: async (clientId) => platform.loadGovernedHistory(clientId),
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
    ".cnp-gate{position:fixed;right:18px;bottom:18px;z-index:99998;padding:10px 14px;",
    "border-radius:12px;border:1px solid var(--line);background:var(--panel);color:var(--muted);",
    "box-shadow:0 6px 22px rgba(0,0,0,.3);font-size:12px;font-weight:700}",
    ".cnp-gate[hidden],.cnp-fab[hidden],.cnp-rail[hidden]{display:none}",
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
    ".cnp-prep{padding:10px 14px;border-bottom:1px solid var(--line);background:#111a26}",
    ".cnp-prep[hidden]{display:none}.cnp-prep-go{width:100%;background:var(--panel2);",
    "border:1px solid var(--accent2);color:var(--ink);border-radius:9px;padding:8px 11px;",
    "font-weight:700;cursor:pointer}.cnp-prep-go:disabled{opacity:.5}",
    ".cnp-prep-view{margin-top:9px;padding:10px;border:1px solid var(--line);border-radius:9px;",
    "background:var(--panel)}.cnp-prep-view[hidden]{display:none}",
    ".cnp-prep-state{font-size:10px;text-transform:uppercase;letter-spacing:.55px;",
    "color:var(--accent);font-weight:800}.cnp-prep-meta{font-size:10.5px;color:var(--muted);margin-top:3px}",
    ".cnp-prep-section{margin-top:9px}.cnp-prep-section h4{font-size:11px;margin:0 0 3px;",
    "color:#b9dcef;text-transform:uppercase;letter-spacing:.4px}.cnp-prep-section p{margin:0}",
    ".cnp-prep-section ul{margin:2px 0 0;padding-left:18px}",
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
    ".cnp-route{font-size:10.5px;color:#b9dcef;margin:0 0 7px;padding:6px 8px;",
    "border:1px solid #294964;border-radius:8px;background:#101e2b}",
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
    ".cnp-cards{display:grid;gap:7px;margin-top:8px}.cnp-card{padding:8px 10px;border:1px solid var(--line);",
    "border-radius:9px;background:var(--panel2)}.cnp-card-name{font-weight:700}.cnp-card-meta{color:var(--muted);font-size:11px}",
    ".cnp-card a{display:inline-block;margin-top:5px;color:#9bd5f5;font-size:11px}",
    ".cnp-chips{display:flex;gap:6px;flex-wrap:wrap;padding:0 14px 8px}",
    ".cnp-chip{font:inherit;font-size:11.5px;color:var(--muted);background:var(--panel);border:1px solid var(--line);",
    "border-radius:999px;padding:4px 10px;cursor:pointer}.cnp-chip:hover{color:var(--ink);border-color:var(--accent2)}",
    ".cnp-foot{border-top:1px solid var(--line);padding:10px 14px}",
    ".cnp-row{display:flex;gap:7px;align-items:flex-end}",
    ".cnp-in{flex:1;resize:none;background:var(--panel);border:1px solid var(--line);color:var(--ink);",
    "border-radius:10px;padding:9px 11px;font:inherit;font-size:13.5px;max-height:110px;outline:none}",
    ".cnp-in:focus{border-color:var(--accent2)}",
    ".cnp-send{background:var(--accent);color:#1a130a;border:none;border-radius:10px;",
    "padding:9px 13px;font-weight:700;cursor:pointer}.cnp-send:disabled{opacity:.5}",
    ".cnp-retry{margin-top:8px;background:var(--panel2);border:1px solid var(--line);",
    "color:var(--ink);border-radius:7px;padding:5px 10px;cursor:pointer}",
    ".cnp-fmt{background:var(--panel);border:1px solid var(--line);color:var(--ink);",
    "border-radius:9px;padding:8px;font:inherit;font-size:12.5px}",
    ".cnp-welcome-disc{font-size:10px;color:var(--muted);margin-top:6px;text-align:center}",
    ".cnp-welcome-disc[hidden],.cnp-chips[hidden]{display:none}",
    ".cnp-typing span{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--muted);",
    "margin-right:3px;animation:cnpb 1s infinite}.cnp-typing span:nth-child(2){animation-delay:.2s}",
    ".cnp-typing span:nth-child(3){animation-delay:.4s}",
    "@keyframes cnpb{0%,60%,100%{opacity:.3}30%{opacity:1}}",
  ].join("");

  var SUPPRESSION_NAMES = [
    "disclaimer", "intent_badge", "doctrinal_badge", "citations",
    "structured_cards", "feature_actions", "guided_paths",
  ];
  var OPAQUE_REF = /^[A-Za-z0-9._~-]{8,160}$/;
  var UUID_V4 = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/;

  function freshClientTurnId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function")
      return window.crypto.randomUUID();
    if (!window.crypto || typeof window.crypto.getRandomValues !== "function")
      throw new Error("Secure client-turn generation is unavailable");
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 15) | 64;
    bytes[8] = (bytes[8] & 63) | 128;
    var hex = Array.prototype.map.call(bytes, function (value) {
      return value.toString(16).padStart(2, "0");
    }).join("");
    return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" +
      hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
  }

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

  function exactServiceOrigin(value, allowLoopbackHttp) {
    if (typeof value !== "string" || !value) throw new Error("sovereignUrl is required");
    var parsed;
    try { parsed = new URL(value); } catch (_error) {
      throw new Error("sovereignUrl must be an absolute HTTPS origin");
    }
    var loopback = allowLoopbackHttp === true && parsed.protocol === "http:" &&
      ["localhost", "127.0.0.1", "[::1]"].indexOf(parsed.hostname) >= 0 &&
      window.location && window.location.origin === parsed.origin;
    if ((parsed.protocol !== "https:" && !loopback) || parsed.username || parsed.password ||
        parsed.pathname !== "/" || parsed.search || parsed.hash ||
        parsed.origin !== value)
      throw new Error("sovereignUrl must be an exact HTTPS origin or same-origin loopback HTTP");
    return { origin: parsed.origin, loopback: loopback };
  }

  function closedObject(value, keys) {
    if (!value || typeof value !== "object" || Array.isArray(value)) return false;
    var actual = Object.keys(value).sort();
    var expected = keys.slice().sort();
    return actual.length === expected.length && actual.every(function (key, index) {
      return key === expected[index];
    });
  }

  function validHumanContact(action) {
    return closedObject(action, ["label", "action_id", "fallback_text"]) &&
      typeof action.label === "string" && action.label.length >= 1 && action.label.length <= 120 &&
      typeof action.action_id === "string" && /^[A-Za-z0-9._~-]{8,128}$/.test(action.action_id) &&
      typeof action.fallback_text === "string" &&
      action.fallback_text.length >= 1 && action.fallback_text.length <= 240;
  }

  function governedPresentation(value) {
    if (!closedObject(value, ["mode", "suppress", "human_contact"]) ||
        ["standard", "emergency", "sensitive_disclosure"].indexOf(value.mode) < 0 ||
        !Array.isArray(value.suppress)) return null;
    var unique = new Set(value.suppress);
    if (unique.size !== value.suppress.length || value.suppress.some(function (name) {
      return SUPPRESSION_NAMES.indexOf(name) < 0;
    })) return null;
    if (value.human_contact !== null && !validHumanContact(value.human_contact)) return null;
    if (value.mode === "sensitive_disclosure" &&
        (value.human_contact === null || unique.size !== SUPPRESSION_NAMES.length ||
         SUPPRESSION_NAMES.some(function (name) { return !unique.has(name); }))) return null;
    return value;
  }

  function governedHistory(value) {
    if (!closedObject(value, ["thread_ref", "messages", "next_cursor"]) ||
        typeof value.thread_ref !== "string" || !OPAQUE_REF.test(value.thread_ref) ||
        !Array.isArray(value.messages) || value.messages.length > 100 ||
        !(value.next_cursor === null ||
          (typeof value.next_cursor === "string" && value.next_cursor.length <= 512))) return null;
    for (var i = 0; i < value.messages.length; i++) {
      var message = value.messages[i];
      if (!closedObject(message, ["turn_ref", "client_turn_id", "role", "content",
                                  "recorded_at", "presentation"]) ||
          typeof message.turn_ref !== "string" || !OPAQUE_REF.test(message.turn_ref) ||
          typeof message.client_turn_id !== "string" || !UUID_V4.test(message.client_turn_id) ||
          ["user", "assistant"].indexOf(message.role) < 0 ||
          typeof message.content !== "string" || message.content.length < 1 ||
          message.content.length > 20000 || typeof message.recorded_at !== "string" ||
          isNaN(Date.parse(message.recorded_at))) return null;
      if (message.role === "user" && message.presentation !== null) return null;
      if (message.role === "assistant" && !governedPresentation(message.presentation)) return null;
    }
    return value;
  }

  function mount(opts) {
    opts = opts || {};
    if (opts.embedVersion !== "1.0")
      throw new Error("CosmaNeura embed version 1.0 is required");
    if (typeof opts.getAuthToken !== "function")
      throw new Error("getAuthToken is required");
    if (typeof opts.getContext !== "function")
      throw new Error("getContext is required");
    if (typeof opts.onHumanContact !== "function")
      throw new Error("onHumanContact is required");
    // Parse and normalize no path at all before any credential callback runs.
    var service = exactServiceOrigin(opts.sovereignUrl, opts.allowLoopbackHttp);
    var url = service.origin;
    var embedVersion = opts.embedVersion;
    var state = {
      open: false, busy: false, switching: false, destroyed: false,
      clientId: null, history: null, persona: "patient", generation: 0,
      accessGranted: false, localFixture: false,
      recordReady: false, contextSnapshot: null, recordHeaders: null,
      sessionPrepAvailable: false, prepBusy: false,
      welcomePromise: null, welcomeController: null, welcomeRenderGeneration: null,
      recordController: null, chatController: null, prepController: null,
      retryAttempt: null,
    };
    var ownedTips = [];
    var listeners = [];

    var style = document.createElement("style");
    style.textContent = CSS;
    document.head.appendChild(style);

    var root = document.createElement("div");
    root.className = "cnp-root";
    root.innerHTML =
      '<div class="cnp-gate" role="status">Sovereign named beta · unavailable</div>' +
      '<button class="cnp-fab" title="Sovereign assistant" hidden>C</button>' +
      '<div class="cnp-rail" role="complementary" aria-label="Sovereign assistant" hidden>' +
      '  <div class="cnp-head"><div class="cnp-logo">C</div>' +
      '    <div><div class="cnp-title">Sovereign</div>' +
      '    <div class="cnp-sub">Named beta · access required</div>' +
      '    <div class="cnp-sandbox" hidden></div></div>' +
      '    <button class="cnp-x" title="Close">×</button></div>' +
      '  <div class="cnp-prep" hidden><button class="cnp-prep-go" type="button">Open Session Prep</button>' +
      '    <div class="cnp-prep-view" role="status" aria-live="polite" hidden></div></div>' +
      '  <div class="cnp-thread"></div>' +
      '  <div class="cnp-chips"></div>' +
      '  <div class="cnp-foot"><div class="cnp-row">' +
      '    <select class="cnp-fmt"><option value="chat">Chat</option>' +
      '      <option value="document">Document</option></select>' +
      '    <textarea class="cnp-in" rows="1" placeholder="Ask a question…"></textarea>' +
      '    <button class="cnp-send">Send</button></div></div></div>';
    (opts.target || document.body).appendChild(root);

    // Hosts often already own the bottom-right corner (support widgets, bug
    // reporters). Let them place the launcher instead of colliding with it.
    if (opts.fabBottom) root.querySelector(".cnp-fab").style.bottom = opts.fabBottom;
    if (opts.fabRight) root.querySelector(".cnp-fab").style.right = opts.fabRight;

    var gate = root.querySelector(".cnp-gate"),
        fab = root.querySelector(".cnp-fab"), rail = root.querySelector(".cnp-rail"),
        thread = root.querySelector(".cnp-thread"), chips = root.querySelector(".cnp-chips"),
        input = root.querySelector(".cnp-in"), send = root.querySelector(".cnp-send"),
        fmt = root.querySelector(".cnp-fmt"), sandboxBadge = root.querySelector(".cnp-sandbox"),
        sub = root.querySelector(".cnp-sub"), prep = root.querySelector(".cnp-prep"),
        prepGo = root.querySelector(".cnp-prep-go"),
        prepView = root.querySelector(".cnp-prep-view");

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

    function clearSessionPrep() {
      abort(state.prepController);
      state.prepController = null;
      state.prepBusy = false;
      prepView.innerHTML = "";
      prepView.hidden = true;
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
      var disabled = state.destroyed || state.busy || state.prepBusy || state.switching ||
        !state.accessGranted || !state.recordReady || state.clientId === null;
      input.disabled = disabled; fmt.disabled = disabled || state.localFixture;
      send.disabled = disabled;
      prep.hidden = !state.sessionPrepAvailable;
      prepGo.disabled = disabled || state.prepBusy;
    }

    function setNamedBetaAccess(granted) {
      var wasGranted = state.accessGranted;
      state.accessGranted = granted === true;
      gate.hidden = state.accessGranted;
      fab.hidden = !state.accessGranted;
      rail.hidden = !state.accessGranted;
      if (!state.accessGranted && wasGranted) setOpen(false);
      syncControls();
      if (state.accessGranted !== wasGranted && typeof opts.onAccessChange === "function")
        Promise.resolve(opts.onAccessChange(state.accessGranted)).catch(function () {});
    }
    setNamedBetaAccess(false);

    function lockNamedBeta() {
      if (state.destroyed) return;
      state.generation += 1;
      abort(state.chatController); abort(state.recordController);
      abort(state.welcomeController);
      state.chatController = null; state.recordController = null;
      state.welcomeController = null; state.welcomePromise = null;
      state.welcomeRenderGeneration = null;
      state.clientId = null; state.history = null; state.persona = "patient";
      state.localFixture = false;
      state.sessionPrepAvailable = false;
      input.readOnly = false;
      state.recordReady = false; state.contextSnapshot = null;
      state.recordHeaders = null; state.retryAttempt = null;
      state.busy = false; state.switching = false;
      clearSessionPrep();
      clearEventHandlers("record"); clearOwnedTips();
      input.value = ""; input.style.height = "auto";
      thread.innerHTML = ""; chips.innerHTML = "";
      sub.textContent = "Named beta · access required";
      setGovernedChrome(null); setNamedBetaAccess(false);
    }

    async function freshBearerHeaders() {
      var token;
      try {
        token = await opts.getAuthToken();
      } catch (_error) {
        var unavailable = new Error("Assistant bearer token is unavailable");
        unavailable.authenticationUnavailable = true;
        throw unavailable;
      }
      if (typeof token !== "string" || token.length < 16 ||
          token.length > 8192 || /\s/.test(token)) {
        var invalid = new Error("Assistant bearer token is invalid");
        invalid.authenticationUnavailable = true;
        throw invalid;
      }
      return {
        "authorization": "Bearer " + token,
        "x-cosmaneura-embed-version": embedVersion,
      };
    }

    function setGovernedChrome(presentation) {
      var suppressed = new Set(presentation ? presentation.suppress : []);
      root.querySelectorAll(".cnp-welcome-disc").forEach(function (notice) {
        notice.hidden = suppressed.has("disclaimer");
      });
      chips.hidden = suppressed.has("guided_paths");
    }

    function welcomeData(headers, generation) {
      if (!state.welcomePromise) {
        if (!headers || !currentGeneration(generation))
          return Promise.reject(new Error("record handshake is required"));
        state.welcomeController = new AbortController();
        var promise = fetch(url + "/welcome", {
          headers: headers,
          signal: state.welcomeController.signal,
        }).then(function (r) {
          if (r.headers.get("x-cosmaneura-embed-version") !== embedVersion)
            throw new Error("welcome contract mismatch");
          if (!r.ok) throw new Error("welcome unavailable");
          return r.json();
        }).then(function (value) {
          if (!value || value.persona !== "provider")
            throw new Error("named-beta provider access unavailable");
          return value;
        }).catch(function (error) {
          if (state.welcomePromise === promise) state.welcomePromise = null;
          throw error;
        });
        state.welcomePromise = promise;
      }
      return state.welcomePromise;
    }

    function governedContext(value, recordId) {
      var allowed = ["clientId", "thread_ref", "context_snapshot_ref", "digest_ref",
                     "terminology", "care_model"];
      if (!value || typeof value !== "object" || Array.isArray(value) ||
          Object.keys(value).some(function (key) { return allowed.indexOf(key) < 0; }) ||
          String(value.clientId == null ? "" : value.clientId) !== recordId) return null;
      var clean = { clientId: recordId };
      ["thread_ref", "context_snapshot_ref", "digest_ref"].forEach(function (key) {
        if (value[key] != null) clean[key] = value[key];
      });
      if (["thread_ref", "context_snapshot_ref", "digest_ref"].some(function (key) {
        return clean[key] != null &&
          (typeof clean[key] !== "string" || !OPAQUE_REF.test(clean[key]));
      })) return null;
      if (value.terminology != null) {
        if (typeof value.terminology !== "string" || value.terminology.length > 40) return null;
        clean.terminology = value.terminology;
      }
      if (value.care_model != null) {
        if (typeof value.care_model !== "string" || value.care_model.length > 80) return null;
        clean.care_model = value.care_model;
      }
      return clean;
    }

    function applyWelcomeUX(w) {
      state.persona = w.persona === "provider" ? "provider" : "patient";
      state.localFixture = w.walkthrough_mode === "local_synthetic" && service.loopback;
      state.sessionPrepAvailable = state.persona === "provider" &&
        (w.paths || []).some(function (path) { return path.intent === "session_prep"; });
      input.readOnly = state.localFixture;
      if (state.localFixture) fmt.value = "chat";
      sub.textContent = state.localFixture ? "Local synthetic walkthrough" : state.persona === "provider"
        ? "Faithful clinical assistant" : "Faithful health assistant";
      input.placeholder = state.localFixture ? "Use a synthetic guided prompt…" : state.persona === "provider"
        ? "Ask about the open record…" : "Ask a health or values question…";
      syncControls();
    }

    function applyEnvironmentLabel(w) {
      if (state.localFixture) {
        sandboxBadge.textContent = "Local synthetic fixture · no live records";
        sandboxBadge.hidden = false;
        return;
      }
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
    // Fail closed until the host explicitly completes setRecord(). A token is
    // never acquired at mount time because it would not yet be record-bound.
    sandboxBadge.textContent = "Environment not verified";
    sandboxBadge.hidden = false;

    // Record-scoped threads. A monotonically increasing generation plus an
    // AbortController protects against both cooperative and non-cooperative
    // history loaders. A late A load can never paint after record B is active.
    async function loadThreadFor(id) {
      if (state.destroyed) return;
      var recordId = id == null ? null : String(id);
      var invalidRecordId = recordId !== null &&
        (recordId.length < 1 || recordId.length > 128);
      if (invalidRecordId) recordId = null;
      var generation = ++state.generation;
      // Synchronously remove every prior-scope Session Prep byte before any
      // authentication, host-context, welcome, or history request begins.
      clearSessionPrep();
      abort(state.chatController); abort(state.recordController);
      abort(state.welcomeController);
      state.chatController = null;
      var recordController = new AbortController();
      state.recordController = recordController;
      state.clientId = recordId;
      state.history = null;
      state.localFixture = false;
      state.sessionPrepAvailable = false;
      state.recordReady = false;
      state.contextSnapshot = null;
      state.recordHeaders = null;
      state.retryAttempt = null;
      state.welcomePromise = null;
      state.welcomeController = null;
      state.welcomeRenderGeneration = null;
      state.busy = false;
      state.switching = recordId !== null;
      clearEventHandlers("record");
      input.value = ""; input.style.height = "auto";
      thread.innerHTML = ""; chips.innerHTML = "";
      setNamedBetaAccess(false);
      setGovernedChrome(null); clearOwnedTips(); syncControls();

      if (recordId === null) {
        state.recordController = null;
        sub.textContent = "Open a record to use the assistant";
        if (invalidRecordId) throw new Error("record identifier is invalid");
        return generation;
      }

      try {
        // Authenticate the named provider before asking the host for even an
        // opaque record reference. Every late result is discarded if the host
        // switches records while the preflight is in flight.
        var headers = await freshBearerHeaders();
        if (!currentGeneration(generation)) return null;
        var welcome = await welcomeData(headers, generation);
        if (!currentGeneration(generation)) return null;
        var suppliedContext = await opts.getContext();
        if (!currentGeneration(generation)) return null;
        var context = governedContext(suppliedContext, recordId);
        if (!context) {
          var mismatch = new Error("record context mismatch");
          mismatch.recordMismatch = true;
          throw mismatch;
        }
        state.contextSnapshot = context;
        state.recordHeaders = headers;
        // Never ask the host for governed history until the server-side role
        // check succeeds and the opaque record context validates locally.
        var history = opts.loadHistory
          ? await opts.loadHistory(recordId, { signal: recordController.signal })
          : null;
        if (!currentGeneration(generation)) return null;
        var loaded = history === null ? null : governedHistory(history);
        if (history !== null && !loaded)
          throw new Error("governed history is invalid");
        state.history = loaded;
        (loaded ? loaded.messages : []).forEach(function (m) {
          var b = bubble(m.role === "user" ? "u" : "a");
          if (m.role === "user") b.textContent = m.content || "";
          else render(b, {
            answer: m.content,
            presentation: m.presentation,
            citations: [],
            doctrinal_review: null,
            structured: {},
            safety_notice: null,
          }, recordId, generation, true);
        });
        state.recordReady = true;
        if (loaded && typeof opts.onHistoryUpdated === "function") {
          Promise.resolve().then(function () {
            if (currentGeneration(generation) && state.clientId === recordId)
              return opts.onHistoryUpdated(recordId, loaded);
          }).catch(function () {});
        }
        applyWelcomeUX(welcome); applyEnvironmentLabel(welcome);
        setNamedBetaAccess(true);
      } catch (error) {
        if (currentGeneration(generation) && error.name !== "AbortError") {
          state.recordReady = false;
          state.contextSnapshot = null;
          state.recordHeaders = null;
          setNamedBetaAccess(false);
          if (error.recordMismatch) {
            state.clientId = null;
            thread.innerHTML = "";
            sub.textContent = "Open a record to use the assistant";
          }
        }
      } finally {
        if (currentGeneration(generation)) {
          if (state.recordController === recordController) state.recordController = null;
          state.switching = false;
          syncControls();
          if (state.recordReady && (!state.history || !state.history.messages.length) &&
              state.open) await loadWelcome(generation);
        }
      }
      return currentGeneration(generation) && !state.switching && state.recordReady
        ? generation : null;
    }

    async function loadWelcome(generation) {
      if (state.welcomeRenderGeneration === generation) return;
      state.welcomeRenderGeneration = generation;
      try {
        var w = await welcomeData(state.recordHeaders, generation);
        if (!currentGeneration(generation)) return;
        applyWelcomeUX(w); applyEnvironmentLabel(w); setGovernedChrome(null);
        var b = bubble("a");
        b.innerHTML = "<p>" + esc(w.greeting) + "</p><p><b>" + esc(w.emergency_note) + "</b></p>";
        var d = document.createElement("div"); d.className = "cnp-cite cnp-welcome-disc";
        d.textContent = w.disclaimer; b.appendChild(d);
        chips.innerHTML = "";
        (w.paths || []).forEach(function (p) {
          if (p.intent === "session_prep") {
            prepGo.textContent = p.label;
            return;
          }
          var c = document.createElement("button"); c.className = "cnp-chip"; c.textContent = p.label;
          c.setAttribute("type", "button");
          c.onclick = function () {
            if (!currentGeneration(generation) || !state.recordReady) return;
            input.value = p.message; input.focus();
          };
          chips.appendChild(c);
        });
      } catch (error) {
        if (currentGeneration(generation) && error.name !== "AbortError") {
          state.welcomeRenderGeneration = null;
          bubble("a").innerHTML = "<p>Assistant unavailable — check the Sovereign service.</p>";
        }
      }
    }

    function render(b, d, recordId, generation, hydrated) {
      var presentation = governedPresentation(d.presentation);
      if (!presentation) throw new Error("assistant presentation is invalid");
      var suppressed = new Set(presentation.suppress);
      var html = "";
      setGovernedChrome(presentation);
      if (state.localFixture && d.intent)
        html += '<div class="cnp-route">Sovereign route · ' +
          esc(String(d.intent).replace(/_/g, " ")) +
          (d.doctrinal_review ? " · final review recorded" : "") + "</div>";
      if (presentation.mode === "emergency") {
        b.classList.add("cnp-emg");
        html += '<div class="cnp-emg-h">⚠ This may be an emergency</div>';
      } else if (!suppressed.has("intent_badge") && d.intent) {
        html += '<span class="cnp-badge">' + esc(d.intent.replace(/_/g, " ")) + "</span>";
      }
      if (!suppressed.has("structured_cards") && d.structured &&
          d.structured.tier === "sandbox" && d.structured.sandbox_label)
        html += '<div class="cnp-sandbox">' + esc(d.structured.sandbox_label) + "</div>";
      html += md(d.answer || "");
      b.innerHTML = html;
      var cites = (d.citations || []).map(function (c) { return c.source; }).filter(Boolean);
      if (!suppressed.has("citations") && cites.length) {
        var e = document.createElement("div"); e.className = "cnp-cite";
        e.textContent = "Sources: " + Array.from(new Set(cites)).join(" · "); b.appendChild(e);
      }
      if (!suppressed.has("doctrinal_badge") && d.doctrinal_review) {
        var t = document.createElement("div"); t.className = "cnp-trust";
        t.textContent = d.doctrinal_review.conforms
          ? "✓ Reviewed for conformity with Catholic teaching"
          : "✓ Revised in keeping with Catholic teaching";
        b.appendChild(t);
      }
      var matches = d.structured && d.structured.matches;
      if (!suppressed.has("structured_cards") && Array.isArray(matches) && matches.length) {
        var cards = document.createElement("div"); cards.className = "cnp-cards";
        matches.slice(0, 4).forEach(function (match) {
          var card = document.createElement("div"); card.className = "cnp-card";
          var name = document.createElement("div"); name.className = "cnp-card-name";
          name.textContent = match.name || "Directory option"; card.appendChild(name);
          var meta = document.createElement("div"); meta.className = "cnp-card-meta";
          meta.textContent = [match.specialty, match.telehealth ? "telehealth" : "in person"]
            .filter(Boolean).join(" · "); card.appendChild(meta);
          if (typeof match.booking_url === "string" && /^https:\/\//.test(match.booking_url)) {
            var link = document.createElement("a"); link.href = match.booking_url;
            link.target = "_blank"; link.rel = "noopener noreferrer";
            link.textContent = "Open directory"; card.appendChild(link);
          }
          cards.appendChild(card);
        });
        b.appendChild(cards);
      }
      var comp = d.structured && d.structured.computed;
      if (!suppressed.has("structured_cards") && comp && Object.keys(comp).length) {
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
      if (!suppressed.has("structured_cards") && trend &&
          trend.points && trend.points.length >= 2) {
        var chart = trendChart(trend, root, ownedTips, listen);
        if (chart) b.appendChild(chart);
      }
      var bar = document.createElement("div"); bar.className = "cnp-acts";
      var ofmt = d.structured && d.structured.output_format;
      function act(label, fn) {
        var x = document.createElement("button"); x.className = "cnp-act";
        x.textContent = label;
        x.onclick = function () {
          if (!currentGeneration(generation) || !state.recordReady ||
              state.clientId !== recordId) return;
          return fn(x);
        };
        bar.appendChild(x);
      }
      if (!hydrated && !suppressed.has("feature_actions") && ofmt === "document")
        act("Download .md", function () {
          var a = document.createElement("a");
          a.href = URL.createObjectURL(new Blob([d.answer || ""], { type: "text/markdown" }));
          a.download = "cosmaneura-" + (d.intent || "output") + ".md"; a.click();
        });
      if (presentation.human_contact) {
        var contact = presentation.human_contact;
        act(contact.label, function (button) {
          Promise.resolve().then(function () {
            if (!currentGeneration(generation) || !state.recordReady ||
                state.clientId !== recordId) return;
            return opts.onHumanContact(contact);
          }).catch(function () {
            if (!currentGeneration(generation) || !state.recordReady ||
                state.clientId !== recordId) return;
            button.disabled = true;
            var fallback = document.createElement("div");
            fallback.className = "cnp-cite";
            fallback.textContent = contact.fallback_text;
            b.appendChild(fallback);
          });
        });
      }
      if (bar.children.length) b.appendChild(bar);
    }

    async function safeProblem(response) {
      try {
        var value = await response.json();
        if (value && typeof value === "object" &&
            typeof value.code === "string" &&
            typeof value.retryable === "boolean") {
          if (value.safety_fallback != null) {
            var fallback = value.safety_fallback;
            if (!closedObject(fallback, ["failure_stage", "answer", "presentation"]) ||
                ["user_accepted", "final_response"].indexOf(fallback.failure_stage) < 0 ||
                typeof fallback.answer !== "string" || fallback.answer.length < 1 ||
                fallback.answer.length > 1000 ||
                !governedPresentation(fallback.presentation) ||
                fallback.presentation.mode !== "sensitive_disclosure" ||
                response.status !== 503 || value.code !== "SERVICE_UNAVAILABLE" ||
                value.retryable !== false) return null;
          }
          return value;
        }
      } catch (_error) {}
      return null;
    }

    function validPrepTextList(value, minimum) {
      return Array.isArray(value) && value.length >= minimum && value.length <= 8 &&
        value.every(function (item) {
          return typeof item === "string" && item.length >= 1 && item.length <= 1000;
        });
    }

    function governedSessionPrep(value) {
      var keys = [
        "client_turn_id", "state", "freshness", "current_through", "source_count",
        "prepared_brief", "retry_after_seconds", "reason_code",
      ];
      if (!closedObject(value, keys) || typeof value.client_turn_id !== "string" ||
          !UUID_V4.test(value.client_turn_id) ||
          ["ready", "refreshing", "pending", "unavailable"].indexOf(value.state) < 0 ||
          !Number.isInteger(value.source_count) || value.source_count < 0 ||
          value.source_count > 6) return null;
      var content = value.state === "ready" || value.state === "refreshing";
      if (!content) {
        if (value.freshness !== "none" || value.current_through !== null ||
            value.source_count !== 0 || value.prepared_brief !== null) return null;
        if (value.state === "pending") {
          if (value.reason_code !== "generation_pending" ||
              !Number.isInteger(value.retry_after_seconds) ||
              value.retry_after_seconds < 1 || value.retry_after_seconds > 300) return null;
        } else if (value.retry_after_seconds !== null ||
                   ["no_eligible_sources", "context_not_available"].indexOf(value.reason_code) < 0) {
          return null;
        }
        return value;
      }
      if (typeof value.current_through !== "string" ||
          !/(?:Z|[+-]\d{2}:\d{2})$/.test(value.current_through) ||
          isNaN(Date.parse(value.current_through)) || value.source_count < 1) return null;
      if (value.state === "ready" &&
          (value.freshness !== "current" || value.retry_after_seconds !== null ||
           value.reason_code !== null)) return null;
      if (value.state === "refreshing" &&
          (value.freshness !== "stale" || value.reason_code !== "refresh_in_progress" ||
           !Number.isInteger(value.retry_after_seconds) ||
           value.retry_after_seconds < 1 || value.retry_after_seconds > 300)) return null;
      var brief = value.prepared_brief;
      if (!closedObject(brief, [
        "recap", "trajectory_and_material_changes", "talking_points",
        "open_threads", "cautions", "suggested_outline", "patient_reported",
      ]) || typeof brief.recap !== "string" || brief.recap.length < 1 ||
          brief.recap.length > 4000 ||
          !validPrepTextList(brief.trajectory_and_material_changes, 0) ||
          !validPrepTextList(brief.talking_points, 1) ||
          !validPrepTextList(brief.open_threads, 0) ||
          !validPrepTextList(brief.cautions, 0) ||
          !validPrepTextList(brief.suggested_outline, 1)) return null;
      if (brief.patient_reported !== null &&
          (!closedObject(brief.patient_reported, ["label", "summary"]) ||
           brief.patient_reported.label !== "Patient-reported Visit Prep" ||
           typeof brief.patient_reported.summary !== "string" ||
           brief.patient_reported.summary.length < 1 ||
           brief.patient_reported.summary.length > 2000)) return null;
      return value;
    }

    function prepSection(title, values) {
      if (!values.length) return;
      var section = document.createElement("div"); section.className = "cnp-prep-section";
      var heading = document.createElement("h4"); heading.textContent = title;
      section.appendChild(heading);
      if (values.length === 1) {
        var paragraph = document.createElement("p"); paragraph.textContent = values[0];
        section.appendChild(paragraph);
      } else {
        var list = document.createElement("ul");
        values.forEach(function (value) {
          var item = document.createElement("li"); item.textContent = value;
          list.appendChild(item);
        });
        section.appendChild(list);
      }
      prepView.appendChild(section);
    }

    function renderSessionPrep(value, recordId, generation) {
      if (!currentGeneration(generation) || state.clientId !== recordId) return;
      prepView.innerHTML = "";
      prepView.hidden = false;
      var stateLabel = document.createElement("div"); stateLabel.className = "cnp-prep-state";
      stateLabel.textContent = value.state === "ready" ? "Session Prep ready" :
        value.state === "refreshing" ? "Session Prep refreshing" :
        value.state === "pending" ? "Session Prep pending" : "Session Prep unavailable";
      prepView.appendChild(stateLabel);
      if (value.state === "pending" || value.state === "unavailable") {
        var empty = document.createElement("p");
        empty.textContent = value.state === "pending"
          ? "The prepared brief is still being generated. No clinical content is available yet."
          : value.reason_code === "no_eligible_sources"
            ? "No eligible finalized notes are available for this encounter."
            : "Session Prep is not available for this encounter.";
        prepView.appendChild(empty);
        if (value.state === "pending") {
          var check = document.createElement("button"); check.className = "cnp-retry";
          check.textContent = "Check again";
          check.onclick = function () {
            if (currentGeneration(generation) && state.clientId === recordId)
              return requestSessionPrep();
          };
          prepView.appendChild(check);
        }
        return;
      }
      var meta = document.createElement("div"); meta.className = "cnp-prep-meta";
      meta.textContent = "Current through " + fdate(value.current_through) + " · " +
        value.source_count + (value.source_count === 1 ? " source" : " sources");
      prepView.appendChild(meta);
      if (value.state === "refreshing") {
        var stale = document.createElement("p");
        stale.textContent = "This still-authorized brief is stale while Core updates it.";
        prepView.appendChild(stale);
      }
      var brief = value.prepared_brief;
      prepSection("Recap", [brief.recap]);
      prepSection("Trajectory and material changes", brief.trajectory_and_material_changes);
      prepSection("Talking points", brief.talking_points);
      prepSection("Open threads", brief.open_threads);
      prepSection("Cautions", brief.cautions);
      prepSection("Suggested outline", brief.suggested_outline);
      if (brief.patient_reported)
        prepSection(brief.patient_reported.label, [brief.patient_reported.summary]);
      if (value.state === "refreshing") {
        var refresh = document.createElement("button"); refresh.className = "cnp-retry";
        refresh.textContent = "Refresh Session Prep";
        refresh.onclick = function () {
          if (currentGeneration(generation) && state.clientId === recordId)
            return requestSessionPrep();
        };
        prepView.appendChild(refresh);
      }
    }

    function paintPrepFailure(attempt, message, allowRetry) {
      prepView.innerHTML = "";
      prepView.hidden = false;
      var label = document.createElement("div"); label.className = "cnp-prep-state";
      label.textContent = "Session Prep unavailable"; prepView.appendChild(label);
      var text = document.createElement("p"); text.textContent = message;
      prepView.appendChild(text);
      if (!allowRetry) return;
      var retry = document.createElement("button"); retry.className = "cnp-retry";
      retry.textContent = "Retry this exact Session Prep request";
      retry.onclick = function () {
        if (state.prepBusy || state.switching || state.destroyed ||
            !currentGeneration(attempt.generation)) return;
        return runSessionPrepAttempt(attempt);
      };
      prepView.appendChild(retry);
    }

    async function prepHeadersForAttempt(attempt) {
      var headers = attempt.initialHeaders;
      if (headers) {
        attempt.initialHeaders = null;
      } else {
        headers = await freshBearerHeaders();
        if (!currentGeneration(attempt.generation))
          throw new DOMException("stale", "AbortError");
        var refreshed = governedContext(await opts.getContext(), attempt.recordId);
        if (!currentGeneration(attempt.generation))
          throw new DOMException("stale", "AbortError");
        if (!refreshed || refreshed.context_snapshot_ref !== attempt.contextSnapshotRef) {
          loadThreadFor(null);
          throw new DOMException("record mismatch", "AbortError");
        }
      }
      headers = Object.assign({}, headers);
      headers["content-type"] = "application/json";
      headers["x-cosmaneura-client-turn-id"] = attempt.clientTurnId;
      return headers;
    }

    async function runSessionPrepAttempt(attempt) {
      if (!currentGeneration(attempt.generation) || state.switching) return;
      state.prepBusy = true; syncControls();
      prepView.innerHTML = '<div class="cnp-typing"><span></span><span></span><span></span></div>';
      prepView.hidden = false;
      var controller = new AbortController(); state.prepController = controller;
      try {
        var headers = await prepHeadersForAttempt(attempt);
        if (!currentGeneration(attempt.generation)) return;
        var response = await fetch(attempt.url, {
          method: "POST", headers: headers, body: attempt.body, signal: controller.signal,
        });
        if (!currentGeneration(attempt.generation) || state.prepController !== controller) return;
        if (response.status === 401 || response.status === 403) {
          lockNamedBeta();
          return;
        }
        if (response.headers.get("x-cosmaneura-embed-version") !== embedVersion) {
          paintPrepFailure(attempt, "The Session Prep integration could not be verified.", false);
          return;
        }
        if (!response.ok) {
          var problem = await safeProblem(response);
          if (!currentGeneration(attempt.generation) || state.prepController !== controller) return;
          if (response.status === 409 && problem &&
              problem.code === "RECORD_CONTEXT_MISMATCH") {
            loadThreadFor(null);
            return;
          }
          var retryable = Boolean(problem && problem.retryable === true &&
            (response.status === 429 || response.status === 503));
          paintPrepFailure(
            attempt,
            retryable ? "Session Prep is temporarily unavailable."
              : "Session Prep could not be loaded for this encounter.",
            retryable
          );
          return;
        }
        var value = governedSessionPrep(await response.json());
        if (!currentGeneration(attempt.generation) || state.prepController !== controller) return;
        if (!value || value.client_turn_id !== attempt.clientTurnId) {
          paintPrepFailure(attempt, "The Session Prep response could not be verified.", false);
          return;
        }
        renderSessionPrep(value, attempt.recordId, attempt.generation);
      } catch (error) {
        if (currentGeneration(attempt.generation) && error.authenticationUnavailable) {
          lockNamedBeta();
        } else if (currentGeneration(attempt.generation) && error.name !== "AbortError") {
          paintPrepFailure(
            attempt,
            error instanceof TypeError ? "The Session Prep connection was interrupted."
              : "The Session Prep request could not be verified.",
            error instanceof TypeError
          );
        }
      } finally {
        if (currentGeneration(attempt.generation) && state.prepController === controller) {
          state.prepController = null; state.prepBusy = false; syncControls();
        }
      }
    }

    async function requestSessionPrep() {
      if (state.busy || state.prepBusy || state.switching || state.destroyed ||
          !state.sessionPrepAvailable || !state.recordReady || state.clientId === null) return;
      var generation = state.generation;
      // A new action never retains a previous brief while authority and the
      // encounter-scoped comparison handle are being re-evaluated.
      clearSessionPrep();
      state.prepBusy = true; syncControls();
      try {
        var headers = await freshBearerHeaders();
        if (!currentGeneration(generation)) return;
        var recordId = state.clientId;
        var context = governedContext(await opts.getContext(), recordId);
        if (!currentGeneration(generation)) return;
        if (!context || !context.context_snapshot_ref) {
          loadThreadFor(null);
          return;
        }
        if (!state.contextSnapshot ||
            state.contextSnapshot.context_snapshot_ref !== context.context_snapshot_ref) {
          loadThreadFor(recordId);
          return;
        }
        var clientTurnId = freshClientTurnId();
        var body = JSON.stringify({
          client_turn_id: clientTurnId,
          context_snapshot_ref: context.context_snapshot_ref,
        });
        var attempt = {
          url: url + "/session-prep", initialHeaders: headers, body: body,
          clientTurnId: clientTurnId, contextSnapshotRef: context.context_snapshot_ref,
          recordId: recordId, generation: generation,
        };
        await runSessionPrepAttempt(attempt);
      } catch (error) {
        if (currentGeneration(generation) && error.authenticationUnavailable) {
          lockNamedBeta();
        } else if (currentGeneration(generation) && error.name !== "AbortError") {
          paintPrepFailure(
            { generation: generation },
            "Couldn’t initialize Session Prep for this encounter.",
            false
          );
        }
      } finally {
        if (currentGeneration(generation) && state.prepController === null) {
          state.prepBusy = false; syncControls();
        }
      }
    }

    async function headersForAttempt(attempt) {
      var headers;
      if (attempt.initialHeaders) {
        headers = attempt.initialHeaders;
        attempt.initialHeaders = null;
      } else {
        // Explicit retry preserves the request body/UUID/draft, authenticates
        // first, and only then reacquires the record context.
        var refreshedHeaders = await freshBearerHeaders();
        if (!currentGeneration(attempt.generation)) throw new DOMException("stale", "AbortError");
        var refreshedContext = await opts.getContext();
        if (!currentGeneration(attempt.generation)) throw new DOMException("stale", "AbortError");
        if (!governedContext(refreshedContext, attempt.recordId)) {
          loadThreadFor(null);
          throw new DOMException("record mismatch", "AbortError");
        }
        headers = refreshedHeaders;
      }
      headers = Object.assign({}, headers);
      headers["content-type"] = "application/json";
      headers["x-cosmaneura-client-turn-id"] = attempt.clientTurnId;
      return headers;
    }

    function paintAttemptFailure(b, attempt, message, allowRetry) {
      b.innerHTML = "<p>" + esc(message) + "</p>";
      input.value = attempt.text;
      input.style.height = "auto";
      input.style.height = Math.min(input.scrollHeight, 110) + "px";
      state.retryAttempt = attempt;
      if (!allowRetry) return;
      var retry = document.createElement("button");
      retry.className = "cnp-retry";
      retry.textContent = "Retry this exact request";
      retry.onclick = function () {
        if (state.busy || state.switching || state.destroyed ||
            !currentGeneration(attempt.generation)) return;
        input.value = attempt.text;
        return runAttempt(attempt, b);
      };
      b.appendChild(retry);
    }

    async function runAttempt(attempt, b) {
      if (!currentGeneration(attempt.generation) || state.switching) return;
      state.busy = true; syncControls();
      b.innerHTML = '<div class="cnp-typing"><span></span><span></span><span></span></div>';
      var controller = new AbortController();
      state.chatController = controller;
      try {
        // There is deliberately one fetch per user action. An explicit retry
        // reuses the same serialized body, UUID, draft, and record generation
        // while acquiring a fresh bearer.
        var requestHeaders = await headersForAttempt(attempt);
        if (!currentGeneration(attempt.generation)) return;
        var r = await fetch(attempt.url, {
          method: "POST",
          headers: requestHeaders,
          body: attempt.body,
          signal: controller.signal,
        });
        if (!currentGeneration(attempt.generation) ||
            state.chatController !== controller) return;
        if (r.status === 401 || r.status === 403) {
          lockNamedBeta();
          return;
        }
        if (r.headers.get("x-cosmaneura-embed-version") !== embedVersion) {
          paintAttemptFailure(
            b, attempt,
            "The assistant integration version could not be verified. Reload this page.",
            false
          );
          return;
        }
        if (!r.ok) {
          var problem = await safeProblem(r);
          if (!currentGeneration(attempt.generation) ||
              state.chatController !== controller) return;
          if (problem && problem.safety_fallback) {
            render(b, {
              answer: problem.safety_fallback.answer,
              presentation: problem.safety_fallback.presentation,
              citations: [], doctrinal_review: null, structured: {}, safety_notice: null,
            }, attempt.recordId, attempt.generation, false);
            state.retryAttempt = null;
            return;
          }
          if (r.status === 409 && problem &&
              problem.code === "RECORD_CONTEXT_MISMATCH") {
            loadThreadFor(null);
            return;
          }
          var canRetry = Boolean(
            problem && problem.retryable === true &&
            (r.status === 429 || r.status === 503)
          );
          var message;
          if (r.status === 426) {
            message = "This assistant integration needs to be updated. Reload this page.";
          } else if (r.status === 503 && problem && problem.retryable === false) {
            message = "This turn is still pending. Your draft and turn identifier are preserved; reload its governed history later before starting a new request.";
          } else if (canRetry) {
            message = "The assistant is temporarily unavailable. Your exact draft is preserved.";
          } else {
            message = "That request could not be completed. Your draft is preserved.";
          }
          paintAttemptFailure(b, attempt, message, canRetry);
          return;
        }
        var d = await r.json();
        if (!currentGeneration(attempt.generation) ||
            state.chatController !== controller) return;
        if (d.client_turn_id !== attempt.clientTurnId) {
          paintAttemptFailure(
            b, attempt,
            "The assistant response could not be matched to this turn. Reload its governed history.",
            false
          );
          return;
        }
        render(b, d, attempt.recordId, attempt.generation, false);
        state.retryAttempt = null;
        if (input.value === attempt.text) input.value = "";
      } catch (error) {
        if (currentGeneration(attempt.generation) && error.authenticationUnavailable) {
          lockNamedBeta();
        } else if (currentGeneration(attempt.generation) && error.name !== "AbortError") {
          paintAttemptFailure(
            b, attempt,
            error instanceof TypeError
              ? "The connection was interrupted. Your exact draft is preserved."
              : "The assistant request could not be verified. Your draft is preserved.",
            error instanceof TypeError
          );
        }
      } finally {
        if (currentGeneration(attempt.generation) &&
            state.chatController === controller) {
          state.chatController = null;
          state.busy = false; syncControls();
          thread.scrollTop = thread.scrollHeight; input.focus();
        }
      }
    }

    async function ask() {
      var text = input.value.trim();
      if (!text || state.busy || state.switching || state.destroyed ||
          !state.recordReady || state.clientId === null) return;
      var generation = state.generation;
      var b = null;
      var handedOff = false;
      state.busy = true; state.retryAttempt = null; syncControls();

      try {
        var initialHeaders = await freshBearerHeaders();
        if (!currentGeneration(generation)) return;
        var recordId = state.clientId;
        var suppliedContext = await opts.getContext();
        if (!currentGeneration(generation)) return;
        var context = governedContext(suppliedContext, recordId);
        if (!context) {
          loadThreadFor(null);
          return;
        }
        var meta = { output_format: fmt.value };
        ["clientId", "thread_ref", "context_snapshot_ref", "digest_ref",
         "terminology", "care_model"].forEach(function (key) {
          if (context[key] != null && context[key] !== "") meta[key] = context[key];
        });
        var clientTurnId = freshClientTurnId();
        var body = JSON.stringify({
          client_turn_id: clientTurnId,
          message: text,
          metadata: meta,
        });

        bubble("u").textContent = text;
        b = bubble("a");
        var attempt = {
          url: url + "/chat",
          initialHeaders: initialHeaders,
          body: body,
          text: text,
          clientTurnId: clientTurnId,
          recordId: recordId,
          generation: generation,
        };
        state.retryAttempt = attempt;
        input.value = ""; input.style.height = "auto";
        handedOff = true;
        await runAttempt(attempt, b);
      } catch (error) {
        if (currentGeneration(generation) && error.authenticationUnavailable) {
          lockNamedBeta();
        } else if (currentGeneration(generation) && error.name !== "AbortError") {
          if (!b) b = bubble("a");
          input.value = text;
          b.innerHTML = "<p>Couldn’t initialize the assistant request. Your draft is preserved.</p>";
        }
      } finally {
        if (!handedOff && currentGeneration(generation)) {
          state.busy = false; syncControls(); input.focus();
        }
      }
    }

    // Reflow the host instead of covering its active workspace. Restore the
    // host's own inline value on close, and skip the shift on narrow screens.
    var savedPad = null;
    function setOpen(open) {
      if (state.destroyed) return;
      if (open && !state.accessGranted) return;
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
      if (open && state.recordReady && !thread.children.length && !state.switching) {
        loadWelcome(state.generation);
      }
      if (open && state.recordReady) input.focus();
    }

    function destroy() {
      if (state.destroyed) return;
      state.destroyed = true;
      state.generation += 1;
      clearSessionPrep();
      abort(state.chatController); abort(state.recordController);
      abort(state.welcomeController);
      state.chatController = null; state.recordController = null;
      state.welcomeController = null;
      state.history = null; state.clientId = null; state.welcomePromise = null;
      state.accessGranted = false;
      state.recordReady = false; state.contextSnapshot = null; state.recordHeaders = null;
      state.sessionPrepAvailable = false;
      state.retryAttempt = null;
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
    prepGo.onclick = requestSessionPrep;
    send.onclick = ask;
    listen(input, "keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); ask(); }
    });
    listen(input, "input", function () {
      input.style.height = "auto"; input.style.height = Math.min(input.scrollHeight, 110) + "px";
    });
    syncControls();

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
