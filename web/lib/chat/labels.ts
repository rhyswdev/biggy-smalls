// Plain labels for the agent's checks (docs/v4-plan.md 5.1): each tool and its arguments as a short phrase a
// Major Incident Manager reads at a glance, in the present ("Search every log for "timeout"") for the approval
// tray and in the past ("searched every log for "timeout"") for the code-written summary of how Smalls got here.
// Generic: they read arguments, never values from any one incident. A tool not listed falls back to the
// model's own reason for the call.

export interface Label { now: string; past: string }

type Args = Record<string, unknown>;

const s = (v: unknown): string => (typeof v === "string" ? v.trim() : typeof v === "number" ? String(v) : "");
const q = (v: string): string => `“${v}”`;
const list = (v: unknown): string[] => (Array.isArray(v) ? v.map(s).filter(Boolean) : s(v) ? [s(v)] : []);
const join = (xs: string[], last = "and"): string => (xs.length <= 1 ? xs.join("") : `${xs.slice(0, -1).join(", ")} ${last} ${xs[xs.length - 1]}`);

const UNITS: Record<string, [string, string]> = { s: ["second", "seconds"], m: ["minute", "minutes"], h: ["hour", "hours"], d: ["day", "days"] };

/** A relative or clock time as words: "-2h" is "the last 2 hours", "14:15" is "14:15". */
function when(v: string): { rel: string | null; at: string } {
  const m = /^-\s*(\d+)\s*([smhd])$/.exec(v);
  if (m) {
    const n = Number(m[1]);
    const [one, many] = UNITS[m[2]!]!;
    return { rel: n === 1 ? `the last ${one}` : `the last ${n} ${many}`, at: v };
  }
  const clock = /T?(\d\d:\d\d)(?::\d\d)?/.exec(v);
  return { rel: null, at: clock ? clock[1]! : v };
}

/** The window a check covers, as a trailing phrase: " over the last 2 hours", " since 14:15", " from 14:00 to 14:30". */
export function windowText(args: Args): string {
  const since = s(args.since), until = s(args.until);
  if (!since && !until) return "";
  const a = since ? when(since) : null, b = until ? when(until) : null;
  if (a && !b) return a.rel ? ` over ${a.rel}` : ` since ${a.at}`;
  if (!a && b) return ` up to ${b.at}`;
  return ` from ${a!.rel ? a!.at : a!.at} to ${b!.at}`;
}

function channel(v: string): string {
  return v ? (v.startsWith("#") ? v : `#${v}`) : "the chat channels";
}

const TABLES: Record<string, string> = { incident: "incidents", journal: "work notes", user: "people", group: "groups" };

function build(tool: string, a: Args): [string, string] | null {
  const w = windowText(a);
  switch (tool) {
    case "search_logs": {
      const where = s(a.sourcetype) ? `${s(a.sourcetype)} logs` : s(a.host) ? `the logs on ${s(a.host)}` : "every log";
      const host = s(a.sourcetype) && s(a.host) ? ` on ${s(a.host)}` : "";
      const terms = list(a.terms);
      const what = terms.length ? ` for ${join(terms.map(q))}` : "";
      const by = s(a.group_by) ? `, counted by ${s(a.group_by)}` : "";
      return [`Search ${where}${host}${what}${w}${by}`, `searched ${where}${host}${what}${w}${by}`];
    }
    case "list_alerts": {
      const about = s(a.text) ? ` about ${q(s(a.text))}` : "";
      const from = s(a.source) ? ` from ${s(a.source)}` : "";
      const which = s(a.state) === "all" ? "alerts, cleared ones too," : "active alerts";
      return [`List ${which}${from}${about}${w}`, `listed ${which}${from}${about}${w}`];
    }
    case "get_record":
      return s(a.handle) ? [`Open the record ${s(a.handle)}`, `opened the record ${s(a.handle)}`] : null;
    case "query_metrics": {
      const scope = s(a.scope) ? ` for ${s(a.scope)}` : "";
      return s(a.metric) ? [`Chart ${s(a.metric)}${scope}${w}`, `charted ${s(a.metric)}${scope}${w}`] : null;
    }
    case "list_changes": {
      const kind = s(a.change_kind) ? ` (${s(a.change_kind)})` : "";
      const about = s(a.text) ? ` mentioning ${q(s(a.text))}` : "";
      return [`List changes${kind}${about}${w}`, `listed changes${kind}${about}${w}`];
    }
    case "itsm_query": {
      const table = TABLES[s(a.table)] ?? "records";
      const about = s(a.text) ? ` for ${q(s(a.text))}` : "";
      return [`Look up ${table} in the service desk${about}`, `looked up ${table} in the service desk${about}`];
    }
    case "search_knowledge":
      return s(a.query) ? [`Search the knowledge base for ${q(s(a.query))}`, `searched the knowledge base for ${q(s(a.query))}`] : null;
    case "get_topology": {
      const far = Number(a.hops) === 2 ? ", two steps out" : "";
      return s(a.entity) ? [`Map what ${s(a.entity)} connects to${far}`, `mapped what ${s(a.entity)} connects to${far}`] : null;
    }
    case "get_oncall": {
      const who = s(a.service) || s(a.policy);
      return [`Find who is on call${who ? ` for ${who}` : ""}`, `found who is on call${who ? ` for ${who}` : ""}`];
    }
    case "read_chat": {
      const about = s(a.text) ? ` for ${q(s(a.text))}` : "";
      return [`Look in ${channel(s(a.channel))}${about}${w}`, `looked in ${channel(s(a.channel))}${about}${w}`];
    }
    case "check_status_page": {
      const who = s(a.provider);
      return [`Check ${who ? `${who}’s` : "the provider’s"} status page`, `checked ${who ? `${who}’s` : "the provider’s"} status page`];
    }
    case "propose_action": {
      const target = s(a.target) && s(a.target).toLowerCase() !== "unknown" ? ` on ${s(a.target)}` : "";
      const kind = s(a.kind).replace(/_/g, " ") || "an action";
      return [`Propose: ${kind}${target}`, `proposed ${kind}${target}`];
    }
    default:
      return null;
  }
}

/** The plain label for one check, from its tool and arguments, else from the model's reason for it. */
export function plainLabel(row: { tool: string; args: Args; rationale?: { tests?: string } | null }): Label {
  const built = build(row.tool, row.args ?? {});
  if (built) return { now: built[0], past: built[1] };
  const why = s(row.rationale?.tests) || row.tool.replace(/_/g, " ");
  const now = why.charAt(0).toUpperCase() + why.slice(1);
  return { now, past: `checked ${why.charAt(0).toLowerCase()}${why.slice(1)}` };
}
