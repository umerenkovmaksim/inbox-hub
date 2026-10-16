"""Declarative catalog: categories, their channels, tracked chats and key scopes in YAML.

`plan` and `apply` run the same reconciliation; `plan` rolls the transaction back.
Fields of declared entries are authoritative and additions always apply; anything
that removes state (a membership, a grant, tracking, a whole category) waits for
`prune`, except `exclude` and `telegram.paused`, which are explicit removals.
Channels of a category's `auto_sources` are never pruned: they joined on their own.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Annotated

import yaml
from pydantic import AfterValidator, BaseModel, ConfigDict, ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from inbox_hub.db.models import ApiKey, Category, Channel
from inbox_hub.enums import Source
from inbox_hub.services.access import hash_token, new_token
from inbox_hub.services.catalog import SLUG, CatalogError
from inbox_hub.services.ingest import utcnow


def _slug(value: str) -> str:
    if not SLUG.match(value):
        raise ValueError(f"invalid name {value!r}: use a-z, 0-9, '-', '_'")
    return value


def _ref(value: str) -> str:
    source, sep, external_id = value.partition(":")
    if not sep or source not in {x.value for x in Source} or not external_id:
        raise ValueError(
            f"channel must be telegram:<chat id> or sdo:<course id>, got {value!r}"
        )
    if source == Source.TELEGRAM and not external_id.lstrip("-").isdigit():
        raise ValueError(f"telegram chat id must be a number, got {value!r}")
    return value


Slug = Annotated[str, AfterValidator(_slug)]
ChannelRef = Annotated[str, AfterValidator(_ref)]


class _Spec(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CategorySpec(_Spec):
    title: str
    description: str | None = None
    auto_sources: list[Source] = []
    channels: list[ChannelRef] = []
    exclude: list[ChannelRef] = []


class TelegramSpec(_Spec):
    track: list[int] = []
    paused: list[int] = []


class KeySpec(_Spec):
    categories: list[Slug] = []


class CatalogSpec(_Spec):
    categories: dict[Slug, CategorySpec] = {}
    telegram: TelegramSpec = TelegramSpec()
    keys: dict[Slug, KeySpec] = {}


def parse_spec(text: str) -> CatalogSpec:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"invalid YAML: {exc}") from None
    try:
        spec = CatalogSpec.model_validate(data or {})
    except ValidationError as exc:
        lines = [
            f"  {'.'.join(map(str, e['loc'])) or '<root>'}: {e['msg']}"
            for e in exc.errors()
        ]
        raise CatalogError("invalid catalog file:\n" + "\n".join(lines)) from None
    for slug, c in spec.categories.items():
        if both := set(c.channels) & set(c.exclude):
            raise CatalogError(f"{slug}: in both channels and exclude: {sorted(both)}")
    if both := set(spec.telegram.track) & set(spec.telegram.paused):
        raise CatalogError(f"telegram: in both track and paused: {sorted(both)}")
    return spec


@dataclass
class Change:
    sign: str  # "+", "~" or "-"
    text: str

    def __str__(self) -> str:
        return f"{self.sign} {self.text}"


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    # Removals that only `prune` performs.
    pending: list[Change] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # Tokens of keys created by `apply`; shown once.
    tokens: dict[str, str] = field(default_factory=dict)


def _label(ch: Channel) -> str:
    ref = f"{ch.source}:{ch.external_id}"
    return ref if ch.title == ch.external_id else f"{ref} ({ch.title})"


class CatalogFile:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.clock = clock

    async def plan(self, spec: CatalogSpec, prune: bool = False) -> Plan:
        return await self._run(spec, prune, commit=False)

    async def apply(self, spec: CatalogSpec, prune: bool = False) -> Plan:
        return await self._run(spec, prune, commit=True)

    async def _run(self, spec: CatalogSpec, prune: bool, commit: bool) -> Plan:
        async with self.sessions() as s:
            try:
                plan = await self._reconcile(s, spec, prune)
            except BaseException:
                await s.rollback()
                raise
            if commit:
                await s.commit()
            else:
                await s.rollback()
                plan.tokens.clear()
            return plan

    async def _reconcile(self, s: AsyncSession, spec: CatalogSpec, prune: bool) -> Plan:
        plan = Plan()

        def remove(change: Change) -> bool:
            (plan.changes if prune else plan.pending).append(change)
            return prune

        categories = {c.slug: c for c in (await s.scalars(select(Category))).all()}
        channels = {
            f"{c.source}:{c.external_id}": c
            for c in (await s.scalars(select(Channel))).all()
        }
        keys = {k.name: k for k in (await s.scalars(select(ApiKey))).all()}

        # Described as they are before this run's other changes.
        undeclared = {
            slug: (
                f"{sum(c in ch.categories for ch in channels.values())} channels, "
                f"keys: {', '.join(k.name for k in keys.values() if c in k.categories) or '-'}"
            )
            for slug, c in sorted(categories.items())
            if slug not in spec.categories
        }
        available = set(spec.categories) | (set() if prune else set(categories))
        for name, k in spec.keys.items():
            if missing := [x for x in k.categories if x not in available]:
                raise CatalogError(f"key {name}: unknown categories {missing}")

        # categories
        for slug, c in spec.categories.items():
            auto = [str(x) for x in c.auto_sources]
            category = categories.get(slug)
            if category is None:
                category = Category(
                    slug=slug,
                    title=c.title,
                    description=c.description,
                    auto_sources=auto,
                )
                s.add(category)
                categories[slug] = category
                plan.changes.append(
                    Change(
                        "+",
                        f"category {slug} {json.dumps(c.title, ensure_ascii=False)}",
                    )
                )
                continue
            for attr, new in (
                ("title", c.title),
                ("description", c.description),
                ("auto_sources", auto),
            ):
                old = getattr(category, attr)
                if (old or None) != (new or None):
                    setattr(category, attr, new)
                    plan.changes.append(
                        Change("~", f"category {slug}: {attr} {old!r} -> {new!r}")
                    )

        # tracked Telegram chats
        paused = {f"{Source.TELEGRAM}:{x}" for x in spec.telegram.paused}
        wanted = {
            ref
            for c in spec.categories.values()
            for ref in c.channels
            if ref.startswith(f"{Source.TELEGRAM}:")
        } | {f"{Source.TELEGRAM}:{x}" for x in spec.telegram.track}
        wanted -= paused
        for ref in sorted(wanted):
            ch = channels.get(ref)
            if ch is None:
                external_id = ref.partition(":")[2]
                ch = Channel(
                    source=Source.TELEGRAM,
                    external_id=external_id,
                    title=external_id,
                    cursor=0,
                    tracked=True,
                    categories=[],
                )
                s.add(ch)
                channels[ref] = ch
                plan.changes.append(Change("+", f"track {ref} (new chat)"))
            elif not ch.tracked:
                ch.tracked = True
                plan.changes.append(Change("+", f"track {_label(ch)}"))
        for ref, ch in sorted(channels.items()):
            if ch.source != Source.TELEGRAM or not ch.tracked or ref in wanted:
                continue
            if ref in paused:
                ch.tracked = False
                plan.changes.append(Change("-", f"untrack {_label(ch)} (paused)"))
            elif remove(Change("-", f"untrack {_label(ch)}")):
                ch.tracked = False

        # memberships
        for slug, c in spec.categories.items():
            category = categories[slug]
            members = [ch for ch in channels.values() if category in ch.categories]
            for ref in c.channels:
                ch = channels.get(ref)
                if ch is None:
                    plan.warnings.append(
                        f"{slug}: {ref} is not known yet (appears after a sync), skipped"
                    )
                elif category not in ch.categories:
                    ch.categories.append(category)
                    plan.changes.append(Change("+", f"{slug} <- {_label(ch)}"))
            for ref in c.exclude:
                ch = channels.get(ref)
                if ch is not None and category in ch.categories:
                    ch.categories.remove(category)
                    plan.changes.append(
                        Change("-", f"{slug} -x {_label(ch)} (excluded)")
                    )
            listed = set(c.channels) | set(c.exclude)
            for ch in members:
                ref = f"{ch.source}:{ch.external_id}"
                if ref in listed or ch.source in c.auto_sources:
                    continue
                if remove(Change("-", f"{slug} -x {_label(ch)}")):
                    ch.categories.remove(category)

        # keys
        for name, k in spec.keys.items():
            wanted_categories = [categories[x] for x in dict.fromkeys(k.categories)]
            key = keys.get(name)
            if key is None:
                token = new_token()
                s.add(
                    ApiKey(
                        name=name,
                        token_hash=hash_token(token),
                        created_at=self.clock(),
                        categories=wanted_categories,
                    )
                )
                plan.tokens[name] = token
                scope = ", ".join(k.categories) or "no categories"
                plan.changes.append(Change("+", f"key {name}: {scope}"))
                continue
            if key.token_hash is None:
                plan.warnings.append(
                    f"key {name} is revoked; `inbox-hub keys rotate {name}` re-enables it"
                )
            for category in wanted_categories:
                if category not in key.categories:
                    key.categories.append(category)
                    plan.changes.append(Change("+", f"key {name} <- {category.slug}"))
            for category in list(key.categories):
                if category.slug not in k.categories and remove(
                    Change("-", f"key {name} -x {category.slug}")
                ):
                    key.categories.remove(category)

        # categories missing from the file
        for slug, detail in undeclared.items():
            if remove(Change("-", f"category {slug} ({detail})")):
                category = categories[slug]
                for holder in [*channels.values(), *keys.values()]:
                    if category in holder.categories:
                        holder.categories.remove(category)
                await s.delete(category)

        await s.flush()
        return plan

    async def export(self) -> str:
        """Current catalog as a file that `apply` (with or without prune) leaves as is."""
        async with self.sessions() as s:
            categories = (
                await s.scalars(select(Category).order_by(Category.slug))
            ).all()
            channels = (
                await s.scalars(select(Channel).order_by(Channel.source, Channel.title))
            ).all()
            keys = (await s.scalars(select(ApiKey).order_by(ApiKey.name))).all()

        out = [
            "# Inbox Hub catalog: `inbox-hub catalog plan`, then `inbox-hub catalog apply`.",
            "# Without --prune apply only adds and updates; removals need --prune.",
            "",
        ]
        listed: set[str] = set()
        out.append("categories:" + ("" if categories else " {}"))
        for c in categories:
            members = [ch for ch in channels if c in ch.categories]
            manual = [ch for ch in members if ch.source not in (c.auto_sources or [])]
            auto = [ch for ch in members if ch.source in (c.auto_sources or [])]
            out.append(f"  {_scalar(c.slug)}:")
            out.append(f"    title: {_scalar(c.title)}")
            if c.description:
                out.append(f"    description: {_scalar(c.description)}")
            if c.auto_sources:
                out.append(f"    auto_sources: [{', '.join(c.auto_sources)}]")
            out.append("    channels:" + ("" if manual else " []"))
            for ch in manual:
                ref = f"{ch.source}:{ch.external_id}"
                listed.add(ref)
                out.append(f"      - {ref}{_comment(ch)}")
            for ch in auto:
                out.append(
                    f"      # auto: {ch.source}:{ch.external_id}{_comment(ch).replace('  #', '', 1)}"
                )
        telegram = [ch for ch in channels if ch.source == Source.TELEGRAM]
        track = [
            ch
            for ch in telegram
            if ch.tracked and f"telegram:{ch.external_id}" not in listed
        ]
        paused = [
            ch
            for ch in telegram
            if not ch.tracked and f"telegram:{ch.external_id}" in listed
        ]
        out += ["", "telegram:"]
        out.append("  track:" + ("" if track else " []"))
        out += [f"    - {ch.external_id}{_comment(ch)}" for ch in track]
        out.append("  paused:" + ("" if paused else " []"))
        out += [f"    - {ch.external_id}{_comment(ch)}" for ch in paused]
        out += ["", "keys:" + ("" if keys else " {}")]
        for k in keys:
            state = "" if k.token_hash else "  # revoked"
            slugs = ", ".join(_scalar(c.slug) for c in k.categories)
            out.append(f"  {_scalar(k.name)}:{state}")
            out.append(f"    categories: [{slugs}]")
        return "\n".join(out) + "\n"


def _scalar(value: str) -> str:
    """Plain YAML scalar when it reads back as the same string, else quoted."""
    try:
        if (
            yaml.safe_load(value) == value
            and value.strip() == value
            and "#" not in value
        ):
            return value
    except yaml.YAMLError:
        pass
    return json.dumps(value, ensure_ascii=False)


def _comment(ch: Channel) -> str:
    if ch.title == ch.external_id:
        return ""
    return "  # " + " ".join(ch.title.split())
