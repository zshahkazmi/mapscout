"""mapscout — a slow, resumable Google Maps business scraper.

Builds a local business directory (name, category, phone, website, address,
rating, hours) by searching Google Maps locality by locality and niche by
niche. Designed to run unattended in short scheduled sittings: it saves
progress after every query, dedupes by the place's stable Maps feature id,
and stops cleanly when Google shows a block page instead of solving it.
"""

__version__ = "0.1.0"
