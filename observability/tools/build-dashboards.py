#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Generate atomic's Grafana dashboards.

The dashboards are written from this one file so that every panel uses the
same datasources, filters and metric names. Edit this file, then run

    python3 -I observability/tools/build-dashboards.py

and commit the JSON it writes to observability/grafana/dashboards/. The JSON is
deterministic: running the script twice gives the same bytes. `--check` exits
non-zero if the committed JSON is out of date.

Metric names follow observability/metrics.md (the OTLP names after Prometheus's
translation: dots become underscores, units and _total are appended).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "grafana" / "dashboards"

# Datasource UIDs, fixed in grafana/provisioning/datasources/datasources.yaml.
PROM = {"type": "prometheus", "uid": "prometheus"}
TEMPO = {"type": "tempo", "uid": "tempo"}
LOKI = {"type": "loki", "uid": "loki"}
PG = {"type": "grafana-postgresql-datasource", "uid": "atomic-postgres"}

# Every atomic series carries the deployment environment (a promoted resource
# attribute); `=~".*"` also matches series without it.
ENV = 'deployment_environment_name=~"$env"'
WF = f'{ENV}, atomic_workflow_name=~"$workflow"'


# ---------------------------------------------------------------------------
# Panel builders. Each returns a dict; layout() assigns ids and grid positions.


def ts(title, targets, unit="short", desc="", w=12, h=8, stack=False, legend="table",
       ds=PROM, min0=True, draw="line"):
    custom = {
        "drawStyle": draw,
        "lineWidth": 1,
        "fillOpacity": 18 if stack or draw == "bars" else 6,
        "showPoints": "never",
        "spanNulls": True,
        "stacking": {"mode": "normal" if stack else "none", "group": "A"},
    }
    defaults = {"unit": unit, "custom": custom}
    if min0:
        defaults["min"] = 0
    return {
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "legend": {"displayMode": legend, "placement": "bottom", "calcs": ["mean", "max"] if legend == "table" else []},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
        "_w": w,
        "_h": h,
    }


def stat(title, expr, unit="short", desc="", w=4, h=4, thresholds=None, decimals=None,
         ds=PROM, raw_sql=None, color_mode="value"):
    steps = thresholds or [{"color": "text", "value": None}]
    defaults = {"unit": unit, "thresholds": {"mode": "absolute", "steps": steps}}
    if decimals is not None:
        defaults["decimals"] = decimals
    if raw_sql is not None:
        targets = [sql(raw_sql, fmt="table")]
    else:
        targets = [prom(expr, instant=True)]
    return {
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": color_mode,
            "graphMode": "none",
            "textMode": "value",
            "justifyMode": "center",
        },
        "_w": w,
        "_h": h,
    }


def table(title, targets, desc="", w=12, h=8, ds=PROM, overrides=None, sort=None, unit=None):
    defaults = {"custom": {"align": "auto", "cellOptions": {"type": "auto"}}}
    if unit:
        defaults["unit"] = unit
    panel = {
        "type": "table",
        "title": title,
        "description": desc,
        "datasource": ds,
        "targets": targets,
        "fieldConfig": {"defaults": defaults, "overrides": overrides or []},
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
        "_w": w,
        "_h": h,
    }
    if sort:
        panel["options"]["sortBy"] = [{"displayName": sort, "desc": True}]
    return panel


def bars(title, targets, unit="short", desc="", w=12, h=8):
    return {
        "type": "bargauge",
        "title": title,
        "description": desc,
        "datasource": PROM,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "min": 0,
                "thresholds": {"mode": "absolute", "steps": [{"color": "blue", "value": None}]},
            },
            "overrides": [],
        },
        "options": {
            "orientation": "horizontal",
            "displayMode": "basic",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "showUnfilled": True,
            "valueMode": "color",
        },
        "_w": w,
        "_h": h,
    }


def logs(title, expr, desc="", w=24, h=9):
    return {
        "type": "logs",
        "title": title,
        "description": desc,
        "datasource": LOKI,
        "targets": [{"refId": "A", "datasource": LOKI, "expr": expr, "queryType": "range"}],
        "options": {
            "showTime": True,
            "wrapLogMessage": True,
            "sortOrder": "Descending",
            "enableLogDetails": True,
            "dedupStrategy": "none",
        },
        "_w": w,
        "_h": h,
    }


def traces(title, traceql, desc="", w=24, h=9, limit=20):
    return {
        "type": "table",
        "title": title,
        "description": desc,
        "datasource": TEMPO,
        "targets": [{
            "refId": "A",
            "datasource": TEMPO,
            "queryType": "traceql",
            "query": traceql,
            "limit": limit,
            "tableType": "traces",
        }],
        "fieldConfig": {"defaults": {"custom": {"align": "auto"}}, "overrides": []},
        "options": {"showHeader": True, "cellHeight": "sm", "footer": {"show": False}},
        "_w": w,
        "_h": h,
    }


def text(content, w=24, h=3, title=""):
    return {"type": "text", "title": title, "options": {"mode": "markdown", "content": content}, "_w": w, "_h": h}


def row(title, collapsed=False):
    return {"type": "row", "title": title, "collapsed": collapsed, "panels": [], "_w": 24, "_h": 1}


def prom(expr, legend="", instant=False, exemplar=False, ref="A", fmt=None):
    t = {
        "refId": ref,
        "datasource": PROM,
        "expr": expr,
        "legendFormat": legend or "__auto",
        "range": not instant,
        "instant": instant,
        "exemplar": exemplar,
    }
    if fmt:
        t["format"] = fmt
    return t


def sql(query, fmt="time_series", ref="A"):
    return {"refId": ref, "datasource": PG, "rawQuery": True, "editorMode": "code", "format": fmt, "rawSql": query.strip()}


def q(phi, metric, by, sel, ref="A", legend=""):
    """A histogram quantile over the dashboard's rate interval."""
    by_le = ", ".join(["le"] + by)
    return prom(
        f"histogram_quantile({phi}, sum by ({by_le}) (rate({metric}_bucket{{{sel}}}[$__rate_interval])))",
        legend=legend, exemplar=True, ref=ref,
    )


def inc(metric, by, sel, window="$__rate_interval", ref="A", legend=""):
    by_s = f" by ({', '.join(by)})" if by else ""
    return prom(f"sum{by_s} (increase({metric}{{{sel}}}[{window}]))", legend=legend, ref=ref)


# ---------------------------------------------------------------------------
# Dashboard assembly.


def layout(panels):
    """Flow panels left to right, wrapping at 24 columns; rows start a line."""
    out, x, y, line_h, pid = [], 0, 0, 0, 1
    for p in panels:
        w, h = p.pop("_w"), p.pop("_h")
        if p["type"] == "row" or x + w > 24:
            y += line_h
            x, line_h = 0, 0
        p["id"] = pid
        p["gridPos"] = {"x": x, "y": y, "w": w, "h": h}
        pid += 1
        out.append(p)
        if p["type"] == "row":
            y += h
            continue
        x += w
        line_h = max(line_h, h)
    return out


def var_env():
    return {
        "name": "env",
        "label": "Environment",
        "type": "query",
        "datasource": PROM,
        "query": {"query": "label_values(deployment_environment_name)", "refId": "env"},
        "definition": "label_values(deployment_environment_name)",
        "includeAll": True,
        "allValue": ".*",
        "multi": True,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "refresh": 2,
        "sort": 1,
    }


def var_workflow():
    return {
        "name": "workflow",
        "label": "Workflow",
        "type": "query",
        "datasource": PROM,
        "query": {"query": f"label_values(atomic_workflow_runs_total{{{ENV}}}, atomic_workflow_name)", "refId": "workflow"},
        "definition": f"label_values(atomic_workflow_runs_total{{{ENV}}}, atomic_workflow_name)",
        "includeAll": True,
        "allValue": ".*",
        "multi": True,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "refresh": 2,
        "sort": 1,
    }


def var_schema():
    return {
        "name": "durable_schema",
        "label": "durare schema",
        "type": "textbox",
        "query": "dbos",
        "current": {"text": "dbos", "value": "dbos"},
        "description": "The Postgres schema holding durare's workflow tables.",
    }


def dashboard(uid, title, desc, panels, variables, time_from="now-6h"):
    return {
        "uid": uid,
        "title": title,
        "description": desc,
        "tags": ["atomic"],
        "editable": True,
        "graphTooltip": 1,
        "refresh": "30s",
        "schemaVersion": 41,
        "time": {"from": time_from, "to": "now"},
        "timepicker": {},
        "timezone": "browser",
        "fiscalYearStartMonth": 0,
        "liveNow": False,
        "weekStart": "",
        "annotations": {"list": [{
            "builtIn": 1,
            "datasource": {"type": "grafana", "uid": "-- Grafana --"},
            "enable": True,
            "hide": True,
            "iconColor": "rgba(0, 211, 255, 1)",
            "name": "Annotations & Alerts",
            "type": "dashboard",
        }]},
        "links": [{
            "title": "atomic dashboards",
            "type": "dashboards",
            "tags": ["atomic"],
            "asDropdown": True,
            "includeVars": True,
            "keepTime": True,
            "icon": "external link",
            "targetBlank": False,
            "tooltip": "",
            "url": "",
        }],
        "templating": {"list": variables},
        "panels": layout(panels),
        "version": 1,
    }


GREEN = {"color": "green", "value": None}


def warn_at(n, color="orange"):
    return [GREEN, {"color": color, "value": n}]


# ---------------------------------------------------------------------------
# The dashboards.


def overview():
    r = "$__range"
    p = [
        text(
            "Runs, people, cost and quality for every atomic workflow in the selected environment. "
            "Each section has its own dashboard under **atomic dashboards** above. "
            "Panels stay empty until atomic exports the metric they read; see `observability/metrics.md`.",
            h=2,
        ),
        stat("Runs started", f'sum(increase(atomic_workflow_runs_total{{{WF}, atomic_run_status="started"}}[{r}]))', decimals=0),
        stat("Completed", f'sum(increase(atomic_workflow_runs_total{{{WF}, atomic_run_status="completed"}}[{r}]))', decimals=0,
             thresholds=[{"color": "green", "value": None}]),
        stat("Failed", f'sum(increase(atomic_workflow_runs_total{{{WF}, atomic_run_status="failed"}}[{r}]))', decimals=0,
             thresholds=warn_at(1, "red")),
        stat("Parked", f'sum(increase(atomic_workflow_runs_total{{{WF}, atomic_run_status="parked"}}[{r}]))', decimals=0,
             desc="Runs stopped for a person: a budget cap, an effect with an unknown outcome, or a stopped turn.",
             thresholds=warn_at(1)),
        stat("Active runs", f"sum(atomic_workflow_active{{{WF}}})", decimals=0,
             desc="Running, or waiting at a gate."),
        stat("Open gates", f"sum(atomic_gate_open{{{WF}}})", decimals=0, thresholds=warn_at(1)),
        stat("Model spend", f"sum(increase(atomic_cost_USD_total{{{WF}}}[{r}]))", unit="currencyUSD", decimals=2,
             desc="Priced model calls only. Models without a price (local models) add nothing."),
        stat("Tokens", f"sum(increase(gen_ai_client_token_usage_sum{{{ENV}}}[{r}]))", unit="short"),
        stat("Step p95", f"histogram_quantile(0.95, sum by (le) (increase(atomic_step_duration_seconds_bucket{{{WF}}}[{r}])))", unit="s"),
        stat("Gate wait p95", f"histogram_quantile(0.95, sum by (le) (increase(atomic_gate_wait_duration_seconds_bucket{{{WF}}}[{r}])))", unit="s"),
        stat("Schema repairs", f"sum(increase(atomic_schema_repairs_total{{{ENV}}}[{r}]))", decimals=0, thresholds=warn_at(1)),
        stat("Recovered runs", f"sum(increase(atomic_runtime_recoveries_total{{{WF}}}[{r}]))", decimals=0,
             desc="Unfinished runs resumed after the runtime restarted. The Durable runs dashboard counts them from the database as well.",
             thresholds=warn_at(1)),
        ts("Runs by outcome", [inc("atomic_workflow_runs_total", ["atomic_run_status"], WF, legend="{{atomic_run_status}}")],
           draw="bars", stack=True, desc="Starts and endings per interval."),
        ts("Step duration p95 by workflow",
           [q(0.95, "atomic_step_duration_seconds", ["atomic_workflow_name"], WF, legend="{{atomic_workflow_name}}")],
           unit="s", desc="Active time of one step attempt; gate waits are not included. Points with a diamond link to a trace."),
        ts("Model spend by tier", [inc("atomic_cost_USD_total", ["atomic_model_tier"], WF, legend="{{atomic_model_tier}}")],
           unit="currencyUSD", stack=True),
        ts("Gate wait p95", [q(0.95, "atomic_gate_wait_duration_seconds", ["atomic_gate_decision"], WF, legend="{{atomic_gate_decision}}")],
           unit="s", desc="From the durable open and decision times, so a restart does not reset the clock."),
        logs("Warnings and errors", '{service_name="atomic", deployment_environment_name=~"$env"} | detected_level=~"warn|error|fatal"',
             desc="atomic's own log records at warning or above."),
    ]
    return dashboard("atomic-overview", "atomic · Overview",
                     "Runs, people, cost and quality for atomic workflows.", p, [var_env(), var_workflow()])


def workflows():
    p = [
        row("Runs"),
        ts("Runs by workflow and outcome",
           [inc("atomic_workflow_runs_total", ["atomic_workflow_name", "atomic_run_status"], WF,
                legend="{{atomic_workflow_name}} · {{atomic_run_status}}")], draw="bars", stack=True),
        ts("Active runs by state", [prom(f"sum by (atomic_run_state) (atomic_workflow_active{{{WF}}})", legend="{{atomic_run_state}}")],
           stack=True, desc="Running, or waiting at a gate."),
        table("Outcomes over the range", [prom(
            f"sum by (atomic_workflow_name, atomic_run_status) (increase(atomic_workflow_runs_total{{{WF}}}[$__range]))",
            instant=True, fmt="table")],
            overrides=[], desc="One row per workflow and outcome.", w=24, h=7),
        row("Steps"),
        ts("Step duration p50 / p95 / p99", [
            q(0.50, "atomic_step_duration_seconds", [], WF, ref="A", legend="p50"),
            q(0.95, "atomic_step_duration_seconds", [], WF, ref="B", legend="p95"),
            q(0.99, "atomic_step_duration_seconds", [], WF, ref="C", legend="p99"),
        ], unit="s", desc="Active time of one step attempt."),
        ts("Step duration p95 by step", [q(0.95, "atomic_step_duration_seconds", ["atomic_step"], WF, legend="{{atomic_step}}")], unit="s"),
        ts("Queue wait p95", [q(0.95, "atomic_step_queue_duration_seconds", ["atomic_workflow_name"], WF, legend="{{atomic_workflow_name}}")],
           unit="s", desc="From a step becoming ready to its attempt starting. Growth means too few runners or a throttled model."),
        ts("Step attempts by outcome", [inc("atomic_step_attempts_total", ["atomic_attempt_outcome"], ENV, legend="{{atomic_attempt_outcome}}")],
           stack=True, draw="bars", desc="A step served from its checkpoint is not an attempt."),
        ts("Attempts by atom", [inc("atomic_step_attempts_total", ["atomic_atom"], ENV, legend="{{atomic_atom}}")], stack=True),
        ts("Checkpoint writes", [inc("atomic_checkpoint_writes_total", ["atomic_workflow_name"], WF, legend="{{atomic_workflow_name}}")], stack=True),
        row("From spans"),
        text("Computed by the collector from every span before sampling, so these work as soon as atomic exports traces, "
             "before its own metrics exist.", h=2),
        ts("Spans by operation", [prom(
            'sum by (gen_ai_operation_name) (rate(traces_span_metrics_calls_total{service_name="atomic", deployment_environment_name=~"$env"}[$__rate_interval]))',
            legend="{{gen_ai_operation_name}}")], unit="ops"),
        ts("Error ratio by operation", [prom(
            'sum by (gen_ai_operation_name) (rate(traces_span_metrics_calls_total{service_name="atomic", deployment_environment_name=~"$env", status_code="STATUS_CODE_ERROR"}[$__rate_interval]))'
            ' / sum by (gen_ai_operation_name) (rate(traces_span_metrics_calls_total{service_name="atomic", deployment_environment_name=~"$env"}[$__rate_interval]))',
            legend="{{gen_ai_operation_name}}")], unit="percentunit"),
        ts("Span duration p95 by span", [prom(
            'histogram_quantile(0.95, sum by (le, span_name) (rate(traces_span_metrics_duration_seconds_bucket{service_name="atomic", deployment_environment_name=~"$env"}[$__rate_interval])))',
            legend="{{span_name}}", exemplar=True)], unit="s", w=24),
        row("Traces"),
        traces("Failed step attempts", '{ resource.service.name = "atomic" && resource.deployment.environment.name =~ "$env" && status = error }',
               desc="Each step attempt is its own trace, linked to its workflow."),
        traces("Recovered runs", '{ resource.service.name = "atomic" && resource.deployment.environment.name =~ "$env" && span.atomic.replay = true }',
               desc="Step attempts that belong to a run recovered after a restart."),
    ]
    return dashboard("atomic-workflows", "atomic · Workflows",
                     "Runs, steps, attempts and checkpoints.", p, [var_env(), var_workflow()])


def models():
    p = [
        row("Tokens and spend"),
        text("Spend follows the **Workflow** filter. Token, call and latency panels do not: "
             "the GenAI metrics carry the model, not the workflow.", h=2),
        ts("Tokens by model and type", [prom(
            f"sum by (gen_ai_request_model, gen_ai_token_type) (increase(gen_ai_client_token_usage_sum{{{ENV}}}[$__rate_interval]))",
            legend="{{gen_ai_request_model}} · {{gen_ai_token_type}}")], stack=True, draw="bars"),
        ts("Spend by provider", [inc("atomic_cost_USD_total", ["gen_ai_provider_name"], WF, legend="{{gen_ai_provider_name}}")],
           unit="currencyUSD", stack=True, desc="Priced model calls. Local models have no price."),
        ts("Spend by workflow", [inc("atomic_cost_USD_total", ["atomic_workflow_name"], WF, legend="{{atomic_workflow_name}}")],
           unit="currencyUSD", stack=True),
        ts("Spend by project", [inc("atomic_cost_USD_total", ["atomic_project"], WF, legend="{{atomic_project}}")],
           unit="currencyUSD", stack=True),
        table("Spend over the range", [prom(
            f"sum by (atomic_workflow_name, atomic_model_tier, gen_ai_provider_name) (increase(atomic_cost_USD_total{{{WF}}}[$__range]))",
            instant=True, fmt="table")], w=24, h=7, unit="currencyUSD"),
        row("Calls"),
        ts("Model calls by model", [prom(
            f"sum by (gen_ai_request_model) (rate(gen_ai_client_operation_duration_seconds_count{{{ENV}}}[$__rate_interval]))",
            legend="{{gen_ai_request_model}}")], unit="reqps"),
        ts("Model call duration p95", [q(0.95, "gen_ai_client_operation_duration_seconds", ["gen_ai_request_model"], ENV,
                                         legend="{{gen_ai_request_model}}")], unit="s"),
        ts("Tokens per call p95", [q(0.95, "gen_ai_client_token_usage", ["gen_ai_request_model", "gen_ai_token_type"], ENV,
                                     legend="{{gen_ai_request_model}} · {{gen_ai_token_type}}")]),
        ts("Agent turns by tier", [inc("codex_turns_total", ["atomic_model_tier"], ENV, legend="{{atomic_model_tier}}")], stack=True),
        ts("Throttled requests", [inc("atomic_model_throttled_total", ["gen_ai_provider_name", "atomic_model_tier"], ENV,
                                      legend="{{gen_ai_provider_name}} · {{atomic_model_tier}}")],
           desc="Requests that waited for a provider quota. They queue rather than fail."),
        ts("Fallbacks", [inc("atomic_model_fallbacks_total", ["atomic_model_tier"], ENV, legend="{{atomic_model_tier}}")],
           desc="Requests served by a fallback tier."),
    ]
    return dashboard("atomic-models", "atomic · Models and cost",
                     "Tokens, spend, model calls, throttling and fallbacks.", p, [var_env(), var_workflow()])


def people():
    p = [
        row("Gates"),
        stat("Open gates", f"sum(atomic_gate_open{{{WF}}})", decimals=0, thresholds=warn_at(1), w=6),
        stat("Decisions", f"sum(increase(atomic_gate_decisions_total{{{ENV}}}[$__range]))", decimals=0, w=6),
        stat("Rejected", f'sum(increase(atomic_gate_decisions_total{{{ENV}, atomic_gate_decision="rejected"}}[$__range]))',
             decimals=0, w=6, thresholds=warn_at(1)),
        stat("Gate wait p50", f"histogram_quantile(0.5, sum by (le) (increase(atomic_gate_wait_duration_seconds_bucket{{{WF}}}[$__range])))",
             unit="s", w=6),
        ts("Open gates by workflow", [prom(f"sum by (atomic_workflow_name) (atomic_gate_open{{{WF}}})", legend="{{atomic_workflow_name}}")],
           stack=True),
        ts("Gate wait p50 / p95", [
            q(0.50, "atomic_gate_wait_duration_seconds", [], WF, ref="A", legend="p50"),
            q(0.95, "atomic_gate_wait_duration_seconds", [], WF, ref="B", legend="p95"),
        ], unit="s", desc="From durable timestamps: a gate keeps waiting while the runtime is down."),
        ts("Decisions by outcome", [inc("atomic_gate_decisions_total", ["atomic_gate_decision"], ENV, legend="{{atomic_gate_decision}}")],
           stack=True, draw="bars"),
        ts("Decisions by channel", [inc("atomic_gate_decisions_total", ["atomic_gate_channel"], ENV, legend="{{atomic_gate_channel}}")],
           stack=True, draw="bars", desc="Where the decision was made: terminal, HTTP, chat or an AI reviewer."),
        row("In-turn approvals and steering"),
        ts("Approval wait p95", [q(0.95, "atomic_approval_wait_duration_seconds", ["atomic_approval_decision"], ENV,
                                   legend="{{atomic_approval_decision}}")], unit="s",
           desc="How long a command the sandbox would not allow waited for a person's answer."),
        ts("Steering", [inc("atomic_steering_events_total", ["atomic_steering_kind"], ENV, legend="{{atomic_steering_kind}}")],
           stack=True, draw="bars", desc="A person steered or stopped a live agent turn."),
    ]
    return dashboard("atomic-people", "atomic · People and gates",
                     "Gates, decisions, approvals and steering: waiting is measured as well as working.",
                     p, [var_env(), var_workflow()])


def quality():
    p = [
        row("Contracts"),
        ts("Schema repairs by atom", [inc("atomic_schema_repairs_total", ["atomic_atom"], ENV, legend="{{atomic_atom}}")],
           stack=True, draw="bars", desc="Model calls repeated because an output broke its schema."),
        ts("Validation failures by atom", [inc("atomic_validation_failures_total", ["atomic_atom"], ENV, legend="{{atomic_atom}}")],
           stack=True, draw="bars", desc="Outputs rejected by a schema or a validate rule."),
        ts("Repairs per attempt", [prom(
            f"sum by (atomic_atom) (increase(atomic_schema_repairs_total{{{ENV}}}[$__rate_interval]))"
            f" / sum by (atomic_atom) (increase(atomic_step_attempts_total{{{ENV}}}[$__rate_interval]))",
            legend="{{atomic_atom}}")], unit="percentunit", w=24,
           desc="A rising line after a model or prompt change is the first sign of a regression."),
        row("Evals"),
        bars("Eval pass rate by atom", [prom(f"max by (atomic_atom) (atomic_eval_pass_rate_ratio{{{ENV}}})", legend="{{atomic_atom}}",
                                             instant=True)],
             unit="percentunit", desc="At each atom's last evaluation."),
        ts("Eval pass rate over time", [prom(f"max by (atomic_atom) (atomic_eval_pass_rate_ratio{{{ENV}}})", legend="{{atomic_atom}}")],
           unit="percentunit", min0=True),
        row("Tools"),
        ts("Tool calls by tool", [prom(f"sum by (gen_ai_tool_name) (rate(atomic_tool_duration_seconds_count{{{ENV}}}[$__rate_interval]))",
                                       legend="{{gen_ai_tool_name}}")], unit="ops", stack=True),
        ts("Tool errors", [inc("atomic_tool_errors_total", ["gen_ai_tool_name", "error_type"], ENV,
                               legend="{{gen_ai_tool_name}} · {{error_type}}")], draw="bars", stack=True),
        ts("Tool duration p95", [q(0.95, "atomic_tool_duration_seconds", ["gen_ai_tool_name"], ENV, legend="{{gen_ai_tool_name}}")],
           unit="s", w=24),
    ]
    return dashboard("atomic-quality", "atomic · Quality and tools",
                     "Schema repairs, validation failures, eval results and tool calls.", p, [var_env()])


def runtime():
    pg = 'postgresql_database_name=~"$database"'
    p = [
        row("Runtime"),
        stat("Instances", f"count(process_uptime_seconds{{{ENV}, service_name=\"atomic\"}})", decimals=0, w=6),
        stat("Runner utilization", f"avg(atomic_runner_utilization_ratio{{{ENV}}})", unit="percentunit", w=6,
             thresholds=[GREEN, {"color": "orange", "value": 0.8}, {"color": "red", "value": 0.95}]),
        stat("Recovered runs", f"sum(increase(atomic_runtime_recoveries_total{{{ENV}}}[$__range]))", decimals=0, w=6,
             thresholds=warn_at(1)),
        stat("Uptime", f"min(process_uptime_seconds{{{ENV}, service_name=\"atomic\"}})", unit="s", w=6),
        ts("Runner utilization by backend", [prom(f"avg by (atomic_runner_backend) (atomic_runner_utilization_ratio{{{ENV}}})",
                                                  legend="{{atomic_runner_backend}}")], unit="percentunit"),
        ts("Recovered runs by workflow", [inc("atomic_runtime_recoveries_total", ["atomic_workflow_name"], ENV,
                                              legend="{{atomic_workflow_name}}")], draw="bars", stack=True,
           desc="Unfinished runs resumed after a restart. Completed steps come back from checkpoints."),
        ts("Memory by instance", [prom(f"sum by (service_instance_id, process_memory_type) (process_memory_usage_bytes{{{ENV}, service_name=\"atomic\"}})",
                                       legend="{{service_instance_id}} · {{process_memory_type}}")], unit="bytes"),
        ts("Checkpoint writes", [inc("atomic_checkpoint_writes_total", [], ENV, legend="checkpoints")]),
        row("Work graph"),
        ts("Items by state", [prom(f"sum by (atomic_graph_state) (atomic_graph_items{{{ENV}}})", legend="{{atomic_graph_state}}")],
           w=8),
        ts("Claim contention", [inc("atomic_graph_claim_contention_total", ["atomic_project"], ENV, legend="{{atomic_project}}")],
           w=8, desc="Claims lost to another worker. Some is normal; a lot means workers are polling the same items."),
        ts("Message cursor lag", [prom(f"max by (atomic_project) (atomic_graph_message_cursor_lag{{{ENV}}})", legend="{{atomic_project}}")],
           w=8, desc="Messages not yet read by the slowest reader."),
        row("Postgres"),
        ts("Connections", [prom(f"sum by (postgresql_database_name) (postgresql_backends{{{pg}}})", legend="{{postgresql_database_name}}")],
           w=8),
        ts("Commits and rollbacks", [
            prom(f"sum(rate(postgresql_commits_total{{{pg}}}[$__rate_interval]))", legend="commits", ref="A"),
            prom(f"sum(rate(postgresql_rollbacks_total{{{pg}}}[$__rate_interval]))", legend="rollbacks", ref="B"),
        ], unit="ops", w=8, desc="Rollbacks include checkpoints refused to a stale owner."),
        ts("Database size", [prom(f"sum by (postgresql_database_name) (postgresql_db_size_bytes{{{pg}}})",
                                  legend="{{postgresql_database_name}}")], unit="bytes", w=8),
        ts("Deadlocks", [prom(f"sum(increase(postgresql_deadlocks_total{{{pg}}}[$__rate_interval]))", legend="deadlocks")], w=12),
        ts("Rows by table", [prom(
            f'topk(10, sum by (postgresql_table_name) (postgresql_rows{{{pg}, state="live"}}))', legend="{{postgresql_table_name}}")],
           w=12, desc="durare's workflow_status and operation_outputs grow with every run until garbage collection."),
        row("Logs"),
        logs("atomic logs", '{service_name="atomic", deployment_environment_name=~"$env"}'),
    ]
    variables = [var_env(), {
        "name": "database",
        "label": "Database",
        "type": "query",
        "datasource": PROM,
        "query": {"query": "label_values(postgresql_backends, postgresql_database_name)", "refId": "database"},
        "definition": "label_values(postgresql_backends, postgresql_database_name)",
        "includeAll": True,
        "allValue": ".*",
        "multi": True,
        "current": {"selected": True, "text": ["All"], "value": ["$__all"]},
        "refresh": 2,
        "sort": 1,
    }]
    return dashboard("atomic-runtime", "atomic · Runtime",
                     "Runners, recovery, the work graph, the database and the process.", p, variables)


def durable():
    t = '"${durable_schema:raw}".workflow_status'
    o = '"${durable_schema:raw}".operation_outputs'
    rng = "created_at BETWEEN $__unixEpochFrom()::bigint * 1000 AND $__unixEpochTo()::bigint * 1000"
    p = [
        text(
            "Read straight from durare's tables in Postgres, the record atomic recovers from. "
            "This works without any telemetry export. Statuses: PENDING (running, waiting or interrupted), "
            "ENQUEUED, DELAYED (scheduled for later), SUCCESS, ERROR, CANCELLED and MAX_RECOVERY_ATTEMPTS_EXCEEDED. "
            "Until atomic has created these tables in this database, the panels report that the relation does not exist.",
            h=2,
        ),
        stat("Pending", None, ds=PG, decimals=0, w=3,
             raw_sql=f"SELECT count(*) AS pending FROM {t} WHERE status = 'PENDING'",
             desc="Runs not finished: running now, waiting at a gate, or interrupted and awaiting recovery."),
        stat("Queued", None, ds=PG, decimals=0, w=3,
             raw_sql=f"SELECT count(*) AS queued FROM {t} WHERE status IN ('ENQUEUED', 'DELAYED')",
             desc="Waiting in a queue, or delayed until a set time."),
        stat("Succeeded", None, ds=PG, decimals=0, w=3, thresholds=[GREEN],
             raw_sql=f"SELECT count(*) AS succeeded FROM {t} WHERE status = 'SUCCESS' AND {rng}"),
        stat("Errored", None, ds=PG, decimals=0, w=3, thresholds=warn_at(1, "red"),
             raw_sql=f"SELECT count(*) AS errored FROM {t} WHERE status = 'ERROR' AND {rng}"),
        stat("Recovered", None, ds=PG, decimals=0, w=3, thresholds=warn_at(1),
             raw_sql=f"SELECT count(*) AS recovered FROM {t} WHERE recovery_attempts > 0 AND {rng}",
             desc="Runs in the range that were recovered at least once after their process stopped."),
        stat("Cancelled", None, ds=PG, decimals=0, w=3,
             raw_sql=f"SELECT count(*) AS cancelled FROM {t} WHERE status = 'CANCELLED' AND {rng}"),
        stat("Recovery limit hit", None, ds=PG, decimals=0, w=3, thresholds=warn_at(1, "red"),
             raw_sql=f"SELECT count(*) AS exceeded FROM {t} WHERE status = 'MAX_RECOVERY_ATTEMPTS_EXCEEDED'",
             desc="Runs durare stopped recovering. Each needs a person."),
        stat("Executors", None, ds=PG, decimals=0, w=3,
             raw_sql=f"SELECT count(DISTINCT executor_id) AS executors FROM {t} WHERE status = 'PENDING'",
             desc="Distinct executor IDs owning unfinished runs. Every runtime process needs its own stable ID."),
        ts("Runs created by status", [sql(
            f"SELECT $__unixEpochGroupAlias(created_at / 1000, $__interval, 0), status AS metric, count(*) AS value "
            f"FROM {t} WHERE {rng} GROUP BY 1, 2 ORDER BY 1")], ds=PG, draw="bars", stack=True),
        ts("Run duration p95 by workflow", [sql(
            f"SELECT $__unixEpochGroupAlias(completed_at / 1000, $__interval, 0), name AS metric, "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY (completed_at - created_at) / 1000.0) AS value "
            f"FROM {t} WHERE completed_at BETWEEN $__unixEpochFrom()::bigint * 1000 AND $__unixEpochTo()::bigint * 1000 "
            f"GROUP BY 1, 2 ORDER BY 1")], ds=PG, unit="s",
           desc="From creation to a terminal state, gate waits included."),
        ts("Steps completed by name", [sql(
            f"SELECT $__unixEpochGroupAlias(completed_at_epoch_ms / 1000, $__interval, 0), function_name AS metric, count(*) AS value "
            f"FROM {o} WHERE completed_at_epoch_ms BETWEEN $__unixEpochFrom()::bigint * 1000 AND $__unixEpochTo()::bigint * 1000 "
            f"GROUP BY 1, 2 ORDER BY 1")], ds=PG, draw="bars", stack=True,
           desc="Checkpointed step results. A step served from its checkpoint on recovery is not written again."),
        ts("Step duration p95 by name", [sql(
            f"SELECT $__unixEpochGroupAlias(completed_at_epoch_ms / 1000, $__interval, 0), function_name AS metric, "
            f"percentile_cont(0.95) WITHIN GROUP (ORDER BY (completed_at_epoch_ms - started_at_epoch_ms) / 1000.0) AS value "
            f"FROM {o} WHERE completed_at_epoch_ms BETWEEN $__unixEpochFrom()::bigint * 1000 AND $__unixEpochTo()::bigint * 1000 "
            f"AND started_at_epoch_ms IS NOT NULL GROUP BY 1, 2 ORDER BY 1")], ds=PG, unit="s"),
        table("Unfinished runs, oldest first", [sql(
            f"SELECT workflow_uuid AS run, name AS workflow, status, executor_id AS executor, queue_name AS queue, "
            f"recovery_attempts AS recoveries, to_timestamp(created_at / 1000.0) AS created, "
            f"now() - to_timestamp(updated_at / 1000.0) AS idle_for "
            f"FROM {t} WHERE status IN ('PENDING', 'ENQUEUED', 'DELAYED') ORDER BY created_at LIMIT 100", fmt="table")],
            ds=PG, w=24, h=9,
            desc="Run IDs belong in tables like this one, never in metric labels."),
        table("Unfinished runs by executor", [sql(
            f"SELECT executor_id AS executor, status, count(*) AS runs, max(recovery_attempts) AS max_recoveries "
            f"FROM {t} WHERE status IN ('PENDING', 'ENQUEUED', 'DELAYED') GROUP BY 1, 2 ORDER BY 3 DESC", fmt="table")],
            ds=PG, w=12, h=8,
            desc="Runs owned by an executor that no longer exists wait here until something recovers them."),
        table("Recent failures", [sql(
            f"SELECT workflow_uuid AS run, name AS workflow, status, executor_id AS executor, recovery_attempts AS recoveries, "
            f"to_timestamp(updated_at / 1000.0) AS at FROM {t} "
            f"WHERE status IN ('ERROR', 'MAX_RECOVERY_ATTEMPTS_EXCEEDED', 'CANCELLED') AND {rng} "
            f"ORDER BY updated_at DESC LIMIT 50", fmt="table")],
            ds=PG, w=12, h=8,
            desc="Error text is left out on purpose: it can quote inputs. Read it with atomic's own run inspection."),
        table("Queues", [sql(
            f"SELECT coalesce(queue_name, '(none)') AS queue, status, count(*) AS runs FROM {t} "
            f"WHERE status IN ('PENDING', 'ENQUEUED', 'DELAYED') GROUP BY 1, 2 ORDER BY 1, 2", fmt="table")],
            ds=PG, w=24, h=6),
    ]
    return dashboard("atomic-durable-runs", "atomic · Durable runs",
                     "Runs and steps read from durare's tables in Postgres: the record atomic recovers from.",
                     p, [var_schema()], time_from="now-24h")


def pipeline():
    p = [
        text("Is telemetry from atomic arriving, and is any of it being dropped? Read this first when another dashboard is empty.", h=2),
        ts("Received by signal", [
            prom('sum(rate(otelcol_receiver_accepted_spans[$__rate_interval]))', legend="spans", ref="A"),
            prom('sum(rate(otelcol_receiver_accepted_metric_points[$__rate_interval]))', legend="metric points", ref="B"),
            prom('sum(rate(otelcol_receiver_accepted_log_records[$__rate_interval]))', legend="log records", ref="C"),
        ], unit="ops"),
        ts("Refused by the collector", [
            prom('sum(rate(otelcol_receiver_refused_spans[$__rate_interval]))', legend="spans", ref="A"),
            prom('sum(rate(otelcol_receiver_refused_metric_points[$__rate_interval]))', legend="metric points", ref="B"),
            prom('sum(rate(otelcol_receiver_refused_log_records[$__rate_interval]))', legend="log records", ref="C"),
        ], unit="ops", desc="Usually the memory limiter pushing back."),
        ts("Sent by exporter", [
            prom('sum by (exporter) (rate(otelcol_exporter_sent_spans[$__rate_interval]))', legend="{{exporter}} spans", ref="A"),
            prom('sum by (exporter) (rate(otelcol_exporter_sent_metric_points[$__rate_interval]))', legend="{{exporter}} points", ref="B"),
            prom('sum by (exporter) (rate(otelcol_exporter_sent_log_records[$__rate_interval]))', legend="{{exporter}} logs", ref="C"),
        ], unit="ops"),
        ts("Export failures", [
            prom('sum by (exporter) (rate(otelcol_exporter_send_failed_spans[$__rate_interval]))', legend="{{exporter}} spans", ref="A"),
            prom('sum by (exporter) (rate(otelcol_exporter_send_failed_metric_points[$__rate_interval]))', legend="{{exporter}} points", ref="B"),
            prom('sum by (exporter) (rate(otelcol_exporter_send_failed_log_records[$__rate_interval]))', legend="{{exporter}} logs", ref="C"),
        ], unit="ops", desc="A backend that is down or refusing data."),
        ts("Export queue", [prom('sum by (exporter) (otelcol_exporter_queue_size)', legend="{{exporter}}")]),
        ts("Traces kept by sampling", [
            prom('sum(rate(otelcol_exporter_sent_spans{exporter="otlp/tempo"}[$__rate_interval]))'
                 ' / sum(rate(otelcol_receiver_accepted_spans[$__rate_interval]))', legend="kept"),
        ], unit="percentunit", desc="Share of received spans stored in Tempo after tail sampling."),
        ts("Collector memory", [prom('sum by (instance) (otelcol_process_memory_rss)', legend="{{instance}}")], unit="bytes"),
        ts("Backends up", [prom('up', legend="{{job}}")], min0=True, desc="Every target this Prometheus scrapes."),
    ]
    return dashboard("atomic-telemetry-pipeline", "atomic · Telemetry pipeline",
                     "The collector and the backends behind atomic's dashboards.", p, [], time_from="now-1h")


DASHBOARDS = [overview, workflows, models, people, quality, runtime, durable, pipeline]


def render(fn):
    d = fn()
    return d["uid"] + ".json", json.dumps(d, indent=2, sort_keys=False) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="fail if the committed dashboards are out of date")
    args = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    expected = set()
    for fn in DASHBOARDS:
        name, body = render(fn)
        expected.add(name)
        path = OUT / name
        if args.check:
            if not path.exists() or path.read_text() != body:
                stale.append(name)
        else:
            path.write_text(body)
    extra = sorted(p.name for p in OUT.glob("*.json") if p.name not in expected)
    if args.check:
        if stale or extra:
            for n in stale:
                print(f"out of date: {n}", file=sys.stderr)
            for n in extra:
                print(f"not generated by this script: {n}", file=sys.stderr)
            print("run: python3 -I observability/tools/build-dashboards.py", file=sys.stderr)
            return 1
        return 0
    for n in extra:
        print(f"warning: {OUT / n} is not generated by this script", file=sys.stderr)
    print(f"wrote {len(expected)} dashboards to {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
