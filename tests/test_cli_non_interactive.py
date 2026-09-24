"""Offline CLI option, streaming, authentication and export regressions."""

import builtins
import datetime
import importlib
import io
import logging
import os
import socket
import sys
import warnings
from copy import deepcopy
from unittest import mock

import pytest
from typer.testing import CliRunner


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
        import cli.utils as utils
        import tradingagents.default_config as dc

        monkeypatch.setattr(dc, "DEFAULT_CONFIG", dc.DEFAULT_CONFIG)
        importlib.reload(dc)
        monkeypatch.setattr(m, "DEFAULT_CONFIG", deepcopy(dc.DEFAULT_CONFIG))
        m.DEFAULT_CONFIG.update(results_dir=str(tmp_path / "results"), data_cache_dir=str(tmp_path / "cache"))
        monkeypatch.setattr(m, "message_buffer", m.MessageBuffer())
        monkeypatch.setattr(builtins, "input", forbidden)
        monkeypatch.setattr(m.typer, "prompt", forbidden)
        for name in ("text", "password", "select", "checkbox", "confirm"):
            monkeypatch.setattr(utils.questionary, name, forbidden)
        for name in ("find_dotenv", "set_key"):
            monkeypatch.setattr(utils, name, forbidden)
        for name in ("fetch_announcements", "display_announcements", "display_complete_report"):
            monkeypatch.setattr(m, name, forbidden)
        yield m


@pytest.fixture
def streamed(isolated, monkeypatch):
    m = isolated
    from tradingagents.agents.rating import parse_rating

    graph = mock.MagicMock()
    graph.begin_checkpoint.return_value = "test-thread"
    graph.checkpoint_input.side_effect = lambda state: state
    graph.propagator.create_initial_state.return_value = {"initial": True}
    graph.create_run_state.side_effect = lambda ticker, date, asset_type="stock", portfolio=None: graph.propagator.create_initial_state(ticker, date, asset_type=asset_type)
    graph.propagator.get_graph_args.return_value = {"stream_mode": "values"}
    graph.graph.stream.return_value = iter([
        {"market_report": "Market evidence", "news_report": "News evidence"},
        {"investment_debate_state": {"bull_history": "Bull evidence", "judge_decision": "Research decision"}},
        {"trader_investment_plan": "Trading plan"},
        {"risk_debate_state": {"judge_decision": "Portfolio evidence"},
         "final_trade_decision": "**Rating**: Buy"},
    ])
    graph.process_signal.side_effect = parse_rating
    factory = mock.MagicMock(return_value=graph)
    monkeypatch.setattr(m, "TradingAgentsGraph", factory)
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
    assert factory.call_args.args == (["market", "social", "news", "fundamentals"],)
    assert factory.call_args.kwargs["config"] == before == m.DEFAULT_CONFIG
    today = datetime.date.today().isoformat()
    from pathlib import Path
    report = Path(before["results_dir"]) / normalized / today / "reports" / "complete_report.md"
    assert report.is_file()
    text = report.read_text()
    for content in ("Market evidence", "News evidence", "Bull evidence", "Trading plan", "Portfolio evidence"):
        assert content in text
    assert "Decision: Buy" in result.output
    assert str(report.resolve()) in result.output
    graph.begin_checkpoint.assert_called_once_with(normalized, today, "crypto" if normalized == "BTC-USD" else "stock")
    assert graph.graph.stream.call_args.kwargs["config"]["configurable"]["thread_id"] == "test-thread"
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
    assert result.exit_code != 0
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
    importlib.reload(dc)
    m.DEFAULT_CONFIG.update({k: v for k, v in dc.DEFAULT_CONFIG.items() if k not in ("results_dir", "data_cache_dir")})
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
    monkeypatch.setattr(m, "save_report_to_disk", mock.Mock(side_effect=OSError("disk full")))
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code == 1
    assert "Error saving report: disk full" in result.output
    graph.end_checkpoint.assert_called_once()


@pytest.mark.parametrize("individual_url", [None, "http://localhost:9999/v1"])
@pytest.mark.parametrize("shared_url", [None, "http://localhost:8317/v1"])
def test_real_graph_resolves_mixed_connections(streamed, monkeypatch, shared_url, individual_url):
    m, graph, _ = streamed
    from tradingagents.graph import trading_graph as gm

    monkeypatch.setenv("GOOGLE_API_KEY", "test-only-key")
    m.DEFAULT_CONFIG.update(
        llm_provider="openai", backend_url=shared_url,
        deep_think_llm_provider="Google", quick_think_llm_provider=None,
        deep_think_llm_backend_url=None, quick_think_llm_backend_url=individual_url,
    )
    clients = mock.Mock(side_effect=[mock.MagicMock(), mock.MagicMock()])
    monkeypatch.setattr(gm, "create_llm_client", clients)
    for name in ("set_config", "TradingMemoryLog", "ConditionalLogic", "GraphSetup",
                 "Propagator", "Reflector"):
        monkeypatch.setattr(gm, name, mock.MagicMock())

    def construct(*args, **kwargs):
        gm.TradingAgentsGraph(*args, **kwargs)
        return graph

    monkeypatch.setattr(m, "TradingAgentsGraph", construct)
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
    graph.graph.stream.side_effect = RuntimeError("stream failed")
    result = invoke(m, "--non-interactive", "--ticker", "FRHC")
    assert result.exit_code != 0
    graph.clear_checkpoint_on_success.assert_not_called()
    graph.end_checkpoint.assert_called_once()


def test_interactive_completion_still_prompts(streamed, monkeypatch):
    m, _, _ = streamed
    from cli.models import AnalystType
    selections = dict(ticker="FRHC", asset_type="stock", analysis_date="2024-01-01",
                      analysts=[AnalystType.MARKET], research_depth=1, shallow_thinker="quick",
                      deep_thinker="deep", llm_provider="openai", backend_url=None)
    select = mock.Mock(return_value=selections)
    prompt = mock.Mock(side_effect=["N", "N"])
    monkeypatch.setattr("cli.run.get_user_selections", select)
    monkeypatch.setattr(m.typer, "prompt", prompt)
    result = invoke(m)
    assert result.exit_code == 0, result.output
    select.assert_called_once_with()
    assert [call.args[0].strip() for call in prompt.call_args_list] == [
        "Save report?", "Display full report on screen?",
    ]


def test_no_args_retains_old_signature(isolated, monkeypatch):
    run = mock.Mock()
    monkeypatch.setattr(isolated, "run_analysis", run)
    result = invoke(isolated)
    assert result.exit_code == 0, result.output
    run.assert_called_once_with(checkpoint=None, portfolio=None)


@pytest.mark.parametrize("supplied", [("ticker",), ("analysis_date",), ("analysts",),
                                     ("ticker", "analysis_date", "analysts")])
def test_interactive_flags_skip_only_matching_prompts(isolated, monkeypatch, supplied):
    m = isolated
    from cli.models import AnalystType
    prompts = {}
    for name, value in {
        "get_ticker": "FRHC", "get_analysis_date": "2024-01-01",
        "select_analysts": [AnalystType.NEWS],
        "fetch_announcements": None, "display_announcements": None,
        "ask_output_language": "English", "select_research_depth": 2,
        "select_llm_provider": ("openai", None), "ensure_api_key": None,
        "select_shallow_thinking_agent": "quick", "select_deep_thinking_agent": "deep",
        "ask_openai_reasoning_effort": "medium",
    }.items():
        prompts[name] = mock.Mock(return_value=value)
        monkeypatch.setattr(m, name, prompts[name])
    options = dict(ticker="frhc", analysis_date="2024-01-01", analysts=[AnalystType.NEWS])
    selections = m.get_user_selections(**{key: options[key] for key in supplied})
    assert selections["ticker"] == "FRHC"
    assert selections["analysis_date"] == "2024-01-01"
    assert selections["analysts"] == [AnalystType.NEWS]
    skipped = {dict(ticker="get_ticker", analysis_date="get_analysis_date", analysts="select_analysts")[key]
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
    from pathlib import Path

    from tradingagents.graph import checkpointer

    clear = mock.Mock(return_value=2)
    monkeypatch.setattr(checkpointer, "clear_all_checkpoints", clear)
    # A handler bound before redirection must not leak output either.
    log_output = io.StringIO()
    logger = logging.getLogger("test.silent")
    handler = logging.StreamHandler(log_output)
    logger.addHandler(handler)
    chunks = graph.graph.stream.return_value

    def noisy_stream(*args, **kwargs):
        print("stdout noise")
        print("stderr noise", file=sys.stderr)
        m.console.print("Rich noise")
        warnings.warn("warning noise", stacklevel=1)
        logger.error("logging noise")
        yield from chunks

    graph.graph.stream.side_effect = noisy_stream
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
        graph.graph.stream.side_effect = RuntimeError("stream failed") if failure == "stream" else KeyboardInterrupt()
    elif failure == "save":
        monkeypatch.setattr(m, "save_report_to_disk", mock.Mock(side_effect=OSError("disk full")))
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
