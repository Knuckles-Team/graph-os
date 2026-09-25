"""SAML 2.0 Web SSO response validation for the native SP (IDM-15).

Library choice: XML signature verification is delegated to ``signxml``
(pinned; pure Python over ``lxml`` + ``cryptography``, actively maintained,
built around "see what is signed": it returns the verified subtree and nothing
else). The SAML-profile checks around it are few and explicit, and live here:

1. **Parse hardened.** ``lxml`` with entity resolution, DTD loading and network
   access off; a document with a DOCTYPE, a comment or a processing instruction
   is refused outright (entity expansion, XXE, and the comment-truncation
   NameID trick all need one of them).
2. **One assertion, unique IDs.** The whole document must hold exactly one
   ``saml:Assertion``, no ``EncryptedAssertion``, and no repeated ``ID``. Every
   XML-signature-wrapping (XSW) variant needs a second assertion or a
   duplicated ID somewhere in the document.
3. **See what is signed.** If the ``Response`` carries a signature it is
   verified at ``./ds:Signature`` and its SINGLE direct child assertion is
   taken from the VERIFIED copy; otherwise the assertion's own signature is
   verified at ``./saml:Assertion/ds:Signature`` and the VERIFIED copy is used.
   Nothing outside the verified copy is ever read, except the ``Status`` and
   ``Destination`` sanity checks on the (possibly unsigned) wrapper.
4. **Algorithms.** RSA/RSA-PSS/ECDSA with SHA-256+ only; SHA-1 and HMAC are
   refused. The key is one of the IdP's configured certificates (metadata),
   never one carried in the message.
5. **Profile checks** (SAML profiles §4.1.4.2-3): issuer, audience, the bearer
   ``SubjectConfirmationData`` ``Recipient``/``NotOnOrAfter``/``InResponseTo``,
   ``Conditions`` ``NotBefore``/``NotOnOrAfter`` with a bounded clock skew, and
   an ``AuthnStatement``. Unsolicited (IdP-initiated) responses are refused.

The replay cache (assertion ``ID`` seen at most once until it expires) and the
request/browser binding live in :mod:`.saml`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from lxml import etree
from signxml import InvalidInput, InvalidSignature, SignatureConfiguration, XMLVerifier
from signxml.algorithms import DigestAlgorithm, SignatureMethod
from signxml.exceptions import InvalidCertificate, InvalidDigest

__all__ = [
    "NS",
    "Expectation",
    "SamlError",
    "ValidatedAssertion",
    "parse_document",
    "validate_response",
]

NS = {
    "samlp": "urn:oasis:names:tc:SAML:2.0:protocol",
    "saml": "urn:oasis:names:tc:SAML:2.0:assertion",
    "ds": "http://www.w3.org/2000/09/xmldsig#",
    "md": "urn:oasis:names:tc:SAML:2.0:metadata",
}
_SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
_BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
MAX_DOCUMENT_BYTES = 256 * 1024
_SIGNATURE_METHODS = frozenset(
    {
        SignatureMethod.RSA_SHA256,
        SignatureMethod.RSA_SHA384,
        SignatureMethod.RSA_SHA512,
        SignatureMethod.SHA256_RSA_MGF1,
        SignatureMethod.SHA384_RSA_MGF1,
        SignatureMethod.SHA512_RSA_MGF1,
        SignatureMethod.ECDSA_SHA256,
        SignatureMethod.ECDSA_SHA384,
        SignatureMethod.ECDSA_SHA512,
    }
)
_DIGESTS = frozenset(
    {DigestAlgorithm.SHA256, DigestAlgorithm.SHA384, DigestAlgorithm.SHA512}
)
_VERIFY_ERRORS = (
    InvalidSignature,
    InvalidInput,
    InvalidCertificate,
    InvalidDigest,
    ValueError,
)


class SamlError(ValueError):
    """The response failed a structural, signature or profile check."""


@dataclass(frozen=True)
class ValidatedAssertion:
    """What the SP trusts, all read from the verified assertion copy."""

    assertion_id: str
    subject: str
    attributes: Mapping[str, tuple[str, ...]]
    not_on_or_after: datetime


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_blank_text=False,
    )


def parse_document(xml: bytes) -> etree._Element:
    """Parse ``xml`` refusing every construct no SAML message needs."""
    if len(xml) > MAX_DOCUMENT_BYTES:
        raise SamlError("document too large")
    if b"<!DOCTYPE" in xml.upper():
        raise SamlError("a DOCTYPE is not allowed")
    try:
        root = etree.fromstring(xml, parser=_parser())
    except etree.XMLSyntaxError:
        raise SamlError("malformed XML") from None
    if root.getroottree().docinfo.doctype:
        raise SamlError("a DOCTYPE is not allowed")
    if root.xpath("//comment() | //processing-instruction()"):
        raise SamlError("comments and processing instructions are not allowed")
    return root


def _text(element: etree._Element) -> str:
    return "".join(element.itertext()).strip()


def _only(elements: Iterable[etree._Element], what: str) -> etree._Element:
    found = list(elements)
    if len(found) != 1:
        raise SamlError(f"expected exactly one {what}, found {len(found)}")
    return found[0]


def _check_structure(root: etree._Element) -> None:
    if root.tag != f"{{{NS['samlp']}}}Response":
        raise SamlError("not a samlp:Response")
    ids = [str(value) for value in root.xpath("//@ID")]
    if len(ids) != len(set(ids)):
        raise SamlError("duplicate ID attributes")
    if root.xpath("//saml:EncryptedAssertion", namespaces=NS):
        raise SamlError("encrypted assertions are not supported")
    _only(
        root.xpath("//saml:Assertion", namespaces=NS), "saml:Assertion in the document"
    )


def _verify_at(xml: bytes, cert: str, location: str) -> etree._Element:
    config = SignatureConfiguration(
        location=location,
        expect_references=1,
        signature_methods=_SIGNATURE_METHODS,
        digest_algorithms=_DIGESTS,
    )
    result = XMLVerifier().verify(xml, x509_cert=cert, expect_config=config)
    signed = getattr(result, "signed_xml", None)
    if signed is None:
        raise SamlError("the signature covers no element")
    return signed


def _verified(xml: bytes, certs: Iterable[str], location: str) -> etree._Element:
    for cert in certs:
        try:
            return _verify_at(xml, cert, location)
        except _VERIFY_ERRORS:
            continue
    raise SamlError("no configured IdP certificate verifies the signature")


def _signed_assertion(
    xml: bytes, root: etree._Element, certs: tuple[str, ...]
) -> etree._Element:
    if root.find("ds:Signature", NS) is not None:
        response = _verified(xml, certs, "./")
        if response.tag != root.tag:
            raise SamlError("the response signature covers another element")
        return _only(response.findall("saml:Assertion", NS), "signed assertion")
    assertion = _verified(xml, certs, f"./{{{NS['saml']}}}Assertion/")
    if assertion.tag != f"{{{NS['saml']}}}Assertion":
        raise SamlError("the assertion signature covers another element")
    return assertion


def _instant(value: str | None, what: str) -> datetime:
    if not value:
        raise SamlError(f"{what} is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise SamlError(f"{what} is not an xs:dateTime") from None
    if parsed.tzinfo is None:
        raise SamlError(f"{what} has no time zone")
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class Expectation:
    """What this SP expects of one response."""

    idp_entity_id: str
    sp_entity_id: str
    acs_url: str
    request_id: str
    now: datetime
    skew: timedelta


def _check_wrapper(root: etree._Element, expect: Expectation) -> None:
    status = root.find("samlp:Status/samlp:StatusCode", NS)
    if status is None or status.get("Value") != _SUCCESS:
        raise SamlError("the IdP did not answer Success")
    destination = root.get("Destination")
    if destination is not None and destination != expect.acs_url:
        raise SamlError("Destination is not this ACS")
    in_response_to = root.get("InResponseTo")
    if in_response_to is not None and in_response_to != expect.request_id:
        raise SamlError("InResponseTo is not the pending request")


def _check_issuer(assertion: etree._Element, expect: Expectation) -> None:
    issuer = assertion.find("saml:Issuer", NS)
    if issuer is None or _text(issuer) != expect.idp_entity_id:
        raise SamlError("assertion Issuer is not the configured IdP")


def _check_conditions(assertion: etree._Element, expect: Expectation) -> None:
    conditions = assertion.find("saml:Conditions", NS)
    if conditions is None:
        raise SamlError("assertion has no Conditions")
    not_before = conditions.get("NotBefore")
    if not_before and _instant(not_before, "NotBefore") > expect.now + expect.skew:
        raise SamlError("assertion is not yet valid")
    if (
        _instant(conditions.get("NotOnOrAfter"), "NotOnOrAfter")
        <= expect.now - expect.skew
    ):
        raise SamlError("assertion has expired")
    audiences = {
        _text(a)
        for a in conditions.findall("saml:AudienceRestriction/saml:Audience", NS)
    }
    if expect.sp_entity_id not in audiences:
        raise SamlError("this SP is not an audience of the assertion")


def _bearer_data(assertion: etree._Element) -> etree._Element:
    confirmations = assertion.findall("saml:Subject/saml:SubjectConfirmation", NS)
    bearers = [c for c in confirmations if c.get("Method") == _BEARER]
    data = _only(bearers, "bearer SubjectConfirmation").find(
        "saml:SubjectConfirmationData", NS
    )
    if data is None:
        raise SamlError("bearer confirmation has no SubjectConfirmationData")
    return data


def _check_bearer(assertion: etree._Element, expect: Expectation) -> datetime:
    data = _bearer_data(assertion)
    if data.get("Recipient") != expect.acs_url:
        raise SamlError("Recipient is not this ACS")
    if data.get("InResponseTo") != expect.request_id:
        raise SamlError("the assertion does not answer the pending request")
    expires = _instant(data.get("NotOnOrAfter"), "SubjectConfirmationData NotOnOrAfter")
    if expires <= expect.now - expect.skew:
        raise SamlError("the bearer confirmation has expired")
    not_before = data.get("NotBefore")
    if not_before and _instant(not_before, "NotBefore") > expect.now + expect.skew:
        raise SamlError("the bearer confirmation is not yet valid")
    return expires


def _attributes(assertion: etree._Element) -> dict[str, tuple[str, ...]]:
    attributes: dict[str, tuple[str, ...]] = {}
    for attribute in assertion.findall("saml:AttributeStatement/saml:Attribute", NS):
        values = tuple(_text(v) for v in attribute.findall("saml:AttributeValue", NS))
        name = str(attribute.get("Name", ""))
        attributes[name] = attributes.get(name, ()) + tuple(v for v in values if v)
    return attributes


def _subject(assertion: etree._Element) -> str:
    name_id = assertion.find("saml:Subject/saml:NameID", NS)
    subject = _text(name_id) if name_id is not None else ""
    if not subject or len(subject) > 255:
        raise SamlError("the assertion has no usable NameID")
    return subject


def validate_response(
    xml: bytes, certs: tuple[str, ...], expect: Expectation
) -> ValidatedAssertion:
    """Every check above, in order; the result is read only from signed data."""
    root = parse_document(xml)
    _check_structure(root)
    assertion = _signed_assertion(xml, root, certs)
    _check_wrapper(root, expect)
    _check_issuer(assertion, expect)
    _check_conditions(assertion, expect)
    expires = _check_bearer(assertion, expect)
    if assertion.find("saml:AuthnStatement", NS) is None:
        raise SamlError("the assertion has no AuthnStatement")
    assertion_id = assertion.get("ID")
    if not assertion_id:
        raise SamlError("the assertion has no ID")
    return ValidatedAssertion(
        assertion_id=assertion_id,
        subject=_subject(assertion),
        attributes=_attributes(assertion),
        not_on_or_after=expires,
    )
