"""Loads facts/catalog.yaml and facts/rates.json.

RULE: `catalog.refresh_rates()` is the ONLY function in this codebase permitted to call FMP.
Every other module reads rates from facts/rates.json. Never scrape; official APIs only.
"""
