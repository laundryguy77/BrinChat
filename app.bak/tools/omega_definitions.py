"""
Omega Tool Definitions

This module defines tool schemas that Omega (the planning model) uses to construct
tool calls. BrinChat executes these tools - Omega only plans them.

SECURITY: Use ${VAR_NAME} syntax for secrets. BrinChat substitutes these at
execution time. Omega never sees actual API keys.
"""

from typing import Dict, Any, List, Optional


OMEGA_TOOLS: Dict[str, Dict[str, Any]] = {
    "image": {
        "name": "image",  # Matches omega_service output
        "description": "Generate an image from a text prompt. Use for creating pictures, artwork, photos, illustrations, or any visual content the user requests.",
        "endpoint": "https://fal.run/fal-ai/stable-diffusion-v35-large",  # NSFW-capable
        "auth_header": "Key ${FAL_KEY}",
        "method": "POST",
        "body_template": {
            "prompt": "{prompt}",
            "image_size": "landscape_16_9",
            "num_images": 1
        },
        "parameters": {
            "prompt": {
                "type": "string",
                "description": "Detailed description of the image to generate. Be specific about style, lighting, composition, and subject matter.",
                "required": True
            }
        }
    },
    "video": {
        "name": "video",  # Matches omega_service output
        "description": "Generate a video from a text prompt. Use for creating short video clips, animations, or motion content. Takes 1-5 minutes to complete.",
        "endpoint": "https://fal.run/fal-ai/hunyuan-video",
        "auth_header": "Key ${FAL_KEY}",
        "method": "POST",
        "body_template": {
            "prompt": "{prompt}",
            "resolution": "720p",
            "num_frames": 45
        },
        "parameters": {
            "prompt": {
                "type": "string",
                "description": "Detailed description of the video to generate. Describe the scene, action, camera movement, and style.",
                "required": True
            }
        }
    },
    "websearch": {
        "name": "websearch",  # Matches omega_service output
        "description": "Search the web for information. Use when you need current information, facts, news, or to research a topic.",
        "endpoint": "https://api.search.brave.com/res/v1/web/search",
        "auth_header": "Bearer ${BRAVE_SEARCH_API_KEY}",
        "method": "GET",
        "params_template": {
            "q": "{query}",
            "safesearch": "off"
        },
        "parameters": {
            "query": {
                "type": "string",
                "description": "The search query. Be specific and use keywords that will find relevant results.",
                "required": True
            }
        }
    }
}


def get_tool_definitions_prompt() -> str:
    """
    Return formatted tool definitions for Omega's system prompt.
    
    This creates a clear, structured description of available tools that
    Omega can use to plan tool calls. Format matches omega_service.py
    TOOL_PLANNING_PROMPT output format (raw JSON).
    """
    lines = [
        "## Available Tools",
        "",
        "You can request the following tools. Output ONLY valid JSON.",
        "",
        "Output format:",
        '{"tool": "image" | "video" | "websearch" | null, "prompt": "...", "style": "...", "safe_search": false, "reason": "..."}',
        "",
        "### Tools:",
        ""
    ]
    
    for tool_key, tool_def in OMEGA_TOOLS.items():
        lines.append(f"**{tool_def['name']}**")
        lines.append(f"- Description: {tool_def['description']}")
        
        # Document parameters
        if "parameters" in tool_def:
            lines.append("- Parameters:")
            for param_name, param_info in tool_def["parameters"].items():
                required = "(required)" if param_info.get("required") else "(optional)"
                lines.append(f"  - `{param_name}` ({param_info['type']}) {required}: {param_info['description']}")
        
        lines.append("")
    
    # Add usage examples matching omega_service format
    lines.extend([
        "### Examples:",
        "",
        "Image generation:",
        '{"tool": "image", "prompt": "A serene sunset over a calm ocean", "style": "photorealistic", "safe_search": false, "reason": null}',
        "",
        "Web search:",
        '{"tool": "websearch", "prompt": "latest news about AI developments", "style": null, "safe_search": false, "reason": null}',
        "",
        "Video generation:",
        '{"tool": "video", "prompt": "A butterfly emerging from a cocoon, slow motion", "style": "photorealistic", "safe_search": false, "reason": null}',
        "",
        "No tool needed:",
        '{"tool": null, "prompt": null, "style": null, "safe_search": false, "reason": "Conversational request"}',
        ""
    ])
    
    return "\n".join(lines)


def get_tool_by_name(name: str) -> Optional[Dict[str, Any]]:
    """
    Look up a tool definition by its name.
    
    Args:
        name: The tool name (e.g., "generate_image")
        
    Returns:
        The tool definition dict, or None if not found.
    """
    for tool_key, tool_def in OMEGA_TOOLS.items():
        if tool_def["name"] == name:
            return tool_def
    return None


def get_tool_names() -> List[str]:
    """Return a list of all available tool names."""
    return [tool_def["name"] for tool_def in OMEGA_TOOLS.values()]


def validate_tool_call(tool_name: str, parameters: Dict[str, Any]) -> tuple[bool, str]:
    """
    Validate a tool call has all required parameters.
    
    Args:
        tool_name: The name of the tool being called
        parameters: The parameters provided
        
    Returns:
        Tuple of (is_valid, error_message)
    """
    tool_def = get_tool_by_name(tool_name)
    
    if tool_def is None:
        return False, f"Unknown tool: {tool_name}"
    
    if "parameters" not in tool_def:
        return True, ""
    
    for param_name, param_info in tool_def["parameters"].items():
        if param_info.get("required") and param_name not in parameters:
            return False, f"Missing required parameter: {param_name}"
    
    return True, ""
