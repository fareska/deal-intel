from importlib.metadata import version

import typer

app = typer.Typer(help="Strategic Deal Intelligence Assistant", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Keeps Typer in multi-command mode while only one command exists."""


@app.command("version")
def show_version() -> None:
    typer.echo(version("deal-intel"))


if __name__ == "__main__":
    app()
