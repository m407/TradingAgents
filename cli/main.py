import contextlib
import logging
import os
import sys
import warnings
from contextlib import redirect_stderr, redirect_stdout

import typer

from cli.display import MessageBuffer, console, message_buffer
from cli.models import AnalystType
from cli.run import run_analysis
from cli.selections import get_user_selections
from tradingagents.backtest import iter_grid, run_backtest, summarize
from tradingagents.default_config import DEFAULT_CONFIG
from tradingagents.portfolio import load_portfolio

# Placeholders for tests monkeypatching cli.main
TradingAgentsGraph = None
fetch_announcements = None
display_announcements = None
display_complete_report = None
get_ticker = None
get_analysis_date = None
select_analysts = None
ask_output_language = None
select_research_depth = None
select_llm_provider = None
ensure_api_key = None
select_shallow_thinking_agent = None
select_deep_thinking_agent = None
ask_openai_reasoning_effort = None
ask_anthropic_effort = None
ask_gemini_thinking_config = None
prompt_openai_compatible_url = None
save_report_to_disk = None


@contextlib.contextmanager
def silent_output(enabled: bool):
    """Silence command execution and restore process output state on every exit."""
    if not enabled:
        yield
        return
    quiet, disabled = console.quiet, logging.root.manager.disable
    with open(os.devnull, "w") as sink, redirect_stdout(sink), redirect_stderr(sink), warnings.catch_warnings():
        console.quiet = True
        logging.disable(max(disabled, logging.CRITICAL))
        warnings.showwarning = lambda *args, **kwargs: None
        try:
            yield
        except typer.Exit:
            raise
        except typer.BadParameter as exc:
            # Click renders these after the handler returns, outside redirection.
            raise typer.Exit(code=exc.exit_code) from exc
        except (Exception, KeyboardInterrupt) as exc:
            raise typer.Exit(code=1) from exc
        finally:
            console.quiet = quiet
            logging.disable(disabled)


# prompt_toolkit's win32 output module is importable only on Windows (it asserts
# the platform at import time), so gate on the platform rather than catching the
# failure — that way a genuinely broken prompt_toolkit on Windows still surfaces
# instead of silently disabling the handler below. Off Windows this stays an
# empty tuple, which `except` accepts and never matches (#1138).
if sys.platform == "win32":  # pragma: no cover - platform dependent
    from prompt_toolkit.output.win32 import NoConsoleScreenBufferError

    _NO_CONSOLE_ERRORS: tuple[type[BaseException], ...] = (NoConsoleScreenBufferError,)
else:
    _NO_CONSOLE_ERRORS = ()

app = typer.Typer(
    name="TradingAgents",
    help="TradingAgents CLI: Multi-Agents LLM Financial Trading Framework",
    add_completion=True,  # Enable shell completion
)


@app.callback(invoke_without_command=True)
def analyze(
    ctx: typer.Context,
    ticker: str | None = typer.Option(None, "--ticker", help="Ticker symbol to analyze."),
    analysis_date: str | None = typer.Option(None, "--date", help="Analysis date (YYYY-MM-DD; defaults to today in noninteractive mode)."),
    analysts: list[AnalystType] | None = typer.Option(None, "--analyst", help="Analyst to include; repeat for a subset. Defaults to all four in noninteractive mode."),
    non_interactive: bool = typer.Option(False, "--non-interactive", help="Run without prompts, using configured defaults, and automatically save reports. Requires --ticker."),
    silent: bool = typer.Option(False, "--silent", help="Suppress execution output, preserving reports and exit codes. Requires --non-interactive; help and parser errors remain visible."),
    checkpoint: bool | None = typer.Option(
        None,
        "--checkpoint/--no-checkpoint",
        help="Enable/disable checkpoint-resume (save state after each node so a "
        "crashed run can resume). Omit to honor TRADINGAGENTS_CHECKPOINT_ENABLED.",
    ),
    clear_checkpoints: bool = typer.Option(
        False,
        "--clear-checkpoints",
        help="Delete all saved checkpoints before running (force fresh start).",
    ),
    portfolio: str = typer.Option(
        None,
        "--portfolio",
        help="JSON file with current holdings and cash, so the trader, risk and "
        "portfolio agents size against your actual position.",
    ),
):
    """Run an analysis. This is what a bare `tradingagents` does."""
    if ctx.invoked_subcommand is not None:
        return
    with silent_output(silent):
        if silent and not non_interactive:
            raise typer.BadParameter("Requires --non-interactive", param_hint="--silent")
        if clear_checkpoints:
            from tradingagents.graph.checkpointer import clear_all_checkpoints
            n = clear_all_checkpoints(DEFAULT_CONFIG["data_cache_dir"])
            console.print(f"[yellow]Cleared {n} checkpoint(s).[/yellow]")
        portfolio_context = None
        if portfolio:
            try:
                portfolio_context = load_portfolio(portfolio)
            except ValueError as exc:
                console.print(f"[red]{exc}[/red]")
                raise typer.Exit(code=1) from None

        try:
            options = {}
            for key, value in (("ticker", ticker), ("analysis_date", analysis_date), ("analysts", analysts)):
                if value is not None:
                    options[key] = value
            if non_interactive:
                options["non_interactive"] = True
            if portfolio_context is not None:
                options["portfolio"] = portfolio_context
            run_analysis(checkpoint=checkpoint, **options)
        except ValueError as exc:
            if not non_interactive:
                raise
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(code=1) from exc
        except _NO_CONSOLE_ERRORS:
            # A terminal with no console buffer cannot host the interactive prompts.
            # Emit one actionable line on stderr instead of a prompt_toolkit
            # traceback; plain text, since rich may not render here either (#1138).
            typer.echo(
                "Error: no Windows console available. The interactive CLI needs a real "
                "console buffer — run it from Windows Terminal, PowerShell, or cmd.exe "
                "rather than a piped or embedded terminal.",
                err=True,
            )
            raise typer.Exit(code=1) from None


@app.command()
def backtest(
    tickers: str = typer.Argument(..., help="Comma-separated tickers, e.g. NVDA,AAPL"),
    start: str = typer.Option(..., "--start", help="First analysis date, YYYY-MM-DD"),
    end: str = typer.Option(..., "--end", help="Last analysis date, YYYY-MM-DD"),
    every: int = typer.Option(7, "--every", help="Days between analysis dates"),
    analysts: str = typer.Option(
        None, "--analysts", help="Comma-separated analysts to run; omit for all four"
    ),
    asset_type: str = typer.Option("stock", "--asset-type", help="stock or crypto"),
    portfolio: str = typer.Option(
        None, "--portfolio", help="JSON file with holdings and cash, held constant across the grid"
    ),
    run_id: str = typer.Option(
        None, "--run-id", help="Continue an earlier sweep: its cells are skipped and its log reused"
    ),
):
    """Score past decisions over a grid of tickers and dates."""

    try:
        dates = iter_grid(start, end, every)
        book = load_portfolio(portfolio) if portfolio else None
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None

    names = [t.strip() for t in tickers.split(",") if t.strip()]
    if not names:
        console.print("[red]No ticker to analyze; pass them comma-separated, e.g. NVDA,AAPL[/red]")
        raise typer.Exit(code=1)

    kwargs = {"asset_type": asset_type, "portfolio": book, "run_id": run_id}
    if analysts:
        kwargs["selected_analysts"] = [a.strip().lower() for a in analysts.split(",") if a.strip()]

    try:
        result = run_backtest(names, dates, DEFAULT_CONFIG, **kwargs)
    except Exception as exc:  # a missing key or an unknown analyst is a setup error
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from None
    console.print(summarize(result).render())
    console.print(f"\nRan {result.cells_run} cells, skipped {result.skipped}. Log: {result.log_path}")
    for ticker, date, reason in result.failures:
        console.print(f"[yellow]failed:[/yellow] {ticker} {date}: {reason}")
    for ticker, reason in result.settlement_failures:
        console.print(f"[yellow]unsettled:[/yellow] {ticker}: {reason}")


if __name__ == "__main__":
    app()
