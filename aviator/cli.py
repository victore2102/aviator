"""
AvIator — CLI entrypoint.

`aviator init`   one-time setup: determine and persist the user's harness
                 plus default tier mappings, so daily use is a single command.
`aviator start`  begin routing (reads the saved config, no prompts).
"""

import typer

from aviator.config import (
    VALID_HARNESSES,
    CONFIG_PATH,
    detect_harness,
    load_config,
    save_config,
    resolve_harness,
)
from aviator.vars import DEFAULT_TIER_MAPPINGS

app = typer.Typer(help="AvIator — route each query to the right model tier.")


@app.command()
def init(
    harness: str = typer.Option(
        None,
        "--harness",
        help="Set the harness non-interactively (claude_code / codex / gemini_cli).",
    ),
    force: bool = typer.Option(
        False, "--force", help="Overwrite existing config without asking."
    ),
):
    """
    One-time setup. Determines your harness (via --flag, detection, or a
    prompt), writes it to the config file, and seeds default tier mappings.
    """
    existing = load_config()

    # Guard against clobbering an existing setup unless --force.
    if existing.get("harness") and not force:
        typer.echo(f"Config already exists at {CONFIG_PATH}")
        typer.echo(f"  current harness: {existing['harness']}")
        if not typer.confirm("Overwrite it?"):
            typer.echo("Left unchanged.")
            raise typer.Exit()

    # ── Resolve the harness in priority order ──

    # 1. Explicit flag always wins.
    chosen = harness

    # 2. No flag? Try detection and OFFER it (confirm, don't assume).
    if chosen is None:
        detected = detect_harness()
        if detected:
            if typer.confirm(f"Detected harness '{detected}'. Use it?", default=True):
                chosen = detected

    # 3. Still nothing? Prompt with a clear choice list.
    if chosen is None:
        typer.echo("Which harness are you using?")
        for i, name in enumerate(sorted(VALID_HARNESSES), 1):
            typer.echo(f"  {i}. {name}")
        chosen = typer.prompt("Enter name").strip()

    # ── Validate ──
    if chosen not in VALID_HARNESSES:
        typer.echo(
            f"'{chosen}' is not a known harness. "
            f"Choose from: {', '.join(sorted(VALID_HARNESSES))}"
        )
        raise typer.Exit(code=1)

    # ── Write config ──
    config = existing or {}
    config["harness"] = chosen
    # Only seed tier mappings if the user hasn't already customised them.
    config.setdefault("tiers", DEFAULT_TIER_MAPPINGS[chosen])

    save_config(config)

    typer.echo(f"\n✓ Harness set to '{chosen}'")
    typer.echo(f"✓ Config written to {CONFIG_PATH}")
    typer.echo("\nTier mappings (edit the config to change):")
    for tier, family in config["tiers"].items():
        typer.echo(f"  {tier:<9} → {family}")
    typer.echo("\nYou're set. Run `aviator start` to begin routing.")


@app.command()
def start():
    """Begin routing using the saved config. No prompts."""
    try:
        harness = resolve_harness()
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    typer.echo(f"Routing with harness: {harness}")
    # ... hand off to the proxy layer here ...


if __name__ == "__main__":
    app()