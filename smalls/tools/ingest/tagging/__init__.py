"""The tagging pass (docs/decisions/ingestion-decisions.md section 4). A closed-set classifier labels
records on three facets; code validates every label against its vocabulary and applies it only at high
confidence. A tag is a query facet, never evidence.

- `profiles`: the config and the three profiles (TOML beside this file).
- `units`: what the tagger sees for a record, and the cache key.
- `cache`: the committed cache and the confidence rule; all that ingest uses.
- `run`: `smalls tag`, the only module that imports the classifier SDK.
- `measure`: the hand-labelled sample and the precision, coverage and stability table.
"""
