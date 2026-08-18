# 0007: Marketplace metadata and the v0.1.x releases

Date: 2026-08-18
PR: #7 (8727686cf0b3, merged 0dcda6e)
Agent: Claude Code (Fable 5), controller session, no subagent dispatch (a
two-file metadata change).
Produced: action.yml's description cut to 122 characters and its branding
block (icon eye, color gray-dark), both required by GitHub Marketplace
validation; pyproject 0.1.0 to 0.1.1.
Human: Mamadou picked the versioning scheme (tag matches the pyproject
version, first tag at the wave A code state), approved the v0.1.0 release
notes pre-publish, chose to list the Action on the Marketplace, picked the
branding, reviewed and merged. The Marketplace publish click is his alone:
it needs the Developer Agreement on his account.
Verification: fast suite 910, ruff clean, the 15 action.yml tests green, no
em dashes in the diff.
Context: v0.1.0 was tagged at 53cde24 earlier the same day (the wave A code
state, which is also the revision wave B's runs start from). The Marketplace
validates action.yml at the tag being published, so v0.1.0 can never pass
and v0.1.1 exists to carry the metadata. The patch bump keeps the
tag-matches-pyproject property. No behavior change anywhere: steps, inputs
and defaults are untouched and pinned by tests/test_action_yml.py.
