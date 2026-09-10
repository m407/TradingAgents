"""Running one analysis from the CLI: build the graph, stream it into the live view, save the report."""

import datetime
import os
import sys
import time
from functools import wraps
from pathlib import Path

import typer
from rich.live import Live

from cli.display import (
    ANALYST_ORDER,
    AnalystWallTimeTracker,
    classify_message_type,
    console,
    create_layout,
    display_complete_report,
    message_buffer,
    update_analyst_statuses,
    update_display,
    update_research_team_status,
)
from cli.prompts import ensure_api_key
from cli.selections import get_user_selections
from cli.stats_handler import StatsCallbackHandler
from tradingagents.agents.rating import is_review
from tradingagents.dataflows.symbols import safe_ticker_component
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.graph.analyst_execution import (
    build_analyst_execution_plan,
)
from tradingagents.graph.trading_graph import TradingAgentsGraph
from tradingagents.reporting import write_report_tree


def save_report_to_disk(final_state, ticker: str, save_path: Path):
    """Save the complete analysis report to disk (shared CLI/API writer)."""
    return write_report_tree(final_state, ticker, save_path)


def _run_directory(config: dict, ticker: str, trade_date: str) -> Path:
    """Where this run writes, with the ticker validated as a path component.

    Every other path that interpolates a ticker checks it first; a value of
    ".." here would place the run outside the results directory.
    """
    return Path(config["results_dir"]) / safe_ticker_component(ticker) / trade_date


def _announce_checkpoint_state(graph, ticker: str, trade_date: str) -> None:
    """Say whether this run resumed a saved one, where the user can see it.

    The graph logs this, but nothing in the CLI configures logging and the live
    view owns the screen, so a resume was invisible.
    """
    _m = sys.modules.get("cli.main")
    buf = getattr(_m, "message_buffer", message_buffer) if _m else message_buffer
    if getattr(graph, "_resuming", False):
        buf.add_message(
            "System", f"Resuming the saved run for {ticker} on {trade_date}"
        )
    else:
        buf.add_message("System", f"Starting fresh for {ticker} on {trade_date}")


def _build_run_config(selections: dict, checkpoint: bool | None) -> dict:
    """Assemble the run config from interactive selections, honoring env precedence.

    Round counts and checkpoint follow "explicit env/flag wins": an env-applied
    value on DEFAULT_CONFIG is preserved unless the user overrode it on the CLI.
    """
    config = DEFAULT_CONFIG.copy()
    # Research depth sets both round counts, but an explicit env override
    # (TRADINGAGENTS_MAX_DEBATE_ROUNDS / _MAX_RISK_ROUNDS) wins over the
    # interactive selection — leave the env-applied value in place (#977).
    for env_var, key in (("TRADINGAGENTS_MAX_DEBATE_ROUNDS", "max_debate_rounds"),
                         ("TRADINGAGENTS_MAX_RISK_ROUNDS", "max_risk_discuss_rounds")):
        if os.environ.get(env_var):
            # The depth prompt still appeared (it is skipped only when both are
            # set), so say which half of the answer the environment overrode.
            console.print(
                f"[green]✓ {key} from environment:[/green] {config[key]} "
                f"(set by {env_var}, so the research depth you chose does not apply to it)"
            )
        else:
            config[key] = selections["research_depth"]
    config["quick_think_llm"] = selections.get("quick_think_llm") or selections.get("shallow_thinker")
    config["deep_think_llm"] = selections.get("deep_think_llm") or selections.get("deep_thinker")
    config["backend_url"] = selections["backend_url"]
    config["llm_provider"] = selections["llm_provider"].lower()
    # Provider-specific thinking configuration
    for key in ("google_thinking_level", "openai_reasoning_effort", "anthropic_effort"):
        if selections.get(key):
            config[key] = selections[key]
    config["output_language"] = selections.get("output_language", "English")
    # --checkpoint/--no-checkpoint overrides only when explicitly given; omitting
    # the flag preserves TRADINGAGENTS_CHECKPOINT_ENABLED / the default (#976).
    if checkpoint is not None:
        config["checkpoint_enabled"] = checkpoint
    return config


def run_analysis(checkpoint: bool | None = None, portfolio=None, *, ticker=None,
                 analysis_date=None, analysts=None, non_interactive=False):
    _m = sys.modules.get("cli.main")
    # First get user selections
    selection_options = {}
    for key, value in (("ticker", ticker), ("analysis_date", analysis_date), ("analysts", analysts)):
        if value is not None:
            selection_options[key] = value
    if non_interactive:
        selection_options["non_interactive"] = True
    get_selections = getattr(_m, "get_user_selections", get_user_selections) if _m else get_user_selections
    selections = get_selections(**selection_options)

    if non_interactive:
        # No interactive depth/model/URL defaults may overwrite the env overlay.
        _cfg = getattr(_m, "DEFAULT_CONFIG", DEFAULT_CONFIG) if _m else DEFAULT_CONFIG
        config = _cfg.copy()
        if checkpoint is not None:
            config["checkpoint_enabled"] = checkpoint
        providers = []
        for tier in ("deep", "quick"):
            provider = (config.get(f"{tier}_think_llm_provider") or config["llm_provider"]).lower()
            url = config.get(f"{tier}_think_llm_backend_url") or config.get("backend_url")
            if provider == "openai_compatible" and not url:
                raise ValueError(f"Provider 'openai_compatible' requires a base_url for {tier} thinking")
            providers.append(provider)
        for provider in dict.fromkeys(providers):
            ensure_api_key(provider, interactive=False)
    else:
        config = _build_run_config(selections, checkpoint)

    stats_handler = StatsCallbackHandler()

    # Normalize analyst selection to predefined order (selection is a 'set', order is fixed)
    selected_set = {analyst.value for analyst in selections["analysts"]}
    selected_analyst_keys = [a for a in ANALYST_ORDER if a in selected_set]
    analyst_execution_plan = build_analyst_execution_plan(selected_analyst_keys)
    analyst_wall_time_tracker = AnalystWallTimeTracker(analyst_execution_plan)

    graph_cls = TradingAgentsGraph
    if _m and getattr(_m, "TradingAgentsGraph", None) is not None:
        graph_cls = getattr(_m, "TradingAgentsGraph")
    graph = graph_cls(
        selected_analyst_keys,
        config=config,
        debug=True,
        callbacks=[stats_handler],
    )

    buf = getattr(_m, "message_buffer", message_buffer) if _m else message_buffer
    buf.init_for_analysis(selected_analyst_keys)

    # Track start time for elapsed display
    start_time = time.time()

    results_dir = _run_directory(config, selections["ticker"], selections["analysis_date"])
    results_dir.mkdir(parents=True, exist_ok=True)
    report_dir = results_dir / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    log_file = results_dir / "message_tool.log"
    log_file.touch(exist_ok=True)

    def save_message_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, message_type, content = obj.messages[-1]
            content = content.replace("\n", " ")  # Replace newlines with spaces
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [{message_type}] {content}\n")
        return wrapper

    def save_tool_call_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(*args, **kwargs):
            func(*args, **kwargs)
            timestamp, tool_name, args = obj.tool_calls[-1]
            args_str = ", ".join(f"{k}={v}" for k, v in args.items())
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"{timestamp} [Tool Call] {tool_name}({args_str})\n")
        return wrapper

    def save_report_section_decorator(obj, func_name):
        func = getattr(obj, func_name)

        @wraps(func)
        def wrapper(section_name, content):
            func(section_name, content)
            if section_name in obj.report_sections and obj.report_sections[section_name] is not None:
                content = obj.report_sections[section_name]
                if content:
                    file_name = f"{section_name}.md"
                    text = "\n".join(str(item) for item in content) if isinstance(content, list) else content
                    with open(report_dir / file_name, "w", encoding="utf-8") as f:
                        f.write(text)
        return wrapper

    buf.add_message = save_message_decorator(buf, "add_message")
    buf.add_tool_call = save_tool_call_decorator(buf, "add_tool_call")
    buf.update_report_section = save_report_section_decorator(buf, "update_report_section")

    layout = create_layout()

    # The alternate screen keeps a layout taller than the window from redrawing
    # by scrolling; the final report prints after this block, on the normal screen.
    with Live(layout, refresh_per_second=4, screen=True):
        # Initial display
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        buf.add_message("System", f"Selected ticker: {selections['ticker']}")
        if selections["asset_type"] != "stock":
            buf.add_message("System", f"Detected asset type: {selections['asset_type']}")
        buf.add_message(
            "System", f"Analysis date: {selections['analysis_date']}"
        )
        buf.add_message(
            "System",
            f"Selected analysts: {', '.join(analyst.value for analyst in selections['analysts'])}",
        )
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        first_analyst = analyst_execution_plan.specs[0].agent_node
        buf.update_agent_status(first_analyst, "in_progress")
        analyst_wall_time_tracker.mark_started(selected_analyst_keys[0])
        update_display(layout, stats_handler=stats_handler, start_time=start_time)

        spinner_text = (
            f"Analyzing {selections['ticker']} on {selections['analysis_date']}..."
        )
        update_display(layout, spinner_text, stats_handler=stats_handler, start_time=start_time)

        # The same initial state propagate() builds: settled decision log, past
        # context and resolved instrument identity.
        if portfolio is not None:
            init_agent_state = graph.create_run_state(
                selections["ticker"], selections["analysis_date"], selections["asset_type"], portfolio
            )
        else:
            init_agent_state = graph.create_run_state(
                selections["ticker"], selections["analysis_date"], selections["asset_type"]
            )
        # Pass callbacks to graph config for tool execution tracking
        # (LLM tracking is handled separately via LLM constructor)
        args = graph.propagator.get_graph_args(callbacks=[stats_handler])

        # Recompile with a checkpointer and inject the thread_id so --checkpoint
        # actually saves and resumes on the CLI path (#1249); a no-op when
        # checkpointing is disabled. Torn down in the finally below.
        if portfolio is not None:
            checkpoint_tid = graph.begin_checkpoint(
                selections["ticker"], selections["analysis_date"], selections["asset_type"], portfolio
            )
        else:
            checkpoint_tid = graph.begin_checkpoint(
                selections["ticker"], selections["analysis_date"], selections["asset_type"]
            )
        if checkpoint_tid is not None:
            args.setdefault("config", {}).setdefault("configurable", {})["thread_id"] = checkpoint_tid
            _announce_checkpoint_state(graph, selections["ticker"], selections["analysis_date"])

        # Stream the analysis. On resume, feed None so LangGraph continues the
        # interrupted run instead of re-appending the initial state (#1249); the
        # try/finally tears the checkpointer down even if the stream raises.
        trace = []
        try:
            for chunk in graph.graph.stream(graph.checkpoint_input(init_agent_state), **args):
                for message in chunk.get("messages", []):
                    msg_id = getattr(message, "id", None)
                    if msg_id is not None:
                        if msg_id in buf._processed_message_ids:
                            continue
                        buf._processed_message_ids.add(msg_id)

                    msg_type, content = classify_message_type(message)
                    if content and content.strip():
                        buf.add_message(msg_type, content)

                    if hasattr(message, "tool_calls") and message.tool_calls:
                        for tool_call in message.tool_calls:
                            if isinstance(tool_call, dict):
                                buf.add_tool_call(tool_call["name"], tool_call["args"])
                            else:
                                buf.add_tool_call(tool_call.name, tool_call.args)

                update_analyst_statuses(
                    buf,
                    chunk,
                    wall_time_tracker=analyst_wall_time_tracker,
                )

                # Research Team - Handle Investment Debate State
                if chunk.get("investment_debate_state"):
                    debate_state = chunk["investment_debate_state"]
                    bull_hist = debate_state.get("bull_history", "").strip()
                    bear_hist = debate_state.get("bear_history", "").strip()
                    judge = debate_state.get("judge_decision", "").strip()

                    # Only update status when there's actual content
                    if bull_hist or bear_hist:
                        update_research_team_status("in_progress")
                    if bull_hist:
                        buf.update_report_section(
                            "investment_plan", f"### Bull Researcher Analysis\n{bull_hist}"
                        )
                    if bear_hist:
                        buf.update_report_section(
                            "investment_plan", f"### Bear Researcher Analysis\n{bear_hist}"
                        )
                    if judge:
                        buf.update_report_section(
                            "investment_plan", f"### Research Manager Decision\n{judge}"
                        )
                        update_research_team_status("completed")
                        buf.update_agent_status("Trader", "in_progress")

                # Trading Team
                if chunk.get("trader_investment_plan"):
                    buf.update_report_section(
                        "trader_investment_plan", chunk["trader_investment_plan"]
                    )
                    if buf.agent_status.get("Trader") != "completed":
                        buf.update_agent_status("Trader", "completed")
                        buf.update_agent_status("Aggressive Analyst", "in_progress")

                # Risk Management Team - Handle Risk Debate State
                if chunk.get("risk_debate_state"):
                    risk_state = chunk["risk_debate_state"]
                    agg_hist = risk_state.get("aggressive_history", "").strip()
                    con_hist = risk_state.get("conservative_history", "").strip()
                    neu_hist = risk_state.get("neutral_history", "").strip()
                    judge = risk_state.get("judge_decision", "").strip()

                    if agg_hist:
                        if buf.agent_status.get("Aggressive Analyst") != "completed":
                            buf.update_agent_status("Aggressive Analyst", "in_progress")
                        buf.update_report_section(
                            "final_trade_decision", f"### Aggressive Analyst Analysis\n{agg_hist}"
                        )
                    if con_hist:
                        if buf.agent_status.get("Conservative Analyst") != "completed":
                            buf.update_agent_status("Conservative Analyst", "in_progress")
                        buf.update_report_section(
                            "final_trade_decision", f"### Conservative Analyst Analysis\n{con_hist}"
                        )
                    if neu_hist:
                        if buf.agent_status.get("Neutral Analyst") != "completed":
                            buf.update_agent_status("Neutral Analyst", "in_progress")
                        buf.update_report_section(
                            "final_trade_decision", f"### Neutral Analyst Analysis\n{neu_hist}"
                        )
                    if judge and buf.agent_status.get("Portfolio Manager") != "completed":
                        buf.update_agent_status("Portfolio Manager", "in_progress")
                        buf.update_report_section(
                            "final_trade_decision", f"### Portfolio Manager Decision\n{judge}"
                        )
                        buf.update_agent_status("Aggressive Analyst", "completed")
                        buf.update_agent_status("Conservative Analyst", "completed")
                        buf.update_agent_status("Neutral Analyst", "completed")
                        buf.update_agent_status("Portfolio Manager", "completed")

                update_display(layout, stats_handler=stats_handler, start_time=start_time)

                trace.append(chunk)

            # Streamed chunks are per-node deltas, not full state. Merge them
            # so every report field populated across the run is present.
            final_state = {}
            for chunk in trace:
                final_state.update(chunk)

            # Clean run: log the decision, then drop this run's checkpoint so a
            # later run starts fresh. A mid-stream failure skips both, keeping
            # the checkpoint for resume.
            graph.record_decision(selections["ticker"], selections["analysis_date"], final_state)
            if portfolio is not None:
                graph.clear_checkpoint_on_success(
                    selections["ticker"], selections["analysis_date"], selections["asset_type"], portfolio
                )
            else:
                graph.clear_checkpoint_on_success(
                    selections["ticker"], selections["analysis_date"], selections["asset_type"]
                )
        finally:
            # Always restore the plain uncheckpointed graph, even on failure.
            graph.end_checkpoint()

        for agent in buf.agent_status:
            buf.update_agent_status(agent, "completed")

        buf.add_message(
            "System", f"Completed analysis for {selections['analysis_date']}"
        )
        buf.add_message("System", analyst_wall_time_tracker.format_summary())

        for section in buf.report_sections:
            if section in final_state:
                buf.update_report_section(section, final_state[section])

        update_display(layout, stats_handler=stats_handler, start_time=start_time)

    # Post-analysis prompts (outside Live context for clean interaction)
    console.print("\n[bold cyan]Analysis Complete![/bold cyan]\n")

    # A decision nobody can read is not a position. Say so here rather than
    # leaving the run to look like a normal result.
    if is_review(graph.process_signal(final_state.get("final_trade_decision", ""))):
        console.print(
            "[yellow]No rating could be read from the final decision, so this run "
            "is recorded for review rather than as a position. Re-run, or read the "
            "decision text below and judge it yourself.[/yellow]\n"
        )
    console.print(f"[dim]{analyst_wall_time_tracker.format_summary()}[/dim]")

    if non_interactive:
        save_report = (getattr(_m, "save_report_to_disk", None) or save_report_to_disk) if _m else save_report_to_disk
        try:
            report_file = save_report(final_state, selections["ticker"], report_dir)
        except Exception as exc:
            typer.echo(f"Error saving report: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        decision = graph.process_signal(final_state.get("final_trade_decision", ""))
        typer.echo(f"Decision: {decision}")
        typer.echo(f"Report saved to: {report_file.resolve()}")
        return

    # Prompt to save report
    save_choice = typer.prompt("Save report?", default="Y").strip().upper()
    if save_choice in ("Y", "YES", ""):
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        # Under results_dir, not the working directory: in Docker the working
        # directory is inside the container and the report goes with it, while
        # results_dir is the mounted volume the rest of the run already writes to.
        default_path = (Path(config["results_dir"]) / "reports"
                        / f"{safe_ticker_component(selections['ticker'])}_{timestamp}")
        save_path_str = typer.prompt(
            "Save path (press Enter for default)",
            default=str(default_path)
        ).strip()
        save_path = Path(save_path_str)
        try:
            report_file = write_report_tree(final_state, selections["ticker"], save_path)
            console.print(f"\n[green]✓ Report saved to:[/green] {save_path.resolve()}")
            console.print(f"  [dim]Complete report:[/dim] {report_file.name}")
        except Exception as e:
            console.print(f"[red]Error saving report: {e}[/red]")

    # Prompt to display full report
    display_choice = typer.prompt("\nDisplay full report on screen?", default="Y").strip().upper()
    if display_choice in ("Y", "YES", ""):
        display_complete_report(final_state)
