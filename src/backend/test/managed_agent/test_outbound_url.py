"""F078 T-07: outbound URL canonicalization, SSRF and allowlist."""

import pytest

from bisheng.common.security.outbound_url import (
    OutboundUrlRejected,
    canonicalize_endpoint,
    endpoint_hash,
    native_endpoint_hash,
    validate_outbound_url,
)


def test_canonicalize_lowercases_and_strips_default_port_and_slash():
    assert canonicalize_endpoint("HTTPS://Agents.Example.com:443/v1/") == "https://agents.example.com/v1"
    assert canonicalize_endpoint("http://agents.example.com:8080/v1/") == "http://agents.example.com:8080/v1"
    assert endpoint_hash("https://Agents.Example.com:443/v1/") == endpoint_hash("https://agents.example.com/v1")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://agents.example.com/v1",
        "https://user:secret@agents.example.com/v1",
        "https://agents.example.com/v1?x=1",
        "https://agents.example.com/v1#frag",
        "https:///v1",
    ],
)
def test_canonicalize_rejects_dangerous_shapes(url):
    with pytest.raises(OutboundUrlRejected):
        canonicalize_endpoint(url)


def test_public_address_allowed_with_allowlist():
    canonical = validate_outbound_url(
        "https://agents.example.com/v1",
        allowlist=["agents.example.com"],
        resolver=lambda host, port: ["93.184.216.34"],
    )
    assert canonical == "https://agents.example.com/v1"


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "127.0.0.1",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "::1",
        "fc00::1",
        "::ffff:10.0.0.1",
    ],
)
def test_non_public_addresses_rejected(address):
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_url(
            "https://agents.example.com/v1",
            allowlist=["agents.example.com"],
            resolver=lambda host, port: [address],
        )


def test_one_bad_answer_rejects_the_whole_host():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_url(
            "https://agents.example.com/v1",
            allowlist=["agents.example.com"],
            resolver=lambda host, port: ["93.184.216.34", "10.0.0.1"],
        )


def test_empty_allowlist_rejects_everything():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_url(
            "https://agents.example.com/v1",
            allowlist=[],
            resolver=lambda host, port: ["93.184.216.34"],
        )


def test_allowlist_port_must_match_when_specified():
    with pytest.raises(OutboundUrlRejected):
        validate_outbound_url(
            "https://agents.example.com:8443/v1",
            allowlist=["agents.example.com:443"],
            resolver=lambda host, port: ["93.184.216.34"],
        )


def test_private_networks_can_be_enabled_for_tests_only():
    canonical = validate_outbound_url(
        "http://127.0.0.1:9000/v1",
        allowlist=["127.0.0.1:9000"],
        allow_private_networks=True,
        resolver=lambda host, port: ["127.0.0.1"],
    )
    assert canonical == "http://127.0.0.1:9000/v1"


def test_native_hash_is_stable_and_not_empty():
    first = native_endpoint_hash(10, "flow-1")
    assert first == native_endpoint_hash(10, "flow-1")
    assert first != native_endpoint_hash(10, "flow-2")
    assert len(first) == 64
