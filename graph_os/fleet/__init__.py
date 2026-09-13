"""Fleet gateway (multiplexer) — RF-ADR-009 §2.4 Phase 5 target.

Owns the in-process fleet loader that lazily fronts every `*-mcp` service
(`find_tools` / `list_catalog` / `load_tools`). Source today:
`agent_utilities/mcp/multiplexer.py` (see AGENTS.md "W5 source measurements").
Per RF-ADR-009 §2.4, the loader itself stays; its catalog moves from a static
file to the epistemic-graph server registry, and the multiplexer's skill/
prompt harvest path (`fleet_skill_harvest.py`, `fleet_prompt_harvest.py`) is
deleted once the agent-connector-sdk sync runner imports packs natively (W2,
§2.1 item 5). Not yet populated — see AGENTS.md "Status".
"""
