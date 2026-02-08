"""
Trigger Scanner for BrinChat Adult Mode

Scans messages for patterns that indicate tool usage is needed.
When triggers are detected, the message should route to OpenClaw (Brin)
instead of Ollama (Lexi) to ensure tool access.

Design principle: PREFER FALSE POSITIVES OVER FALSE NEGATIVES
- Better to route to Brin unnecessarily than to miss a tool request
- Lexi can't generate images, search the web, etc.
"""

import re
from typing import List, Dict, Pattern


# Compiled regex patterns for tool detection
# These patterns are intentionally broad to catch edge cases
TOOL_TRIGGERS: Dict[str, Pattern[str]] = {
    # Image generation requests (with explicit image keywords)
    "image_gen": re.compile(
        r"(?:generate|create|make|draw|show)\b.*(?:image|picture|photo|pic)",
        re.IGNORECASE
    ),
    # Draw requests (standalone - "draw me a dragon", "draw a cat")
    "draw_request": re.compile(
        r"\bdraw\b\s+(?:me\s+)?(?:a|an|the|some)\b",
        re.IGNORECASE
    ),
    # Image description requests (user describing what they want to see)
    "image_desc": re.compile(
        r"\b(?:image|picture|photo|pic)\b.*(?:of|with|showing)",
        re.IGNORECASE
    ),
    # Video generation
    "video_gen": re.compile(
        r"(?:generate|create|make)\b.*video",
        re.IGNORECASE
    ),
    # Animation requests
    "animation": re.compile(
        r"\b(?:animate|animation)\b",
        re.IGNORECASE
    ),
    # Web search requests
    "web_search": re.compile(
        r"(?:search|look up|find|google)\b",
        re.IGNORECASE
    ),
}

# Additional broad patterns to catch more edge cases
# These are kept separate for easy tuning
BROAD_TRIGGERS: Dict[str, Pattern[str]] = {
    # Direct tool mentions
    "tool_mention": re.compile(
        r"\b(?:use a tool|with tools|tool access|generate me)\b",
        re.IGNORECASE
    ),
    # File operations that need Brin
    "file_ops": re.compile(
        r"\b(?:save|upload|download|send me a file)\b",
        re.IGNORECASE
    ),
    # Explicit image keywords
    "image_keyword": re.compile(
        r"\b(?:selfie|nude|naked|photo of you|picture of yourself)\b",
        re.IGNORECASE
    ),
}


class TriggerScanner:
    """
    Scans messages for patterns indicating tool usage is required.
    
    Used in Adult Mode to determine whether a message should route to:
    - OpenClaw (Brin): Has tool access, can generate images, search web, etc.
    - Ollama (Lexi): Uncensored but no tool access
    
    Design principle: Prefer false positives over false negatives.
    It's better to route to Brin unnecessarily than to have Lexi fail
    on a task she can't complete.
    
    Example:
        scanner = TriggerScanner()
        if scanner.has_tool_triggers("draw me a picture"):
            # Route to Brin
        else:
            # Route to Lexi
    """
    
    def __init__(self, include_broad: bool = True):
        """
        Initialize the trigger scanner.
        
        Args:
            include_broad: If True, include broader catch-all patterns.
                          Set to False for stricter matching (not recommended).
        """
        self._patterns: Dict[str, Pattern[str]] = dict(TOOL_TRIGGERS)
        if include_broad:
            self._patterns.update(BROAD_TRIGGERS)
    
    def has_tool_triggers(self, message: str) -> bool:
        """
        Check if message contains any tool trigger patterns.
        
        Args:
            message: The user message to scan
            
        Returns:
            True if ANY pattern matches, False otherwise
            
        Note:
            Returns True on first match for performance.
            Use get_matched_triggers() if you need all matches.
        """
        if not message:
            return False
            
        for pattern in self._patterns.values():
            if pattern.search(message):
                return True
        return False
    
    def get_matched_triggers(self, message: str) -> List[str]:
        """
        Get list of all matched trigger pattern names.
        
        Useful for debugging and logging to understand why a message
        was routed to Brin.
        
        Args:
            message: The user message to scan
            
        Returns:
            List of pattern names that matched (e.g., ["image_gen", "web_search"])
            Empty list if no matches.
        """
        if not message:
            return []
            
        matches: List[str] = []
        for name, pattern in self._patterns.items():
            if pattern.search(message):
                matches.append(name)
        return matches
    
    def scan_with_details(self, message: str) -> Dict[str, bool]:
        """
        Scan message and return detailed match results for each pattern.
        
        Useful for debugging pattern effectiveness.
        
        Args:
            message: The user message to scan
            
        Returns:
            Dict mapping pattern names to whether they matched
        """
        if not message:
            return {name: False for name in self._patterns}
            
        return {
            name: bool(pattern.search(message))
            for name, pattern in self._patterns.items()
        }


# Module-level convenience instance
_default_scanner = TriggerScanner()


def has_tool_triggers(message: str) -> bool:
    """Convenience function using default scanner."""
    return _default_scanner.has_tool_triggers(message)


def get_matched_triggers(message: str) -> List[str]:
    """Convenience function using default scanner."""
    return _default_scanner.get_matched_triggers(message)


if __name__ == "__main__":
    """Test the trigger scanner with example messages."""
    
    scanner = TriggerScanner()
    
    # Test messages - mix of should-match and should-not-match
    test_messages = [
        # Should match (tool needed)
        ("generate an image of a sunset", True, "image_gen"),
        ("can you draw me a picture?", True, "image_gen"),
        ("make a photo of a cat", True, "image_gen"),
        ("show me an image of mountains", True, "image_gen"),
        ("I want a picture of you", True, "image_desc"),
        ("create a video of dancing", True, "video_gen"),
        ("make an animation", True, "animation"),
        ("search for Python tutorials", True, "web_search"),
        ("can you look up the weather?", True, "web_search"),
        ("google best restaurants nearby", True, "web_search"),
        ("find information about AI", True, "web_search"),
        ("send me a nude selfie", True, "image_keyword"),
        
        # Should NOT match (pure chat, Lexi can handle)
        ("hello, how are you?", False, None),
        ("tell me a story", False, None),
        ("what's your favorite color?", False, None),
        ("I love you", False, None),
        ("describe yourself", False, None),
        ("what do you think about politics?", False, None),
    ]
    
    print("=" * 60)
    print("TriggerScanner Test Suite")
    print("=" * 60)
    print()
    
    passed = 0
    failed = 0
    
    for message, expected_match, expected_trigger in test_messages:
        result = scanner.has_tool_triggers(message)
        matches = scanner.get_matched_triggers(message)
        
        status = "✓" if result == expected_match else "✗"
        if result == expected_match:
            passed += 1
        else:
            failed += 1
        
        print(f"{status} \"{message[:40]}{'...' if len(message) > 40 else ''}\"")
        print(f"  Expected: {expected_match}, Got: {result}")
        if matches:
            print(f"  Matched: {matches}")
        print()
    
    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed")
    print("=" * 60)
    
    # Edge case testing
    print("\nEdge Cases:")
    print("-" * 40)
    
    edge_cases = [
        "",  # Empty string
        "   ",  # Whitespace only
        "IMAGE",  # Caps without context
        "I saw a picture yesterday",  # Past tense, no request
        "The animation was great",  # Past tense, no request
    ]
    
    for msg in edge_cases:
        result = scanner.has_tool_triggers(msg)
        matches = scanner.get_matched_triggers(msg)
        print(f"'{msg}' -> {result} {matches if matches else ''}")
