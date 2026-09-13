"""graph-os MCP server — RF-ADR-009 §2/§2.4 Phase 5 target.

Owns the always-on `graph_*` / `ontology_*` / `object_*` / `engine_*` MCP tool
surface. Source today: `agent_utilities/mcp/kg_server.py` (see AGENTS.md "W5
source measurements" for the current line count), split into modules on the
way out per RF-ADR-009 §2.4 ("`kg_server.py` is split into modules"). Not yet
populated — this package intentionally carries no runtime code until
Migration Wave 5 extracts it; see AGENTS.md "Status".
"""
