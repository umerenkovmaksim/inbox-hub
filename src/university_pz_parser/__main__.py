import asyncio
import json
from pathlib import Path

import typer

from university_pz_parser.logging import configure_logging
from university_pz_parser.settings import check_api_token, get_settings

app = typer.Typer(no_args_is_help=True)


@app.command()
def login() -> None:
    from university_pz_parser.sources.telegram.client import (
        SessionLock,
        TelegramClientManager,
    )

    async def run():
        settings = get_settings().telegram
        with SessionLock(settings.lock_path):
            tg = TelegramClientManager(settings)
            try:
                if await tg.connect():
                    typer.echo("session is already authorized, nothing to do")
                    return
                await tg.client.start()
                me = await tg.client.get_me()
                typer.echo(f"logged in as {me.first_name} ({me.id})")
            finally:
                await tg.stop()

    asyncio.run(run())


@app.command("list-chats")
def list_chats() -> None:
    """One-off: needs the collector to be stopped (same session)."""
    from university_pz_parser.sources.telegram.client import (
        SessionLock,
        TelegramClientManager,
    )

    async def run():
        settings = get_settings().telegram
        with SessionLock(settings.lock_path):
            tg = TelegramClientManager(settings)
            try:
                await tg.start()
                async for d in tg.client.iter_dialogs():
                    if d.is_group or d.is_channel:
                        typer.echo(f"{d.id}\t{d.name}")
            finally:
                await tg.stop()

    asyncio.run(run())


@app.command("init-db")
def init_db() -> None:
    from alembic.config import Config

    from alembic import command

    Path("data").mkdir(exist_ok=True)
    command.upgrade(Config("alembic.ini"), "head")


@app.command()
def sync(source: str) -> None:
    """One-off sync of a polled source (sdo). Telegram is handled by `collect`."""
    from university_pz_parser.container import Container

    async def run():
        c = await Container.build(get_settings())
        try:
            if source not in c.jobs:
                raise typer.BadParameter(f"unknown source {source!r}")
            report = await c.runner.run(c.jobs[source])
            typer.echo(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
        finally:
            await c.stop()

    configure_logging()
    asyncio.run(run())


@app.command("import-export")
def import_export(
    file: Path, chat_id: int = typer.Option(..., help="marked id, e.g. -1002362006703")
) -> None:
    from university_pz_parser.cli.import_export import export_to_items
    from university_pz_parser.container import Container

    async def run():
        c = await Container.build(get_settings(), with_sources=False)
        counts: dict[str, int] = {}
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
            for item in export_to_items(data, chat_id, file.parent):
                status = await c.ingest.ingest(item)
                counts[status] = counts.get(status, 0) + 1
        finally:
            await c.stop()
        typer.echo(counts)

    configure_logging()
    asyncio.run(run())


@app.command()
def collect() -> None:
    """Long-running Telegram collector: updates + periodic reconcile."""
    from university_pz_parser.sources.telegram.runtime import run_collector

    configure_logging()
    asyncio.run(run_collector(get_settings()))


@app.command()
def serve() -> None:
    import uvicorn

    from university_pz_parser.api.app import create_app

    configure_logging()
    s = get_settings()
    check_api_token(s.api)
    uvicorn.run(
        create_app(), host=s.api.host, port=s.api.port, workers=1, log_config=None
    )


if __name__ == "__main__":
    app()
