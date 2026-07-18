# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain-level constants for framework-neutral assessment logic."""

from typing import Final

# Public reference token used when a framework does not provide its own source token.
LEGAL_REF_ELI: Final[str] = "IntrinsicalPolicyEngine:public-framework"

# Fallback dates (used when calendar is missing or incomplete)
DATE_FALLBACK_SNAPSHOT: Final[str] = "1970-01-01"

# Routing fallback labels used when a framework does not configure custom labels.
FALLBACK_REVIEW_SECTIONS: Final[set[str]] = {
    "SECTION-IV",
    "SECTION-VIII",
}

# Known section prefixes for detection.
PREFIX_SECTION: Final[str] = "SECTION-"

# Outcomes
OUTCOME_OUT_OF_SCOPE: Final[str] = "out_of_scope"
OUTCOME_EXCLUDED: Final[str] = "excluded"
OUTCOME_BLOCKED: Final[str] = "blocked"
OUTCOME_REVIEW_PROVIDER_DEPLOYER: Final[str] = "review_provider_and_deployer"
OUTCOME_REVIEW_PROVIDER: Final[str] = "review_provider"
OUTCOME_REVIEW_DEPLOYER: Final[str] = "review_deployer"
OUTCOME_IMPACT_REVIEW_ONLY_PUBLIC: Final[str] = "impact_review_only_public"
OUTCOME_MODEL_SYSTEMIC: Final[str] = "model_systemic"
OUTCOME_MODEL_ONLY: Final[str] = "model_only"
OUTCOME_LIMITED_RISK_TRANSPARENCY: Final[str] = "limited_risk_transparency_only"
OUTCOME_OTHER_REGULATED: Final[str] = "other_regulated"

# Risk Tiers
RISK_TIER_BLOCKED: Final[str] = "blocked"
RISK_TIER_HIGH: Final[str] = "review"
RISK_TIER_IMPACT_REVIEW_ONLY: Final[str] = "impact_review_only"
RISK_TIER_LIMITED: Final[str] = "limited_risk"
RISK_TIER_OTHER: Final[str] = "other"
RISK_TIER_NONE: Final[str] = "none"

# Moved from intrinsical_policy_engine.app.constants
MAX_TEXT_LENGTH: Final[int] = 2048
MAX_ACTION_LOG_ENTRIES: Final[int] = 10

# Moved from intrinsical_policy_engine.app.filenames
QUESTIONS_FILE: Final[str] = "questions.yml"
MAX_DERIVATION_ITERATIONS: Final[int] = 128

# Due date priority weights
PRIORITY_TRANSPARENCY: Final[int] = 60
PRIORITY_MODEL_GOVERNANCE: Final[int] = 50
PRIORITY_REVIEW: Final[int] = 300
PRIORITY_FULL_APPLICATION: Final[int] = 100

# Content limits
MD_DRAFT_MIN_CHARS: Final[int] = 150
MD_READY_MIN_CHARS: Final[int] = 400

# Encoding (kept in domain/common scope to avoid app-layer dependency)
DEFAULT_ENCODING: Final[str] = "utf-8"

# Storage Constants (Porcelain vs Plumbing)
METADATA_DIR: Final[str] = "_metadata"

# Action IDs (Conformance Routing)
ACTION_CONF_A6: Final[str] = "CONF-A6"
ACTION_CONF_A7: Final[str] = "CONF-A7"
