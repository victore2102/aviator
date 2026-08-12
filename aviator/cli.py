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
    ConfigError,
    detect_harness,
    load_config,
    resolve_tiers,
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
    # `init` is the escape hatch from a broken config, so a config it cannot
    # parse must not stop it running. Report the problem, confirm, then carry
    # on with a blank slate — but confirm first, because regenerating throws
    # away whatever the user had in there.
    try:
        existing = load_config()
    except ConfigError as e:
        typer.echo(str(e), err=True)
        if not force and not typer.confirm(
            "\nRegenerate this config from scratch?", default=True
        ):
            typer.echo("Left unchanged.")
            raise typer.Exit(code=1)
        existing = {}

    # Guard against clobbering an existing setup unless --force.
    if existing.get("harness") and not force:
        typer.echo(f"Config already exists at {CONFIG_PATH}")
        typer.echo(f"  current harness: {existing['harness']}")
        if not typer.confirm("Overwrite it?"):
            typer.echo("Left unchanged.")
            raise typer.Exit()

    # ── Resolve the harness in priority order ──
    harness_options = sorted(VALID_HARNESSES)

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
        for i, name in enumerate(harness_options, 1):
            typer.echo(f"  {i}. {name}")

        def parse_choice(raw: str) -> str:
            """
            Turn the typed line into a harness name.

            Raising BadParameter (a UsageError) makes typer.prompt re-ask
            instead of exiting, and doing the index arithmetic here means the
            caller only ever sees a validated harness name.
            """
            raw = raw.strip()
            if not raw.isdigit() or not (1 <= int(raw) <= len(harness_options)):
                raise typer.BadParameter(
                    f"enter a number between 1 and {len(harness_options)}"
                )
            return harness_options[int(raw) - 1]

        chosen = typer.prompt("Select", value_proc=parse_choice)

    # ── Validate ──
    if chosen not in VALID_HARNESSES:
        typer.echo(
            f"'{chosen}' is not a known harness. "
            f"Choose from: {', '.join(harness_options)}"
        )
        raise typer.Exit(code=1)

    # ── Write config ──
    previous = existing.get("harness")

    config = existing or {}
    config["harness"] = chosen

    if previous is not None and previous != chosen:
        # Switching harness invalidates the whole tier table — the families in
        # it name models from the OLD provider, so keeping them would route
        # every query to a model string the new harness cannot resolve. Any
        # customisation was written in the old provider's vocabulary and does
        # not carry over, so it is replaced rather than merged.
        config["tiers"] = DEFAULT_TIER_MAPPINGS[chosen]
        typer.echo(
            f"\nHarness changed from '{previous}' to '{chosen}' — "
            "tier mappings reset to the defaults for the new harness."
        )
    else:
        # Same harness (or a fresh config): seed defaults only if absent, so
        # re-running init never wipes the user's own tier choices.
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
        config = load_config()
        harness = resolve_harness(config)
        tier_mappings = resolve_tiers(config, harness)
    except RuntimeError as e:
        typer.echo(str(e))
        raise typer.Exit(code=1)

    typer.echo(f"Routing with harness: {harness}")
    # ... hand off to the proxy layer here ...


if __name__ == "__main__":
    app()