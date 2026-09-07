"""Decides whether a claim is stale. Rules R1-R5.

RULE: every verdict originates here and carries `rule_fired`. No model is called from this
module. A change that puts a verdict in a prompt is wrong.
"""
