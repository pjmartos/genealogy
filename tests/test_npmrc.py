import pytest

from stemmata.errors import SchemaError
from stemmata.npmrc import NpmConfig, parse_npmrc, _canonicalize_url


def test_basic_key_value():
    entries = parse_npmrc("registry=https://registry.example.com/\n", env={})
    assert entries == {"registry": "https://registry.example.com/"}


def test_comments_and_blank_lines():
    text = """
# full line comment
; semicolon comment
registry=https://registry.example.com/
@acme:registry=https://private.example/  # trailing comment
"""
    entries = parse_npmrc(text, env={})
    assert entries["registry"] == "https://registry.example.com/"
    assert entries["@acme:registry"] == "https://private.example/"


def test_whitespace_tolerance():
    entries = parse_npmrc("   registry   =   https://x.y/   \n", env={})
    assert entries["registry"] == "https://x.y/"


def test_var_substitution():
    entries = parse_npmrc("//host/:_authToken=${TOKEN}\n", env={"TOKEN": "abc"})
    assert entries["//host/:_authToken"] == "abc"


def test_var_substitution_undefined_raises():
    with pytest.raises(SchemaError):
        parse_npmrc("//host/:_authToken=${MISSING}\n", env={})


def test_dollar_escape():
    entries = parse_npmrc("foo=$$LITERAL\n", env={})
    assert entries["foo"] == "$LITERAL"


def test_quoted_values():
    entries = parse_npmrc('foo="a b c"\n', env={})
    assert entries["foo"] == "a b c"


def test_last_wins_duplicate_keys():
    entries = parse_npmrc("registry=one\nregistry=two\n", env={})
    assert entries["registry"] == "two"


def test_canonicalize_url_strips_scheme_and_trailing_slash():
    assert _canonicalize_url("https://HOST.Com/path/") == "//host.com/path"
    assert _canonicalize_url("http://host.com:8080/a") == "//host.com:8080/a"


def test_auth_longest_prefix_wins():
    cfg = NpmConfig(entries={
        "//host.com/:_authToken": "short",
        "//host.com/scope/:_authToken": "long",
    })
    auth = cfg.auth_for_url("https://host.com/scope/pkg/-/pkg-1.0.0.tgz")
    assert auth.auth_token == "long"


def test_auth_scoped_registry_resolution():
    cfg = NpmConfig(entries={
        "registry": "https://default.example/",
        "@acme:registry": "https://private.example/repo/",
    })
    assert cfg.registry_for_scope("@acme") == "https://private.example/repo/"
    assert cfg.registry_for_scope("@other") == "https://default.example/"


def test_auth_basic_from_username_password():
    import base64
    encoded = base64.b64encode(b"hunter2").decode()
    cfg = NpmConfig(entries={
        "//host.com/:username": "alice",
        "//host.com/:_password": encoded,
    })
    auth = cfg.auth_for_url("https://host.com/pkg/")
    assert auth.username == "alice"
    assert auth.password_b64 == encoded


def test_unknown_keys_ignored():
    entries = parse_npmrc("strict-ssl=true\nregistry=https://x/\n", env={})
    assert entries["strict-ssl"] == "true"
    assert entries["registry"] == "https://x/"


def test_crlf_tolerated():
    entries = parse_npmrc("registry=https://x/\r\n", env={})
    assert entries["registry"] == "https://x/"


def test_bom_tolerated():
    entries = parse_npmrc("\ufeffregistry=https://x/\n", env={})
    assert entries["registry"] == "https://x/"
