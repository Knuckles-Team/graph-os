"""Control plane — RF-ADR-009 §2 Phase 5 authority.

Owns fleet reconciliation, action-policy enforcement, and desired-state
control loops for the deployed composition. The implementation is native to
``graph_os.control_plane``; the former AU runtime copy was removed in the
ownership cutover recorded by AU commit ``4d930326c``.
"""
