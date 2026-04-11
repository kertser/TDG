"""
HTML report generator for tactical test results.

Generates a self-contained HTML file with:
  - Summary dashboard (pass/fail per scenario)
  - Per-scenario detail sections
  - Unit timeline tables
  - Event logs
  - Issues & recommendations
"""
from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from scripts.tactical_tests.collector import ScenarioResult, StatisticalResult


def generate_report(results: list[ScenarioResult], output_path: str | Path,
                    stat_results: list[StatisticalResult] | None = None) -> str:
    """Generate HTML report and write to file. Returns the output path."""
    output_path = Path(output_path)

    total = len(results)
    passed = sum(1 for r in results if r.passed)
    failed = total - passed

    total_assertions = sum(r.assertions_total for r in results)
    passed_assertions = sum(r.assertions_passed for r in results)

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    sections = []
    for i, r in enumerate(results):
        sections.append(_render_scenario_detail(r, i))

    issues_html = _render_issues(results)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Tactical Test Report — {now}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background: #1a1a2e; color: #e0e0e0; padding: 20px; }}
h1 {{ color: #ffd700; margin-bottom: 10px; font-size: 24px; }}
h2 {{ color: #87ceeb; margin: 20px 0 10px; font-size: 20px; }}
h3 {{ color: #98fb98; margin: 15px 0 8px; font-size: 16px; }}
.summary {{ background: #16213e; border: 1px solid #0f3460; border-radius: 8px; padding: 15px; margin: 15px 0; }}
.summary-stats {{ display: flex; gap: 30px; flex-wrap: wrap; }}
.stat {{ text-align: center; }}
.stat-value {{ font-size: 32px; font-weight: bold; }}
.stat-label {{ font-size: 12px; color: #888; }}
.pass {{ color: #4caf50; }}
.fail {{ color: #f44336; }}
.warn {{ color: #ff9800; }}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 13px; }}
th, td {{ border: 1px solid #333; padding: 6px 10px; text-align: left; }}
th {{ background: #0f3460; color: #87ceeb; }}
tr:nth-child(even) {{ background: #16213e; }}
tr:hover {{ background: #1a3a5c; }}
.badge {{ display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; }}
.badge-pass {{ background: #1b5e20; color: #4caf50; }}
.badge-fail {{ background: #b71c1c; color: #ef9a9a; }}
details {{ margin: 10px 0; border: 1px solid #333; border-radius: 6px; overflow: hidden; }}
summary {{ background: #16213e; padding: 10px 15px; cursor: pointer; font-weight: bold; }}
summary:hover {{ background: #1a3a5c; }}
.detail-content {{ padding: 15px; background: #0d1117; }}
.event-log {{ max-height: 400px; overflow-y: auto; font-family: 'Consolas', monospace; font-size: 12px; background: #0a0a15; padding: 10px; border-radius: 4px; }}
.event-log div {{ padding: 2px 0; border-bottom: 1px solid #1a1a2e; }}
.evt-combat {{ color: #ff6b6b; }}
.evt-detection {{ color: #69db7c; }}
.evt-movement {{ color: #74c0fc; }}
.evt-morale {{ color: #ffd43b; }}
.evt-destroyed {{ color: #ff0000; font-weight: bold; }}
.evt-artillery {{ color: #da77f2; }}
.timeline {{ overflow-x: auto; }}
.timeline table {{ font-size: 11px; }}
.timeline td {{ white-space: nowrap; }}
.strength-bar {{ display: inline-block; height: 10px; border-radius: 2px; }}
.issues {{ background: #2d1b1b; border: 1px solid #5c2020; border-radius: 8px; padding: 15px; margin: 15px 0; }}
.issues li {{ margin: 5px 0; }}
.footer {{ text-align: center; color: #555; margin-top: 30px; font-size: 11px; }}
</style>
</head>
<body>
<h1>🎯 Tactical Engine Test Report</h1>
<p style="color:#888">Generated: {now}</p>

<div class="summary">
<h2>Summary Dashboard</h2>
<div class="summary-stats">
<div class="stat">
<div class="stat-value">{total}</div>
<div class="stat-label">SCENARIOS</div>
</div>
<div class="stat">
<div class="stat-value pass">{passed}</div>
<div class="stat-label">PASSED</div>
</div>
<div class="stat">
<div class="stat-value fail">{failed}</div>
<div class="stat-label">FAILED</div>
</div>
<div class="stat">
<div class="stat-value">{passed_assertions}/{total_assertions}</div>
<div class="stat-label">ASSERTIONS</div>
</div>
</div>

<table style="margin-top:15px">
<tr>
<th>#</th><th>Scenario</th><th>Status</th>
<th>Assertions</th><th>Ticks</th><th>Time</th><th>Errors</th>
</tr>
{"".join(_render_summary_row(i, r) for i, r in enumerate(results))}
</table>
</div>

{issues_html}

{"".join(sections)}

<div class="footer">
KShU Tactical Engine Test Framework v1.0 — Automated scenario-based testing
</div>
</body>
</html>"""

    output_path.write_text(html_content, encoding="utf-8")
    return str(output_path)


def _render_summary_row(idx: int, r: ScenarioResult) -> str:
    status = '<span class="badge badge-pass">✅ PASS</span>' if r.passed else '<span class="badge badge-fail">❌ FAIL</span>'
    errs = f'<span class="fail">{len(r.errors)}</span>' if r.errors else "0"
    return f"""<tr>
<td>{idx+1}</td>
<td>{html.escape(r.scenario_name)}</td>
<td>{status}</td>
<td>{r.assertions_passed}/{r.assertions_total}</td>
<td>{r.ticks_run}</td>
<td>{r.duration_seconds:.1f}s</td>
<td>{errs}</td>
</tr>"""


def _render_scenario_detail(r: ScenarioResult, idx: int) -> str:
    status_icon = "✅" if r.passed else "❌"
    badge = "badge-pass" if r.passed else "badge-fail"

    # Assertions table
    assertions_rows = []
    for a in r.assertions:
        icon = "✅" if a.passed else "❌"
        css = "pass" if a.passed else "fail"
        assertions_rows.append(f"""<tr>
<td>{icon}</td>
<td class="{css}">{html.escape(a.description)}</td>
<td>{html.escape(a.detail[:120])}</td>
</tr>""")

    # Unit timeline (sampled every 5 ticks + first + last)
    timeline_html = _render_unit_timeline(r)

    # Event log (filtered to significant events)
    event_log = _render_event_log(r)

    # Errors
    errors_html = ""
    if r.errors:
        error_items = "".join(f"<li class='fail'>{html.escape(e)}</li>" for e in r.errors)
        errors_html = f"<h3>⚠️ Errors</h3><ul>{error_items}</ul>"

    # Key metrics
    all_events = r.all_events()
    combat_count = sum(1 for e in all_events if e.get("event_type") == "combat")
    destroyed_count = sum(1 for e in all_events if e.get("event_type") == "unit_destroyed")
    detection_count = sum(1 for e in all_events if e.get("event_type") == "contact_new")
    arty_count = sum(1 for e in all_events if e.get("event_type") == "artillery_support")
    movement_count = sum(1 for e in all_events if e.get("event_type") == "movement")

    return f"""
<details {"open" if not r.passed else ""}>
<summary>{status_icon} #{idx+1} {html.escape(r.scenario_name)} <span class="badge {badge}">{r.assertions_passed}/{r.assertions_total}</span></summary>
<div class="detail-content">
<p><em>{html.escape(r.scenario_description)}</em></p>
<p>Ticks: {r.ticks_run} | Duration: {r.duration_seconds:.1f}s</p>

<h3>📊 Key Metrics</h3>
<table style="width:auto">
<tr><td>Combat exchanges</td><td>{combat_count}</td></tr>
<tr><td>Units destroyed</td><td>{destroyed_count}</td></tr>
<tr><td>New contacts</td><td>{detection_count}</td></tr>
<tr><td>Artillery support</td><td>{arty_count}</td></tr>
<tr><td>Movement events</td><td>{movement_count}</td></tr>
</table>

<h3>✔️ Assertions</h3>
<table>
<tr><th></th><th>Assertion</th><th>Detail</th></tr>
{"".join(assertions_rows)}
</table>

{errors_html}

<h3>📋 Unit Timeline</h3>
{timeline_html}

<h3>📡 Event Log</h3>
{event_log}
</div>
</details>"""


def _render_unit_timeline(r: ScenarioResult) -> str:
    """Render a table showing unit states at key ticks."""
    if not r.snapshots:
        return "<p>No data</p>"

    # Sample ticks: 0, every 5, and last
    tick_indices = [0]
    for i in range(5, len(r.snapshots), 5):
        tick_indices.append(i)
    if len(r.snapshots) - 1 not in tick_indices:
        tick_indices.append(len(r.snapshots) - 1)

    # Get unit names from first snapshot
    if not r.snapshots[0].units:
        return "<p>No units</p>"

    unit_names = [u.name for u in r.snapshots[0].units]

    # Build header
    header = "<tr><th>Unit</th><th>Side</th>"
    for ti in tick_indices:
        header += f"<th>T{r.snapshots[ti].tick}</th>"
    header += "</tr>"

    rows = []
    for name in unit_names:
        row = f"<tr><td>{html.escape(name)}</td>"
        # Get side from first snapshot
        first_unit = next((u for u in r.snapshots[0].units if u.name == name), None)
        side = first_unit.side if first_unit else "?"
        side_color = "#4488ff" if side == "blue" else "#ff4444"
        row += f"<td style='color:{side_color}'>{side}</td>"

        for ti in tick_indices:
            snap = r.snapshots[ti]
            u = next((u for u in snap.units if u.name == name), None)
            if u is None:
                row += "<td>—</td>"
            elif u.is_destroyed:
                row += "<td style='color:#ff0000'>💀</td>"
            else:
                # Strength bar
                pct = int(u.strength * 100)
                color = "#4caf50" if pct > 60 else "#ff9800" if pct > 30 else "#f44336"
                task_type = u.current_task.get("type", "—") if u.current_task else "idle"
                row += f'<td><span class="strength-bar" style="width:{max(2, pct//2)}px;background:{color}"></span> {pct}% {task_type}</td>'

        row += "</tr>"
        rows.append(row)

    return f'<div class="timeline"><table>{header}{"".join(rows)}</table></div>'


def _render_event_log(r: ScenarioResult) -> str:
    """Render filtered event log."""
    SIGNIFICANT_TYPES = {
        "combat", "unit_destroyed", "contact_new", "contact_refreshed",
        "morale_break", "artillery_support", "order_issued", "order_completed",
        "ceasefire_friendly", "ceasefire_requested", "ceasefire_cleared",
        "obstacle_damage", "minefield_avoidance", "water_blocked",
        "effect_damage", "effect_dissipated", "conditional_order_activated",
        "game_finished",
    }

    EVENT_CSS = {
        "combat": "evt-combat",
        "unit_destroyed": "evt-destroyed",
        "contact_new": "evt-detection",
        "contact_refreshed": "evt-detection",
        "movement": "evt-movement",
        "morale_break": "evt-morale",
        "artillery_support": "evt-artillery",
    }

    lines = []
    for snap in r.snapshots:
        for e in snap.events:
            etype = e.get("event_type", "")
            if etype not in SIGNIFICANT_TYPES:
                continue
            css = EVENT_CSS.get(etype, "")
            summary = html.escape(e.get("text_summary", "")[:100])
            lines.append(f'<div class="{css}">T{snap.tick:3d} [{etype:25s}] {summary}</div>')

    if not lines:
        return '<div class="event-log"><div>No significant events</div></div>'

    return f'<div class="event-log">{"".join(lines[:500])}</div>'


def _render_issues(results: list[ScenarioResult]) -> str:
    """Aggregate all failures and generate issues section."""
    all_failures = []
    for r in results:
        for a in r.assertions:
            if not a.passed:
                all_failures.append((r.scenario_name, a))
        for err in r.errors:
            all_failures.append((r.scenario_name, err))

    if not all_failures:
        return '<div class="summary" style="border-color:#1b5e20"><h2>✅ All Tests Passed</h2><p>No issues found.</p></div>'

    # Categorize by subsystem
    subsystem_issues: dict[str, list[str]] = {}
    subsystem_keywords = {
        "Movement": ["moved", "reach", "position", "advance", "halt", "water", "minefield"],
        "Detection": ["detect", "contact", "concealment", "recon"],
        "Combat": ["strength", "destroy", "damage", "fire", "artillery", "ceasefire", "combat"],
        "Morale": ["morale", "break", "suppress"],
        "Orders": ["order", "task", "completed"],
    }

    for scenario_name, failure in all_failures:
        if isinstance(failure, str):
            text = failure
            desc = failure
        else:
            text = failure.detail + " " + failure.description
            desc = f"{failure.description}: {failure.detail}"

        categorized = False
        for subsystem, keywords in subsystem_keywords.items():
            if any(kw in text.lower() for kw in keywords):
                subsystem_issues.setdefault(subsystem, []).append(f"[{scenario_name}] {desc}")
                categorized = True
                break
        if not categorized:
            subsystem_issues.setdefault("Other", []).append(f"[{scenario_name}] {desc}")

    sections = []
    for subsystem, issues in subsystem_issues.items():
        items = "".join(f"<li>{html.escape(i[:200])}</li>" for i in issues)
        sections.append(f"<h3>{subsystem}</h3><ul>{items}</ul>")

    return f"""
<div class="issues">
<h2>⚠️ Issues Found ({len(all_failures)} failures)</h2>
{"".join(sections)}
</div>"""

