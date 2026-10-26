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


if __name__ == "__main__":
    app()
