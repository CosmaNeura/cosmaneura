"""Engagement engine v2 integration: communication-role scoring of visit transcripts.

The vendored scorer lives in ``app.engagement.cosmas_engagement``; ``service`` wires
it to cosmaneura (text reconstruction + Azure client), and ``app.jobs.score_engagement``
runs it after a transcript is ready.
"""
