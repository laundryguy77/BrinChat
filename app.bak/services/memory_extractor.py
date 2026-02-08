"""Extract memory-worthy information from model responses.

When a model doesn't support native tool calling, or when we want to
automatically capture information the model learned about the user,
we can extract memory-relevant facts from its responses.
"""
import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# Structured format pattern for explicit memory tags in model output
STRUCTURED_MEMORY_PATTERN = re.compile(
    r'\[MEMORY(?:\s+SAVE)?\]\s*'
    r'(?:category\s*[=:]\s*(\w+)\s*[,;]?\s*)?'
    r'(?:importance\s*[=:]\s*(\d+)\s*[,;]?\s*)?'
    r'["\']?([^"\'\]\n]+)["\']?',
    re.IGNORECASE
)

# Alternative simpler format: [REMEMBER] content
SIMPLE_MEMORY_PATTERN = re.compile(
    r'\[REMEMBER\]\s*["\']?([^"\'\]\n]+)["\']?',
    re.IGNORECASE
)

# Patterns for extracting implicit memory-worthy information
# These detect when the model acknowledges learning something about the user
IMPLICIT_MEMORY_PATTERNS = {
    "personal": [
        # Name acknowledgment
        r"(?:nice to meet you|hello|hi),?\s+([A-Z][a-z]+)(?:\s*[!.,]|$)",
        r"(?:I'll remember|I've noted|noted that)\s+(?:your name is|you're called)\s+([A-Z][a-z]+)",
        # Location
        r"(?:I'll remember|I've noted|noted that)\s+you(?:'re| are) (?:in|from|based in)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
        r"(?:I see you're|so you're)\s+(?:in|from|based in)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    ],
    "preference": [
        # Explicit preference acknowledgment
        r"(?:I'll remember|I've noted|noted that|understood that)\s+you\s+(?:prefer|like|enjoy|love|hate|dislike)\s+([^.,!?]+)",
        r"(?:I'll keep in mind|I'll remember)\s+(?:that\s+)?you\s+(?:prefer|like|want)\s+([^.,!?]+)",
    ],
    "topic": [
        # Project/work acknowledgment
        r"(?:I'll remember|I've noted|noted that)\s+you(?:'re| are) working on\s+([^.,!?]+)",
        r"(?:I see you're|so you're)\s+working on\s+([^.,!?]+)",
        # Interest acknowledgment
        r"(?:I'll remember|I've noted|noted that)\s+you(?:'re| are) interested in\s+([^.,!?]+)",
    ],
    "instruction": [
        # Style/format preferences
        r"(?:I'll remember|I've noted|understood)\s+(?:to|that I should)\s+([^.,!?]+)",
        r"(?:I'll make sure to|I'll be sure to)\s+([^.,!?]+)\s+(?:from now on|going forward)",
    ]
}


def extract_memories(
    response_text: str,
    user_message: str,
    include_implicit: bool = True
) -> List[Dict[str, Any]]:
    """Extract memory-worthy information from a model response.

    Args:
        response_text: The model's response text
        user_message: The original user message (for context)
        include_implicit: Whether to include implicit extractions (default True)

    Returns:
        List of memory candidates: [{"content": "...", "category": "...", "importance": int, "source": "extracted"}]
    """
    memories = []
    seen_content = set()

    # First, check for structured [MEMORY] tags
    for match in STRUCTURED_MEMORY_PATTERN.finditer(response_text):
        category = match.group(1) or "general"
        importance = int(match.group(2)) if match.group(2) else 5
        content = match.group(3).strip()

        if content and content.lower() not in seen_content:
            # Validate category
            if category.lower() not in ("preference", "personal", "topic", "instruction", "general"):
                category = "general"
            # Clamp importance
            importance = max(1, min(10, importance))

            memories.append({
                "content": content,
                "category": category.lower(),
                "importance": importance,
                "source": "extracted"
            })
            seen_content.add(content.lower())
            logger.info(f"Extracted structured memory: [{category}] {content[:50]}...")

    # Check for simple [REMEMBER] tags
    for match in SIMPLE_MEMORY_PATTERN.finditer(response_text):
        content = match.group(1).strip()
        if content and content.lower() not in seen_content:
            # Infer category from content
            category = _infer_category(content)
            memories.append({
                "content": content,
                "category": category,
                "importance": 5,
                "source": "extracted"
            })
            seen_content.add(content.lower())
            logger.info(f"Extracted simple memory: {content[:50]}...")

    # Then try implicit extraction if enabled
    if include_implicit:
        for category, patterns in IMPLICIT_MEMORY_PATTERNS.items():
            for pattern in patterns:
                matches = re.finditer(pattern, response_text, re.IGNORECASE)
                for match in matches:
                    content = match.group(1).strip() if match.lastindex else match.group(0).strip()

                    # Clean up the content
                    content = _clean_extracted_content(content, category)

                    if content and len(content) >= 3 and content.lower() not in seen_content:
                        if _validate_extracted_memory(content, category):
                            # Format as proper memory content
                            formatted = _format_memory_content(content, category, user_message)
                            if formatted:
                                memories.append({
                                    "content": formatted,
                                    "category": category,
                                    "importance": _infer_importance(category, content),
                                    "source": "extracted"
                                })
                                seen_content.add(content.lower())
                                logger.info(f"Extracted implicit memory: [{category}] {formatted[:50]}...")

    return memories


def _infer_category(content: str) -> str:
    """Infer memory category from content."""
    content_lower = content.lower()

    if any(word in content_lower for word in ["name is", "called", "i'm", "i am"]):
        return "personal"
    elif any(word in content_lower for word in ["prefer", "like", "enjoy", "hate", "dislike", "favorite"]):
        return "preference"
    elif any(word in content_lower for word in ["working on", "project", "studying", "interested in"]):
        return "topic"
    elif any(word in content_lower for word in ["always", "never", "should", "want you to", "please"]):
        return "instruction"
    else:
        return "general"


def _infer_importance(category: str, content: str) -> int:
    """Infer importance based on category and content."""
    content_lower = content.lower()

    # Names are very important
    if category == "personal" and "name" in content_lower:
        return 9
    # Explicit instructions are important
    if category == "instruction":
        return 7
    # Strong preferences
    if any(word in content_lower for word in ["love", "hate", "always", "never"]):
        return 7
    # Regular preferences/topics
    if category in ("preference", "topic"):
        return 5

    return 5


def _clean_extracted_content(content: str, category: str) -> str:
    """Clean up extracted content."""
    # Remove trailing punctuation except for meaningful ones
    content = content.rstrip(".,;:")
    # Remove common filler words at start
    content = re.sub(r'^(?:that\s+|I\s+|you\s+)', '', content, flags=re.IGNORECASE)
    # Trim whitespace
    content = content.strip()
    return content


def _validate_extracted_memory(content: str, category: str) -> bool:
    """Validate extracted memory content."""
    # Too short or too long
    if len(content) < 3 or len(content) > 200:
        return False

    # Only whitespace or punctuation
    if not re.search(r'[a-zA-Z]', content):
        return False

    # Category-specific validation
    if category == "personal":
        # Names should be reasonably formatted
        if "name" in content.lower():
            # Should contain at least one capitalized word
            if not re.search(r'[A-Z][a-z]+', content):
                return False

    return True


def _format_memory_content(extracted: str, category: str, user_message: str) -> Optional[str]:
    """Format extracted content into a proper memory statement."""
    # For personal info (names, locations), format as "User's name is X"
    if category == "personal":
        # Check if it's a name
        name_match = re.search(r'^([A-Z][a-z]+)$', extracted)
        if name_match:
            return f"User's name is {name_match.group(1)}"
        # Check if it's a location
        if any(word in extracted.lower() for word in ["city", "town", "state", "country"]):
            return f"User is from {extracted}"
        return f"User info: {extracted}"

    # For preferences, format as "User prefers/likes X"
    if category == "preference":
        if not extracted.lower().startswith("user"):
            return f"User {extracted}"
        return extracted

    # For topics, format as "User is interested in / working on X"
    if category == "topic":
        if not extracted.lower().startswith("user"):
            return f"User is working on/interested in: {extracted}"
        return extracted

    # For instructions, format as "User wants X"
    if category == "instruction":
        if not extracted.lower().startswith("user"):
            return f"User prefers: {extracted}"
        return extracted

    return extracted


def get_memory_extraction_instructions() -> str:
    """Get instructions for models to output structured memory tags.

    These instructions tell the model to output structured memory tags
    that we can parse from the response.
    """
    return """
## REMEMBERING USER INFORMATION

When the user shares important information about themselves that you should remember
for future conversations, include a memory tag in your response:

Format: [MEMORY] category=TYPE, importance=N, "content to remember"

Categories:
- personal: Name, location, job, age, relationships
- preference: Likes, dislikes, favorites, style preferences
- topic: Projects, interests, hobbies, what they're working on
- instruction: How they want you to respond, format preferences

Importance (1-10):
- 10: Critical info like their name
- 7: Strong preferences, important facts
- 5: General preferences, casual mentions
- 3: Minor details

Examples:
- User says "I'm Alex" → [MEMORY] category=personal, importance=10, "User's name is Alex"
- User says "I hate long explanations" → [MEMORY] category=instruction, importance=7, "User prefers brief, concise responses"
- User mentions working on a React app → [MEMORY] category=topic, importance=5, "User is working on a React application"

Only add [MEMORY] tags for genuinely useful information, not casual small talk.
"""
