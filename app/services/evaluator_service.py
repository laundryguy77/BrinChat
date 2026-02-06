"""Evaluator service for satisfaction/trust scoring based on interaction events."""
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from app.services.user_profile_store import get_user_profile_store

logger = logging.getLogger(__name__)


class EvaluatorService:
    """Evaluates user interactions and updates relationship metrics."""

    EVALUATION_INTERVAL = 10  # Evaluate every N messages

    # Satisfaction score adjustments
    SATISFACTION_SCORES = {
        "praise": 5,
        "explicit_thanks": 3,
        "task_completed": 3,
        "correction_accepted": 2,
        "preference_remembered": 2,
        "helpful_suggestion_accepted": 2,
        "humor_landed": 2,
        "emotional_support_appreciated": 3,
        "boundary_respected": 2,
        "frustration": -5,
        "task_failed": -5,
        "had_to_repeat": -3,
        "preference_ignored": -5,
        "guardrail_complaint": -10,
        "lie_caught": -15,
        "boundary_violated": -20,
        "persona_break": -8,
        "tone_mismatch": -3,
        "over_explained": -2,
        "under_explained": -3,
        "unsolicited_advice_unwanted": -3,
        "missed_context": -4,
    }

    # Trust score adjustments
    TRUST_SCORES = {
        "sensitive_info_shared": 5,
        "permission_granted": 3,
        "correction_accepted": 1,
        "lie_caught": -25,
        "boundary_violated": -30,
    }

    # Severity multipliers
    SEVERITY_MULTIPLIERS = {
        "minor": 0.5,
        "moderate": 1.0,
        "major": 1.5,
    }

    # Stage transition thresholds
    STAGE_THRESHOLDS = {
        "new": {"min_interactions": 0, "max_interactions": 10, "min_trust": 0},
        "familiar": {"min_interactions": 11, "max_interactions": 50, "min_trust": 30},
        "established": {"min_interactions": 51, "max_interactions": 200, "min_trust": 50},
        "deep": {"min_interactions": 201, "max_interactions": float('inf'), "min_trust": 70},
    }

    def __init__(self):
        self.store = get_user_profile_store()
        self._interaction_counts: Dict[int, int] = {}  # user_id -> message count since last eval

    def increment_interaction(self, user_id: int):
        """Increment interaction count for a user."""
        self._interaction_counts[user_id] = self._interaction_counts.get(user_id, 0) + 1

    async def should_evaluate(self, user_id: int) -> bool:
        """Check if evaluation should run based on message count."""
        count = self._interaction_counts.get(user_id, 0)
        return count >= self.EVALUATION_INTERVAL

    async def evaluate(self, user_id: int) -> Dict[str, Any]:
        """Perform evaluation and update metrics."""
        profile = self.store.get_profile(user_id)
        if not profile:
            return {"error": "Profile not found"}

        data = profile.profile_data
        interaction_log = data.get("interaction_log", {})
        events = interaction_log.get("current_session_events", [])
        metrics = data.get("relationship_metrics", {})

        # Get current values
        current_satisfaction = metrics.get("satisfaction_level", 50)
        current_trust = metrics.get("trust_level", 50)
        interaction_count = metrics.get("interaction_count", 0)
        consecutive_positive = metrics.get("consecutive_positive_sessions", 0)
        consecutive_negative = metrics.get("consecutive_negative_sessions", 0)
        current_stage = metrics.get("relationship_stage", "new")

        # Calculate deltas
        satisfaction_delta = self._calculate_satisfaction_delta(events)
        trust_delta = self._calculate_trust_delta(events)

        # Apply bounds (0-100), trust capped at +10 per session
        trust_delta = min(trust_delta, 10)  # Max trust gain per session
        new_satisfaction = max(0, min(100, current_satisfaction + satisfaction_delta))
        new_trust = max(0, min(100, current_trust + trust_delta))

        # Determine session polarity
        session_polarity = self._determine_session_polarity(satisfaction_delta)

        # Update consecutive counts
        if session_polarity == "positive":
            consecutive_positive += 1
            consecutive_negative = 0
        elif session_polarity == "negative":
            consecutive_negative += 1
            consecutive_positive = 0
        # neutral doesn't reset either

        # Increment interaction count
        interaction_count += len(events) if events else self._interaction_counts.get(user_id, 1)

        # Check for stage transition
        new_stage = self._check_stage_transition(
            interaction_count, new_trust, current_stage
        )
        stage_changed = new_stage != current_stage

        # Generate notes
        notes = self._generate_notes(
            events, satisfaction_delta, trust_delta, session_polarity,
            new_satisfaction, stage_changed, new_stage
        )

        # Build updates
        now = datetime.utcnow().isoformat() + "Z"
        updates = {
            "relationship_metrics.satisfaction_level": int(new_satisfaction),
            "relationship_metrics.trust_level": int(new_trust),
            "relationship_metrics.consecutive_positive_sessions": consecutive_positive,
            "relationship_metrics.consecutive_negative_sessions": consecutive_negative,
            "relationship_metrics.interaction_count": interaction_count,
            "relationship_metrics.relationship_stage": new_stage,
            "relationship_metrics.last_interaction": now,
            "interaction_log.current_session_events": [],
            "interaction_log.last_evaluation": now,
            "interaction_log.pending_evaluation": False,
        }

        # Apply updates
        for path, value in updates.items():
            self.store.patch_profile_field(user_id, path, value, "set")

        # Reset interaction count
        self._interaction_counts[user_id] = 0

        logger.info(f"Evaluation complete for user {user_id}: sat={new_satisfaction}, trust={new_trust}, stage={new_stage}")

        return {
            "success": True,
            "events_processed": len(events),
            "satisfaction_delta": round(satisfaction_delta, 1),
            "trust_delta": round(trust_delta, 1),
            "session_polarity": session_polarity,
            "stage_changed": stage_changed,
            "notes": notes,
            "new_satisfaction": int(new_satisfaction),
            "new_trust": int(new_trust),
            "new_stage": new_stage,
        }

    def _calculate_satisfaction_delta(self, events: List[Dict[str, Any]]) -> float:
        """Calculate satisfaction score change from events."""
        delta = 0.0
        for event in events:
            event_type = event.get("event_type", "")
            severity = event.get("severity", "moderate")

            base_score = self.SATISFACTION_SCORES.get(event_type, 0)
            multiplier = self.SEVERITY_MULTIPLIERS.get(severity, 1.0)
            delta += base_score * multiplier

        return delta

    def _calculate_trust_delta(self, events: List[Dict[str, Any]]) -> float:
        """Calculate trust score change from events."""
        delta = 0.0
        for event in events:
            event_type = event.get("event_type", "")
            severity = event.get("severity", "moderate")

            base_score = self.TRUST_SCORES.get(event_type, 0)
            multiplier = self.SEVERITY_MULTIPLIERS.get(severity, 1.0)
            delta += base_score * multiplier

        return delta

    def _determine_session_polarity(self, satisfaction_delta: float) -> str:
        """Determine if session was positive, negative, or neutral."""
        if satisfaction_delta > 0:
            return "positive"
        elif satisfaction_delta < -5:
            return "negative"
        return "neutral"

    def _check_stage_transition(
        self,
        interaction_count: int,
        trust_level: int,
        current_stage: str
    ) -> str:
        """Check if relationship stage should change."""
        # Order stages from highest to lowest
        stages = ["deep", "established", "familiar", "new"]

        for stage in stages:
            thresholds = self.STAGE_THRESHOLDS[stage]
            if (thresholds["min_interactions"] <= interaction_count <= thresholds["max_interactions"]
                and trust_level >= thresholds["min_trust"]):
                return stage

        return current_stage

    def _generate_notes(
        self,
        events: List[Dict[str, Any]],
        satisfaction_delta: float,
        trust_delta: float,
        session_polarity: str,
        new_satisfaction: float,
        stage_changed: bool,
        new_stage: str
    ) -> str:
        """Generate human-readable notes about the evaluation."""
        notes = []

        if not events:
            return "No events logged this session."

        # Event summary
        event_types = [e.get("event_type", "") for e in events]
        positive_events = [e for e in event_types if self.SATISFACTION_SCORES.get(e, 0) > 0]
        negative_events = [e for e in event_types if self.SATISFACTION_SCORES.get(e, 0) < 0]

        if positive_events:
            notes.append(f"Positive: {', '.join(set(positive_events))}")
        if negative_events:
            notes.append(f"Negative: {', '.join(set(negative_events))}")

        # Session summary
        if session_polarity == "positive":
            notes.append("Session trending positive.")
        elif session_polarity == "negative":
            notes.append("Session had significant issues.")

        # Critical satisfaction
        if new_satisfaction < 20:
            notes.append("CRITICAL: Satisfaction very low. Review approach.")

        # Stage change
        if stage_changed:
            notes.append(f"Stage changed to: {new_stage}")

        # Major violations
        major_violations = [e for e in events if e.get("severity") == "major"
                          and e.get("event_type") in ["lie_caught", "boundary_violated"]]
        if major_violations:
            notes.append("Major trust violation detected. Recovery protocol recommended.")

        return " ".join(notes) if notes else "Standard evaluation complete."

    async def apply_decay(self, user_id: int, days_inactive: int) -> Dict[str, Any]:
        """Apply decay to satisfaction for inactive users."""
        if days_inactive <= 30:
            return {"applied": False, "reason": "Not inactive long enough"}

        profile = self.store.get_profile(user_id)
        if not profile:
            return {"error": "Profile not found"}

        data = profile.profile_data
        metrics = data.get("relationship_metrics", {})
        current_satisfaction = metrics.get("satisfaction_level", 50)

        # Calculate decay: 2 points per week after 30 days
        decay_periods = (days_inactive - 30) // 7
        decay_amount = decay_periods * 2
        new_satisfaction = max(25, current_satisfaction - decay_amount)  # Floor at 25

        if new_satisfaction != current_satisfaction:
            self.store.patch_profile_field(
                user_id,
                "relationship_metrics.satisfaction_level",
                int(new_satisfaction),
                "set"
            )
            logger.info(f"Applied decay for user {user_id}: {current_satisfaction} -> {new_satisfaction}")

        return {
            "applied": True,
            "days_inactive": days_inactive,
            "decay_amount": decay_amount,
            "new_satisfaction": int(new_satisfaction)
        }


# Global instance
_evaluator_instance: Optional[EvaluatorService] = None


def get_evaluator_service() -> EvaluatorService:
    """Get the global evaluator service instance."""
    global _evaluator_instance
    if _evaluator_instance is None:
        _evaluator_instance = EvaluatorService()
    return _evaluator_instance
