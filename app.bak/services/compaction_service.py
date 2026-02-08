"""Conversation compaction and summary generation service.

Implements a sliding context window that automatically compacts older
conversation history into summaries, allowing longer conversations
while staying within context limits.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.config import AppSettings
from app.services.ollama import ollama_service
from app.services.conversation_store import (
    conversation_store,
    CompactionRecord,
)

logger = logging.getLogger(__name__)

# Prompt for generating conversation summaries
COMPACTION_PROMPT = """Summarize this conversation, preserving:
- Key facts shared by the user
- Decisions and conclusions reached
- Specific requests or preferences
- Technical details that might be referenced

Keep under 200 words. Write as flowing text, not bullets.

CONVERSATION:
{conversation}

SUMMARY:"""


def estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English"""
    return len(text) // 4 + 1


def calculate_budgets(settings: AppSettings) -> Dict[str, int]:
    """Calculate token budgets based on settings.

    Returns:
        Dict with keys: total, summary_buffer, active_window, threshold
    """
    total = settings.num_ctx
    response_reserve = 1000  # Reserve for model response

    # Summary buffer is a percentage of total context
    summary_buffer = int(total * settings.compaction_buffer_percent / 100)

    # Active window is everything else minus response reserve
    active_window = total - summary_buffer - response_reserve

    # Threshold is when we trigger compaction
    threshold = int(active_window * settings.compaction_threshold_percent / 100)

    return {
        "total": total,
        "summary_buffer": summary_buffer,
        "active_window": active_window,
        "threshold": threshold,
        "response_reserve": response_reserve
    }


def should_compact(
    messages: List[Dict],
    settings: AppSettings,
    summary_tokens: int = 0
) -> Tuple[bool, List[Dict], List[int]]:
    """Determine if compaction is needed and which messages to compact.

    Args:
        messages: List of message dicts (API format)
        settings: App settings
        summary_tokens: Current summary token count

    Returns:
        Tuple of (should_compact, messages_to_compact, indices_to_compact)
    """
    if not settings.compaction_enabled:
        return False, [], []

    budgets = calculate_budgets(settings)

    # Calculate current token usage
    total_tokens = summary_tokens
    message_tokens = []
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, dict):
            import json
            content = json.dumps(content)
        tokens = estimate_tokens(str(content))
        message_tokens.append(tokens)
        total_tokens += tokens

    # Check if we exceed threshold
    if total_tokens <= budgets["threshold"]:
        return False, [], []

    # Find messages to compact
    # Skip: system prompt (index 0 if role=system), protected recent messages
    protected_count = settings.compaction_protected_messages

    # Identify start index (skip system message if present)
    start_idx = 1 if messages and messages[0].get("role") == "system" else 0

    # Identify end index (protect recent messages)
    end_idx = max(start_idx, len(messages) - protected_count)

    # Can't compact if protected region overlaps
    if end_idx <= start_idx:
        logger.debug("Cannot compact: protected region covers all messages")
        return False, [], []

    # Find active tool interactions (don't compact mid-chain)
    # Look for tool role from the end
    tool_start_idx = end_idx
    for i in range(len(messages) - 1, end_idx - 1, -1):
        if messages[i].get("role") == "tool":
            # Find the assistant message that triggered this tool
            for j in range(i - 1, -1, -1):
                if messages[j].get("role") == "assistant" and messages[j].get("tool_calls"):
                    tool_start_idx = min(tool_start_idx, j)
                    break

    # Adjust end_idx to not cut into active tool chain
    end_idx = min(end_idx, tool_start_idx)

    if end_idx <= start_idx:
        logger.debug("Cannot compact: tool chain or protected messages")
        return False, [], []

    # Select messages to compact (aim to reduce by ~30% of total)
    target_reduction = int(total_tokens * 0.3)
    compact_tokens = 0
    indices_to_compact = []

    for i in range(start_idx, end_idx):
        if compact_tokens >= target_reduction:
            break
        indices_to_compact.append(i)
        compact_tokens += message_tokens[i]

    if not indices_to_compact:
        return False, [], []

    messages_to_compact = [messages[i] for i in indices_to_compact]

    logger.info(
        f"Compaction needed: {total_tokens} tokens, "
        f"compacting {len(indices_to_compact)} messages ({compact_tokens} tokens)"
    )

    return True, messages_to_compact, indices_to_compact


def format_messages_for_summary(messages: List[Dict]) -> str:
    """Format messages into text for summarization."""
    lines = []
    for msg in messages:
        role = msg.get("role", "unknown").upper()
        content = msg.get("content", "")

        # Handle tool messages
        if role == "TOOL":
            tool_name = msg.get("tool_name", "tool")
            lines.append(f"[TOOL RESULT from {tool_name}]: {content[:500]}...")
        elif msg.get("tool_calls"):
            # Assistant with tool calls
            tool_names = [tc.get("function", {}).get("name", "?") for tc in msg["tool_calls"]]
            lines.append(f"ASSISTANT: [Called tools: {', '.join(tool_names)}] {content}")
        else:
            lines.append(f"{role}: {content}")

    return "\n\n".join(lines)


def _pick_summary_model(model: str) -> str:
    """Pick a model usable by Ollama for compaction summaries.

    BrinChat chat models may be OpenClaw agent selectors like `openclaw:main`.
    Those are NOT valid Ollama model IDs.

    We keep this conservative: use the configured tool model as a local summarizer.
    """
    from app.config import UNCENSORED_TOOL_MODEL

    if not model:
        return UNCENSORED_TOOL_MODEL

    if model.startswith("openclaw:") or model == "openclaw":
        return UNCENSORED_TOOL_MODEL

    return model


async def generate_summary(
    messages: List[Dict],
    model: str,
    existing_summary: Optional[str] = None
) -> Tuple[str, int]:
    """Generate a summary of messages using LLM.

    Args:
        messages: Messages to summarize
        model: Chat model (may be OpenClaw selector)
        existing_summary: Previous summary to merge with

    Returns:
        Tuple of (summary_text, token_count)
    """
    conversation_text = format_messages_for_summary(messages)

    # If there's an existing summary, include it
    if existing_summary:
        conversation_text = f"[PREVIOUS SUMMARY]\n{existing_summary}\n\n[NEW MESSAGES]\n{conversation_text}"

    prompt = COMPACTION_PROMPT.format(conversation=conversation_text)

    try:
        response = await ollama_service.chat_complete(
            messages=[
                {"role": "system", "content": "You are a precise summarizer. Create concise summaries."},
                {"role": "user", "content": prompt}
            ],
            model=_pick_summary_model(model),
            options={
                "temperature": 0.3,  # Low temperature for consistent summaries
                "num_ctx": 2048  # Small context for summary generation
            }
        )

        summary = response.get("message", {}).get("content", "").strip()

        # Quality check: if summary is too long, it might be worse than original
        summary_tokens = estimate_tokens(summary)
        original_tokens = estimate_tokens(conversation_text)

        if summary_tokens > original_tokens * 0.5:
            logger.warning(
                f"Summary quality check failed: {summary_tokens} tokens "
                f"(>{original_tokens * 0.5:.0f} = 50% of original)"
            )
            # Try to truncate intelligently
            if len(summary) > 800:
                summary = summary[:800] + "..."
                summary_tokens = estimate_tokens(summary)

        return summary, summary_tokens

    except Exception as e:
        logger.error(f"Failed to generate summary: {e}")
        # Fallback: simple truncation
        fallback = f"[Earlier conversation about: {conversation_text[:200]}...]"
        return fallback, estimate_tokens(fallback)


async def compact_conversation(
    conv_id: str,
    messages: List[Dict],
    indices_to_compact: List[int],
    model: str,
    existing_summary: Optional[str] = None,
    existing_summary_tokens: int = 0,
    user_id: Optional[int] = None
) -> Optional[CompactionRecord]:
    """Perform compaction on a conversation.

    Args:
        conv_id: Conversation ID
        messages: All messages (API format)
        indices_to_compact: Indices of messages to compact
        model: Model for summarization
        existing_summary: Previous summary to merge
        existing_summary_tokens: Token count of existing summary
        user_id: User ID for ownership verification (required for security)

    Returns:
        CompactionRecord if successful, None otherwise

    Security: If user_id is provided, verifies that the conversation
    belongs to that user before performing compaction.
    """
    if not indices_to_compact:
        return None

    messages_to_compact = [messages[i] for i in indices_to_compact]

    # Calculate original token count
    original_tokens = sum(
        estimate_tokens(str(m.get("content", "")))
        for m in messages_to_compact
    )

    # Generate new summary (merging with existing if present)
    new_summary, summary_tokens = await generate_summary(
        messages_to_compact,
        model,
        existing_summary
    )

    # Get message IDs from the conversation store
    conv = conversation_store.get(conv_id)
    if not conv:
        logger.error(f"Conversation {conv_id} not found")
        return None

    # SECURITY: Verify ownership if user_id is provided
    if user_id is not None and conv.user_id != user_id:
        logger.error(
            f"Compaction denied: user {user_id} attempted to compact "
            f"conversation {conv_id} owned by user {conv.user_id}"
        )
        return None

    # Map indices to message IDs
    # CRITICAL: The indices_to_compact are indices into the API message list,
    # which includes a dynamically-added system prompt at index 0.
    # But conv.messages does NOT include this system prompt.
    # So we need to adjust: if messages[0] is role=system, subtract 1 from all indices.
    message_ids = []

    # Determine the offset: if messages has system at index 0, indices are off by 1
    has_system_prefix = (
        messages and
        len(messages) > 0 and
        messages[0].get("role") == "system"
    )
    offset = 1 if has_system_prefix else 0

    for idx in indices_to_compact:
        # Adjust index to account for system message offset
        conv_idx = idx - offset
        if 0 <= conv_idx < len(conv.messages):
            message_ids.append(conv.messages[conv_idx].id)
        else:
            logger.warning(
                f"Compaction index {idx} (adjusted to {conv_idx}) "
                f"out of range for conversation with {len(conv.messages)} messages"
            )

    # Create compaction record
    record = CompactionRecord(
        id=str(uuid.uuid4())[:8],
        created_at=datetime.now().isoformat(),
        summary=new_summary,
        message_ids=message_ids,
        token_count=summary_tokens,
        original_token_count=original_tokens + existing_summary_tokens
    )

    # Update conversation store
    await conversation_store.add_compaction(conv_id, record)
    await conversation_store.update_summary(conv_id, new_summary, summary_tokens)
    await conversation_store.mark_messages_compacted(conv_id, message_ids)

    logger.info(
        f"Compacted {len(message_ids)} messages: "
        f"{original_tokens + existing_summary_tokens} -> {summary_tokens} tokens"
    )

    return record


def build_compacted_messages(
    messages: List[Dict],
    summary: Optional[str],
    indices_compacted: List[int]
) -> List[Dict]:
    """Build message list with summary replacing compacted messages.

    Args:
        messages: Original messages
        summary: Summary text to insert
        indices_compacted: Indices of messages that were compacted

    Returns:
        New message list with summary
    """
    if not summary or not indices_compacted:
        return messages

    result = []
    compacted_set = set(indices_compacted)

    # Add system message if present
    if messages and messages[0].get("role") == "system":
        result.append(messages[0])
        start_idx = 1
    else:
        start_idx = 0

    # Add summary as a system message after the main system prompt
    result.append({
        "role": "system",
        "content": f"[CONVERSATION SUMMARY]\n{summary}\n[END SUMMARY - Recent messages follow]"
    })

    # Add non-compacted messages
    for i in range(start_idx, len(messages)):
        if i not in compacted_set:
            result.append(messages[i])

    return result
