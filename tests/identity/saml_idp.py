"""A test SAML IdP: a self-signed key pair and response/assertion builders.

Assertions are signed with exclusive C14N (the SAML norm) so a signed
assertion can be embedded into, or moved within, a response -- which is what
the signature-wrapping corpus does.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from lxml import etree
from signxml import XMLSigner
from signxml.algorithms import DigestAlgorithm, SignatureMethod

from graph_os.identity.saml_assertion import NS

IDP_ENTITY = "https://idp.corp.example/saml"
SP_ENTITY = "https://graphos.example/saml/sp"
ACS = "https://graphos.example/auth/saml/acs"
SSO = "https://idp.corp.example/saml/sso"
EXC_C14N = "http://www.w3.org/2001/10/xml-exc-c14n#"


class _LegacySigner(XMLSigner):
    """Signs with SHA-1 too, like a legacy IdP would (signxml refuses by default)."""

    def check_deprecated_methods(self) -> None:
        return None


class TestIdp:
    """One signing identity."""

    __test__ = False

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "idp.corp.example")])
        now = datetime.now(UTC)
        self.cert = (
            x509.CertificateBuilder()
            .subject_name(name)
            .issuer_name(name)
            .public_key(self.key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=365))
            .sign(self.key, hashes.SHA256())
        )

    @property
    def cert_pem(self) -> str:
        return self.cert.public_bytes(serialization.Encoding.PEM).decode()

    @property
    def cert_body(self) -> str:
        der = self.cert.public_bytes(serialization.Encoding.DER)
        return base64.b64encode(der).decode()

    def sign(
        self,
        element: etree._Element,
        *,
        method: Any = SignatureMethod.RSA_SHA256,
        digest: Any = DigestAlgorithm.SHA256,
    ) -> etree._Element:
        signer = _LegacySigner(
            signature_algorithm=method, digest_algorithm=digest, c14n_algorithm=EXC_C14N
        )
        pem_key = self.key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return signer.sign(
            element, key=pem_key, cert=self.cert_pem, reference_uri=element.get("ID")
        )


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def assertion(request_id: str, now: datetime, **over: Any) -> etree._Element:
    """A valid bearer assertion; ``over`` replaces any one field."""
    f = {
        "id": "_a1",
        "issuer": IDP_ENTITY,
        "name_id": "alice@corp.example",
        "recipient": ACS,
        "in_response_to": request_id,
        "confirm_until": _iso(now + timedelta(minutes=5)),
        "not_before": _iso(now - timedelta(minutes=1)),
        "not_on_or_after": _iso(now + timedelta(minutes=5)),
        "audience": SP_ENTITY,
        "groups": ("staff", "admins"),
    } | over
    groups = "".join(f"<saml:AttributeValue>{g}</saml:AttributeValue>" for g in f["groups"])
    xml = f"""<saml:Assertion xmlns:saml="{NS['saml']}" ID="{f['id']}" Version="2.0" IssueInstant="{_iso(now)}">
<saml:Issuer>{f['issuer']}</saml:Issuer>
<saml:Subject><saml:NameID Format="urn:oasis:names:tc:SAML:2.0:nameid-format:persistent">{f['name_id']}</saml:NameID>
<saml:SubjectConfirmation Method="urn:oasis:names:tc:SAML:2.0:cm:bearer">
<saml:SubjectConfirmationData Recipient="{f['recipient']}" InResponseTo="{f['in_response_to']}" NotOnOrAfter="{f['confirm_until']}"/>
</saml:SubjectConfirmation></saml:Subject>
<saml:Conditions NotBefore="{f['not_before']}" NotOnOrAfter="{f['not_on_or_after']}">
<saml:AudienceRestriction><saml:Audience>{f['audience']}</saml:Audience></saml:AudienceRestriction>
</saml:Conditions>
<saml:AuthnStatement AuthnInstant="{_iso(now)}"/>
<saml:AttributeStatement>
<saml:Attribute Name="groups">{groups}</saml:Attribute>
<saml:Attribute Name="uid"><saml:AttributeValue>alice</saml:AttributeValue></saml:Attribute>
</saml:AttributeStatement>
</saml:Assertion>"""
    return etree.fromstring(xml.encode())


def response(
    request_id: str,
    *children: etree._Element,
    destination: str = ACS,
    status: str = "urn:oasis:names:tc:SAML:2.0:status:Success",
    response_id: str = "_r1",
) -> etree._Element:
    xml = f"""<samlp:Response xmlns:samlp="{NS['samlp']}" xmlns:saml="{NS['saml']}" ID="{response_id}" Version="2.0" IssueInstant="2026-09-24T00:00:00Z" Destination="{destination}" InResponseTo="{request_id}">
<saml:Issuer>{IDP_ENTITY}</saml:Issuer>
<samlp:Status><samlp:StatusCode Value="{status}"/></samlp:Status>
</samlp:Response>"""
    root = etree.fromstring(xml.encode())
    for child in children:
        root.append(child)
    return root


def to_bytes(element: etree._Element) -> bytes:
    return etree.tostring(element)
