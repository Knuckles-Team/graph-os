"""Names that are intentionally load-bearing public Protocol parameters."""

# These names are keyword-callable parts of the repository contracts.  Their
# implementations land in adapters, so static analysis cannot yet see their
# use.  Renaming them with a leading underscore would silently change the API.
after_agent_id: object = None
after_version_id: object = None
after_server_id: object = None
after_sequence: object = None
_ = (after_agent_id, after_version_id, after_server_id, after_sequence)
