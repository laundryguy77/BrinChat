"""Extract profile updates from model responses for non-tool-capable models.

When a model doesn't support native tool calling, we can still extract
profile-relevant information from its responses and save it to the user profile.
"""
import re
import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


# Patterns for extracting profile information from natural language
EXTRACTION_PATTERNS = {
    # Identity extraction
    "identity.preferred_name": [
        r"(?:your name is|I'll call you|you(?:'re| are) called|nice to meet you,?)\s+([A-Z][a-z]+)",
        r"(?:name|called):\s*([A-Z][a-z]+)",
    ],
    "identity.timezone": [
        r"(?:you(?:'re| are) in|your timezone is|located in)\s+([A-Z][a-z]+(?:/[A-Z][a-z_]+)?)\s+(?:timezone|time zone)",
        r"timezone:\s*([A-Z][a-z]+(?:/[A-Z][a-z_]+)?)",
    ],
    "identity.city": [
        r"(?:you(?:'re| are) (?:in|from|located in)|live in)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)",
    ],
    # Communication preferences
    "communication.conversation_style": [
        r"(?:you prefer|you like)\s+(casual|professional|friendly|formal|playful)\s+(?:conversation|communication|style)",
        r"conversation.?style:\s*(casual|professional|friendly|formal|playful)",
    ],
    "communication.response_length": [
        r"(?:you prefer|you like)\s+(brief|detailed|concise|verbose)\s+(?:response|answer)",
        r"response.?length:\s*(brief|detailed|moderate|verbose|adaptive)",
    ],
    # Technical preferences
    "technical.primary_languages": [
        r"(?:you (?:use|work with|code in|program in))\s+(Python|JavaScript|TypeScript|Rust|Go|Java|C\+\+|Ruby|PHP)",
        r"(?:favorite|primary|main)\s+(?:programming\s+)?language[s]?:\s*(Python|JavaScript|TypeScript|Rust|Go|Java)",
    ],
    "technical.skill_level": [
        r"(?:you(?:'re| are) (?:a|an)?)\s*(beginner|intermediate|advanced|expert|senior)\s+(?:developer|programmer|coder)",
        r"skill.?level:\s*(beginner|intermediate|advanced|expert)",
    ],
}

# Structured format patterns (for explicit profile updates in model output)
STRUCTURED_UPDATE_PATTERN = re.compile(
    r'\[PROFILE(?:\s+UPDATE)?\]\s*(\w+(?:\.\w+)*)\s*[=:]\s*["\']?([^"\'\]\n]+)["\']?',
    re.IGNORECASE
)


def extract_profile_updates(
    response_text: str,
    user_message: str
) -> List[Dict[str, Any]]:
    """Extract profile updates from a model response.

    Args:
        response_text: The model's response text
        user_message: The original user message (for context)

    Returns:
        List of updates in the format: [{"path": "...", "value": ..., "operation": "set"}]
    """
    updates = []
    seen_paths = set()

    # First, check for structured [PROFILE] updates
    structured_matches = STRUCTURED_UPDATE_PATTERN.findall(response_text)
    for path, value in structured_matches:
        path = path.lower()
        value = value.strip()
        if path not in seen_paths and value:
            updates.append({
                "path": path,
                "value": value,
                "operation": "set"
            })
            seen_paths.add(path)
            logger.info(f"Extracted structured profile update: {path} = {value}")

    # Then, try natural language extraction
    combined_text = f"{user_message}\n{response_text}"

    for field_path, patterns in EXTRACTION_PATTERNS.items():
        if field_path in seen_paths:
            continue

        for pattern in patterns:
            match = re.search(pattern, combined_text, re.IGNORECASE)
            if match:
                value = match.group(1).strip()

                # Validate the extracted value
                if _validate_extracted_value(field_path, value):
                    # Handle array fields
                    if field_path == "technical.primary_languages":
                        updates.append({
                            "path": field_path,
                            "value": value,
                            "operation": "append"
                        })
                    else:
                        updates.append({
                            "path": field_path,
                            "value": value,
                            "operation": "set"
                        })
                    seen_paths.add(field_path)
                    logger.info(f"Extracted NL profile update: {field_path} = {value}")
                    break

    return updates


def _validate_extracted_value(path: str, value: str) -> bool:
    """Validate an extracted value before saving."""
    if not value or len(value) > 100:
        return False

    # Path-specific validation
    if path == "identity.preferred_name":
        # Names should be simple, no special characters
        if not re.match(r'^[A-Za-z][A-Za-z\s\-]{0,30}$', value):
            return False

    elif path == "communication.conversation_style":
        if value.lower() not in ("casual", "professional", "friendly", "formal", "playful", "candid_direct"):
            return False

    elif path == "communication.response_length":
        if value.lower() not in ("brief", "moderate", "detailed", "verbose", "adaptive"):
            return False

    elif path == "technical.skill_level":
        if value.lower() not in ("beginner", "intermediate", "advanced", "expert", "senior"):
            return False

    return True


def get_non_tool_profile_instructions() -> str:
    """Get profile instructions for models without tool support.

    These instructions tell the model to output structured profile updates
    that we can parse from the response.
    """
    return """
## LEARNING ABOUT THE USER

When the user shares information about themselves (name, preferences, etc.),
acknowledge it naturally and include a structured update tag:

Format: [PROFILE] field.path = value

Examples:
- User says "I'm John" → [PROFILE] identity.preferred_name = John
- User says "I prefer brief answers" → [PROFILE] communication.response_length = brief
- User mentions coding in Python → [PROFILE] technical.primary_languages = Python

Common fields:
- identity.preferred_name, identity.city, identity.timezone
- communication.conversation_style (casual/professional/friendly)
- communication.response_length (brief/detailed/adaptive)
- technical.primary_languages, technical.skill_level
- persona_preferences.formality_level (casual/formal)

Only include [PROFILE] tags when the user explicitly shares new information.
"""
