import asyncio
import json
from pathlib import Path

import typer

from inbox_hub.logging import configure_logging
from inbox_hub.settings import check_api_token, get_settings

app = typer.Typer(no_args_is_help=True)
categories_app = typer.Typer(no_args_is_help=True, help="Categories of channels.")
channels_app = typer.Typer(no_args_is_help=True, help="Known chats and courses.")
chats_app = typer.Typer(no_args_is_help=True, help="Telegram chats to collect.")
keys_app = typer.Typer(
    no_args_is_help=True, help="API keys; each sees only its categories."
)
app.add_typer(categories_app, name="categories")
app.add_typer(channels_app, name="channels")
app.add_typer(chats_app, name="chats")
app.add_typer(keys_app, name="keys")
catalog_app = typer.Typer(
    no_args_is_help=True,
    help="Categories, chats and key scopes from a YAML file (plan, apply, export).",
)
app.add_typer(catalog_app, name="catalog")


def _catalog(action):
    """Run `action(catalog)` against the configured DB; works while the server runs."""
    from inbox_hub.db.session import make_engine, make_sessions
    from inbox_hub.services.catalog import CatalogError, CatalogService

    async def run():
        engine = make_engine(get_settings().database.url)
        try:
            return await action(CatalogService(make_sessions(engine)))
        finally:
            await engine.dispose()

    try:
        return asyncio.run(run())
    except CatalogError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


def _slugs(items) -> str:
    return ",".join(c.slug for c in items) or "-"


@categories_app.command("list")
def categories_list() -> None:
    for c in _catalog(lambda cat: cat.list_categories()):
        auto = f"\tauto: {','.join(c.auto_sources)}" if c.auto_sources else ""
        typer.echo(f"{c.slug}\t{c.title}{auto}")


@categories_app.command("create")
def categories_create(
    slug: str,
    title: str = typer.Option(..., help="human-readable name"),
    description: str | None = typer.Option(None, help="what belongs here"),
    auto_source: list[str] = typer.Option(
        [], help="new channels of this source join automatically (telegram, sdo)"
    ),
) -> None:
    _catalog(lambda cat: cat.create_category(slug, title, description, auto_source))
    typer.echo(f"created {slug}")


@categories_app.command("delete")
def categories_delete(slug: str) -> None:
    _catalog(lambda cat: cat.delete_category(slug))
    typer.echo(f"deleted {slug}")


@categories_app.command("add-channel")
def categories_add_channel(
    slug: str, channel: str = typer.Argument(help="id or source:external_id")
) -> None:
    c = _catalog(lambda cat: cat.add_channel(slug, channel))
    typer.echo(f"{c.title}: {_slugs(c.categories)}")


@categories_app.command("remove-channel")
def categories_remove_channel(
    slug: str, channel: str = typer.Argument(help="id or source:external_id")
) -> None:
    c = _catalog(lambda cat: cat.remove_channel(slug, channel))
    typer.echo(f"{c.title}: {_slugs(c.categories)}")


@channels_app.command("list")
def channels_list() -> None:
    for c in _catalog(lambda cat: cat.list_channels()):
        tracked = "tracked" if c.tracked else "-"
        typer.echo(
            f"{c.id}\t{c.source}:{c.external_id}\t{tracked}\t"
            f"{_slugs(c.categories)}\t{c.title}"
        )


# Marked chat ids are negative (-100...), which click would take for options.
NEGATIVE_IDS = {"ignore_unknown_options": True}


@chats_app.command("add", context_settings=NEGATIVE_IDS)
def chats_add(
    chat_id: int = typer.Argument(help="marked id from list-chats, e.g. -100..."),
    category: list[str] = typer.Option([], help="category slug, repeatable"),
) -> None:
    c = _catalog(lambda cat: cat.track_chat(chat_id, category))
    typer.echo(
        f"tracking {chat_id}: {_slugs(c.categories)} (picked up within a minute)"
    )


@chats_app.command("remove", context_settings=NEGATIVE_IDS)
def chats_remove(chat_id: int) -> None:
    _catalog(lambda cat: cat.untrack_chat(chat_id))
    typer.echo(f"stopped tracking {chat_id}; collected items are kept")


@keys_app.command("list")
def keys_list() -> None:
    for k in _catalog(lambda cat: cat.list_keys()):
        state = "active" if k.token_hash and k.revoked_at is None else "inactive"
        used = k.last_used_at.isoformat() if k.last_used_at else "never"
        typer.echo(f"{k.name}\t{state}\t{_slugs(k.categories)}\tlast used {used}")


@keys_app.command("create")
def keys_create(
    name: str,
    category: list[str] = typer.Option([], help="category slug, repeatable"),
) -> None:
    _, token = _catalog(lambda cat: cat.create_key(name, category))
    typer.echo(token)


@keys_app.command("rotate")
def keys_rotate(name: str) -> None:
    typer.echo(_catalog(lambda cat: cat.rotate_key(name)))


@keys_app.command("revoke")
def keys_revoke(name: str) -> None:
    _catalog(lambda cat: cat.revoke_key(name))
    typer.echo(f"revoked {name}")


@keys_app.command("grant")
def keys_grant(name: str, slug: str) -> None:
    k = _catalog(lambda cat: cat.grant(name, slug))
    typer.echo(f"{k.name}: {_slugs(k.categories)}")


@keys_app.command("ungrant")
def keys_ungrant(name: str, slug: str) -> None:
    k = _catalog(lambda cat: cat.ungrant(name, slug))
    typer.echo(f"{k.name}: {_slugs(k.categories)}")


CATALOG_FILE = typer.Option(
    Path("catalog.yaml"), "--file", "-f", help="catalog file, '-' for stdin"
)
PRUNE = typer.Option(
    False,
    help="also remove categories, channels, chats and grants missing from the file",
)


def _catalog_file(action):
    from inbox_hub.services.catalog_file import CatalogFile

    return _catalog(lambda cat: action(CatalogFile(cat.sessions)))


def _read_spec(file: Path):
    import sys

    from inbox_hub.services.catalog import CatalogError
    from inbox_hub.services.catalog_file import parse_spec

    try:
        text = (
            sys.stdin.read() if str(file) == "-" else file.read_text(encoding="utf-8")
        )
        return parse_spec(text)
    except (OSError, CatalogError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from None


def _print_plan(plan) -> None:
    for w in plan.warnings:
        typer.echo(f"! {w}", err=True)
    for c in plan.changes:
        typer.echo(str(c))
    if plan.pending:
        typer.echo("not removed without --prune:")
        for c in plan.pending:
            typer.echo(f"  {c}")
    if not plan.changes:
        typer.echo("no changes")


@catalog_app.command("plan")
def catalog_plan(file: Path = CATALOG_FILE, prune: bool = PRUNE) -> None:
    """Show what `apply` would change; touches nothing."""
    spec = _read_spec(file)
    _print_plan(_catalog_file(lambda cf: cf.plan(spec, prune)))


@catalog_app.command("apply")
def catalog_apply(
    file: Path = CATALOG_FILE,
    prune: bool = PRUNE,
    yes: bool = typer.Option(False, "--yes", "-y", help="do not ask for confirmation"),
) -> None:
    """Bring the database in line with the file, in one transaction."""
    spec = _read_spec(file)
    preview = _catalog_file(lambda cf: cf.plan(spec, prune))
    _print_plan(preview)
    if not preview.changes:
        return
    if not yes:
        if str(file) == "-":
            typer.echo("error: reading the file from stdin needs --yes", err=True)
            raise typer.Exit(1)
        typer.confirm("apply?", abort=True)
    plan = _catalog_file(lambda cf: cf.apply(spec, prune))
    typer.echo(f"applied {len(plan.changes)} changes")
    for name, token in plan.tokens.items():
        typer.echo(f"token for {name} (shown once): {token}")


@catalog_app.command("export")
def catalog_export(
    output: Path | None = typer.Option(
        None, "--output", "-o", help="write here instead of stdout"
    ),
) -> None:
    """Current categories, chats and key scopes as a catalog file."""
    text = _catalog_file(lambda cf: cf.export())
    if output is None:
        typer.echo(text, nl=False)
    else:
        output.write_text(text, encoding="utf-8")


@app.command()
def login() -> None:
    """One-off interactive login; needs the collector to be stopped (same session)."""
    from inbox_hub.sources.telegram.client import SessionLock, TelegramClientManager

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
    """Groups and channels seen by the collector's last reconcile (no Telegram call)."""
    dialogs = _catalog(lambda cat: cat.telegram_dialogs())
    if not dialogs:
        typer.echo(
            "no dialogs yet: start `inbox-hub collect` and wait for its first pass"
        )
        return
    for d in dialogs:
        typer.echo(f"{d.chat_id}\t{d.kind}\t{d.title}")
    typer.echo(f"as of {min(d.seen_at for d in dialogs).isoformat()}", err=True)


@app.command("init-db")
def init_db() -> None:
    from alembic.config import Config

    from alembic import command

    Path("data").mkdir(exist_ok=True)
    command.upgrade(Config("alembic.ini"), "head")


@app.command()
def sync(source: str) -> None:
    """One-off sync of a polled source (sdo). Telegram is handled by `collect`."""
    from inbox_hub.container import Container

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
    from inbox_hub.cli.import_export import export_to_items
    from inbox_hub.container import Container

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
    from inbox_hub.sources.telegram.runtime import run_collector

    configure_logging()
    asyncio.run(run_collector(get_settings()))


@app.command()
def serve() -> None:
    import uvicorn

    from inbox_hub.api.app import create_app

    configure_logging()
    s = get_settings()
    check_api_token(s.api)
    uvicorn.run(
        create_app(), host=s.api.host, port=s.api.port, workers=1, log_config=None
    )


if __name__ == "__main__":
    app()
