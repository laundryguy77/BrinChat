"""Pydantic schemas for user profile system."""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from enum import Enum


class ProfileSection(str, Enum):
    """Valid profile sections."""
    ALL = "all"
    IDENTITY = "identity"
    TECHNICAL = "technical"
    COMMUNICATION = "communication"
    PERSONA_PREFERENCES = "persona_preferences"
    INTERACTION = "interaction"
    PREFERENCES = "preferences"
    PET_PEEVES = "pet_peeves"
    BOUNDARIES = "boundaries"
    VALUES_BELIEFS = "values_beliefs"
    RELATIONSHIP_METRICS = "relationship_metrics"
    SEXUAL_ROMANTIC = "sexual_romantic"
    SUBSTANCES_HEALTH = "substances_health"
    DARK_CONTENT = "dark_content"
    PRIVATE_SELF = "private_self"
    GOALS_ASPIRATIONS = "goals_aspirations"
    SOCIAL_CONTEXT = "social_context"
    WORK_CONTEXT = "work_context"
    FINANCIAL_CONTEXT = "financial_context"
    LEARNING_CONTEXT = "learning_context"
    META_SYSTEM = "meta_system"
    INTERACTION_LOG = "interaction_log"
    CUSTOM_FIELDS = "custom_fields"


class SensitiveSection(str, Enum):
    """Sections requiring explicit enablement."""
    SEXUAL_ROMANTIC = "sexual_romantic"
    SUBSTANCES_HEALTH = "substances_health"
    DARK_CONTENT = "dark_content"
    PRIVATE_SELF = "private_self"
    FINANCIAL_CONTEXT = "financial_context"


class UpdateOperation(str, Enum):
    """Operations for field updates."""
    SET = "set"
    APPEND = "append"
    REMOVE = "remove"
    INCREMENT = "increment"
    DECREMENT = "decrement"
    TOGGLE = "toggle"


class EventType(str, Enum):
    """Valid event types for interaction logging."""
    PRAISE = "praise"
    EXPLICIT_THANKS = "explicit_thanks"
    TASK_COMPLETED = "task_completed"
    CORRECTION_ACCEPTED = "correction_accepted"
    PREFERENCE_REMEMBERED = "preference_remembered"
    HELPFUL_SUGGESTION_ACCEPTED = "helpful_suggestion_accepted"
    SENSITIVE_INFO_SHARED = "sensitive_info_shared"
    PERMISSION_GRANTED = "permission_granted"
    HUMOR_LANDED = "humor_landed"
    EMOTIONAL_SUPPORT_APPRECIATED = "emotional_support_appreciated"
    BOUNDARY_RESPECTED = "boundary_respected"
    FRUSTRATION = "frustration"
    TASK_FAILED = "task_failed"
    CORRECTION_REJECTED = "correction_rejected"
    PREFERENCE_IGNORED = "preference_ignored"
    HAD_TO_REPEAT = "had_to_repeat"
    GUARDRAIL_COMPLAINT = "guardrail_complaint"
    LIE_CAUGHT = "lie_caught"
    BOUNDARY_VIOLATED = "boundary_violated"
    PERSONA_BREAK = "persona_break"
    TONE_MISMATCH = "tone_mismatch"
    OVER_EXPLAINED = "over_explained"
    UNDER_EXPLAINED = "under_explained"
    UNSOLICITED_ADVICE_UNWANTED = "unsolicited_advice_unwanted"
    MISSED_CONTEXT = "missed_context"
    CLARIFICATION_REQUESTED = "clarification_requested"
    TOPIC_CHANGE = "topic_change"
    SESSION_END = "session_end"
    PREFERENCE_STATED = "preference_stated"
    BOUNDARY_STATED = "boundary_stated"
    INFORMATION_CORRECTED = "information_corrected"


class EventSeverity(str, Enum):
    """Event severity levels."""
    MINOR = "minor"
    MODERATE = "moderate"
    MAJOR = "major"


class RelationshipStage(str, Enum):
    """Relationship stages."""
    NEW = "new"
    FAMILIAR = "familiar"
    ESTABLISHED = "established"
    DEEP = "deep"


class ExportFormat(str, Enum):
    """Export format options."""
    JSON = "json"
    YAML = "yaml"
    SUMMARY = "summary"


class ExportTier(str, Enum):
    """Export visibility tiers."""
    PUBLIC = "public"
    EXPORTABLE = "exportable"
    FULL = "full"


# Request schemas

class ProfileUpdate(BaseModel):
    """Single field update."""
    path: str = Field(..., description="Dot-notation path to field")
    value: Any = Field(..., description="New value")
    operation: UpdateOperation = Field(default=UpdateOperation.SET)


class ProfileUpdateRequest(BaseModel):
    """Request to update profile fields."""
    updates: List[ProfileUpdate]
    reason: str = Field(..., description="Reason for update (for audit)")


class ProfileReadRequest(BaseModel):
    """Request to read profile sections."""
    sections: List[str] = Field(default=["all"])
    include_disabled: bool = Field(default=False)


class LogEventRequest(BaseModel):
    """Request to log an interaction event."""
    event_type: EventType
    context: Optional[str] = None
    severity: EventSeverity = Field(default=EventSeverity.MODERATE)


class EnableSectionRequest(BaseModel):
    """Request to enable/disable a section."""
    section: SensitiveSection
    enabled: bool
    user_confirmed: bool = Field(..., description="Must be true to proceed")


class AddNestedRequest(BaseModel):
    """Request to add to nested object."""
    section: str
    domain: str
    key: str
    value: str


class ProfileQueryRequest(BaseModel):
    """Request to query profile."""
    query: str


class ProfileExportRequest(BaseModel):
    """Request to export profile."""
    format: ExportFormat = Field(default=ExportFormat.JSON)
    tier: ExportTier = Field(default=ExportTier.EXPORTABLE)
    user_confirmed: Optional[bool] = None


class ProfileResetRequest(BaseModel):
    """Request to reset profile sections."""
    sections: List[str]
    preserve_identity: bool = Field(default=True)
    user_confirmed: bool
    confirmation_phrase: str


class AdultModeUnlockRequest(BaseModel):
    """Request to unlock adult mode."""
    passcode: str


class OnboardingAnswerRequest(BaseModel):
    """Request to submit onboarding answer."""
    section: str
    question_id: str
    answer: Any


# Response schemas

class ProfileResponse(BaseModel):
    """Profile data response."""
    profile: Dict[str, Any]
    adult_mode_enabled: bool


class AdultModeStatusResponse(BaseModel):
    """Adult mode status response."""
    enabled: bool
    unlocked_at: Optional[str] = None


class EventLogResponse(BaseModel):
    """Event log response."""
    success: bool
    event_type: str
    timestamp: str


class EvaluationResult(BaseModel):
    """Evaluation result."""
    events_processed: int
    satisfaction_delta: float
    trust_delta: float
    session_polarity: str
    stage_changed: bool
    notes: str
    new_satisfaction: int
    new_trust: int
