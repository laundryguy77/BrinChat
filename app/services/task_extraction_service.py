"""
Task Extraction Service

Automatically extracts task requests from non-primary users and creates
Nextcloud Deck cards for Brin to process.
"""
import httpx
import json
import logging
import re
from typing import Optional, Dict, Any, Tuple

from app.config import (
    NEXTCLOUD_URL, NEXTCLOUD_USER, NEXTCLOUD_PASS,
    DECK_BOARD_ID, DECK_BACKLOG_STACK_ID,
    OLLAMA_BASE_URL, EXTRACTION_MODEL
)

logger = logging.getLogger(__name__)


class TaskExtractionService:
    """Service for extracting tasks from user messages and creating Deck cards."""
    
    def __init__(self):
        self.deck_url = f"{NEXTCLOUD_URL}/index.php/apps/deck/api/v1.0"
        self.auth = (NEXTCLOUD_USER, NEXTCLOUD_PASS)
        self.ollama_url = OLLAMA_BASE_URL
        self.extraction_model = EXTRACTION_MODEL
        self.client = httpx.AsyncClient(timeout=30.0)
        
        # Task detection keywords
        self.task_keywords = [
            "can you", "could you", "please", "i need", "i want",
            "would you", "help me", "do this", "make", "create",
            "fix", "update", "change", "add", "remove", "set up",
            "configure", "install", "build", "write", "generate"
        ]
    
    def _is_potential_task(self, message: str) -> bool:
        """Quick check if message might be a task request."""
        msg_lower = message.lower()
        
        # Check for task keywords
        for keyword in self.task_keywords:
            if keyword in msg_lower:
                return True
        
        # Check for imperative sentences (start with verb-like words)
        first_word = msg_lower.split()[0] if msg_lower.split() else ""
        imperative_starters = ["add", "create", "make", "fix", "update", "change",
                               "remove", "delete", "set", "configure", "install",
                               "build", "write", "generate", "run", "check"]
        if first_word in imperative_starters:
            return True
        
        return False
    
    async def extract_task(self, user_message: str, assistant_response: str, username: str) -> Optional[Dict[str, str]]:
        """Extract task details from a conversation using the extraction model.
        
        Returns:
            Dict with 'title' and 'description' if a task was detected, None otherwise.
        """
        # Quick pre-filter
        if not self._is_potential_task(user_message):
            logger.debug(f"Message doesn't appear to be a task request")
            return None
        
        # Use Ollama to extract task details
        prompt = f"""Analyze this conversation and determine if the user is requesting a task to be done.

USER MESSAGE: {user_message}

ASSISTANT RESPONSE: {assistant_response[:500]}

If this is a task request, extract:
1. A short title (max 60 chars)
2. A description with full details

Respond with JSON only:
- If it IS a task request: {{"is_task": true, "title": "Short task title", "description": "Full task description"}}
- If it is NOT a task request (just a question or conversation): {{"is_task": false}}

Be conservative - only mark as task if the user is clearly asking for something to be DONE, not just asking a question."""

        try:
            response = await self.client.post(
                f"{self.ollama_url}/api/generate",
                json={
                    "model": self.extraction_model,
                    "prompt": prompt,
                    "stream": False,
                    "options": {"temperature": 0.1}
                }
            )
            
            if response.status_code != 200:
                logger.warning(f"Ollama extraction failed: {response.status_code}")
                return None
            
            result = response.json()
            content = result.get("response", "")
            
            # Parse JSON from response
            json_match = re.search(r'\{[^}]+\}', content, re.DOTALL)
            if json_match:
                data = json.loads(json_match.group())
                if data.get("is_task"):
                    task = {
                        "title": data.get("title", user_message[:60]),
                        "description": data.get("description", user_message),
                        "requester": username
                    }
                    logger.info(f"Extracted task from {username}: {task['title']}")
                    return task
            
            return None
            
        except Exception as e:
            logger.warning(f"Task extraction failed: {e}")
            return None
    
    async def create_deck_card(self, task: Dict[str, str]) -> Optional[Dict[str, Any]]:
        """Create a Nextcloud Deck card for the extracted task.
        
        Args:
            task: Dict with 'title', 'description', and 'requester' keys.
            
        Returns:
            Created card data or None on failure.
        """
        if not NEXTCLOUD_PASS:
            logger.warning("Nextcloud credentials not configured, skipping card creation")
            return None
        
        # Build card data
        title = f"[BrinChat] {task['title']}"
        description = f"""**Requested by:** {task['requester']} (via BrinChat)

**Request:**
{task['description']}

---
*This task was automatically created from a BrinChat conversation.*
"""
        
        try:
            response = await self.client.post(
                f"{self.deck_url}/boards/{DECK_BOARD_ID}/stacks/{DECK_BACKLOG_STACK_ID}/cards",
                auth=self.auth,
                headers={
                    "Content-Type": "application/json",
                    "OCS-APIRequest": "true"
                },
                json={
                    "title": title[:255],  # Deck has title length limit
                    "description": description,
                    "type": "plain"
                }
            )
            
            if response.status_code in (200, 201):
                card = response.json()
                logger.info(f"Created Deck card #{card.get('id')}: {title}")
                return card
            else:
                logger.error(f"Failed to create Deck card: {response.status_code} - {response.text[:200]}")
                return None
                
        except Exception as e:
            logger.error(f"Error creating Deck card: {e}")
            return None
    
    async def process_conversation(
        self,
        user_id: int,
        username: str,
        user_message: str,
        assistant_response: str,
        is_primary_user: bool
    ) -> Optional[Dict[str, Any]]:
        """Process a conversation and create a Deck card if it's a task from non-primary user.
        
        Args:
            user_id: BrinChat user ID
            username: BrinChat username
            user_message: The user's message
            assistant_response: Brin's response
            is_primary_user: Whether this is Joel (primary user)
            
        Returns:
            Created card data or None
        """
        # Primary user doesn't get task extraction - they have full access
        if is_primary_user:
            logger.debug(f"Skipping task extraction for primary user {username}")
            return None
        
        # Extract task from conversation
        task = await self.extract_task(user_message, assistant_response, username)
        if not task:
            return None
        
        # Create Deck card
        card = await self.create_deck_card(task)
        return card
    
    async def close(self):
        """Close the HTTP client."""
        await self.client.aclose()


# Global service instance
_task_service: Optional[TaskExtractionService] = None


def get_task_extraction_service() -> TaskExtractionService:
    """Get the global task extraction service instance."""
    global _task_service
    if _task_service is None:
        _task_service = TaskExtractionService()
    return _task_service
