"""CLI entry point for the S3 foundation and offline Agent Loop demo."""

from __future__ import annotations

import typer
from pydantic import ValidationError

from . import __version__
from .config import load_config, model_configuration_status


app = typer.Typer(
    name="github-scout-agent",
    help="Read-only, evidence-backed GitHub research agent.",
    add_completion=False,
    no_args_is_help=True,
)


@app.command("version")
def version_command() -> None:
    """Show the installed package version."""

    typer.echo(f"GitHub Scout Agent {__version__}")


@app.command("config")
def config_command() -> None:
    """Show safe configuration status without displaying secrets or making network calls."""

    try:
        config = load_config()
    except ValidationError as exc:
        typer.echo(f"Configuration error: {exc}", err=True)
        raise typer.Exit(code=2) from exc

    typer.echo(f"model_base_url: {config.model_base_url or '<not set>'}")
    typer.echo(f"model: {config.model or '<not set>'}")
    typer.echo(f"model_api_key: {'configured' if config.model_api_key else 'not configured'}")
    typer.echo(f"github_token: {'configured' if config.github_token else 'not configured'}")
    typer.echo(f"model_configuration: {model_configuration_status(config)}")
    if not config.model_api_key:
        typer.echo("Model API Key is not configured; no model call will be attempted.")
    typer.echo("S3-03 real model and GitHub connections are not enabled by this command.")


@app.command("run")
def run_command(
    task: str = typer.Argument(..., help="Natural-language GitHub research task."),
    confirm_live: bool = typer.Option(False, "--confirm-live", help="Required before any real model/GitHub request."),
    max_model_calls: int = typer.Option(10, "--max-model-calls", help="Maximum model completion rounds."),
    max_github_requests: int = typer.Option(20, "--max-github-requests", help="Maximum GitHub REST GET requests."),
    max_candidates: int = typer.Option(3, "--max-candidates", help="Maximum accepted repository candidates."),
    max_task_seconds: int = typer.Option(180, "--max-task-seconds", help="Maximum task wall-clock seconds (default: 180)."),
    require_readme: bool = typer.Option(False, "--require-readme", help="Require README evidence for every accepted candidate."),
) -> None:
    """Run the real CLI MVP path; requires explicit confirmation."""

    import json

    if not confirm_live:
        typer.echo("run is network-capable only with --confirm-live; no request was made.", err=True)
        raise typer.Exit(code=2)
    from .application import render_mvp_report, run_research_task
    from .budget import RequestBudget
    from .github_api import GitHubRestClient
    from .model_client import ModelAdapterError, OpenAICompatibleClient

    try:
        config = load_config()
        if not config.model_ready:
            typer.echo("Model configuration is incomplete; no request was made.", err=True)
            raise typer.Exit(code=2)
        budget = RequestBudget(
            max_model_calls=max_model_calls,
            max_github_requests=max_github_requests,
            max_candidates=max_candidates,
            max_task_seconds=max_task_seconds,
            max_feedback_bytes=64 * 1024,
            max_model_response_bytes=32 * 1024,
        )
        github_token = config.github_token.get_secret_value() if config.github_token else None
        with GitHubRestClient(token=github_token, budget=budget, timeout=15.0, max_retries=0) as github:
            with OpenAICompatibleClient.from_config(config, budget=budget, timeout=45.0) as model:
                result = run_research_task(task, model=model, github=github, budget=budget, require_readme_override=require_readme if require_readme else None)
        typer.echo(render_mvp_report(task, result))
        typer.echo("\n## Execution events\n" + "\n".join(
            f"- {event.event_type}: {json.dumps(event.details, ensure_ascii=False, sort_keys=True)}"
            for event in result.events
        ))
        if result.status != "completed":
            raise typer.Exit(code=1)
    except ModelAdapterError as exc:
        typer.echo(f"Model error: {exc.code}; no retry was attempted.", err=True)
        raise typer.Exit(code=1) from exc
    except ValidationError:
        typer.echo("Model error: validation_error; no retry was attempted.", err=True)
        raise typer.Exit(code=1)
    except ValueError as exc:
        typer.echo("Model error: value_error; no retry was attempted.", err=True)
        raise typer.Exit(code=1) from exc


@app.command("mock-demo")
def mock_demo_command() -> None:
    """Run one fully offline, feedback-driven Agent Loop demonstration."""

    import json

    from .demo import run_mock_demo

    result = run_mock_demo()
    for event in result.events:
        typer.echo(f"[{event.event_type}] {json.dumps(event.details, ensure_ascii=False, sort_keys=True)}")
    typer.echo(
        f"status={result.status} termination={result.termination_reason} "
        f"budget={json.dumps(result.budget, ensure_ascii=False, sort_keys=True)}"
    )


@app.command("live-agent-demo")
def live_agent_demo_command(
    scenario: str = typer.Option("search", "--scenario", help="One explicit live scenario: search or repository."),
) -> None:
    """Run one real GitHub public REST scenario with the Mock LLM (no real model)."""

    import json

    from .live_agent_demo import run_live_agent_demo, summarize_live_result

    if scenario not in {"search", "repository"}:
        raise typer.BadParameter("scenario must be search or repository")
    typer.echo("LIVE GitHub REST demo: public GET-only requests; no model API will be called.")
    result, request_log = run_live_agent_demo(scenario)  # type: ignore[arg-type]
    for event in result.events:
        typer.echo(f"[{event.event_type}] {json.dumps(event.details, ensure_ascii=False, sort_keys=True)}")
    typer.echo(json.dumps(summarize_live_result(result, request_log), ensure_ascii=False, sort_keys=True))
    if result.status != "completed":
        raise typer.Exit(code=1)


@app.command("gate-a")
def gate_a_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="Required: run only the offline MockTransport preflight."),
    online: bool = typer.Option(False, "--online", help="Explicitly enable the single authorized live attempt."),
    confirm_live: bool = typer.Option(False, "--confirm-live", help="Required confirmation for the live attempt."),
    scenario: str = typer.Option("valid", "--scenario", help="Offline scenario for protocol/error testing."),
) -> None:
    """Run the offline-only DeepSeek Gate A protocol preflight."""

    import json

    if dry_run and online:
        typer.echo("--dry-run and --online cannot be combined.", err=True)
        raise typer.Exit(code=2)
    if online:
        if not confirm_live:
            typer.echo("Online Gate A requires --confirm-live.", err=True)
            raise typer.Exit(code=2)
        if scenario != "valid":
            typer.echo("Online Gate A does not accept offline scenarios.", err=True)
            raise typer.Exit(code=2)
        from .gate_a import run_gate_a_online

        result = run_gate_a_online()
    else:
        if not dry_run:
            typer.echo("Gate A online mode is disabled; pass --dry-run for offline execution.", err=True)
            raise typer.Exit(code=2)
        from .gate_a import run_gate_a_dry_run

        result = run_gate_a_dry_run(scenario)
    typer.echo(json.dumps(result.model_dump(), ensure_ascii=False, sort_keys=True))
    if result.status != "passed":
        raise typer.Exit(code=1)


@app.command("gate-b")
def gate_b_command(
    dry_run: bool = typer.Option(False, "--dry-run", help="Required: run only the offline Mock LLM + Mock GitHub chain."),
    online: bool = typer.Option(False, "--online", help="Explicitly enable the single authorized online Gate B task."),
    confirm_live: bool = typer.Option(False, "--confirm-live", help="Required confirmation for the online task."),
) -> None:
    """Run the offline-only Gate B compatibility chain."""

    import json

    if dry_run and online:
        typer.echo("--dry-run and --online cannot be combined.", err=True)
        raise typer.Exit(code=2)
    if online:
        if not confirm_live:
            typer.echo("Online Gate B requires --confirm-live.", err=True)
            raise typer.Exit(code=2)
        from .gate_b import run_gate_b_online

        result = run_gate_b_online()
    else:
        if not dry_run:
            typer.echo("Gate B online mode is disabled; pass --dry-run for offline execution.", err=True)
            raise typer.Exit(code=2)
        from .gate_b import run_gate_b_dry_run

        result = run_gate_b_dry_run()
    typer.echo(json.dumps(result.model_dump(), ensure_ascii=False, sort_keys=True))
    if result.status != "completed":
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()