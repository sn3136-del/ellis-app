"""H1B edition: two-party (petitioner + beneficiary) petition pipeline.

One parent case (visa_type='h1b') owns a sequence of government filings, each a
linked child VisaApplication running the standard single-filing machinery. The
pipeline is sequenced by verified government evidence, never by timers or
assumptions. See docs/H1B_ARCHITECTURE.md for the pinned design.
"""
