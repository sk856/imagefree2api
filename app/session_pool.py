"""Small session pool for imagefree.org requests."""

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from loguru import logger


@dataclass
class ImagefreeSession:
    name: str
    visitor_id: str
    session_id: str
    cookies: Dict[str, str] = field(default_factory=dict)
    in_use: int = 0
    failures: int = 0
    cooldown_until: float = 0.0


class SessionLease:
    def __init__(self, pool: "ImagefreeSessionPool", session: ImagefreeSession):
        self.pool = pool
        self.session = session

    @property
    def name(self) -> str:
        return self.session.name

    @property
    def visitor_id(self) -> str:
        return self.session.visitor_id

    @property
    def session_id(self) -> str:
        return self.session.session_id

    @property
    def cookies(self) -> Dict[str, str]:
        return dict(self.session.cookies)

    async def update_cookies(self, cookies: Dict[str, str]) -> None:
        await self.pool.update_cookies(self.session, cookies)

    async def __aenter__(self) -> "SessionLease":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.pool.release(self.session, failed=exc is not None)


class ImagefreeSessionPool:
    def __init__(
        self,
        state_path: Path,
        session_count: int,
        max_concurrent_per_session: int,
        cooldown_seconds: int,
        wait_timeout_seconds: int,
    ):
        self.state_path = state_path
        self.session_count = max(1, session_count)
        self.max_concurrent_per_session = max(1, max_concurrent_per_session)
        self.cooldown_seconds = max(1, cooldown_seconds)
        self.wait_timeout_seconds = max(1, wait_timeout_seconds)
        self._condition = asyncio.Condition()
        self._next_index = 0
        self.sessions = self._load_sessions()

    @property
    def total_slots(self) -> int:
        return self.session_count * self.max_concurrent_per_session

    def _new_session(self, index: int) -> ImagefreeSession:
        return ImagefreeSession(
            name=f"session-{index + 1}",
            visitor_id=uuid.uuid4().hex,
            session_id=uuid.uuid4().hex,
        )

    def _load_sessions(self) -> List[ImagefreeSession]:
        data = {}
        if self.state_path.exists():
            try:
                data = json.loads(self.state_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning(f"Failed to load session pool state: {e}")

        raw_sessions = data.get("sessions", []) if isinstance(data, dict) else []
        sessions: List[ImagefreeSession] = []
        for index in range(self.session_count):
            raw = raw_sessions[index] if index < len(raw_sessions) else {}
            sessions.append(
                ImagefreeSession(
                    name=raw.get("name") or f"session-{index + 1}",
                    visitor_id=raw.get("visitor_id") or uuid.uuid4().hex,
                    session_id=raw.get("session_id") or uuid.uuid4().hex,
                    cookies=raw.get("cookies") or {},
                )
            )

        self._save_sessions(sessions)
        return sessions

    def _save_sessions(self, sessions: Optional[List[ImagefreeSession]] = None) -> None:
        sessions = sessions or self.sessions
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "updated_at": int(time.time()),
            "sessions": [
                {
                    "name": session.name,
                    "visitor_id": session.visitor_id,
                    "session_id": session.session_id,
                    "cookies": session.cookies,
                }
                for session in sessions
            ],
        }
        tmp_path = self.state_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)

    async def acquire(self) -> SessionLease:
        deadline = time.monotonic() + self.wait_timeout_seconds
        async with self._condition:
            while True:
                now = time.monotonic()
                cooldown_wait = None
                for offset in range(len(self.sessions)):
                    index = (self._next_index + offset) % len(self.sessions)
                    session = self.sessions[index]
                    if session.cooldown_until > now:
                        remaining = session.cooldown_until - now
                        cooldown_wait = remaining if cooldown_wait is None else min(cooldown_wait, remaining)
                        continue
                    if session.in_use < self.max_concurrent_per_session:
                        session.in_use += 1
                        self._next_index = (index + 1) % len(self.sessions)
                        logger.info(f"Acquired imagefree {session.name} ({session.in_use}/{self.max_concurrent_per_session})")
                        return SessionLease(self, session)

                remaining_time = deadline - now
                if remaining_time <= 0:
                    raise TimeoutError("No imagefree session slot available")

                wait_for = min(remaining_time, cooldown_wait or 1.0)
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=wait_for)
                except asyncio.TimeoutError:
                    pass

    async def update_cookies(self, session: ImagefreeSession, cookies: Dict[str, str]) -> None:
        if not cookies:
            return
        async with self._condition:
            session.cookies.update(cookies)
            self._save_sessions()

    async def release(self, session: ImagefreeSession, failed: bool) -> None:
        async with self._condition:
            session.in_use = max(0, session.in_use - 1)
            if failed:
                session.failures += 1
                cooldown = min(self.cooldown_seconds * session.failures, self.cooldown_seconds * 5)
                session.cooldown_until = time.monotonic() + cooldown
                logger.warning(f"Cooling down imagefree {session.name} for {cooldown}s after failure")
            else:
                session.failures = 0
                session.cooldown_until = 0.0
            self._condition.notify_all()
