"""Version constants for the Cosmas Engagement Engine.

These are surfaced in API responses and on the dashboard so that any score can be
traced back to the exact engine + framework version that produced it.

v2.0.0 is a major change: the output ontology moved from a five-style,
Big-Five-derived personality segmentation to a three-role *communication style*
model grounded in the Control Preferences Scale (Degner & Sloan). The engine now
estimates observable communication behaviour (decision-control and
information-engagement) rather than latent personality traits. See README.
"""

ENGINE_VERSION = "2.0.0"
# Bump FRAMEWORK_VERSION whenever role names, descriptions, prototype vectors, or
# the mapping math change. Stored alongside every result.
FRAMEWORK_VERSION = "2.0.0"
# Backwards-compatible alias (older callers referenced SEGMENT_FRAMEWORK_VERSION).
SEGMENT_FRAMEWORK_VERSION = FRAMEWORK_VERSION
RUBRIC_VERSION = "2.0.0"
