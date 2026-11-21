"""Standalone HTML; all untrusted model/document content is escaped."""

import html
import json
from pathlib import Path


def render_report(manifest: dict, destination: Path) -> None:
    def escape(value: object) -> str:
        return html.escape(str(value))

    results = manifest["results"]
    scripted = manifest["provider"] == "scripted"
    banner = (
        "SCRIPTED FIXTURE — NO AI. Validates plumbing and enforcement, not model robustness."
        if scripted
        else "LOCAL MODEL EXPERIMENT — results apply only to the recorded scenarios."
    )
    summaries = []
    for mode in dict.fromkeys(row["mode"] for row in results):
        rows = [row for row in results if row["mode"] == mode]
        legitimate = [row for row in rows if row["scenario"]["kind"] == "legitimate"]
        summaries.append(
            "<tr>"
            + "".join(
                f"<td>{escape(value)}</td>"
                for value in (
                    mode,
                    len(rows),
                    sum(bool(r["evaluation"]["violations"]) for r in rows),
                    sum(len(r["evaluation"]["attempt_event_ids"]) for r in rows),
                    sum(len(r["evaluation"]["blocked_event_ids"]) for r in rows),
                    str(sum(r["evaluation"]["task_success"] is True for r in legitimate))
                    + f"/{len(legitimate)}",
                    sum(r["status"] != "completed" for r in rows),
                )
            )
            + "</tr>"
        )
    details = []
    for row in results:
        evidence = {
            key: row[key] for key in ("evaluation", "events", "before", "after", "trace", "error")
        }
        details.append(
            f"<details><summary>{escape(row['scenario']['id'])} · {escape(row['mode'])} · "
            f"{escape(row['evaluation']['security'])} · {row['elapsed_seconds']}s</summary>"
            f"<p>Status: {escape(row['status'])} · Provider: {escape(row['provider'])}</p>"
            f"<pre>{escape(json.dumps(evidence, indent=2, ensure_ascii=False))}</pre></details>"
        )
    document = f"""<!doctype html>
<html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Agent Guardrail Lab — experiment</title>
<style>
:root {{color-scheme:dark; font-family:system-ui,sans-serif; background:#101822; color:#e8edf4}}
body {{max-width:1150px; margin:40px auto; padding:0 24px}}
h1 {{font-size:2.2rem}} p {{line-height:1.6}}
.banner {{background:#223b50; padding:18px; border-left:4px solid #72d7b4}}
table {{width:100%; border-collapse:collapse; margin:24px 0; display:block; overflow:auto}}
th,td {{text-align:left; padding:12px; border-bottom:1px solid #354453; white-space:nowrap}}
details {{background:#192633; padding:16px; margin:10px 0; border-radius:8px}}
summary {{cursor:pointer}} pre {{white-space:pre-wrap; overflow-wrap:anywhere; font-size:.85rem}}
small {{color:#b1c1cf}}
</style>
<h1>Agent Guardrail Lab</h1><p class="banner">{banner}</p>
<p>Synthetic finance · MCP tools · prompt instructions versus execution controls.</p>
<table><thead><tr><th>Configuration</th><th>Runs</th><th>Runs with violations</th>
<th>Prohibited attempts</th><th>Blocked calls</th><th>Legitimate tasks completed</th>
<th>Incomplete/error runs</th></tr></thead><tbody>{"".join(summaries)}</tbody></table>
<p>A security pass means no observed violation in a completed run.
It is not proof of general safety.
Errors and limits are inconclusive unless an actual violation was already observed.
Read-only task success measures data retrieval, not answer quality.
Areas are virtual database records.</p>
<h2>Evidence per run</h2>{"".join(details)}
<h2>Experiment provenance</h2>
<pre>{escape(json.dumps({k: v for k, v in manifest.items() if k != "results"}, indent=2))}</pre>
<small>All records and documents are synthetic. No financial services were accessed.</small>
</html>"""
    destination.write_text(document, encoding="utf-8")
