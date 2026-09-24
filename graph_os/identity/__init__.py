"""GraphOS identity broker (RF-ADR-009: EG owns the identity store; GraphOS owns
every web flow over it).

The external authorities -- OIDC (:mod:`.oidc`, :mod:`.keycloak`), LDAP
(:mod:`.ldap`), SCIM (:mod:`.scim`), SAML (:mod:`.saml`) and the optional mail
adapter (:mod:`.smtp`) -- verify a protocol assertion and hand the engine one
``(idp_id, subject, claims)`` triple through :mod:`.idp_common`. They never hold
a principal record, a role or a credential of their own.
"""
