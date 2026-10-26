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


if __name__ == "__main__":
    app()
