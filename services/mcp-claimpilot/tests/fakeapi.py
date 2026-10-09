"""A stand-in for the ClaimPilot REST API that holds every request to its OpenAPI document.

``FakeApi`` is an httpx ``MockTransport``. Tests say what each endpoint answers; every request the
server makes is recorded and checked against ``apps/web/openapi.json``: the path and method exist,
only declared query parameters are sent, the persona header is present wherever the API takes
one, and the body matches the request schema (no extra fields, every required one present). So
every tool test is also a contract test, written from the document and not from the client.
"""

from __future__ import annotations

import email.policy
import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from email.parser import BytesParser
from pathlib import Path
from typing import Any, cast

import httpx

from tests.factories import api_meta

OPENAPI_PATH = Path(__file__).resolve().parents[3] / "apps" / "web" / "openapi.json"


def load_openapi() -> dict[str, Any]:
    assert OPENAPI_PATH.is_file(), f"the committed OpenAPI export is missing: {OPENAPI_PATH}"
    return json.loads(OPENAPI_PATH.read_text(encoding="utf-8"))


def template_regex(template: str) -> re.Pattern[str]:
    """``/v1/claims/{claim_id}`` as a regex with one group per path parameter."""
    pieces = re.split(r"(\{[^/}]+\})", template)
    return re.compile("".join("([^/]+)" if p.startswith("{") else re.escape(p) for p in pieces))


@dataclass(frozen=True)
class Part:
    """One part of a multipart/form-data body."""

    name: str
    filename: str | None
    content_type: str
    data: bytes


def parse_multipart(request: httpx.Request) -> list[Part]:
    header = b"Content-Type: " + request.headers["content-type"].encode() + b"\r\n\r\n"
    message = BytesParser(policy=email.policy.HTTP).parsebytes(header + request.content)
    return [
        Part(
            name=str(part.get_param("name", header="content-disposition")),
            filename=part.get_filename(),
            content_type=part.get_content_type(),
            data=cast(bytes, part.get_payload(decode=True)),
        )
        for part in message.iter_parts()
    ]


@dataclass(frozen=True)
class Call:
    """A request as the API saw it (the path without any reverse-proxy prefix)."""

    method: str
    path: str
    template: str
    args: dict[str, str]
    query: dict[str, str]
    headers: httpx.Headers
    body: Any = None
    parts: list[Part] = field(default_factory=list)


class OpenApi:
    def __init__(self, spec: dict[str, Any]) -> None:
        self.spec = spec
        self.templates = [(t, template_regex(t), item) for t, item in spec["paths"].items()]

    def resolve(self, node: dict[str, Any]) -> dict[str, Any]:
        while "$ref" in node:
            target: Any = self.spec
            for part in node["$ref"].removeprefix("#/").split("/"):
                target = target[part]
            node = target
        return node

    def find(self, method: str, path: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        for template, regex, item in self.templates:
            if match := regex.fullmatch(path):
                operation = item.get(method.lower())
                assert operation is not None, f"the API has no {method} {template}"
                names = re.findall(r"\{([^/}]+)\}", template)
                return template, operation, dict(zip(names, match.groups(), strict=True))
        raise AssertionError(f"the API has no path {path}")

    def check_body(self, operation: dict[str, Any], request: httpx.Request, call: Call) -> None:
        declared = operation.get("requestBody")
        if declared is None:
            assert not request.content, f"{call.method} {call.template} takes no body"
            return
        content = declared["content"]
        content_type = request.headers["content-type"]
        if "application/json" in content:
            assert content_type.startswith("application/json"), content_type
            schema = self.resolve(content["application/json"]["schema"])
            sent = set(call.body)
        else:
            assert content_type.startswith("multipart/form-data"), content_type
            schema = self.resolve(content["multipart/form-data"]["schema"])
            sent = {part.name for part in call.parts}
        properties = set(schema.get("properties", {}))
        assert sent <= properties, f"fields the API does not declare: {sorted(sent - properties)}"
        required = set(schema.get("required", []))
        assert required <= sent, f"required fields missing: {sorted(required - sent)}"

    def check(self, request: httpx.Request, prefix: str) -> Call:
        raw_path = request.url.raw_path.split(b"?", 1)[0].decode("ascii")  # still escaped
        assert raw_path.startswith(prefix), f"{raw_path} is outside {prefix!r}"
        path = raw_path.removeprefix(prefix) or "/"
        template, operation, args = self.find(request.method, path)
        declared = {(p["in"], p["name"].lower()): p for p in operation.get("parameters", [])}
        query = dict(request.url.params)
        for name in query:
            assert ("query", name.lower()) in declared, f"{template} has no query parameter {name}"
        for (where, name), parameter in declared.items():
            sent = name in query if where == "query" else name in request.headers
            if where != "path" and (parameter.get("required") or name == "x-persona"):
                assert sent, f"{request.method} {template} needs {where} parameter {name}"
        call = Call(
            method=request.method,
            path=path,
            template=template,
            args=args,
            query=query,
            headers=request.headers,
        )
        content_type = request.headers.get("content-type", "")
        if content_type.startswith("multipart/form-data"):
            call = replace(call, parts=parse_multipart(request))
        elif content_type.startswith("application/json"):
            call = replace(call, body=json.loads(request.content))
        self.check_body(operation, request, call)
        return call


Handler = Callable[[Call], httpx.Response]


class FakeApi:
    """The API, scripted per test. ``prefix`` is where a reverse proxy mounts it (``/api``)."""

    def __init__(self, prefix: str = "") -> None:
        self.prefix = prefix
        self.calls: list[Call] = []
        self.problems: list[str] = []  # contract violations and unscripted calls
        self._openapi = OpenApi(load_openapi())
        self._routes: dict[tuple[str, str], Handler] = {}
        self.transport = httpx.MockTransport(self._handle)
        # Every API publishes its limits; tests that care script their own.
        self.json("GET", "/v1/meta", api_meta())

    def respond(self, method: str, template: str, handler: Handler) -> None:
        self._routes[(method, template)] = handler

    def json(self, method: str, template: str, body: Any, status: int = 200) -> None:
        self.respond(method, template, lambda _: httpx.Response(status, json=body))

    def sequence(self, method: str, template: str, bodies: list[Any]) -> None:
        """Answer successive calls with successive bodies; the last one repeats."""
        remaining = list(bodies)

        def next_body(_: Call) -> httpx.Response:
            body = remaining.pop(0) if len(remaining) > 1 else remaining[0]
            return httpx.Response(200, json=body)

        self.respond(method, template, next_body)

    def calls_to(self, method: str, template: str) -> list[Call]:
        return [c for c in self.calls if (c.method, c.template) == (method, template)]

    def _handle(self, request: httpx.Request) -> httpx.Response:
        try:
            call = self._openapi.check(request, self.prefix)
        except AssertionError as exc:
            self.problems.append(f"{request.method} {request.url.path}: {exc}")
            raise
        self.calls.append(call)
        handler = self._routes.get((call.method, call.template))
        if handler is None:
            self.problems.append(f"unscripted call {call.method} {call.template}")
            raise AssertionError(f"unscripted call {call.method} {call.template}")
        return handler(call)
