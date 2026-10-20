/* @ds-bundle: {"format":4,"namespace":"Smalls","components":[{"name":"Button"},{"name":"Toggle"},{"name":"Handle"},{"name":"StatusTag"},{"name":"Confidence"},{"name":"Notice"},{"name":"Question"},{"name":"CheckpointBar"},{"name":"ToolGate"},{"name":"ToolResult"},{"name":"EvidenceRecord"},{"name":"ConflictPair"},{"name":"ImpactSummary"},{"name":"HypothesisCard"},{"name":"Timeline"},{"name":"ActionList"},{"name":"AvoidList"},{"name":"CoverageTable"},{"name":"StakeholderMessage"}]} */
(function () {
  "use strict";
  var React = window.React;
  var h = React.createElement;
  var Frag = React.Fragment;

  function cx() {
    var out = [];
    for (var i = 0; i < arguments.length; i++) if (arguments[i]) out.push(arguments[i]);
    return out.join(" ");
  }
  function rest(props, omit) {
    var o = {};
    for (var k in props) if (Object.prototype.hasOwnProperty.call(props, k) && omit.indexOf(k) < 0) o[k] = props[k];
    return o;
  }
  // "2026-10-13T14:31:07.221Z" -> "14:31:07"; seconds=false -> "14:31". Non-ISO input is returned as given.
  function utc(iso, seconds) {
    var m = /T(\d\d):(\d\d)(?::(\d\d))?/.exec(iso || "");
    if (!m) return iso || "";
    return seconds === false || !m[3] ? m[1] + ":" + m[2] : m[1] + ":" + m[2] + ":" + m[3];
  }
  function utcDay(iso) {
    var m = /^(\d{4})-(\d\d)-(\d\d)/.exec(iso || "");
    if (!m) return "";
    var mon = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"][+m[2] - 1];
    return +m[3] + " " + mon;
  }
  function fixed2(n) { return (Math.round(n * 100) / 100).toFixed(2); }
  function label(text) { return h("span", { className: "sm-label" }, text); }

  /* Button */
  function Button(props) {
    var variant = props.variant || "secondary";
    return h("button", Object.assign({ type: "button" }, rest(props, ["variant", "size", "className"]), {
      className: cx("sm-btn", "sm-btn-" + variant, props.size === "sm" && "sm-btn-sm", props.className)
    }), props.children);
  }

  /* Toggle */
  function Toggle(props) {
    var on = !!props.checked;
    return h("button", {
      type: "button", role: "switch", "aria-checked": on ? "true" : "false", className: cx("sm-toggle", props.className),
      disabled: props.disabled,
      onClick: function () { if (props.onChange) props.onChange(!on); }
    }, h("span", { className: "sm-toggle-track", "aria-hidden": "true" }),
       h("span", null, props.label, props.hint ? h("span", { className: "sm-toggle-hint" }, props.hint) : null));
  }

  /* Handle: a cited evidence id. "log:pay-01/9c2e41d0" renders the kind prefix muted. */
  function Handle(props) {
    var id = props.id || "";
    var i = id.indexOf(":");
    var kind = i > 0 ? id.slice(0, i + 1) : "";
    var tail = i > 0 ? id.slice(i + 1) : id;
    if (props.missing) {
      return h("span", { className: "sm-handle sm-handle-missing", title: id + " not found in this bundle" }, kind, tail);
    }
    return h("button", {
      type: "button", className: cx("sm-handle", props.className),
      "aria-pressed": props.active ? "true" : undefined,
      title: props.title || "Open " + id,
      onClick: function () { if (props.onOpen) props.onOpen(id); }
    }, h("span", { className: "sm-handle-kind" }, kind), tail);
  }
  function Handles(list, onOpen, activeId) {
    if (!list || !list.length) return h("span", { className: "sm-none" }, "None cited");
    return h("span", { className: "sm-handles" }, list.map(function (id) {
      return h(Handle, { key: id, id: id, onOpen: onOpen, active: id === activeId });
    }));
  }

  /* StatusTag: every status carries a glyph and a word, never colour alone. */
  var STATUS = {
    leading: ["action", "●", "Leading"],
    open: ["neutral", "○", "Open"],
    rejected: ["muted", "✕", "Rejected"],
    awaiting_approval: ["attention", "◆", "Awaiting approval"],
    approved: ["ok", "✓", "Approved"],
    edited: ["ok", "✓", "Approved with edits"],
    auto_approved: ["muted", "✓", "Auto-approved"],
    skipped: ["muted", "–", "Skipped"],
    rejected_by_user: ["danger", "✕", "Rejected by IC"],
    running: ["neutral", "…", "Running"],
    ok: ["ok", "✓", "Returned"],
    error: ["danger", "!", "Error"],
    investigating: ["attention", "◆", "Investigating"],
    resolved: ["ok", "✓", "Resolved"]
  };
  function StatusTag(props) {
    var s = STATUS[props.status] || ["neutral", "·", props.status];
    return h("span", { className: cx("sm-tag", "sm-tag-" + s[0], props.className) },
      h("span", { className: "sm-tag-glyph", "aria-hidden": "true" }, s[1]), props.children || s[2]);
  }

  /* Confidence: a 0–1 value, the previous checkpoint's value as a tick, and the delta. */
  function Confidence(props) {
    var v = Math.max(0, Math.min(1, +props.value || 0));
    var hasPrev = typeof props.previous === "number";
    var d = hasPrev ? v - props.previous : 0;
    var delta = hasPrev ? (d === 0 ? "±0.00" : (d > 0 ? "+" : "−") + fixed2(Math.abs(d))) + (props.since ? " since " + props.since : "") : null;
    return h("span", {
      className: cx("sm-conf", props.muted && "sm-conf-muted", props.className),
      title: "Confidence " + fixed2(v) + (hasPrev ? " (was " + fixed2(props.previous) + ")" : "")
    },
      h("span", { className: "sm-conf-num" }, fixed2(v)),
      h("span", { className: "sm-conf-bar", "aria-hidden": "true" },
        h("span", { className: "sm-conf-fill", style: { width: v * 100 + "%" } }),
        hasPrev ? h("span", { className: "sm-conf-prev", style: { left: props.previous * 100 + "%" } }) : null),
      delta ? h("span", { className: "sm-conf-delta" }, delta) : h("span", null));
  }

  /* Notice: tool error states are text, never crashes. */
  var NOTICE_GLYPH = { info: "·", clamped: "⇥", empty: "∅", error: "!" };
  function Notice(props) {
    var tone = props.tone || "info";
    return h("div", { className: cx("sm-notice", "sm-notice-" + tone, props.className), role: tone === "error" ? "alert" : undefined },
      h("span", { className: "sm-notice-glyph", "aria-hidden": "true" }, NOTICE_GLYPH[tone] || "·"), h("span", null, props.children));
  }

  /* Question: the IC's question at a checkpoint. */
  function Question(props) {
    return h("div", { className: cx("sm-q", props.className) },
      h("div", { className: "sm-q-who" }, h("b", null, props.who), props.role ? h("span", null, props.role) : null,
        props.at ? h("span", { className: "sm-mono" }, props.at + " UTC") : null),
      h("div", { className: "sm-q-text" }, props.children));
  }

  /* CheckpointBar */
  var CP_STATE = { done: "Report ready", current: "Investigating", upcoming: "Not reached", stretch: "Stretch" };
  function CheckpointBar(props) {
    var cps = props.checkpoints || [];
    return h("nav", { className: cx("sm-cpbar", props.className), "aria-label": "Checkpoints" },
      cps.map(function (cp) {
        var current = cp.as_of === props.current;
        var reachable = cp.state === "done" || cp.state === "current";
        return h("button", {
          key: cp.as_of, type: "button", className: "sm-cp", disabled: !reachable,
          "aria-current": current ? "step" : undefined,
          onClick: function () { if (props.onSelect) props.onSelect(cp.as_of); }
        }, h("span", { className: "sm-cp-time" }, cp.as_of), h("span", { className: "sm-cp-state" }, cp.label || CP_STATE[cp.state] || cp.state));
      }),
      props.cutoff ? h("div", { className: "sm-cp-cutoff" }, label("Evidence cutoff"), h("span", { className: "sm-mono" }, props.cutoff + " UTC")) : null);
  }

  /* Params as key = value; arrays joined, strings quoted only when they hold spaces. */
  function fmtParam(v) {
    if (Array.isArray(v)) return v.map(fmtParam).join(", ");
    if (typeof v === "string") return /\s/.test(v) ? "\"" + v + "\"" : v;
    return JSON.stringify(v);
  }
  function Params(params) {
    var keys = Object.keys(params || {});
    return h("dl", { className: "sm-params" }, keys.map(function (k) {
      return h(Frag, { key: k }, h("dt", null, k), h("dd", null, fmtParam(params[k])));
    }));
  }

  /* ToolGate: one proposed read-only call, waiting on the IC. */
  function ToolGate(props) {
    var call = props.call || {};
    var status = call.status || "awaiting_approval";
    var awaiting = status === "awaiting_approval";
    var st = React.useState(false), rejecting = st[0], setRejecting = st[1];
    var rs = React.useState(""), reason = rs[0], setReason = rs[1];
    return h("article", { className: cx("sm-call", awaiting && "sm-call-awaiting", status === "rejected_by_user" && "sm-call-rejected", props.className), "aria-label": "Proposed call " + (call.tool || "") },
      h("div", { className: "sm-call-head" },
        h(StatusTag, { status: status }),
        h("span", { className: "sm-call-tool" }, call.tool),
        h("span", { className: "sm-call-id" }, [call.checkpoint && "@" + call.checkpoint, call.id].filter(Boolean).join(" · "))),
      call.reason ? h("div", { className: "sm-call-reason" }, call.reason) : null,
      Params(call.params),
      call.native_query_preview ? h("details", { className: "sm-details", open: props.queryOpen },
        h("summary", null, "Native query"), h("pre", { className: "sm-pre" }, call.native_query_preview)) : null,
      status === "rejected_by_user" && call.rejection ? h("div", { className: "sm-call-verdict" }, h("b", null, "Rejected: "), call.rejection) : null,
      awaiting && rejecting ? h("div", { className: "sm-reject" },
        label("Why reject? The agent reads this and takes another path."),
        h("textarea", { className: "sm-input", rows: 2, value: reason, placeholder: "e.g. We already know PayCore is slow. Check the DB side.", onChange: function (e) { setReason(e.target.value); } }),
        h("div", { className: "sm-call-actions" },
          h(Button, { variant: "danger", size: "sm", disabled: !reason.trim(), onClick: function () { if (props.onReject) props.onReject(reason.trim()); setRejecting(false); } }, "Reject call"),
          h(Button, { variant: "quiet", size: "sm", onClick: function () { setRejecting(false); } }, "Cancel"))) : null,
      awaiting && !rejecting ? h("div", { className: "sm-call-actions" },
        h(Button, { variant: "primary", size: "sm", onClick: props.onApprove }, "Approve"),
        h(Button, { size: "sm", onClick: props.onEdit }, "Edit"),
        h(Button, { size: "sm", onClick: props.onSkip }, "Skip"),
        h(Button, { variant: "danger", size: "sm", onClick: function () { setRejecting(true); } }, "Reject…"),
        props.onAutoApproveChange ? h(Toggle, { checked: props.autoApprove, onChange: props.onAutoApproveChange, label: "Auto-approve reads" }) : null) : null);
  }

  /* Highlight matched terms inside raw text. */
  function marked(text, terms) {
    if (!terms || !terms.length || !text) return text;
    var esc = terms.filter(Boolean).map(function (t) { return t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"); });
    if (!esc.length) return text;
    var re = new RegExp("(" + esc.join("|") + ")", "g");
    return text.split(re).map(function (part, i) { return i % 2 ? h("mark", { key: i }, part) : part; });
  }

  /* ToolResult: what a call returned. Text is shown as the tool wrote it. */
  function ToolResult(props) {
    var r = props.result || {};
    var w = r.window || {};
    var s = r.sampling;
    var isErr = r.status === "error";
    return h("article", { className: cx("sm-call", isErr ? "sm-call-error" : "sm-call-ok", props.className) },
      h("div", { className: "sm-call-head" },
        h(StatusTag, { status: isErr ? "error" : "ok" }),
        props.tool ? h("span", { className: "sm-call-tool" }, props.tool) : null,
        h("span", { className: "sm-call-id" }, r.id)),
      h("pre", { className: "sm-pre" }, marked(r.text, props.highlight)),
      w.clamped ? h(Notice, { tone: "clamped" }, "Window clamped to the checkpoint (" + utc(w.until, false) + " UTC). Nothing after it is visible.") : null,
      h("div", { className: "sm-call-meta" },
        w.since ? h("span", null, "Window ", h("span", { className: "sm-mono" }, utc(w.since, false) + "–" + utc(w.until, false) + " UTC")) : null,
        s ? h("span", null, "Sampled ", h("span", { className: "sm-mono" }, "1 in " + s.keep_1_in + (s.per ? " per " + s.per : "")), "; counts are estimates") : null),
      r.handles && r.handles.length ? h("div", { className: "sm-hyp-ev" }, label("Handles"), Handles(r.handles, props.onOpenHandle, props.activeHandle)) : null,
      r.native_query ? h("details", { className: "sm-details" }, h("summary", null, "Native query"), h("pre", { className: "sm-pre" }, r.native_query)) : null);
  }

  /* EvidenceRecord: one record, normalised beside raw. Never merge the two. */
  function EvidenceRecord(props) {
    var r = props.record || {};
    return h("article", { className: cx("sm-rec", props.className) },
      h("div", { className: "sm-rec-head" },
        h(Handle, { id: r.handle, active: true }),
        r.level ? h("span", { className: "sm-level sm-level-" + r.level }, r.level) : null,
        h("span", { className: "sm-muted", style: { fontSize: 12 } }, r.source)),
      h("div", { className: "sm-rec-grid" },
        h("div", { className: "sm-rec-col" },
          label("Normalised"),
          h("dl", { className: "sm-params" },
            h("dt", null, "t_utc"), h("dd", null, r.t_utc),
            r.host ? h(Frag, null, h("dt", null, "host"), h("dd", null, r.host)) : null,
            r.sampling ? h(Frag, null, h("dt", null, "sampling"), h("dd", null, r.sampling)) : null),
          h("div", { className: "sm-rec-summary" }, r.summary)),
        h("div", { className: "sm-rec-col" },
          label("Raw, as the source wrote it"),
          h("pre", { className: "sm-pre" }, marked(r.raw, props.highlight)),
          r.raw_format ? h("span", { className: "sm-muted", style: { fontSize: 12 } }, r.raw_format) : null)));
  }

  /* ConflictPair: sources that disagree, side by side. */
  function ConflictPair(props) {
    var c = props.conflict || {};
    return h("section", { className: cx("sm-conflict", props.className) },
      h("div", { className: "sm-conflict-head" },
        h("span", { className: "sm-conflict-q" }, c.question),
        h(StatusTag, { status: "awaiting_approval" }, (c.answers || []).length + " sources disagree, not merged")),
      h("div", { className: "sm-conflict-cols" }, (c.answers || []).map(function (a, i) {
        return h("div", { key: i, className: "sm-conflict-col" },
          label(a.source),
          h("span", { className: "sm-conflict-val" }, a.value),
          a.note ? h("span", { className: "sm-conflict-note" }, a.note) : null,
          a.handle ? h("span", null, h(Handle, { id: a.handle, onOpen: props.onOpenHandle })) : null);
      })));
  }

  /* ImpactSummary */
  function ImpactSummary(props) {
    var i = props.impact || {};
    var p = i.priority || {};
    function cell(name, value, wide) {
      return value ? h("div", { className: cx("sm-impact-cell", wide && "sm-impact-wide") }, label(name), h("span", null, value)) : null;
    }
    return h("section", { className: cx("sm-impact", props.className), "aria-label": "Impact" },
      cell("Customer impact", i.customer, true),
      cell("Business service", i.business_service),
      p.suggested ? h("div", { className: "sm-impact-cell" }, label("Priority (suggested)"),
        h("span", null, h("span", { className: "sm-prio sm-prio-" + p.suggested }, p.suggested),
          h("span", { className: "sm-muted" }, " impact " + p.impact + ", urgency " + p.urgency))) : null,
      cell("Assignment group", i.assignment_group),
      cell("On call", i.oncall),
      i.cis && i.cis.length ? h("div", { className: "sm-impact-cell" }, label("Affected CIs"), h("span", { className: "sm-mono", style: { fontSize: 12 } }, i.cis.join(", "))) : null);
  }

  /* HypothesisCard */
  function HypothesisCard(props) {
    var hy = props.hypothesis || {};
    var hist = hy.history || [];
    var prev = hist.length > 1 ? hist[hist.length - 2] : null;
    var muted = hy.status === "rejected";
    return h("article", { className: cx("sm-hyp", "sm-hyp-" + hy.status, props.className) },
      h("div", { className: "sm-hyp-head" },
        h(StatusTag, { status: hy.status }),
        hy.role ? label(hy.role) : null,
        prev && prev.status !== hy.status ? h("span", { className: "sm-moved" }, prev.status + " → " + hy.status) : null,
        h(Confidence, { value: hy.confidence, previous: prev ? prev.confidence : undefined, since: prev ? utc(prev.as_of, false) : undefined, muted: muted })),
      h("div", { className: "sm-hyp-statement" }, hy.statement),
      h("div", { className: "sm-hyp-evidence" },
        h("div", { className: "sm-hyp-ev" }, label("Support"), Handles(hy.support, props.onOpenHandle)),
        h("div", { className: "sm-hyp-ev" }, label("Against"), Handles(hy.against, props.onOpenHandle)),
        hy.changes && hy.changes.length ? h("div", { className: "sm-hyp-ev" }, label("Changes"), Handles(hy.changes, props.onOpenHandle)) : null),
      hist.length && props.showHistory !== false ? h("details", { className: "sm-details", open: props.historyOpen },
        h("summary", null, "Why it moved"),
        h("ul", { className: "sm-hist" }, hist.map(function (e, k) {
          return h("li", { key: k },
            h("span", { className: "sm-mono" }, utc(e.as_of, false)),
            h("span", { className: "sm-mono" }, e.status + " " + fixed2(e.confidence)),
            h("span", { className: "sm-hist-why" }, (e.because || []).join("; ")));
        }))) : null);
  }

  /* Timeline: UTC on the left; the source's own clock on hover. */
  function Timeline(props) {
    var events = props.events || [];
    var lastDay = null;
    return h("ol", { className: cx("sm-tl", props.className) }, events.map(function (e, i) {
      var day = utcDay(e.t_utc);
      var showDay = day !== lastDay;
      lastDay = day;
      return h("li", { key: i, className: cx("sm-tl-row", e.key && "sm-tl-key") },
        h("span", { className: "sm-tl-time", title: e.source_clock ? "Source clock: " + e.source_clock : e.t_utc, tabIndex: 0 },
          showDay ? h("span", { className: "sm-tl-time-day" }, day) : null, utc(e.t_utc)),
        h("span", { className: "sm-tl-rail", "aria-hidden": "true" }, h("span", { className: "sm-tl-dot" })),
        h("div", { className: "sm-tl-body" },
          h("span", { className: "sm-tl-event" }, e.event),
          e.evidence && e.evidence.length ? Handles(e.evidence, props.onOpenHandle) : null,
          e.gap ? h("span", { className: "sm-tl-gap" }, e.gap) : null));
    }));
  }

  /* ActionList */
  function ActionList(props) {
    return h("ol", { className: cx("sm-acts", props.className) }, (props.actions || []).map(function (a) {
      return h("li", { key: a.rank, className: "sm-act" },
        h("span", { className: "sm-act-rank" }, a.rank),
        h("span", { className: "sm-act-text" }, a.action),
        h("span", { className: "sm-act-meta" },
          a.owner ? h("span", null, "Owner ", h("b", { style: { color: "var(--ink)", fontWeight: 500 } }, a.owner)) : null,
          a.evidence && a.evidence.length ? Handles(a.evidence, props.onOpenHandle) : h("span", null, "No evidence cited; a judgement call")));
    }));
  }

  /* AvoidList: harmful actions and why. */
  function AvoidList(props) {
    return h("section", { className: cx("sm-acts sm-avoid", props.className) },
      h("div", { className: "sm-avoid-head" }, h("span", { "aria-hidden": "true", className: "sm-mono" }, "✕"), props.title || "Do not"),
      (props.avoid || []).map(function (a, i) {
        return h("div", { key: i, className: "sm-act" },
          h("span", { className: "sm-act-rank", "aria-hidden": "true" }, "✕"),
          h("span", { className: "sm-act-text" }, a.action),
          h("span", { className: "sm-act-why" }, a.why));
      }));
  }

  /* CoverageTable: what was checked, derived from the trace. */
  function CoverageTable(props) {
    var c = props.coverage || {};
    return h("div", { className: props.className },
      h("table", { className: "sm-cov" },
        h("thead", null, h("tr", null, h("th", null, label("Source")), h("th", null, label("Window")), h("th", null, label("Queries")), h("th", null, label("Finding")))),
        h("tbody", null, (c.checked || []).map(function (r, i) {
          return h("tr", { key: i },
            h("td", { className: "sm-cov-src" }, r.source),
            h("td", { className: "sm-cov-num" }, r.window || "–"),
            h("td", { className: "sm-cov-num" }, r.queries != null ? r.queries : "–"),
            h("td", null, r.finding));
        }))),
      c.not_checked && c.not_checked.length ? h("div", { className: "sm-cov-gaps" }, label("Not checked"),
        c.not_checked.map(function (n, i) { return h(StatusTag, { key: i, status: "skipped" }, n); })) : null);
  }

  /* StakeholderMessage */
  function StakeholderMessage(props) {
    var cs = React.useState(false), copied = cs[0], setCopied = cs[1];
    function copy() {
      var done = function () { setCopied(true); setTimeout(function () { setCopied(false); }, 1600); };
      if (props.onCopy) props.onCopy(props.text);
      try { navigator.clipboard.writeText(props.text).then(done, done); } catch (e) { done(); }
    }
    return h("article", { className: cx("sm-msg", props.className) },
      h("div", { className: "sm-msg-head" },
        h("span", { className: "sm-msg-aud" }, props.audience),
        props.channel ? h("span", { className: "sm-muted", style: { fontSize: 12 } }, props.channel) : null,
        h(Button, { size: "sm", onClick: copy, "aria-live": "polite" }, copied ? "Copied" : "Copy")),
      h("div", { className: "sm-msg-body" }, props.text),
      props.as_of || props.next_update ? h("div", { className: "sm-msg-foot" },
        props.as_of ? h("span", null, "As of ", h("span", { className: "sm-mono" }, props.as_of + " UTC")) : null,
        props.next_update ? h("span", null, "Next update ", h("span", { className: "sm-mono" }, props.next_update + " UTC")) : null) : null);
  }

  window.Smalls = Object.assign(window.Smalls || {}, {
    Button: Button, Toggle: Toggle, Handle: Handle, StatusTag: StatusTag, Confidence: Confidence, Notice: Notice,
    Question: Question, CheckpointBar: CheckpointBar, ToolGate: ToolGate, ToolResult: ToolResult,
    EvidenceRecord: EvidenceRecord, ConflictPair: ConflictPair, ImpactSummary: ImpactSummary,
    HypothesisCard: HypothesisCard, Timeline: Timeline, ActionList: ActionList, AvoidList: AvoidList,
    CoverageTable: CoverageTable, StakeholderMessage: StakeholderMessage
  });
})();
