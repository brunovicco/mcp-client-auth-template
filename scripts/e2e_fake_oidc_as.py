"""Local-only OIDC authorization server used by the cross-repository E2E suite."""

import base64
import hashlib
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

_ISSUER = os.environ["FAKE_OIDC_ISSUER"].rstrip("/")
_KID = "mcp-e2e-rsa-1"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_NUMBERS = _PRIVATE_KEY.public_key().public_numbers()

_AUTHORIZATION_CODES: dict[str, dict[str, str]] = {}
_REGISTRATION_COUNT = 0
_AUTHORIZATION_COUNT = 0
_TOKEN_EXCHANGE_COUNT = 0
_AUTHORIZATION_SCOPES: list[str] = []
_CLIENT_CREDENTIALS_EXCHANGE_COUNT = 0
_CLIENT_CREDENTIALS_SCOPES: list[str] = []
_AUTHORIZATION_RESPONSE_ISSUER_OVERRIDE: str | None = None
_CLIENT_ID_METADATA_DOCUMENT_SUPPORTED = False
_CIMD_CLIENT_ID = "https://client.example.invalid/oauth/client-metadata.json"
_MACHINE_CLIENT_ID = "mcp-e2e-machine"
_MACHINE_CLIENT_CREDENTIAL = "e2e-test-credential"


def _b64url_uint(value: int) -> str:
    """Encode an unsigned integer using JWK base64url rules."""
    width = max(1, (value.bit_length() + 7) // 8)
    encoded = base64.urlsafe_b64encode(value.to_bytes(width, "big"))
    return encoded.rstrip(b"=").decode("ascii")


def _public_jwk() -> dict[str, str]:
    """Return the fake server's public signing key in JWK form."""
    return {
        "kty": "RSA",
        "use": "sig",
        "alg": "RS256",
        "kid": _KID,
        "n": _b64url_uint(_PUBLIC_NUMBERS.n),
        "e": _b64url_uint(_PUBLIC_NUMBERS.e),
    }


def _form_values(body: bytes) -> dict[str, str]:
    """Decode an application/x-www-form-urlencoded body to single values."""
    values = parse_qs(body.decode("utf-8"), keep_blank_values=True)
    return {key: items[-1] for key, items in values.items() if items}


def _basic_credentials(request: Request) -> tuple[str, str] | None:
    """Decode an HTTP Basic client credential without retaining or logging it."""
    authorization = request.headers.get("authorization", "")
    scheme, _, encoded = authorization.partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return None
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    client_id, separator, client_secret = decoded.partition(":")
    if not separator:
        return None
    return client_id, client_secret


def _pkce_challenge(verifier: str) -> str:
    """Return the RFC 7636 S256 challenge for ``verifier``."""
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _sign_access_token(
    *,
    issuer: str,
    audience: str,
    scope: str | None,
    expires_in: int,
    client_id: str = "mcp-e2e-client",
    subject: str = "e2e-user",
) -> str:
    """Mint an RS256 JWT accepted by the companion server's generic verifier."""
    now = int(time.time())
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject,
        "azp": client_id,
        "iat": now,
        "exp": now + expires_in,
    }
    if scope is not None:
        claims["scope"] = scope
    return jwt.encode(claims, _PRIVATE_KEY, algorithm="RS256", headers={"kid": _KID})


async def metadata(_: Request) -> Response:
    """Publish OIDC discovery plus the capabilities exercised by the MCP client."""
    return JSONResponse(
        {
            "issuer": _ISSUER,
            "authorization_endpoint": f"{_ISSUER}/authorize",
            "token_endpoint": f"{_ISSUER}/token",
            "jwks_uri": f"{_ISSUER}/jwks",
            "registration_endpoint": f"{_ISSUER}/register",
            "scopes_supported": [
                "mcp:tools:call",
                "mcp:tools:health",
                "mcp:tools:list",
            ],
            "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "client_credentials"],
            "token_endpoint_auth_methods_supported": ["none", "client_secret_basic"],
            "code_challenge_methods_supported": ["S256"],
            "authorization_response_iss_parameter_supported": True,
            "client_id_metadata_document_supported": _CLIENT_ID_METADATA_DOCUMENT_SUPPORTED,
        }
    )


async def jwks(_: Request) -> Response:
    """Publish the public RSA key used for access-token signatures."""
    return JSONResponse({"keys": [_public_jwk()]})


async def register(request: Request) -> Response:
    """Implement enough RFC 7591 DCR for the generic MCP client path."""
    global _REGISTRATION_COUNT
    if _CLIENT_ID_METADATA_DOCUMENT_SUPPORTED:
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid_client_metadata"}, status_code=400)

    redirect_uris = payload.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not redirect_uris:
        return JSONResponse({"error": "invalid_redirect_uri"}, status_code=400)

    _REGISTRATION_COUNT += 1
    return JSONResponse(
        {
            "client_id": "mcp-e2e-client",
            "redirect_uris": redirect_uris,
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
            "scope": payload.get("scope"),
            "application_type": "native",
        },
        status_code=201,
    )


async def authorize(request: Request) -> Response:
    """Issue an authorization code and redirect without requiring a real browser/user."""
    global _AUTHORIZATION_COUNT
    query = request.query_params
    required = ("client_id", "redirect_uri", "state", "code_challenge")
    missing = [name for name in required if not query.get(name)]
    if missing:
        return JSONResponse(
            {"error": "invalid_request", "error_description": f"missing: {', '.join(missing)}"},
            status_code=400,
        )
    if query.get("code_challenge_method") != "S256":
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if _CLIENT_ID_METADATA_DOCUMENT_SUPPORTED and query.get("client_id") != _CIMD_CLIENT_ID:
        return JSONResponse({"error": "invalid_client"}, status_code=400)

    requested_scope = query.get("scope", "")
    code = secrets.token_urlsafe(24)
    _AUTHORIZATION_CODES[code] = {
        "client_id": query["client_id"],
        "redirect_uri": query["redirect_uri"],
        "code_challenge": query["code_challenge"],
        "resource": query.get("resource", ""),
        "scope": requested_scope,
    }
    _AUTHORIZATION_COUNT += 1
    _AUTHORIZATION_SCOPES.append(requested_scope)
    callback_issuer = _AUTHORIZATION_RESPONSE_ISSUER_OVERRIDE or _ISSUER
    callback_query = urlencode({"code": code, "state": query["state"], "iss": callback_issuer})
    separator = "&" if "?" in query["redirect_uri"] else "?"
    return RedirectResponse(f"{query['redirect_uri']}{separator}{callback_query}", status_code=302)


async def token(request: Request) -> Response:
    """Issue a resource-bound JWT for an authorization code or fixed machine credential."""
    global _CLIENT_CREDENTIALS_EXCHANGE_COUNT, _TOKEN_EXCHANGE_COUNT
    form = _form_values(await request.body())
    grant_type = form.get("grant_type")
    if grant_type == "client_credentials":
        credentials = _basic_credentials(request)
        if credentials is None:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        client_id, client_secret = credentials
        if not (
            secrets.compare_digest(client_id, _MACHINE_CLIENT_ID)
            and secrets.compare_digest(client_secret, _MACHINE_CLIENT_CREDENTIAL)
        ):
            return JSONResponse({"error": "invalid_client"}, status_code=401)

        resource = form.get("resource", "")
        if not resource:
            return JSONResponse({"error": "invalid_target"}, status_code=400)
        requested_scope = form.get("scope", "")
        _TOKEN_EXCHANGE_COUNT += 1
        _CLIENT_CREDENTIALS_EXCHANGE_COUNT += 1
        _CLIENT_CREDENTIALS_SCOPES.append(requested_scope)
        access_token = _sign_access_token(
            issuer=_ISSUER,
            audience=resource,
            scope=requested_scope or None,
            expires_in=300,
            client_id=client_id,
            subject=client_id,
        )
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 300,
                "scope": requested_scope or None,
            }
        )

    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    code = form.get("code")
    record = _AUTHORIZATION_CODES.pop(code, None) if code is not None else None
    if record is None:
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    if (
        form.get("client_id") != record["client_id"]
        or form.get("redirect_uri") != record["redirect_uri"]
    ):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    verifier = form.get("code_verifier")
    if verifier is None or not secrets.compare_digest(
        _pkce_challenge(verifier), record["code_challenge"]
    ):
        return JSONResponse({"error": "invalid_grant"}, status_code=400)

    resource = form.get("resource", "")
    if not resource or resource != record["resource"]:
        return JSONResponse({"error": "invalid_target"}, status_code=400)

    _TOKEN_EXCHANGE_COUNT += 1
    access_token = _sign_access_token(
        issuer=_ISSUER,
        audience=resource,
        scope=record["scope"] or None,
        expires_in=300,
        client_id=record["client_id"],
    )
    return JSONResponse(
        {
            "access_token": access_token,
            "token_type": "Bearer",
            "expires_in": 300,
            "scope": record["scope"] or None,
        }
    )


async def configure(request: Request) -> Response:
    """Adjust negative-test behavior without restarting the local fake AS."""
    global _AUTHORIZATION_RESPONSE_ISSUER_OVERRIDE, _CLIENT_ID_METADATA_DOCUMENT_SUPPORTED
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    issuer = payload.get("authorization_response_iss")
    if issuer is not None and not isinstance(issuer, str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    _AUTHORIZATION_RESPONSE_ISSUER_OVERRIDE = issuer

    if "client_id_metadata_document_supported" in payload:
        cimd_supported = payload["client_id_metadata_document_supported"]
        if not isinstance(cimd_supported, bool):
            return JSONResponse({"error": "invalid_request"}, status_code=400)
        _CLIENT_ID_METADATA_DOCUMENT_SUPPORTED = cimd_supported
    return JSONResponse({"ok": True})


async def mint(request: Request) -> Response:
    """Mint deliberately malformed/under-scoped tokens for resource-server rejection tests."""
    payload = await request.json()
    if not isinstance(payload, dict):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    audience = payload.get("audience")
    issuer = payload.get("issuer")
    scope = payload.get("scope", "mcp:tools:call")
    expires_in = payload.get("expires_in", 300)
    if not isinstance(audience, str) or not isinstance(issuer, str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if scope is not None and not isinstance(scope, str):
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    if not isinstance(expires_in, int):
        return JSONResponse({"error": "invalid_request"}, status_code=400)

    return JSONResponse(
        {
            "access_token": _sign_access_token(
                issuer=issuer,
                audience=audience,
                scope=scope,
                expires_in=expires_in,
            )
        }
    )


async def state(_: Request) -> Response:
    """Expose aggregate counters used to prove the real DCR/authorization/token path ran."""
    return JSONResponse(
        {
            "registrations": _REGISTRATION_COUNT,
            "authorizations": _AUTHORIZATION_COUNT,
            "token_exchanges": _TOKEN_EXCHANGE_COUNT,
            "authorization_scopes": list(_AUTHORIZATION_SCOPES),
            "client_credentials_exchanges": _CLIENT_CREDENTIALS_EXCHANGE_COUNT,
            "client_credentials_scopes": list(_CLIENT_CREDENTIALS_SCOPES),
        }
    )


app = Starlette(
    routes=[
        Route("/.well-known/openid-configuration", metadata, methods=["GET"]),
        Route("/.well-known/oauth-authorization-server", metadata, methods=["GET"]),
        Route("/jwks", jwks, methods=["GET"]),
        Route("/register", register, methods=["POST"]),
        Route("/authorize", authorize, methods=["GET"]),
        Route("/token", token, methods=["POST"]),
        Route("/__test__/configure", configure, methods=["POST"]),
        Route("/__test__/mint", mint, methods=["POST"]),
        Route("/__test__/state", state, methods=["GET"]),
    ]
)
