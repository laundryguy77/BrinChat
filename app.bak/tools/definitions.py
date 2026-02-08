WEB_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "web_search",
        "description": "Search the web and read page content to find information. Automatically fetches and reads the top search results to provide comprehensive answers.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to look up on the web. Be specific and include relevant keywords."
                }
            },
            "required": ["query"]
        }
    }
}

CONVERSATION_SEARCH_TOOL = {
    "type": "function",
    "function": {
        "name": "search_conversations",
        "description": "Search through previous conversations for context and information. Use this when the user references something they discussed before, or when you need to recall information from past chats. Returns relevant snippets from past conversations.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find in past conversations. Use keywords related to the topic the user is asking about."
                }
            },
            "required": ["query"]
        }
    }
}

BROWSE_WEBSITE_TOOL = {
    "type": "function",
    "function": {
        "name": "browse_website",
        "description": "Visit a specific URL and retrieve its content. Use this when you need to access a webpage directly (e.g., when given a URL, reading documentation, accessing a specific article). Returns the page content as readable text.",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "The full URL to visit (must start with http:// or https://)"
                }
            },
            "required": ["url"]
        }
    }
}

KNOWLEDGE_BASE_TOOL = {
    "type": "function",
    "function": {
        "name": "search_knowledge_base",
        "description": "Search through the user's uploaded documents and files in the knowledge base. Use this to find information from PDFs, code files, text documents, and other files the user has uploaded. Returns relevant excerpts with source attribution.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The search query to find in the knowledge base. Use keywords related to what you're looking for."
                }
            },
            "required": ["query"]
        }
    }
}

ADD_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "add_memory",
        "description": "Store important information about the user for future reference. Use this when learning something significant about the user's preferences, personal details, or topics they care about. CRITICAL: If the user explicitly asks you to remember something (e.g., 'remember that...', 'don't forget...', 'keep in mind...'), use this tool IMMEDIATELY with source='explicit'.",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "The information to remember. Be concise but complete."
                },
                "category": {
                    "type": "string",
                    "enum": ["preference", "personal", "topic", "instruction"],
                    "description": "Category: 'preference' for likes/dislikes, 'personal' for name/location/job, 'topic' for interests/projects, 'instruction' for how they prefer things done"
                },
                "importance": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 10,
                    "description": "How important (1-10). Name=10, preferences=7, casual mentions=3."
                },
                "source": {
                    "type": "string",
                    "enum": ["explicit", "inferred"],
                    "description": "Set to 'explicit' if user directly asked you to remember this. Set to 'inferred' if you're proactively saving something they mentioned."
                }
            },
            "required": ["content", "category"]
        }
    }
}

QUERY_MEMORY_TOOL = {
    "type": "function",
    "function": {
        "name": "query_memory",
        "description": "Search your memory for relevant information about the user. Use this to recall their preferences, personal details, projects, or past discussions.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "What to search for in memory. Be descriptive."
                }
            },
            "required": ["query"]
        }
    }
}

SET_CONVERSATION_TITLE_TOOL = {
    "type": "function",
    "function": {
        "name": "set_conversation_title",
        "description": "Set or update the title of the current conversation. Use this proactively when: 1) The conversation topic becomes clear, 2) The topic significantly changes, 3) A more descriptive title would help the user find this conversation later. Keep titles concise (3-8 words) and descriptive of the main topic or task.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The new title for the conversation. Should be concise (3-8 words) and describe the main topic or purpose."
                }
            },
            "required": ["title"]
        }
    }
}

# Consolidated User Profile Tool (replaces 8 individual profile tools)
USER_PROFILE_TOOL = {
    "type": "function",
    "function": {
        "name": "user_profile",
        "description": "Manage user profile: read sections, update fields, log events, query info. Actions: read, update, log_event, enable_section, add_nested, query, export, reset.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["read", "update", "log_event", "enable_section", "add_nested", "query", "export", "reset"],
                    "description": "Action to perform: read=load profile sections, update=modify fields, log_event=track interactions, enable_section=toggle sensitive sections, add_nested=add to nested objects, query=ask about profile, export=export data, reset=restore defaults"
                },
                # Read action params
                "sections": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "[read/reset] Sections to read/reset. Use ['all'] for complete profile."
                },
                "include_disabled": {
                    "type": "boolean",
                    "description": "[read] Include disabled sections"
                },
                # Update action params
                "updates": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Dot-notation path (e.g. 'identity.preferred_name')"},
                            "value": {"description": "New value"},
                            "operation": {"type": "string", "enum": ["set", "append", "remove", "increment", "decrement", "toggle"]}
                        }
                    },
                    "description": "[update] Array of field updates"
                },
                "reason": {
                    "type": "string",
                    "description": "[update] Why this update is being made"
                },
                # Log event params
                "event_type": {
                    "type": "string",
                    "description": "[log_event] Event type (praise, frustration, task_completed, etc.)"
                },
                "context": {
                    "type": "string",
                    "description": "[log_event/add_nested] Brief context or value"
                },
                "severity": {
                    "type": "string",
                    "enum": ["minor", "moderate", "major"],
                    "description": "[log_event] Event severity"
                },
                # Enable section params
                "section": {
                    "type": "string",
                    "description": "[enable_section/add_nested] Section name"
                },
                "enabled": {
                    "type": "boolean",
                    "description": "[enable_section] Enable or disable"
                },
                # Add nested params
                "domain": {
                    "type": "string",
                    "description": "[add_nested] Domain/category key"
                },
                "key": {
                    "type": "string",
                    "description": "[add_nested] Specific key within domain"
                },
                "value": {
                    "type": "string",
                    "description": "[add_nested] Value to set"
                },
                # Query params
                "query": {
                    "type": "string",
                    "description": "[query] Natural language question about the user"
                },
                # Export params
                "format": {
                    "type": "string",
                    "enum": ["json", "yaml", "summary"],
                    "description": "[export] Export format"
                },
                "tier": {
                    "type": "string",
                    "enum": ["public", "exportable", "full"],
                    "description": "[export] Visibility tier"
                },
                # Common confirmation params
                "user_confirmed": {
                    "type": "boolean",
                    "description": "[enable_section/export/reset] User explicitly confirmed"
                },
                # Reset params
                "preserve_identity": {
                    "type": "boolean",
                    "description": "[reset] Keep identity on full reset"
                },
                "confirmation_phrase": {
                    "type": "string",
                    "description": "[reset] User's confirmation phrase"
                }
            },
            "required": ["action"]
        }
    }
}

# Consolidated Video Tool (replaces 3 individual video tools)
VIDEO_TOOL = {
    "type": "function",
    "function": {
        "name": "video",
        "description": "Generate videos from text or animate images. Actions: generate (text-to-video), animate (image-to-video). Takes 1-5 minutes.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["generate", "animate"],
                    "description": "Action: generate=create video from text prompt, animate=turn image into video"
                },
                "prompt": {
                    "type": "string",
                    "description": "Text description of video to generate, or motion description for animation"
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Things to avoid (e.g., 'blurry, low quality, distorted')"
                },
                "duration": {
                    "type": "number",
                    "description": "[generate] Video duration in seconds (2-10)"
                },
                "image_base64": {
                    "type": "string",
                    "description": "[animate] Base64-encoded source image to animate"
                }
            },
            "required": ["action", "prompt"]
        }
    }
}

# Consolidated Image Tool (replaces 4 individual image tools)
IMAGE_TOOL = {
    "type": "function",
    "function": {
        "name": "image",
        "description": "Generate and manipulate images. Actions: generate (text-to-image), transform (image-to-image), inpaint (edit regions), upscale (enhance resolution). Takes 30-120 seconds.",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["generate", "transform", "inpaint", "upscale"],
                    "description": "Action: generate=create from text, transform=modify image with prompt, inpaint=edit masked regions, upscale=enhance resolution"
                },
                "prompt": {
                    "type": "string",
                    "description": "Text description for generation/transformation/inpainting"
                },
                "negative_prompt": {
                    "type": "string",
                    "description": "Things to avoid in the result"
                },
                "image_base64": {
                    "type": "string",
                    "description": "[transform/inpaint/upscale] Base64-encoded source image"
                },
                "mask_base64": {
                    "type": "string",
                    "description": "[inpaint] Base64 mask (white=edit, black=keep)"
                },
                "width": {
                    "type": "integer",
                    "description": "[generate] Image width in pixels (256-2048, default: 1024)"
                },
                "height": {
                    "type": "integer",
                    "description": "[generate] Image height in pixels (256-2048, default: 1024)"
                },
                "strength": {
                    "type": "number",
                    "description": "[transform] How much to change (0.0-1.0, default: 0.7)"
                },
                "scale": {
                    "type": "number",
                    "description": "[upscale] Scale factor (2.0 or 4.0, default: 2.0)"
                }
            },
            "required": ["action"]
        }
    }
}

# All available tools (BrinChat: web_search and browse_website removed - handled by OpenClaw)
# Kept: Knowledge base, Memory, Conversation mgmt, Media generation, User profile
ALL_TOOLS = [
    # Core tools (2) - web_search/browse_website handled by OpenClaw
    CONVERSATION_SEARCH_TOOL,
    KNOWLEDGE_BASE_TOOL,
    # Memory tools (2)
    ADD_MEMORY_TOOL,
    QUERY_MEMORY_TOOL,
    # Conversation management (1)
    SET_CONVERSATION_TITLE_TOOL,
    # Media generation - consolidated (2)
    IMAGE_TOOL,
    VIDEO_TOOL,
    # User profile - consolidated (1)
    USER_PROFILE_TOOL,
]

def get_tools_for_model(supports_tools: bool = True, supports_vision: bool = False) -> list:
    """Get available tools filtered by model capabilities.

    Args:
        supports_tools: Whether the model supports tool calling
        supports_vision: Whether the model supports vision/images

    Returns:
        List of available tools in OpenAI function calling format
    """
    if not supports_tools:
        return []

    tools = ALL_TOOLS.copy()

    # Filter vision-only tools if model doesn't support vision
    if not supports_vision:
        tools = [t for t in tools if t.get('function', {}).get('name') != 'analyze_image']

    return tools
