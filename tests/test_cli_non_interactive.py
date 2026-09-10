"""Offline CLI option, streaming, authentication and export regressions."""

import builtins
import datetime
import io
import json
import logging
import os
import socket
import sys
import warnings
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest
from typer.testing import CliRunner

from cli import display, prompts, run, selections


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    import dotenv
    import httpx
    import requests

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected prompt, credential write, announcement or network access")

    monkeypatch.setattr(dotenv, "load_dotenv", lambda *a, **k: False)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, forbidden)
    for name in ("create_connection", "getaddrinfo", "gethostbyname"):
        monkeypatch.setattr(socket, name, forbidden)
    monkeypatch.setattr(requests.Session, "request", forbidden)
    monkeypatch.setattr(httpx.Client, "send", forbidden)
    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden)
    with mock.patch.dict(os.environ, {"PYTHON_DOTENV_DISABLED": "1", "HOME": str(tmp_path)}, clear=True):
        import cli.main as m
        import tradingagents.default_config as dc

        config = dc.build_default_config()
        config.update(results_dir=str(tmp_path / "results"), data_cache_dir=str(tmp_path / "cache"),
                      memory_log_path=str(tmp_path / "memory.md"))
        for module in (m, run, selections):
            monkeypatch.setattr(module, "DEFAULT_CONFIG", config)
        buffer = display.MessageBuffer()
        monkeypatch.setattr(run, "message_buffer", buffer)
        monkeypatch.setattr(display, "message_buffer", buffer)
        monkeypatch.setattr(builtins, "input", forbidden)
        monkeypatch.setattr(m.typer, "prompt", forbidden)
        for name in ("text", "password", "select", "checkbox", "confirm"):
            monkeypatch.setattr(prompts.questionary, name, forbidden)
        for name in ("find_dotenv", "set_key"):
            monkeypatch.setattr(prompts, name, forbidden)
        monkeypatch.setattr(selections.updates, "enabled", lambda: False)
        monkeypatch.setattr(selections.updates, "show", forbidden)
        monkeypatch.setattr(selections.updates, "start_background_refresh", forbidden)
        monkeypatch.setattr(run, "display_complete_report", forbidden)
        monkeypatch.setattr(run, "_open_page", forbidden)
        yield m


@pytest.fixture
def streamed(isolated, monkeypatch):
    m = isolated
    from tradingagents.graph.trading_graph import TradingAgentsGraph

    graph = mock.MagicMock()
    graph.begin_checkpoint.return_value = "test-thread"
    graph.checkpoint_input.side_effect = lambda state: state
    graph.propagator.create_initial_state.return_value = {"initial": True}
    graph.create_run_state.side_effect = lambda ticker, date, asset_type="stock", portfolio=None: graph.propagator.create_initial_state(ticker, date, asset_type=asset_type)
    graph.propagator.get_graph_args.return_value = {"stream_mode": "values"}
    graph.stream_run.return_value = iter([
        ([], None),  # an analyst subgraph can emit messages without a state delta
        ([], {"market_report": "Market evidence", "news_report": "News evidence"}),
        ([], {"investment_debate_state": {"bull_history": "Bull evidence"},
              "investment_plan": "Research decision"}),
        ([], {"trader_investment_plan": "Trading plan"}),
        ([], {"risk_debate_state": {"aggressive_history": "Portfolio evidence"},
              "final_trade_decision": "**Rating**: Buy", "final_rating": "Buy"}),
    ])
    report_graph = object.__new__(TradingAgentsGraph)
    report_graph.config = m.DEFAULT_CONFIG
    report_graph.run_settings = lambda: {"llm_provider": "openai", "deep_think_llm": "deep-model",
                                       "quick_think_llm": "quick-model"}
    graph.save_reports.side_effect = report_graph.save_reports
    graph.default_report_path.side_effect = report_graph.default_report_path
    factory = mock.MagicMock(return_value=graph)
    monkeypatch.setattr(run, "TradingAgentsGraph", factory)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-key")
    return m, graph, factory


def invoke(m, *args):
    return CliRunner().invoke(m.app, list(args))


@pytest.mark.parametrize("ticker,normalized", [(" frhc ", "FRHC"), ("btcusdt", "BTC-USD")])
def test_stream_defaults_save_without_prompts(streamed, ticker, normalized):
    m, graph, factory = streamed
    before = deepcopy(m.DEFAULT_CONFIG)
    result = invoke(m, "--non-interactive", "--ticker", ticker)
    assert result.exit_code == 0, result.output
    expected = ["market", "social", "news"]
    if normalized != "BTC-USD":
        expected.append("fundamentals")
    assert factory.call_args.args == (expected,)
    assert factory.call_args.kwargs["config"] == before == m.DEFAULT_CONFIG
    today = datetime.date.today().isoformat()
    report = Path(before["results_dir"]) / normalized / today / "reports" / "complete_report.md"
    assert report.is_file()
    text = report.read_text()
    for content in ("Market evidence", "News evidence", "Bull evidence", "Trading plan", "Portfolio evidence"):
        assert content in text
    assert "Decision: Buy" in result.output
    assert str(report.resolve()) in result.output
    graph.begin_checkpoint.assert_called_once_with(normalized, today, "crypto" if normalized == "BTC-USD" else "stock", None)
    assert graph.stream_run.call_args.kwargs["config"]["configurable"]["thread_id"] == "test-thread"
    assert factory.call_args.kwargs["callbacks"]
    graph.clear_checkpoint_on_success.assert_called_once()
    graph.end_checkpoint.assert_called_once()


def test_subset_deduplicated_and_ordered(streamed):
    m, graph, factory = streamed
    result = invoke(m, "--non-interactive", "--ticker", "FRHC", "--date", "2024-02-29",
                    "--analyst", "news", "--analyst", "market", "--analyst", "news")
    assert result.exit_code == 0, result.output
    assert factory.call_args.args == (["market", "news"],)
    assert graph.propagator.create_initial_state.call_args.args == ("FRHC", "2024-02-29")


@pytest.mark.parametrize("args", [[], ["--ticker", ""], ["--ticker", "  "],
    ["--ticker", "."], ["--ticker", ".."], ["--ticker", "../FRHC"],
    ["--ticker", "A" * 33], ["--ticker", "bad symbol!"],
    ["--ticker", "FRHC", "--date", "2024-2-01"],
    ["--ticker", "FRHC", "--date", "2024-02-30"],
    ["--ticker", "FRHC", "--date", "9999-01-01"],
    ["--ticker", "FRHC", "--date", ""],
    ["--ticker", "FRHC", "--analyst", "technical"]])
def test_invalid_options_fail_before_graph(streamed, args):
    m, _, factory = streamed
    result = invoke(m, "--non-interactive", *args)
    assert result.exit_code == 2
    assert "Invalid value" in result.output
    factory.assert_not_called()


@pytest.mark.parametrize("provider,key", [("openai", "OPENAI_API_KEY"),
    ("google", "GOOGLE_API_KEY"), ("anthropic", "ANTHROPIC_API_KEY")])
@pytest.mark.parametrize("value", [None, "   "])
def test_missing_actual_provider_key(streamed, monkeypatch, provider, key, value):
    m, _, factory = streamed
    m.DEFAULT_CONFIG.update(deep_think_llm_provider=provider, quick_think_llm_provider="ollama")
    monkeypatch.delenv(key, raising=False)
    if value is not None:
        monkeypatch.setenv(key, value)
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 1
    assert key in result.output
    factory.assert_not_called()


@pytest.mark.parametrize("provider", ["ollama", "openai_compatible"])
@pytest.mark.parametrize("with_key", [False, True])
def test_optional_keys_and_unused_common_provider(streamed, monkeypatch, provider, with_key):
    m, _, factory = streamed
    monkeypatch.delenv("OPENAI_API_KEY")
    if with_key:
        monkeypatch.setenv("OPENAI_COMPATIBLE_API_KEY", "test-only-key")
    m.DEFAULT_CONFIG.update(llm_provider="anthropic", deep_think_llm_provider=provider,
                            quick_think_llm_provider=provider, backend_url="http://localhost:8317/v1")
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 0, result.output
    assert factory.called


@pytest.mark.parametrize("provider", ["unknown", "openai_compatible"])
def test_invalid_provider_configuration(streamed, provider):
    m, _, factory = streamed
    m.DEFAULT_CONFIG.update(deep_think_llm_provider=provider, backend_url=None)
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 1
    assert provider in result.output
    factory.assert_not_called()


@pytest.mark.parametrize("flag,expected", [(None, True), ("--checkpoint", True), ("--no-checkpoint", False)])
def test_env_connections_rounds_checkpoints(streamed, monkeypatch, flag, expected):
    m, _, factory = streamed
    import tradingagents.default_config as dc
    for key, value in {
        "TRADINGAGENTS_LLM_BACKEND_URL": "http://localhost:8317/v1",
        "TRADINGAGENTS_DEEP_THINK_LLM_PROVIDER": "Google",
        "TRADINGAGENTS_QUICK_THINK_LLM_BACKEND_URL": "http://localhost:9999/v1",
        "TRADINGAGENTS_MAX_DEBATE_ROUNDS": "2", "TRADINGAGENTS_MAX_RISK_ROUNDS": "4",
        "TRADINGAGENTS_CHECKPOINT_ENABLED": "true", "GOOGLE_API_KEY": "test-only-key",
    }.items():
        monkeypatch.setenv(key, value)
    m.DEFAULT_CONFIG.update({k: v for k, v in dc.build_default_config().items()
                            if k not in ("results_dir", "data_cache_dir", "memory_log_path")})
    result = invoke(m, "--non-interactive", "--ticker", "FRHC", *([flag] if flag else []))
    assert result.exit_code == 0, result.output
    cfg = factory.call_args.kwargs["config"]
    assert cfg["backend_url"] == "http://localhost:8317/v1"
    assert cfg["deep_think_llm_provider"] == "Google"
    assert cfg["deep_think_llm_backend_url"] is None
    assert cfg["quick_think_llm_provider"] is None
    assert cfg["quick_think_llm_backend_url"] == "http://localhost:9999/v1"
    assert (cfg["max_debate_rounds"], cfg["max_risk_discuss_rounds"]) == (2, 4)
    assert cfg["checkpoint_enabled"] is expected


def test_distinct_default_rounds_preserved(streamed):
    m, _, factory = streamed
    m.DEFAULT_CONFIG.update(max_debate_rounds=3, max_risk_discuss_rounds=5)
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 0, result.output
    cfg = factory.call_args.kwargs["config"]
    assert (cfg["max_debate_rounds"], cfg["max_risk_discuss_rounds"]) == (3, 5)


def test_save_failure_is_nonzero(streamed, monkeypatch):
    m, graph, _ = streamed
    graph.save_reports.side_effect = OSError("disk full")
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 1
    assert "Error saving report: disk full" in result.output
    graph.end_checkpoint.assert_called_once()


@pytest.mark.parametrize("individual_url", [None, "http://localhost:9999/v1"])
@pytest.mark.parametrize("shared_url", [None, "http://localhost:8317/v1"])
def test_real_graph_resolves_mixed_connections(streamed, monkeypatch, shared_url, individual_url):
    m, graph, _ = streamed
    from tradingagents.graph import trading_graph as gm
    from tradingagents.llm_clients import factory as llm_factory

    monkeypatch.setenv("GOOGLE_API_KEY", "test-only-key")
    m.DEFAULT_CONFIG.update(
        llm_provider="openai", backend_url=shared_url,
        deep_think_llm_provider="Google", quick_think_llm_provider=None,
        deep_think_llm_backend_url=None, quick_think_llm_backend_url=individual_url,
    )
    clients = mock.Mock(side_effect=[mock.MagicMock(), mock.MagicMock()])
    def create_client(provider, model, base_url=None, **kwargs):
        return clients(provider=provider, model=model, base_url=base_url, **kwargs)

    monkeypatch.setattr(llm_factory, "create_llm_client", create_client)
    for name in ("set_config", "TradingMemoryLog", "ConditionalLogic", "GraphSetup",
                 "Propagator", "Reflector"):
        monkeypatch.setattr(gm, name, mock.MagicMock())

    def construct(*args, **kwargs):
        gm.TradingAgentsGraph(*args, **kwargs)
        return graph

    monkeypatch.setattr(run, "TradingAgentsGraph", construct)
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 0, result.output
    deep, quick = clients.call_args_list
    assert deep.kwargs["provider"] == "google"
    assert quick.kwargs["provider"] == "openai"
    assert deep.kwargs["base_url"] == shared_url
    assert quick.kwargs["base_url"] == (individual_url or shared_url)
    assert deep.kwargs["callbacks"] is quick.kwargs["callbacks"]


def test_missing_quick_key_checked_before_graph(streamed):
    m, _, factory = streamed
    m.DEFAULT_CONFIG["quick_think_llm_provider"] = "google"
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 1
    assert "GOOGLE_API_KEY" in result.output
    factory.assert_not_called()


def test_clear_checkpoints_flag_unchanged(streamed, monkeypatch):
    m, _, _ = streamed
    from tradingagents.graph import checkpointer
    clear = mock.Mock(return_value=2)
    monkeypatch.setattr(checkpointer, "clear_all_checkpoints", clear)
    result = invoke(m, "--non-interactive", "--ticker", "FRHC", "--clear-checkpoints")
    assert result.exit_code == 0, result.output
    clear.assert_called_once_with(m.DEFAULT_CONFIG["data_cache_dir"])


def test_stream_failure_preserves_checkpoint(streamed):
    m, graph, _ = streamed
    graph.stream_run.side_effect = RuntimeError("stream failed")
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code != 0
    graph.clear_checkpoint_on_success.assert_not_called()
    graph.end_checkpoint.assert_called_once()


def test_interactive_completion_still_prompts(streamed, monkeypatch):
    from cli.models import AnalystType
    chosen = {"ticker": "FRHC", "asset_type": "stock", "analysis_date": "2024-01-01",
              "analysts": [AnalystType.MARKET], "research_depth": 1, "quick_think_llm": "quick",
              "deep_think_llm": "deep", "llm_provider": "openai", "backend_url": None}
    select = mock.Mock(return_value=chosen)
    prompt = mock.Mock(side_effect=["N", "N"])
    monkeypatch.setattr(run, "get_user_selections", select)
    monkeypatch.setattr(run.sys, "stdin", mock.Mock(isatty=lambda: True))
    monkeypatch.setattr(run.typer, "prompt", prompt)
    run.run_analysis()
    select.assert_called_once_with({})
    assert [call.args[0].strip() for call in prompt.call_args_list] == [
        "Save report?", "Display full report on screen?",
    ]


def test_no_args_retains_upstream_flags_interface(isolated, monkeypatch):
    runner = mock.Mock()
    monkeypatch.setattr(isolated, "run_analysis", runner)
    result = invoke(isolated)
    assert result.exit_code == 0, result.output
    runner.assert_called_once_with(checkpoint=None, portfolio=None,
        flags={"ticker": None, "date": None, "analysts": None, "save": None, "show": None, "html": None})


@pytest.mark.parametrize("supplied", [("ticker",), ("analysis_date",), ("analysts",),
                                     ("ticker", "analysis_date", "analysts")])
def test_interactive_flags_skip_only_matching_prompts(isolated, monkeypatch, supplied):
    from cli.models import AnalystType
    prompts = {}
    for name, value in {
        "get_ticker": "FRHC", "get_analysis_date": "2024-01-01",
        "select_analysts": [AnalystType.NEWS],
        "ask_output_language": "English", "select_research_depth": 2,
        "select_llm_provider": ("openai", None), "ensure_api_key": None,
        "select_shallow_thinking_agent": "quick", "select_deep_thinking_agent": "deep",
        "ask_openai_reasoning_effort": "medium",
    }.items():
        prompts[name] = mock.Mock(return_value=value)
        monkeypatch.setattr(selections, name, prompts[name])
    options = {"ticker": "frhc", "analysis_date": "2024-01-01", "analysts": "news"}
    chosen = selections.get_user_selections({("date" if key == "analysis_date" else key): options[key]
                                             for key in supplied})
    assert chosen["ticker"] == "FRHC"
    assert chosen["analysis_date"] == "2024-01-01"
    assert chosen["analysts"] == [AnalystType.NEWS]
    skipped = {{"ticker": "get_ticker", "analysis_date": "get_analysis_date", "analysts": "select_analysts"}[key]
               for key in supplied}
    for name, prompt in prompts.items():
        if name in skipped:
            prompt.assert_not_called()
        else:
            prompt.assert_called_once()


def test_options_visible_in_help(isolated, monkeypatch):
    monkeypatch.setenv("COLUMNS", "160")
    result = CliRunner().invoke(isolated.app, ["--help"], terminal_width=160)
    assert result.exit_code == 0
    for option in ("--ticker", "--date", "--analyst", "--non-interactive", "--silent", "--checkpoint", "--clear-checkpoints"):
        assert option in result.output


def test_silent_stream_saves_reports_and_suppresses_output(streamed, monkeypatch):
    m, graph, _ = streamed
    from tradingagents.graph import checkpointer

    clear = mock.Mock(return_value=2)
    monkeypatch.setattr(checkpointer, "clear_all_checkpoints", clear)
    # A handler bound before redirection must not leak output either.
    log_output = io.StringIO()
    logger = logging.getLogger("test.silent")
    handler = logging.StreamHandler(log_output)
    logger.addHandler(handler)
    chunks = graph.stream_run.return_value

    def noisy_stream(*args, **kwargs):
        print("stdout noise")
        print("stderr noise", file=sys.stderr)
        m.console.print("Rich noise")
        warnings.warn("warning noise", stacklevel=1)
        logger.error("logging noise")
        yield from chunks

    graph.stream_run.side_effect = noisy_stream
    try:
        result = invoke(m, "--silent", "--non-interactive", "--ticker", "FRHC",
                        "--date", "2024-01-01", "--clear-checkpoints")
        assert result.exit_code == 0, result.exception
        assert result.stdout == result.stderr == ""
        assert log_output.getvalue() == ""
        logger.error("logging restored")
        assert "logging restored" in log_output.getvalue()
    finally:
        logger.removeHandler(handler)
        handler.close()
    clear.assert_called_once()
    root = Path(m.DEFAULT_CONFIG["results_dir"]) / "FRHC" / "2024-01-01"
    report = (root / "reports" / "complete_report.md").read_text()
    for text in ("Market evidence", "News evidence", "Bull evidence", "Trading plan", "Portfolio evidence"):
        assert text in report
    assert (root / "reports" / "market_report.md").read_text() == "Market evidence"
    assert "Completed analysis" in (root / "message_tool.log").read_text()
    graph.end_checkpoint.assert_called_once()


@pytest.mark.parametrize("failure,code", [("mode", 2), ("ticker", 2), ("date", 2),
    ("key", 1), ("stream", 1), ("save", 1), ("interrupt", 1)])
def test_silent_failures_are_quiet_and_restore_output(streamed, monkeypatch, failure, code):
    m, graph, factory = streamed
    args = ["--silent", "--non-interactive", "--ticker", "FRHC"]
    if failure == "mode":
        args.remove("--non-interactive")
    elif failure == "ticker":
        args = ["--silent", "--non-interactive"]
    elif failure == "date":
        args += ["--date", "2024-02-30"]
    elif failure == "key":
        monkeypatch.delenv("OPENAI_API_KEY")
    elif failure in ("stream", "interrupt"):
        graph.stream_run.side_effect = RuntimeError("stream failed") if failure == "stream" else KeyboardInterrupt()
    elif failure == "save":
        graph.save_reports.side_effect = OSError("disk full")
    before = (sys.stdout, sys.stderr, m.console.quiet, logging.root.manager.disable, warnings.showwarning)
    result = invoke(m, *args)
    assert result.exit_code == code, result.exception
    assert result.stdout == result.stderr == ""
    assert before == (sys.stdout, sys.stderr, m.console.quiet, logging.root.manager.disable, warnings.showwarning)
    if failure in ("mode", "ticker", "date", "key"):
        factory.assert_not_called()
    else:
        graph.end_checkpoint.assert_called_once()
    if failure in ("stream", "interrupt"):
        graph.clear_checkpoint_on_success.assert_not_called()

    def visible(**kwargs):
        print("stdout restored")
        print("stderr restored", file=sys.stderr)
        m.console.print("Rich restored")

    monkeypatch.setattr(m, "run_analysis", visible)
    result = invoke(m)
    assert result.exit_code == 0
    assert "stdout restored" in result.stdout
    assert "stderr restored" in result.stderr
    assert "Rich restored" in result.stdout


@pytest.mark.parametrize("args,code", [(["--help"], 0), (["--unknown"], 2),
    (["--non-interactive", "--ticker", "FRHC", "--analyst", "technical"], 2)])
def test_silent_does_not_hide_help_or_parser_errors(streamed, args, code):
    m, _, factory = streamed
    result = invoke(m, "--silent", *args)
    assert result.exit_code == code
    assert result.output
    factory.assert_not_called()


@pytest.mark.parametrize("args,expected", [
    (["--analysts", "sentiment,news", "--analyst", "market", "--analyst", "news"],
     ["market", "social", "news"]),
    (["--analysts", "news,market,news"], ["market", "news"]),
])
def test_both_analyst_vocabularies_merge_and_deduplicate(streamed, args, expected):
    m, _, factory = streamed
    result = invoke(m, "--non-interactive", "--ticker", "FRHC", *args)
    assert result.exit_code == 0, result.output
    assert factory.call_args.args == (expected,)


@pytest.mark.parametrize("args", [
    ["--analysts", ""], ["--analysts", "technical"],
    ["--ticker", "BTC-USD", "--analyst", "fundamentals"],
])
def test_invalid_combined_analysts_are_validation_errors(streamed, args):
    m, _, factory = streamed
    result = invoke(m, "--non-interactive", "--ticker", "FRHC", *args)
    assert result.exit_code == 2, result.output
    assert "Invalid value" in result.output
    factory.assert_not_called()


@pytest.mark.parametrize("args,saved,shown,html", [
    ([], True, False, True),
    (["--no-save"], False, False, False),
    (["--show", "--no-html"], True, True, False),
    (["--no-save", "--show", "--html"], False, True, False),
    (["--save", "--no-show", "--html"], True, False, True),
])
def test_explicit_report_flags_override_automation_defaults(streamed, monkeypatch, args, saved, shown, html):
    m, graph, _ = streamed
    show = mock.Mock()
    monkeypatch.setattr(run, "display_complete_report", show)
    result = invoke(m, "--non-interactive", "--ticker", "FRHC", "--date", "2024-01-01", *args)
    assert result.exit_code == 0, result.output
    assert graph.save_reports.call_count == int(saved)
    assert show.call_count == int(shown)
    folder = Path(m.DEFAULT_CONFIG["results_dir"]) / "FRHC" / "2024-01-01" / "reports"
    assert (folder / "complete_report.md").exists() is saved
    assert (folder / "complete_report.html").exists() is html
    assert ("Report saved to:" in result.output) is saved
    assert "Decision: Buy" in result.output
    if saved:
        assert graph.save_reports.call_args.kwargs["html"] is html
        assert "deep-model" in (folder / "complete_report.md").read_text()


def test_noninteractive_never_loads_or_saves_interactive_preferences(streamed, monkeypatch):
    m, _, _ = streamed
    for name in ("load_last_run", "save_last_run"):
        monkeypatch.setattr(selections, name, mock.Mock(side_effect=AssertionError(name)))
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 0, result.output


def test_noninteractive_sign_in_failure_never_launches_login_even_on_a_terminal(streamed, monkeypatch):
    import typer

    from tradingagents.llm_clients import chatgpt_auth

    m, _, factory = streamed
    m.DEFAULT_CONFIG.update(deep_think_provider="chatgpt", quick_think_provider="chatgpt")
    monkeypatch.setattr(prompts.sys, "stdin", mock.Mock(isatty=lambda: True))
    monkeypatch.setattr(chatgpt_auth, "access_token", mock.Mock(side_effect=chatgpt_auth.ChatGPTAuthError("Sign in first")))
    login = mock.Mock(side_effect=AssertionError("An unattended run launched login"))
    monkeypatch.setattr(prompts.subprocess, "run", login)
    with pytest.raises(typer.Exit) as caught:
        run.run_analysis(flags={"non_interactive": True, "ticker": "FRHC"})
    assert caught.value.exit_code == 1
    login.assert_not_called()
    factory.assert_not_called()


@pytest.mark.parametrize("rating,decision,expected", [
    ("Sell", "**Rating**: Buy", "Sell"),
    (None, "**Rating**: Overweight", "Overweight"),
    (None, "No readable decision", "REVIEW"),
])
def test_cli_uses_actual_run_rating(streamed, rating, decision, expected):
    m, graph, _ = streamed
    graph.stream_run.return_value = iter([
        ([], {"final_trade_decision": decision, "final_rating": rating}),
    ])
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 0, result.output
    assert f"Decision: {expected}" in result.output


def test_portfolio_resume_stream_decision_log_and_html_export(streamed, monkeypatch, tmp_path):
    from langchain_core.messages import AIMessage

    from tradingagents.graph.propagation import Propagator
    from tradingagents.graph.trading_graph import TradingAgentsGraph
    from tradingagents.memory import TradingMemoryLog
    from tradingagents.portfolio import PortfolioContext

    m, graph, factory = streamed
    book_file = tmp_path / "portfolio.json"
    book_data = {"cash": 25000.0, "currency": "USD",
                 "positions": [{"ticker": "FRHC", "quantity": 120, "average_price": 150.0}]}
    book_file.write_text(json.dumps(book_data))
    graph._resuming = True
    graph.checkpoint_input.side_effect = lambda state: None
    chunks = list(graph.stream_run.return_value)
    initial_state = Propagator().create_initial_state("FRHC", "2024-01-01")
    for _, chunk in chunks:
        if chunk is not None:
            for key in ("investment_debate_state", "risk_debate_state"):
                if key in chunk:
                    chunk[key] = {**initial_state[key], **chunk[key]}
    update = AIMessage(content="Resumed analyst message", id="resumed-message")

    def stream(initial, **kwargs):
        assert initial is None
        assert kwargs["config"]["configurable"]["thread_id"] == "test-thread"
        assert all(run.message_buffer.agent_status[name] == "in_progress"
                   for name in ("Market Analyst", "News Analyst"))
        yield [update], None
        yield [update], None  # repeated checkpoint message must not be logged twice
        yield [], initial_state
        yield from chunks

    graph.stream_run.side_effect = stream
    # Real persistence methods run against a temporary journal and results tree;
    # model construction and the graph stream remain offline boundaries.
    persisted = object.__new__(TradingAgentsGraph)
    persisted.config = {**m.DEFAULT_CONFIG, "checkpoint_enabled": True}
    persisted.selected_analysts = ("market", "news")
    persisted.memory_log = TradingMemoryLog(persisted.config)
    graph.record_decision.side_effect = persisted.record_decision
    graph.save_reports.side_effect = persisted.save_reports

    result = invoke(m, "--non-interactive", "--ticker", "FRHC", "--date", "2024-01-01",
                    "--analysts", "market,news", "--portfolio", str(book_file), "--checkpoint")
    assert result.exit_code == 0, (result.output, result.exception)
    assert factory.call_args.kwargs["config"]["checkpoint_enabled"] is True
    book = graph.create_run_state.call_args.args[3]
    assert isinstance(book, PortfolioContext)
    assert book == PortfolioContext.model_validate(book_data)
    for method in (graph.create_run_state, graph.begin_checkpoint, graph.clear_checkpoint_on_success):
        method.assert_called_once_with("FRHC", "2024-01-01", "stock", book)
    graph.checkpoint_input.assert_called_once_with({"initial": True})
    assert graph.stream_run.call_args.args == (None,)
    graph.end_checkpoint.assert_called_once()
    graph.record_decision.assert_called_once()
    final_state = graph.record_decision.call_args.args[2]
    assert final_state["market_report"] == "Market evidence"
    assert final_state["investment_plan"] == "Research decision"
    assert final_state["final_rating"] == "Buy"
    folder = Path(m.DEFAULT_CONFIG["results_dir"]) / "FRHC" / "2024-01-01" / "reports"
    graph.save_reports.assert_called_once_with(final_state, "FRHC", folder, html=True)
    order = [call[0] for call in graph.mock_calls]
    assert order.index("stream_run") < order.index("record_decision") < order.index("clear_checkpoint_on_success")
    assert order.index("clear_checkpoint_on_success") < order.index("end_checkpoint") < order.index("save_reports")
    for name in ("complete_report.md", "complete_report.html"):
        text = (folder / name).read_text()
        assert "Market evidence" in text
        assert m.DEFAULT_CONFIG["deep_think_llm"] in text
        assert m.DEFAULT_CONFIG["quick_think_llm"] in text
    entries = persisted.memory_log.load_entries()
    assert [(entry["ticker"], entry["rating"], entry["pending"]) for entry in entries] == [("FRHC", "Buy", True)]
    state_log = Path(m.DEFAULT_CONFIG["results_dir"]) / "FRHC" / "TradingAgentsStrategy_logs" / "full_states_log_2024-01-01.json"
    assert '"final_rating": "Buy"' in state_log.read_text()
    log = (folder.parent / "message_tool.log").read_text()
    assert "Resuming the saved run" in log
    assert log.count("Resumed analyst message") == 1
