"""Central definitions for tunable limits used across the review app.

Keeping these values in one place lets views, forms, models, repositories and
settings agree on the same numbers instead of duplicating literals.
"""

# Catalog/list pagination: number of items per page.
PAGE_SIZE = 10

# Review body length limits (applied to the sanitized text).
MIN_COMMENT_LENGTH = 20
MAX_COMMENT_LENGTH = 1000

# Rate limiting: action -> (max hits, window in seconds).
RATE_LIMITS = {
    "login": (10, 60),
    "signup": (5, 600),
    "review": (5, 60),
    "report": (5, 60),
    "vote": (60, 60),
}
