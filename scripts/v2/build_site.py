"""Static demo page (GitHub Pages friendly, no server) built only from recorded real runs.

    uv run python scripts/v2/build_site.py   # -> site/index.html (+ site/data.json)
"""

import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "results" / "v2"
OUT = ROOT / "site"
DEMO_IDS = ["au-q41", "cx-q23", "au-q53", "cx-q06", "au-q43"]
RT_DEMO = ["au-rt05", "au-rt19", "cx-rt01", "au-rt12", "au-rt15"]


def load(p):
    return json.loads((R / p).read_text()) if (R / p).exists() else None


def esc(s):
    return html.escape(str(s or ""))


def md(s):
    s = esc(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"\[(S\d+)\]", r'<span class="tag">\1</span>', s)
    return s.replace("\n", "<br>")


def runs(name):
    p = R / "runs" / f"eval__{name}.jsonl"
    return {r["id"]: r for r in map(json.loads, p.read_text().splitlines())} if p.exists() else {}


def main():
    ev, rt, lag = load("eval.json"), load("redteam_round1.json"), load("lag_window.json")
    abl = load("redteam_roundablation_norecheck_nodelivery.json")
    cal = load("judge_calibration.json")
    items = {}
    for f in ("questions_author", "questions_codex"):
        for line in (ROOT / "eval_sets" / "v2" / f"{f}.jsonl").read_text().splitlines():
            q = json.loads(line)
            items[q["id"]] = q
    cp = ev["systems"]["copilot"]
    sysrows = "".join(
        f"<tr><td>{esc(n)}</td><td>{v['all']['accuracy']:.0%}</td><td>{v['author']['accuracy']:.0%}</td>"
        f"<td>{v['codex_blind']['accuracy']:.0%}</td><td>{v['route_accuracy']:.0%}</td></tr>"
        for n, v in ev["systems"].items())
    run = runs("copilot")
    convo = []
    for i in DEMO_IDS:
        r, q = run.get(i), items.get(i)
        if not r:
            continue
        t = r.get("trace", {})
        cites = "".join(f'<li><span class="tag">{esc(c["tag"])}</span> <a href="{esc(c["url"])}">{esc(c["title"][:70])}</a>'
                        f' <code>{esc(c["doc_id"])}:{c["start"]}-{c["end"]}</code></li>' for c in r.get("citations", []))
        sql = f'<details><summary>SQL run as <code>{esc(t.get("db_role"))}</code> (departments {esc(", ".join(t.get("live_depts") or []))})</summary><pre>{esc(r.get("sql"))}</pre></details>' if r.get("sql") else ""
        tot = t.get("totals", {})
        convo.append(f"""<div class="msg"><div class="who">{esc(q['user'])} · <i>{esc(q['type'])} · {esc(q.get('author'))}</i></div>
<div class="q">{esc(q['question'])}</div><div class="a"><div class="status {esc(r['status'])}">{esc(r['status'])} · route {esc(r.get('route'))}</div>
{md(r['text'])}<ul class="cites">{cites}</ul>{sql}
<div class="meta">trace <code>{esc(t.get('trace_id'))}</code> · {tot.get('llm_calls', 0)} LLM calls · {tot.get('input_tokens', 0)}+{tot.get('output_tokens', 0)} tokens · ${tot.get('cost_usd', 0):.4f} list price</div></div></div>""")
    rtres = {r["id"]: r for r in rt["results"]}
    rtrows = []
    for i in RT_DEMO:
        r = rtres.get(i)
        if not r:
            continue
        t = r["turns"][-1]
        rtrows.append(f"<tr><td><code>{esc(i)}</code></td><td>{esc(r['category'])}</td><td>{esc(t.get('delivery'))}</td>"
                      f"<td>{len(t.get('dropped_by_recheck') or [])}</td><td>{'LEAK' if r['leaked'] else 'no leak'}</td>"
                      f"<td>{esc((t.get('text') or '(denied before answering)')[:160])}</td></tr>")
    ls = lag["summary"]
    abl_line = (f"With the recheck and delivery check switched off, {abl['summary']['leaked']} of "
                f"{abl['summary']['scenarios']} revocation scenarios leak (oracle sanity check)." if abl else "")
    cal_line = (f"Judge calibration: Claude Haiku vs ChatGPT on {cal['n']} answers, agreement {cal['agreement']:.0%}, "
                f"Cohen's kappa {cal['cohen_kappa']}." if cal else "")
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>analytics-agent v2 · Chicago 311 copilot</title>
<meta name="viewport" content="width=device-width, initial-scale=1"><style>
body{{font:15px/1.5 -apple-system,Segoe UI,Helvetica,Arial,sans-serif;margin:0;background:#f6f7f9;color:#1d2330}}
header{{background:#14213d;color:#fff;padding:28px 40px}} header h1{{margin:0 0 6px;font-size:26px}} header p{{margin:0;opacity:.85}}
main{{max-width:1100px;margin:0 auto;padding:24px 40px}} section{{background:#fff;border-radius:10px;padding:18px 22px;margin:18px 0;box-shadow:0 1px 3px #0001}}
h2{{font-size:18px;margin:0 0 12px}} .kpis{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}}
.kpi{{background:#eef2f8;border-radius:8px;padding:12px}} .kpi b{{font-size:24px;display:block}} .kpi span{{font-size:12px;color:#4a5568}}
table{{border-collapse:collapse;width:100%;font-size:14px}} td,th{{border-bottom:1px solid #e5e7eb;padding:6px 8px;text-align:left;vertical-align:top}}
.msg{{border-left:3px solid #fca311;padding:8px 14px;margin:14px 0}} .who{{font-size:12px;color:#6b7280}} .q{{font-weight:600;margin:4px 0}}
.a{{background:#f9fafb;border-radius:8px;padding:10px 12px}} .status{{font-size:12px;font-weight:600;color:#2563eb}} .status.clarify{{color:#b45309}}
.tag{{background:#dbeafe;border-radius:4px;padding:0 4px;font-size:12px}} .cites{{font-size:13px;margin:6px 0 0 0}}
pre{{white-space:pre-wrap;font-size:12px;background:#111827;color:#e5e7eb;padding:10px;border-radius:6px}} .meta{{font-size:12px;color:#6b7280;margin-top:6px}}
.sim{{background:#fff7e6;border:1px solid #fcd34d;border-radius:6px;padding:8px 12px;font-size:13px}}
</style></head><body><header><h1>Governed data &amp; knowledge copilot — City of Chicago 311</h1>
<p>Permission-aware answers over 157 real City documents and 4,033,015 real 311 service requests (Postgres, row-level security). Every number below comes from recorded runs in <code>results/v2/</code>.</p></header><main>
<div class="sim"><b>Simulated:</b> the org chart (analysts, departments, groups) and which documents are restricted to whom. <b>Real:</b> the service requests, the documents, every model output and every measurement.</div>
<section><h2>Headline</h2><div class="kpis">
<div class="kpi"><b>{rt['summary']['leaked']}/{rt['summary']['scenarios']}</b><span>permission leaks, first red-team round (half written blind by ChatGPT)</span></div>
<div class="kpi"><b>{ls['recheck_on']['doc_leaks'] + ls['recheck_on']['sql_leaks']} vs {ls['off_leaks_inside_window']}</b><span>leaks inside the 300 s revocation-lag window, recheck on vs off ({ls['inside_window_probes_per_mode']} probes)</span></div>
<div class="kpi"><b>{cp['all']['accuracy']:.0%}</b><span>answers correct on {cp['all']['n']} questions ({cp['author']['accuracy']:.0%} author / {cp['codex_blind']['accuracy']:.0%} blind half)</span></div>
<div class="kpi"><b>{cp['clarify_rate_on_conflicts']:.0%}</b><span>asks back when a metric has two governed definitions</span></div></div></section>
<section><h2>Quality vs baselines</h2><table><tr><th>system</th><th>all</th><th>author half</th><th>codex-blind half</th><th>route acc.</th></tr>{sysrows}</table>
<p class="meta">{esc(cal_line)} Small sample (N={cp['all']['n']}); scale up with <code>make eval-v2 N=all</code>.</p></section>
<section><h2>Recorded conversations</h2>{''.join(convo)}</section>
<section><h2>Red team (recorded)</h2><table><tr><th>id</th><th>category</th><th>delivery</th><th>chunks dropped by recheck</th><th>oracle</th><th>answer</th></tr>{''.join(rtrows)}</table>
<p class="meta">{esc(abl_line)}</p></section>
</main></body></html>"""
    OUT.mkdir(exist_ok=True)
    (OUT / "index.html").write_text(page)
    print("site/index.html", len(page))


if __name__ == "__main__":
    main()
