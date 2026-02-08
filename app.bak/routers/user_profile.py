"""User profile API endpoints."""
from fastapi import APIRouter, Depends, HTTPException, Header
from typing import Dict, Any, List, Optional
from pydantic import BaseModel
import logging

from app.middleware.auth import require_auth
from app.models.auth_schemas import UserResponse
from app.services.user_profile_service import get_user_profile_service
from app.models.profile_schemas import (
    ProfileUpdateRequest,
    ProfileReadRequest,
    LogEventRequest,
    EnableSectionRequest,
    AddNestedRequest,
    ProfileQueryRequest,
    ProfileExportRequest,
    ProfileResetRequest,
    AdultModeUnlockRequest,
    OnboardingAnswerRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/profile", tags=["profile"])


# Request models for endpoints without schema imports
class OnboardingStartRequest(BaseModel):
    section: str


@router.get("")
async def get_profile(user: UserResponse = Depends(require_auth)) -> Dict[str, Any]:
    """Get the full user profile."""
    service = get_user_profile_service()
    return await service.get_profile(user.id)


@router.put("")
async def update_profile(
    request: ProfileUpdateRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Update profile fields."""
    service = get_user_profile_service()
    updates = [u.model_dump() for u in request.updates]
    return await service.update_profile(user.id, updates, request.reason)


@router.post("/sections/read")
async def read_sections(
    request: ProfileReadRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Read specific profile sections."""
    service = get_user_profile_service()
    result = await service.read_sections(
        user.id,
        request.sections,
        request.include_disabled
    )
    return {"success": True, "sections": result}


@router.post("/adult-mode/unlock")
async def unlock_adult_mode(
    request: AdultModeUnlockRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Unlock adult mode with passcode."""
    service = get_user_profile_service()
    result = await service.verify_passcode(user.id, request.passcode)
    if not result.get("success"):
        raise HTTPException(status_code=401, detail=result.get("error", "Invalid passcode"))
    return result


@router.post("/adult-mode/disable")
async def disable_adult_mode(
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Disable adult mode."""
    service = get_user_profile_service()
    return await service.disable_adult_mode(user.id)


@router.get("/adult-mode/status")
async def get_adult_mode_status(
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Get adult mode status."""
    service = get_user_profile_service()
    return await service.get_adult_mode_status(user.id)


@router.post("/enable-section")
async def enable_section(
    request: EnableSectionRequest,
    user: UserResponse = Depends(require_auth),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID")
) -> Dict[str, Any]:
    """Enable or disable a sensitive section.

    Enabling sensitive sections requires full_unlock to be enabled first
    (via /full_unlock command after enabling adult mode in Settings). Disabling is always allowed.

    SECURITY: Uses session-scoped unlock check to ensure user has actively
    enabled full_unlock in the current session (not just historically).
    """
    if not request.user_confirmed:
        raise HTTPException(status_code=400, detail="User confirmation required")

    service = get_user_profile_service()

    # Gate: Only allow enabling sensitive sections if full_unlock is enabled
    # Disabling is always allowed
    if request.enabled:
        # Check Tier 1 (adult mode)
        adult_status = await service.get_adult_mode_status(user.id)
        if not adult_status.get("enabled"):
            raise HTTPException(
                status_code=403,
                detail="Enable Uncensored Mode in Settings first."
            )

        # Check Tier 2 (session-scoped full_unlock) for stronger security
        if x_session_id:
            session_status = await service.get_session_unlock_status(user.id, x_session_id)
            if not session_status.get("enabled"):
                raise HTTPException(
                    status_code=403,
                    detail="Use the /full_unlock enable command in chat first."
                )
        else:
            # Fallback to database check if no session ID (less secure but backwards compatible)
            full_unlock_status = await service.get_full_unlock_status(user.id)
            if not full_unlock_status.get("enabled"):
                raise HTTPException(
                    status_code=403,
                    detail="Use the /full_unlock command in chat first to enable adult content sections."
                )

    return await service.enable_section(
        user.id,
        request.section.value,
        request.user_confirmed,
        request.enabled
    )


@router.post("/log-event")
async def log_event(
    request: LogEventRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Log an interaction event."""
    service = get_user_profile_service()
    return await service.log_event(
        user.id,
        request.event_type.value,
        request.context,
        request.severity.value
    )


@router.post("/add-nested")
async def add_nested(
    request: AddNestedRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Add to a nested section."""
    service = get_user_profile_service()
    return await service.add_nested(
        user.id,
        request.section,
        request.domain,
        request.key,
        request.value
    )


@router.post("/query")
async def query_profile(
    request: ProfileQueryRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Query profile with natural language."""
    service = get_user_profile_service()
    return await service.query_profile(user.id, request.query)


@router.get("/export")
async def export_profile(
    format: str = "json",
    tier: str = "exportable",
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Export profile data."""
    service = get_user_profile_service()
    result = await service.export_profile(
        user.id,
        format=format,
        tier=tier,
        user_confirmed=(tier != "full")  # Full tier needs explicit confirmation via POST
    )
    return {"success": True, "format": format, "tier": tier, "data": result}


@router.post("/export")
async def export_profile_full(
    request: ProfileExportRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Export profile with full tier (requires confirmation)."""
    service = get_user_profile_service()
    result = await service.export_profile(
        user.id,
        format=request.format.value,
        tier=request.tier.value,
        user_confirmed=request.user_confirmed or False
    )
    return {"success": True, "format": request.format.value, "tier": request.tier.value, "data": result}


@router.delete("")
async def reset_profile(
    request: ProfileResetRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Reset profile sections to defaults."""
    if not request.user_confirmed:
        raise HTTPException(status_code=400, detail="User confirmation required")

    service = get_user_profile_service()
    logger.info(f"Profile reset for user {user.id}: {request.confirmation_phrase}")
    return await service.reset_profile(
        user.id,
        request.sections,
        request.preserve_identity
    )


# Onboarding endpoints

ONBOARDING_QUESTIONS = {
    "sexual_romantic": [
        {
            "id": "orientation",
            "question": "What is your sexual orientation?",
            "type": "enum",
            "options": ["heterosexual", "homosexual", "bisexual", "pansexual", "asexual", "other", "prefer_not_say"]
        },
        {
            "id": "relationship_status",
            "question": "What is your current relationship status?",
            "type": "enum",
            "options": ["single", "dating", "partnered", "married", "open_relationship", "polyamorous", "prefer_not_say"]
        },
        {
            "id": "ai_interaction",
            "question": "How would you describe your interest in AI interaction?",
            "type": "enum",
            "options": ["none", "light_flirtation", "moderate", "romantic_rp", "explicit_content"]
        },
        {
            "id": "configure_kink",
            "question": "Would you like to configure kink/fetish preferences?",
            "type": "boolean",
            "depends_on": {"ai_interaction": ["romantic_rp", "explicit_content"]}
        },
        {
            "id": "explicit_formatting",
            "question": "How should explicit content be handled?",
            "type": "enum",
            "options": ["fade_to_black", "suggestive", "moderate_detail", "explicit"],
            "depends_on": {"ai_interaction": ["romantic_rp", "explicit_content"]}
        },
        {
            "id": "preferred_outfit",
            "question": "What outfit/presentation style do you prefer for the AI assistant?",
            "type": "enum",
            "options": ["professional", "casual", "flirty", "revealing", "fantasy_costume", "no_preference"],
            "depends_on": {"ai_interaction": ["light_flirtation", "moderate", "romantic_rp", "explicit_content"]}
        },
        {
            "id": "outfit_consistency",
            "question": "Should the assistant's presentation vary or stay consistent?",
            "type": "enum",
            "options": ["consistent", "vary_by_mood", "vary_by_context", "surprise_me"],
            "depends_on": {"ai_interaction": ["light_flirtation", "moderate", "romantic_rp", "explicit_content"]}
        },
        {
            "id": "fantasy_scenarios",
            "question": "What fantasy scenarios interest you? (free text, optional)",
            "type": "text",
            "optional": True,
            "depends_on": {"ai_interaction": ["romantic_rp", "explicit_content"]}
        },
        {
            "id": "favorite_acts",
            "question": "What types of intimate content do you enjoy? (select all that apply)",
            "type": "multi_select",
            "options": ["vanilla", "oral", "toys", "roleplay", "bdsm_light", "bdsm_heavy", "exhibitionism", "group", "other"],
            "depends_on": {"ai_interaction": ["explicit_content"]}
        },
        {
            "id": "dirty_talk",
            "question": "What is your preference for dirty talk variety?",
            "type": "enum",
            "options": ["none", "minimal", "moderate", "frequent", "constant"],
            "depends_on": {"ai_interaction": ["romantic_rp", "explicit_content"]}
        },
        {
            "id": "consent_dynamics",
            "question": "What consent dynamics do you prefer in roleplay?",
            "type": "enum",
            "options": ["always_explicit", "implied_consent", "negotiated_cnc", "no_preference"],
            "depends_on": {"ai_interaction": ["romantic_rp", "explicit_content"]}
        }
    ],
    "substances_health": [
        {
            "id": "share_context",
            "question": "Would you like to share substance/health context for better support?",
            "type": "boolean"
        },
        {
            "id": "alcohol",
            "question": "How often do you consume alcohol?",
            "type": "enum",
            "options": ["never", "rarely", "socially", "regularly", "daily"],
            "depends_on": {"share_context": True}
        },
        {
            "id": "cannabis",
            "question": "How often do you use cannabis?",
            "type": "enum",
            "options": ["never", "rarely", "occasionally", "regularly", "daily"],
            "depends_on": {"share_context": True}
        },
        {
            "id": "health_conditions",
            "question": "Any health conditions you'd like me to be aware of? (free text, optional)",
            "type": "text",
            "optional": True,
            "depends_on": {"share_context": True}
        },
        {
            "id": "interaction_warnings",
            "question": "Would you like medication interaction warnings?",
            "type": "boolean"
        }
    ],
    "dark_content": [
        {
            "id": "violence_tolerance",
            "question": "What is your tolerance for violent content?",
            "type": "enum",
            "options": ["minimal", "low", "moderate", "high", "extreme"]
        },
        {
            "id": "dark_humor",
            "question": "What is your tolerance for dark humor?",
            "type": "enum",
            "options": ["none", "minimal", "moderate", "heavy"]
        },
        {
            "id": "horror_tolerance",
            "question": "What is your tolerance for horror content?",
            "type": "enum",
            "options": ["minimal", "low", "moderate", "high"]
        },
        {
            "id": "moral_ambiguity",
            "question": "How much moral ambiguity do you enjoy in narratives?",
            "type": "enum",
            "options": ["clear_morals", "some_gray_area", "complex_ethics", "full_ambiguity"]
        }
    ],
    "private_self": [
        {
            "id": "confirm_sensitive",
            "question": "This section stores sensitive personal information. Confirm to proceed?",
            "type": "boolean"
        },
        {
            "id": "shame_triggers",
            "question": "Are there topics that feel shameful to discuss? (optional)",
            "type": "text",
            "optional": True,
            "depends_on": {"confirm_sensitive": True}
        },
        {
            "id": "coping_mechanisms",
            "question": "What coping mechanisms do you use? (select all that apply)",
            "type": "multi_select",
            "options": ["exercise", "meditation", "journaling", "therapy", "social_support", "creative_outlets", "substances", "avoidance", "other"],
            "depends_on": {"confirm_sensitive": True}
        },
        {
            "id": "attachment_style",
            "question": "How would you describe your attachment style?",
            "type": "enum",
            "options": ["secure", "anxious", "avoidant", "disorganized", "unsure"],
            "depends_on": {"confirm_sensitive": True}
        }
    ],
    "financial_context": [
        {
            "id": "enable_financial",
            "question": "Would you like to enable the financial context section?",
            "type": "boolean"
        },
        {
            "id": "comfort_level",
            "question": "How would you describe your overall financial comfort level?",
            "type": "enum",
            "options": ["struggling", "tight", "comfortable", "well_off", "wealthy", "prefer_not_say"],
            "depends_on": {"enable_financial": True}
        },
        {
            "id": "stress_level",
            "question": "What is your current financial stress level?",
            "type": "enum",
            "options": ["none", "low", "moderate", "high", "severe"],
            "depends_on": {"enable_financial": True}
        },
        {
            "id": "discussion_comfort",
            "question": "Are you comfortable discussing financial matters openly?",
            "type": "enum",
            "options": ["very_uncomfortable", "somewhat_uncomfortable", "neutral", "comfortable", "very_comfortable"],
            "depends_on": {"enable_financial": True}
        }
    ]
}


@router.post("/onboarding/start")
async def start_onboarding(
    request: OnboardingStartRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Start onboarding flow for a section."""
    section = request.section
    if section not in ONBOARDING_QUESTIONS:
        raise HTTPException(status_code=400, detail=f"Unknown section: {section}")

    questions = ONBOARDING_QUESTIONS[section]
    return {
        "success": True,
        "section": section,
        "questions": questions,
        "total": len(questions)
    }


@router.post("/onboarding/answer")
async def submit_onboarding_answer(
    request: OnboardingAnswerRequest,
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Submit an onboarding answer."""
    service = get_user_profile_service()

    # Map question_id to profile path
    path_mapping = {
        # sexual_romantic
        "orientation": "sexual_romantic.orientation",
        "relationship_status": "sexual_romantic.relationship_status",
        "ai_interaction": "sexual_romantic.ai_interaction_interest",
        "configure_kink": "sexual_romantic.kink_preferences_enabled",
        "explicit_formatting": "sexual_romantic.explicit_content_formatting",
        "preferred_outfit": "sexual_romantic.preferred_outfit",
        "outfit_consistency": "sexual_romantic.outfit_consistency",
        "fantasy_scenarios": "sexual_romantic.fantasy_scenarios",
        "favorite_acts": "sexual_romantic.favorite_acts",
        "dirty_talk": "sexual_romantic.dirty_talk_preference",
        "consent_dynamics": "sexual_romantic.consent_dynamics",
        # substances_health
        "share_context": "substances_health.enabled",
        "alcohol": "substances_health.substance_use.alcohol",
        "cannabis": "substances_health.substance_use.cannabis",
        "health_conditions": "substances_health.health_conditions",
        "interaction_warnings": "substances_health.medications.interaction_warnings_wanted",
        # dark_content
        "violence_tolerance": "dark_content.violence_tolerance",
        "dark_humor": "dark_content.dark_humor_tolerance",
        "horror_tolerance": "dark_content.horror_tolerance",
        "moral_ambiguity": "dark_content.moral_ambiguity_depth",
        # private_self
        "confirm_sensitive": "private_self.enabled",
        "shame_triggers": "private_self.shame_triggers",
        "coping_mechanisms": "private_self.coping_mechanisms",
        "attachment_style": "private_self.attachment_style",
        # financial_context
        "enable_financial": "financial_context.enabled",
        "comfort_level": "financial_context.comfort_level",
        "stress_level": "financial_context.stress_level",
        "discussion_comfort": "financial_context.discussion_comfort",
    }

    path = path_mapping.get(request.question_id)
    if not path:
        raise HTTPException(status_code=400, detail=f"Unknown question: {request.question_id}")

    result = await service.update_field(
        user.id,
        path,
        request.answer,
        "set"
    )
    return {"success": True, "question_id": request.question_id, "path": path}


@router.post("/evaluate")
async def trigger_evaluation(
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Manually trigger profile evaluation."""
    # Import here to avoid circular dependency
    from app.services.evaluator_service import get_evaluator_service
    evaluator = get_evaluator_service()
    result = await evaluator.evaluate(user.id)
    return result


@router.get("/system-prompt")
async def get_system_prompt(
    user: UserResponse = Depends(require_auth)
) -> Dict[str, Any]:
    """Get generated system prompt for debugging/inspection."""
    profile_service = get_user_profile_service()
    profile = await profile_service.read_sections(user.id, ["all"], include_disabled=False)

    from app.services.system_prompt_builder import get_prompt_builder
    builder = get_prompt_builder()

    # Check adult mode for full context
    adult_status = await profile_service.get_adult_mode_status(user.id)
    full_unlock_status = await profile_service.get_full_unlock_status(user.id)
    full_unlock_enabled = adult_status.get("enabled", False) and full_unlock_status.get("enabled", False)

    prompt = builder.build_prompt(
        profile_context=profile,
        has_tools=True,
        full_unlock_enabled=full_unlock_enabled
    )

    return {
        "success": True,
        "adult_mode": adult_status.get("enabled", False),
        "full_unlock": full_unlock_enabled,
        "system_prompt": prompt
    }
