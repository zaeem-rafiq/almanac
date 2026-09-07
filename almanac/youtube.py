"""YouTube Data API v3 access (owner OAuth).

RULE: `videos.update` runs only when ALL THREE guards hold:
  1. ALMANAC_WRITE=true
  2. the target channel id equals ALMANAC_TEST_CHANNEL_ID
  3. the CLI was given --apply
Default is dry-run (print the diff). Nothing else in this codebase mutates YouTube.

NOTE (ADR-000 3a): videos.update is a PUT and a partial snippet drops omitted fields.
Read the snippet with videos().list(part="snippet") first, mutate only `description`,
and send the whole snippet back.
"""
