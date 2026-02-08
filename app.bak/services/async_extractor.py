"""Async extraction service for fire-and-forget memory/profile updates.

This service processes model responses in the background to extract:
- Memories (facts about the user)
- Profile updates (preferences, settings)

Uses a small, fast model (qwen2.5-coder:3b by default) to avoid blocking
the main chat response.
"""
import asyncio
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass
from collections import deque

logger = logging.getLogger(__name__)

# Queue for pending extraction tasks
_extraction_queue: deque = deque(maxlen=100)  # Limit queue size
_processing = False


@dataclass
class ExtractionTask:
    """A pending extraction task."""
    user_id: int
    user_message: str
    assistant_response: str
    conversation_id: Optional[str] = None


async def queue_extraction(
    user_id: int,
    user_message: str,
    assistant_response: str,
    conversation_id: Optional[str] = None
) -> None:
    """Queue an extraction task to run in the background.

    This returns immediately - extraction happens asynchronously.
    """
    task = ExtractionTask(
        user_id=user_id,
        user_message=user_message,
        assistant_response=assistant_response,
        conversation_id=conversation_id
    )
    _extraction_queue.append(task)
    logger.debug(f"[AsyncExtract] Queued extraction task for user {user_id}, queue size: {len(_extraction_queue)}")

    # Start processor if not running
    asyncio.create_task(_process_queue())


async def _process_queue() -> None:
    """Process queued extraction tasks."""
    global _processing

    if _processing:
        return  # Already processing

    _processing = True
    try:
        while _extraction_queue:
            task = _extraction_queue.popleft()
            try:
                await _process_extraction(task)
            except Exception as e:
                logger.warning(f"[AsyncExtract] Task failed for user {task.user_id}: {e}")
    finally:
        _processing = False


async def _process_extraction(task: ExtractionTask) -> None:
    """Process a single extraction task."""
    from app.config import EXTRACTION_MODEL
    from app.services.ollama import ollama_service
    from app.services.memory_service import get_memory_service
    from app.services.user_profile_service import get_user_profile_service

    logger.info(f"[AsyncExtract] Processing extraction for user {task.user_id}")

    # Build extraction prompt
    extraction_prompt = f"""Analyze this conversation and extract any information worth remembering about the user.

USER MESSAGE:
{task.user_message[:1000]}

ASSISTANT RESPONSE:
{task.assistant_response[:2000]}

Extract ONLY if you find concrete facts. Output in this format (one per line):
[MEMORY category=personal|preference|topic|instruction importance=1-10] content here
[PROFILE section.field] value

Examples:
[MEMORY category=personal importance=9] User's name is Joel
[MEMORY category=preference importance=7] User prefers concise responses
[MEMORY category=topic importance=5] User is working on BrinChat project
[PROFILE identity.preferred_name] Joel
[PROFILE communication.response_length] concise

If nothing notable to extract, output: [NONE]

Extract:"""

    try:
        # Call the small extraction model (non-streaming for simplicity)
        response = await ollama_service.chat_complete(
            model=EXTRACTION_MODEL,
            messages=[{"role": "user", "content": extraction_prompt}],
            options={"temperature": 0.1, "num_predict": 500}  # Low temp for consistent extraction
        )

        if not response:
            logger.debug("[AsyncExtract] No response from extraction model")
            return

        content = response.get("message", {}).get("content", "")
        if not content or "[NONE]" in content:
            logger.debug("[AsyncExtract] No extractions found")
            return

        # Parse and save extractions
        await _save_extractions(task.user_id, content)

    except Exception as e:
        logger.warning(f"[AsyncExtract] Extraction model call failed: {e}")


async def _save_extractions(user_id: int, content: str) -> None:
    """Parse extraction output and save to memory/profile."""
    import re
    from app.services.memory_service import get_memory_service
    from app.services.user_profile_service import get_user_profile_service

    memory_service = get_memory_service()
    profile_service = get_user_profile_service()

    # Parse memory extractions
    memory_pattern = re.compile(
        r'\[MEMORY\s+category=(\w+)\s+importance=(\d+)\]\s*(.+)',
        re.IGNORECASE
    )

    for match in memory_pattern.finditer(content):
        category = match.group(1).lower()
        importance = int(match.group(2))
        memory_content = match.group(3).strip()

        if memory_content and len(memory_content) > 5:
            try:
                result = await memory_service.add_memory(
                    user_id=user_id,
                    content=memory_content,
                    category=category,
                    importance=importance,
                    source="extracted"
                )
                if result.get("success"):
                    logger.info(f"[AsyncExtract] Saved memory: {memory_content[:50]}...")
                else:
                    logger.debug(f"[AsyncExtract] Memory not saved: {result.get('error')}")
            except Exception as e:
                logger.warning(f"[AsyncExtract] Failed to save memory: {e}")

    # Parse profile extractions
    profile_pattern = re.compile(
        r'\[PROFILE\s+([\w.]+)\]\s*(.+)',
        re.IGNORECASE
    )

    profile_updates = []
    for match in profile_pattern.finditer(content):
        path = match.group(1).strip()
        value = match.group(2).strip()

        if path and value:
            profile_updates.append({
                "path": path,
                "value": value,
                "operation": "set"
            })

    if profile_updates:
        try:
            await profile_service.update_profile(
                user_id=user_id,
                updates=profile_updates,
                reason="Auto-extracted from conversation"
            )
            logger.info(f"[AsyncExtract] Saved {len(profile_updates)} profile updates")
        except Exception as e:
            logger.warning(f"[AsyncExtract] Failed to save profile updates: {e}")
