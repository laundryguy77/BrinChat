"""In-memory rate limiter for login attempts."""
import hashlib
import time
import threading
import logging
from typing import Dict, Tuple, Optional
from dataclasses import dataclass, field
from collections import OrderedDict

logger = logging.getLogger(__name__)


def _hash_key(key: str) -> str:
    """Hash a rate limit key for safe logging."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


@dataclass
class AttemptRecord:
    """Track login attempts for a key."""
    attempts: int
    first_attempt: float  # monotonic time
    last_access: float  # monotonic time for LRU
    locked_until: float = 0.0


class RateLimiter:
    """Thread-safe in-memory rate limiter with LRU eviction.

    Tracks failed login attempts per IP+username key.
    After max_attempts within window_seconds, locks out for lockout_seconds.

    Uses monotonic time to prevent clock skew vulnerabilities.
    Implements LRU eviction to prevent memory exhaustion attacks.
    """

    def __init__(
        self,
        max_attempts: int = 5,
        window_seconds: int = 300,  # 5 minutes
        lockout_seconds: int = 900,  # 15 minutes
        max_entries: int = 10000  # Maximum tracked keys
    ):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.lockout_seconds = lockout_seconds
        self.max_entries = max_entries
        self._records: OrderedDict[str, AttemptRecord] = OrderedDict()
        self._lock = threading.Lock()

    def _cleanup_expired(self, now: float) -> None:
        """Remove expired records (call while holding lock)."""
        expired_keys = []
        for key, record in self._records.items():
            # Remove if window expired and not locked
            if record.locked_until < now and (now - record.first_attempt) > self.window_seconds:
                expired_keys.append(key)
        for key in expired_keys:
            del self._records[key]

    def _evict_lru(self) -> None:
        """Evict least recently used entries if over limit (call while holding lock)."""
        while len(self._records) > self.max_entries:
            # OrderedDict.popitem(last=False) removes oldest item
            evicted_key, _ = self._records.popitem(last=False)
            logger.debug(f"Evicted rate limit entry: {_hash_key(evicted_key)}")

    def is_allowed(self, key: str) -> Tuple[bool, int]:
        """Check if a request is allowed.

        Args:
            key: Unique identifier (e.g., "ip:username")

        Returns:
            Tuple of (allowed: bool, retry_after_seconds: int)
            If allowed, retry_after is 0.
            If blocked, retry_after is seconds until lockout expires.
        """
        now = time.monotonic()

        with self._lock:
            self._cleanup_expired(now)

            record = self._records.get(key)
            if not record:
                return True, 0

            # Update last access for LRU and move to end
            record.last_access = now
            self._records.move_to_end(key)

            # Check if locked out
            if record.locked_until > now:
                retry_after = int(record.locked_until - now) + 1
                return False, retry_after

            # Check if window expired - reset attempts
            if (now - record.first_attempt) > self.window_seconds:
                del self._records[key]
                return True, 0

            # Check if at or over limit (fix off-by-one: use <= not <)
            if record.attempts >= self.max_attempts:
                return False, 0  # At limit but not locked (will be locked on next failed attempt)

            return True, 0

    def record_attempt(self, key: str, success: bool) -> None:
        """Record a login attempt.

        Args:
            key: Unique identifier (e.g., "ip:username")
            success: Whether the login was successful
        """
        now = time.monotonic()

        with self._lock:
            if success:
                # Clear on successful login
                self._records.pop(key, None)
                return

            record = self._records.get(key)

            if not record:
                # First failed attempt - check if need to evict first
                self._evict_lru()
                self._records[key] = AttemptRecord(
                    attempts=1,
                    first_attempt=now,
                    last_access=now
                )
                return

            # Update last access and move to end for LRU
            record.last_access = now
            self._records.move_to_end(key)

            # Check if window expired - start fresh
            if (now - record.first_attempt) > self.window_seconds:
                self._records[key] = AttemptRecord(
                    attempts=1,
                    first_attempt=now,
                    last_access=now
                )
                return

            # Increment attempts
            record.attempts += 1

            # Check if should lock out
            if record.attempts >= self.max_attempts:
                record.locked_until = now + self.lockout_seconds
                logger.warning(f"Rate limit exceeded for key hash '{_hash_key(key)}', locked out for {self.lockout_seconds}s")


# Global login rate limiter instance
_login_limiter: Optional[RateLimiter] = None

# Rate limiter for registration endpoint
_register_limiter: Optional[RateLimiter] = None

# Rate limiter for token refresh endpoint
_refresh_limiter: Optional[RateLimiter] = None


def get_login_limiter() -> RateLimiter:
    """Get the global login rate limiter instance."""
    global _login_limiter
    if _login_limiter is None:
        _login_limiter = RateLimiter(
            max_attempts=5,
            window_seconds=300,
            lockout_seconds=900
        )
    return _login_limiter


def get_register_limiter() -> RateLimiter:
    """Get the global registration rate limiter instance.

    More restrictive than login - fewer attempts allowed per IP.
    """
    global _register_limiter
    if _register_limiter is None:
        _register_limiter = RateLimiter(
            max_attempts=3,  # Only 3 registration attempts
            window_seconds=3600,  # Per hour
            lockout_seconds=3600  # 1 hour lockout
        )
    return _register_limiter


def get_refresh_limiter() -> RateLimiter:
    """Get the global token refresh rate limiter instance."""
    global _refresh_limiter
    if _refresh_limiter is None:
        _refresh_limiter = RateLimiter(
            max_attempts=10,  # Allow more refreshes
            window_seconds=60,  # Per minute
            lockout_seconds=300  # 5 minute lockout
        )
    return _refresh_limiter


# Rate limiter for chat messages
_chat_limiter: Optional[RateLimiter] = None


def get_chat_limiter() -> RateLimiter:
    """Get the global chat rate limiter instance.

    Allows generous but bounded message rates:
    - 30 messages per minute per user
    - 5 minute cooldown if exceeded
    """
    global _chat_limiter
    if _chat_limiter is None:
        _chat_limiter = RateLimiter(
            max_attempts=30,  # 30 messages per minute
            window_seconds=60,  # Per minute window
            lockout_seconds=300,  # 5 minute cooldown
            max_entries=50000  # Support more concurrent users
        )
    return _chat_limiter
