import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from inbox_hub.db.models import ApiKey
from inbox_hub.services.ingest import utcnow

TOKEN_PREFIX = "ihk_"
LAST_USED_RESOLUTION = timedelta(minutes=1)


def new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass(frozen=True)
class Principal:
    """Who is calling. Scope is resolved from the credential, never from the request.

    `category_ids is None` means unrestricted (the admin token from settings).
    """

    name: str
    key_id: int | None
    category_ids: frozenset[int] | None

    @property
    def is_admin(self) -> bool:
        return self.category_ids is None


ADMIN = Principal(name="admin", key_id=None, category_ids=None)


class AccessService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        admin_token: str,
        clock: Callable[[], datetime] = utcnow,
    ):
        self.sessions = sessions
        self.admin_token = admin_token
        self.clock = clock

    async def authenticate(self, token: str) -> Principal | None:
        if not token:
            return None
        if self.admin_token and hmac.compare_digest(
            token.encode(), self.admin_token.encode()
        ):
            return ADMIN
        now = self.clock()
        async with self.sessions.begin() as s:
            key = await s.scalar(
                select(ApiKey).where(ApiKey.token_hash == hash_token(token))
            )
            if key is None or key.revoked_at is not None:
                return None
            if key.last_used_at is None or now - key.last_used_at > (
                LAST_USED_RESOLUTION
            ):
                key.last_used_at = now
            return Principal(
                name=key.name,
                key_id=key.id,
                category_ids=frozenset(c.id for c in key.categories),
            )
