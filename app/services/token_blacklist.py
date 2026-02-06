"""Token blacklist service for invalidating JWTs on logout/password change."""
import time
import threading
import logging
from typing import Optional, Set
from collections import OrderedDict

logger = logging.getLogger(__name__)


class TokenBlacklist:
    """In-memory token blacklist with automatic expiration cleanup.

    Stores invalidated token JTIs (JWT IDs) or token hashes to prevent
    reuse after logout or password change.

    Uses monotonic time to prevent clock skew issues.
    Implements LRU-style eviction for memory management.
    """

    def __init__(self, max_entries: int = 10000):
        self.max_entries = max_entries
        # Maps token identifier -> expiration time (monotonic)
        self._blacklist: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def _cleanup_expired(self, now: float) -> None:
        """Remove expired entries (call while holding lock)."""
        expired_keys = []
        for token_id, expires_at in self._blacklist.items():
            if expires_at <= now:
                expired_keys.append(token_id)
        for key in expired_keys:
            del self._blacklist[key]

    def _evict_oldest(self) -> None:
        """Evict oldest entries if over limit (call while holding lock)."""
        while len(self._blacklist) > self.max_entries:
            self._blacklist.popitem(last=False)
            logger.debug("Evicted oldest token from blacklist")

    def add(self, token_id: str, ttl_seconds: int) -> None:
        """Add a token to the blacklist.

        Args:
            token_id: Unique identifier for the token (JTI or hash)
            ttl_seconds: Time in seconds until the token would expire naturally
        """
        now = time.monotonic()
        expires_at = now + ttl_seconds

        with self._lock:
            self._cleanup_expired(now)
            self._evict_oldest()
            self._blacklist[token_id] = expires_at
            self._blacklist.move_to_end(token_id)

    def is_blacklisted(self, token_id: str) -> bool:
        """Check if a token is blacklisted.

        Args:
            token_id: Unique identifier for the token

        Returns:
            True if blacklisted and not expired, False otherwise
        """
        now = time.monotonic()

        with self._lock:
            self._cleanup_expired(now)

            if token_id not in self._blacklist:
                return False

            # Update access order
            self._blacklist.move_to_end(token_id)
            return True

    def clear(self) -> None:
        """Clear all blacklisted tokens."""
        with self._lock:
            self._blacklist.clear()


# Global token blacklist instance
_token_blacklist: Optional[TokenBlacklist] = None


def get_token_blacklist() -> TokenBlacklist:
    """Get the global token blacklist instance."""
    global _token_blacklist
    if _token_blacklist is None:
        _token_blacklist = TokenBlacklist()
    return _token_blacklist
