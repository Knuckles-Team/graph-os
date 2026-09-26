"""Native SAML SP: the served routes, and a known-bad corpus refused both ways.

Every refusal is built from the same helpers as the accepted baseline, so a
check that passes everything fails the baseline and a check that refuses
everything fails the corpus.
"""

from __future__ import annotations

import base64
import copy
import zlib
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from lxml import etree
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from starlette.applications import Starlette
from starlette.testclient import TestClient

from graph_os.identity.idp_common import (
    SESSION_COOKIE,
    EngineLoginCompleter,
    IdpDirectory,
    IdpRecord,
    OneShotStore,
)
from graph_os.identity.saml import (
    TX_COOKIE,
    SamlBroker,
    SamlSettings,
    parse_idp_metadata,
    sp_metadata,
)
from graph_os.identity.saml_assertion import NS, SamlError
from tests.identity.fakes import FakeIdentityPort, FakeSecrets, idp_wire
from tests.identity.saml_idp import (
    ACS,
    IDP_ENTITY,
    SP_ENTITY,
    SSO,
    TestIdp,
    assertion,
    response,
    to_bytes,
)

RID = "_req0123"
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
IDP = TestIdp()


def _settings(**over: Any) -> dict[str, Any]:
    return (
        SamlSettings(
            idp_entity_id=IDP_ENTITY,
            idp_sso_url=SSO,
            idp_certs=(IDP.cert_body,),
            sp_entity_id=SP_ENTITY,
            acs_url=ACS,
            attribute_paths={"groups": "groups"},
            username_attribute="uid",
        ).model_dump()
        | over
    )


def _world(**settings: Any) -> Any:
    port = FakeIdentityPort([idp_wire("corp", "saml", _settings(**settings))])
    secrets = FakeSecrets()
    broker = SamlBroker(
        directory=IdpDirectory(port),
        transactions=OneShotStore(secrets, "saml-tx"),
        replay=OneShotStore(secrets, "saml-replay", clock=lambda: NOW.timestamp()),
        completer=EngineLoginCompleter(port),
        clock=lambda: NOW.timestamp(),
    )
    client = TestClient(
        Starlette(routes=broker.routes()),
        base_url="https://graphos.example",
        follow_redirects=False,
    )
    record = IdpRecord.from_wire(port.idps[0])
    return SimpleNamespace(port=port, broker=broker, client=client, record=record)


def _signed_assertion(**over: Any) -> etree._Element:
    return IDP.sign(assertion(RID, NOW, **over))


def _accept(world: Any, xml: bytes) -> Any:
    return world.broker.accept(xml, world.record, RID)


def test_assertion_signed_response_is_accepted() -> None:
    world = _world()
    validated = _accept(world, to_bytes(response(RID, _signed_assertion())))

    assert validated.subject == "alice@corp.example"
    assert validated.attributes["groups"] == ("staff", "admins")


def test_response_level_signature_covers_the_assertion() -> None:
    world = _world()
    signed = IDP.sign(response(RID, assertion(RID, NOW)))

    assert _accept(world, to_bytes(signed)).subject == "alice@corp.example"


# ---------------------------------------------------------------------------
# The known-bad corpus
# ---------------------------------------------------------------------------
def _unsigned() -> bytes:
    return to_bytes(response(RID, assertion(RID, NOW)))


def _foreign_signer() -> bytes:
    return to_bytes(response(RID, TestIdp().sign(assertion(RID, NOW))))


def _tampered_name_id() -> bytes:
    doc = response(RID, _signed_assertion())
    doc.find(".//saml:NameID", NS).text = "admin@corp.example"
    return to_bytes(doc)


def _xsw_second_assertion() -> bytes:
    """XSW: keep the genuine signed assertion, append an evil unsigned one."""
    evil = assertion(RID, NOW, id="_evil", name_id="admin@corp.example")
    return to_bytes(response(RID, _signed_assertion(), evil))


def _xsw_evil_first_signature_moved() -> bytes:
    """XSW: evil assertion at the expected spot carries the genuine signature
    whose reference still points at the genuine assertion hidden inside it."""
    genuine = _signed_assertion()
    evil = assertion(RID, NOW, id="_evil", name_id="admin@corp.example")
    signature = genuine.find("ds:Signature", NS)
    evil.append(copy.deepcopy(signature))
    genuine.remove(signature)
    evil.append(genuine)
    return to_bytes(response(RID, evil))


def _xsw_signed_response_wrapping() -> bytes:
    """XSW on a response signature: the signed response is wrapped inside an
    unsigned one that carries the attacker's assertion."""
    signed = IDP.sign(response(RID, assertion(RID, NOW), response_id="_r1"))
    outer = response(RID, assertion(RID, NOW, id="_evil", name_id="admin@corp.example"))
    outer.set("ID", "_outer")
    outer.append(signed.find("ds:Signature", NS))
    outer.append(signed)
    return to_bytes(outer)


def _duplicate_ids() -> bytes:
    doc = response(RID, _signed_assertion(), response_id="_a1")
    return to_bytes(doc)


def _comment_in_name_id() -> bytes:
    xml = to_bytes(response(RID, _signed_assertion()))
    return xml.replace(b"alice@corp.example<", b"alice@corp.example<!-- -->.evil<", 1)


def _doctype_entity() -> bytes:
    body = to_bytes(response(RID, _signed_assertion())).decode()
    dtd = '<!DOCTYPE r [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
    return (dtd + body).encode()


def _billion_laughs() -> bytes:
    dtd = '<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;">]>'
    return (
        dtd + f"<samlp:Response xmlns:samlp='{NS['samlp']}'>&lol2;</samlp:Response>"
    ).encode()


def _sha1_digest() -> bytes:
    signed = IDP.sign(
        assertion(RID, NOW),
        method=SignatureMethod.RSA_SHA256,
        digest=DigestAlgorithm.SHA1,
    )
    return to_bytes(response(RID, signed))


def _rsa_sha1_signature() -> bytes:
    signed = IDP.sign(
        assertion(RID, NOW),
        method=SignatureMethod.RSA_SHA1,
        digest=DigestAlgorithm.SHA256,
    )
    return to_bytes(response(RID, signed))


def _encrypted() -> bytes:
    doc = response(RID, _signed_assertion())
    etree.SubElement(doc, f"{{{NS['saml']}}}EncryptedAssertion")
    return to_bytes(doc)


def _processing_instruction() -> bytes:
    body = to_bytes(response(RID, _signed_assertion()))
    return b'<?xml-stylesheet href="x"?>' + body


BAD: dict[str, Any] = {
    "unsigned": _unsigned,
    "signed by a key the IdP never published": _foreign_signer,
    "NameID changed after signing": _tampered_name_id,
    "XSW: second unsigned assertion appended": _xsw_second_assertion,
    "XSW: signature moved onto an evil assertion": _xsw_evil_first_signature_moved,
    "XSW: signed response wrapped by an unsigned one": _xsw_signed_response_wrapping,
    "duplicate ID attributes": _duplicate_ids,
    "comment splitting the NameID": _comment_in_name_id,
    "DOCTYPE with an external entity (XXE)": _doctype_entity,
    "entity expansion (billion laughs)": _billion_laughs,
    "SHA-1 digest": _sha1_digest,
    "RSA-SHA1 signature": _rsa_sha1_signature,
    "EncryptedAssertion": _encrypted,
    "processing instruction": _processing_instruction,
    "wrong issuer": lambda: to_bytes(
        response(RID, _signed_assertion(issuer="https://evil"))
    ),
    "wrong audience": lambda: to_bytes(
        response(RID, _signed_assertion(audience="https://x"))
    ),
    "wrong recipient": lambda: to_bytes(
        response(RID, _signed_assertion(recipient="https://x/acs"))
    ),
    "answers another request": lambda: to_bytes(
        response(RID, _signed_assertion(in_response_to="_other"))
    ),
    "bearer confirmation expired": lambda: to_bytes(
        response(RID, _signed_assertion(confirm_until="2026-09-24T11:50:00Z"))
    ),
    "conditions expired": lambda: to_bytes(
        response(RID, _signed_assertion(not_on_or_after="2026-09-24T11:50:00Z"))
    ),
    "conditions not yet valid": lambda: to_bytes(
        response(RID, _signed_assertion(not_before="2026-09-24T12:10:00Z"))
    ),
    "status is not Success": lambda: to_bytes(
        response(
            RID,
            _signed_assertion(),
            status="urn:oasis:names:tc:SAML:2.0:status:Requester",
        )
    ),
    "Destination is another SP": lambda: to_bytes(
        response(RID, _signed_assertion(), destination="https://other.example/acs")
    ),
}


@pytest.mark.parametrize("case", sorted(BAD))
def test_known_bad_responses_are_refused(case: str) -> None:
    world = _world()
    with pytest.raises(SamlError):
        _accept(world, BAD[case]())


def test_comment_trick_would_truncate_the_subject_without_the_refusal() -> None:
    """The comment case is refused for the right reason, not incidentally."""
    with pytest.raises(SamlError, match="comments"):
        _accept(_world(), _comment_in_name_id())


def test_assertion_replay_is_refused() -> None:
    world = _world()
    xml = to_bytes(response(RID, _signed_assertion()))

    world.broker.accept(xml, world.record, RID)
    with pytest.raises(SamlError, match="replayed"):
        world.broker.accept(xml, world.record, RID)


def test_certificate_rotation_accepts_either_configured_certificate() -> None:
    other = TestIdp()
    world = _world(idp_certs=[other.cert_body, IDP.cert_body])

    assert _accept(world, to_bytes(response(RID, _signed_assertion()))).subject


# ---------------------------------------------------------------------------
# The served routes
# ---------------------------------------------------------------------------
def _begin(world: Any) -> str:
    started = world.client.get("/auth/saml/corp/login")
    assert started.status_code == 303
    target = urlsplit(started.headers["location"])
    assert target.geturl().startswith(SSO)
    raw = base64.b64decode(parse_qs(target.query)["SAMLRequest"][0])
    request = etree.fromstring(zlib.decompress(raw, -15))
    assert request.get("AssertionConsumerServiceURL") == ACS
    assert "samesite=none" in started.headers["set-cookie"].lower()
    return str(request.get("ID"))


def _post(world: Any, xml: bytes) -> Any:
    body = {"SAMLResponse": base64.b64encode(xml).decode()}
    return world.client.post("/auth/saml/acs", data=body)


def test_sp_initiated_login_reaches_the_engine() -> None:
    world = _world()
    request_id = _begin(world)
    signed = IDP.sign(assertion(request_id, NOW))

    answer = _post(world, to_bytes(response(request_id, signed)))

    assert answer.headers["location"] == "/"
    assert SESSION_COOKIE in answer.cookies
    (login,) = world.port.ops("credential", "external_login")
    assert login["request"]["subject"] == "alice@corp.example"
    assert login["request"]["claims"] == {"groups": ["staff", "admins"]}
    assert login["request"]["username_hint"] == "alice"


def test_unsolicited_response_is_refused() -> None:
    world = _world()
    answer = _post(world, to_bytes(response(RID, _signed_assertion())))

    assert answer.headers["location"] == "/auth/login?error=stale_login"
    assert world.port.ops("credential", "external_login") == []


def test_pending_request_is_single_use() -> None:
    world = _world()
    request_id = _begin(world)
    xml = to_bytes(response(request_id, IDP.sign(assertion(request_id, NOW))))
    assert _post(world, xml).headers["location"] == "/"

    world.client.cookies.set(
        TX_COOKIE, request_id, domain="graphos.example", path="/auth/saml/acs"
    )
    assert _post(world, xml).headers["location"] == "/auth/login?error=stale_login"


def test_bad_response_on_the_route_never_reaches_the_engine() -> None:
    world = _world()
    request_id = _begin(world)
    evil = assertion(request_id, NOW, id="_evil", name_id="admin@corp.example")
    xml = to_bytes(response(request_id, IDP.sign(assertion(request_id, NOW)), evil))

    assert _post(world, xml).headers["location"] == "/auth/login?error=idp_unverified"
    assert world.port.ops("credential", "external_login") == []


def test_sp_metadata_and_idp_metadata_import() -> None:
    settings = SamlSettings.model_validate(_settings())
    sp = etree.fromstring(sp_metadata(settings).encode())
    acs = sp.find("md:SPSSODescriptor/md:AssertionConsumerService", NS)
    assert sp.get("entityID") == SP_ENTITY and acs.get("Location") == ACS

    idp_md = f"""<md:EntityDescriptor xmlns:md="{NS["md"]}" xmlns:ds="{NS["ds"]}" entityID="{IDP_ENTITY}">
<md:IDPSSODescriptor protocolSupportEnumeration="urn:oasis:names:tc:SAML:2.0:protocol">
<md:KeyDescriptor use="signing"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>{IDP.cert_body}</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
<md:KeyDescriptor use="encryption"><ds:KeyInfo><ds:X509Data><ds:X509Certificate>AAAA</ds:X509Certificate></ds:X509Data></ds:KeyInfo></md:KeyDescriptor>
<md:SingleSignOnService Binding="urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect" Location="{SSO}"/>
</md:IDPSSODescriptor></md:EntityDescriptor>"""
    imported = parse_idp_metadata(idp_md.encode())

    assert imported == {
        "idp_entity_id": IDP_ENTITY,
        "idp_sso_url": SSO,
        "idp_certs": [IDP.cert_body],
    }
    with pytest.raises(ValueError):
        parse_idp_metadata(
            b'<!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><x>&e;</x>'
        )


def test_settings_refuse_plain_http_and_bad_certificates() -> None:
    with pytest.raises(ValueError):
        SamlSettings.model_validate(
            _settings(acs_url="http://graphos.example/auth/saml/acs")
        )
    with pytest.raises(ValueError):
        SamlSettings.model_validate(_settings(idp_certs=["not base64!"]))
    with pytest.raises(ValueError):
        SamlSettings.model_validate(_settings(idp_certs=[]))


def test_response_older_than_skew_is_refused_even_if_signed() -> None:
    world = _world()
    late = NOW - timedelta(hours=1)
    xml = to_bytes(response(RID, IDP.sign(assertion(RID, late))))

    with pytest.raises(SamlError):
        _accept(world, xml)
