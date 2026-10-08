"""Contract drift guard: everything this server relies on exists in the API's OpenAPI document.

``apps/web/openapi.json`` is the committed export of the API's contract (CI fails when it is stale).
Every REST call this server makes (``client.OPERATIONS``) and every response field it reads
(``api_models``) is checked against it, so an API change breaks this test instead of a tool at run
time. The mutation tests at the end prove the guard bites: each tampers with a copy of the
document the way an API change would, and the checks must notice.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Callable
from typing import Any, get_args

import pytest
from pydantic import BaseModel

from claimpilot_mcp import api_models as m
from claimpilot_mcp.client import (
    CREATE_BATCH,
    DECIDE,
    GET_BATCH,
    GET_CLAIM,
    GET_DOCUMENT,
    IDEMPOTENCY_HEADER,
    LIST_APPROVALS,
    LIST_CLAIMS,
    ME,
    OPERATIONS,
    PERSONA_HEADER,
    READY,
    REPLY,
    SUBMIT,
    Operation,
)
from claimpilot_mcp.models import SEVERITY_ORDER
from claimpilot_mcp.server import ApprovalStatus, ClaimStatusFilter
from tests.fakeapi import OpenApi, load_openapi

SPEC = load_openapi()
SCHEMAS: dict[str, Any] = SPEC["components"]["schemas"]
API = OpenApi(SPEC)

# What each call answers with, by schema name (and whether it is a list of them).
RESPONSES: dict[Operation, tuple[str, bool]] = {
    READY: ("Readiness", False),
    ME: ("Me", False),
    LIST_CLAIMS: ("ClaimView", True),
    GET_CLAIM: ("ClaimView", False),
    GET_DOCUMENT: ("DocumentView", False),
    CREATE_BATCH: ("BatchCreated", False),
    GET_BATCH: ("BatchView", False),
    REPLY: ("ReplyOut", False),
    SUBMIT: ("ClaimView", False),
    LIST_APPROVALS: ("ClaimView", True),
    DECIDE: ("ClaimView", False),
}

# Each parse model and the OpenAPI schema it reads.
MODELS: dict[type[BaseModel], str] = {
    m.ApiFinding: "Finding",
    m.ApiQuestion: "OpenQuestion",
    m.ApiClaim: "ClaimView",
    m.ApiReceipt: "ExtractedReceipt",
    m.ApiDecisions: "Decisions",
    m.ApiProcessedDocument: "ProcessedDocument",
    m.ApiDocument: "DocumentView",
    m.ApiBatch: "BatchView",
    m.ApiDocumentRef: "DocumentRef",
    m.ApiBatchCreated: "BatchCreated",
    m.ApiReply: "ReplyOut",
    m.ApiEmployee: "Employee",
    m.ApiMe: "Me",
}

BODY_TYPES = {"json": "application/json", "multipart": "multipart/form-data"}


def violations(op: Operation, spec: dict[str, Any]) -> list[str]:
    """Everything about one call that the document does not bear out."""
    found = spec["paths"].get(op.path, {}).get(op.method.lower())
    if found is None:
        return [f"the API has no {op.method} {op.path}"]
    problems: list[str] = []
    params = {(p["in"], p["name"].lower()): p for p in found.get("parameters", [])}

    for name in re.findall(r"\{([^/}]+)\}", op.path):
        declared = params.get(("path", name.lower()))
        if declared is None or declared.get("required") is not True:
            problems.append(f"path parameter {name} is not declared as required")
    for name in op.query:
        if ("query", name.lower()) not in params:
            problems.append(f"no query parameter {name}")
    for name in op.headers:
        if ("header", name.lower()) not in params:
            problems.append(f"no header parameter {name}")
    sent = {("query", n.lower()) for n in op.query} | {("header", n.lower()) for n in op.headers}
    for (where, name), parameter in params.items():
        if where != "path" and parameter.get("required") and (where, name) not in sent:
            problems.append(f"required {where} parameter {name} is not sent")
    if (("header", PERSONA_HEADER.lower()) in params) != (PERSONA_HEADER in op.headers):
        problems.append("the persona header is sent where it is not taken, or not where it is")

    body = found.get("requestBody")
    if op.body is None and body is not None:
        problems.append("the call now takes a request body")
    if op.body is not None and (body is None or BODY_TYPES[op.body] not in body["content"]):
        problems.append(f"no {op.body} request body")

    responses = found["responses"]
    if str(op.success) not in responses:
        problems.append(f"status {op.success} is not documented")
        return problems
    name, is_list = RESPONSES[op]
    schema = responses[str(op.success)].get("content", {}).get("application/json", {}).get("schema")
    if schema and is_list:
        schema = schema.get("items", {}) if schema.get("type") == "array" else {}
    if not schema or schema.get("$ref") != f"#/components/schemas/{name}":
        problems.append(f"the response is no longer {'a list of ' if is_list else ''}{name}")
    return problems


# -- the document bears out every call -----------------------------------------------------------


@pytest.mark.parametrize("op", OPERATIONS, ids=lambda op: f"{op.method} {op.path}")
def test_every_call_the_server_makes_exists_exactly_as_it_makes_it(op: Operation):
    assert violations(op, SPEC) == []


def test_the_idempotency_key_header_is_declared_on_submit_only():
    assert [op for op in OPERATIONS if IDEMPOTENCY_HEADER in op.headers] == [SUBMIT]


def test_the_operation_table_has_no_duplicates_and_every_call_has_a_response_schema():
    assert len({(op.method, op.path) for op in OPERATIONS}) == len(OPERATIONS)
    assert set(RESPONSES) == set(OPERATIONS)


@pytest.mark.parametrize(("model", "schema_name"), MODELS.items(), ids=lambda v: str(v))
def test_parse_models_only_read_fields_the_api_always_sends(
    model: type[BaseModel], schema_name: str
):
    schema = API.resolve(SCHEMAS[schema_name])
    properties = set(schema["properties"])
    required = set(schema.get("required", []))
    for name, field in model.model_fields.items():
        assert name in properties, f"{schema_name} has no field {name}"
        if field.is_required():
            assert name in required, (
                f"{schema_name}.{name} is optional but {model.__name__} needs it"
            )


def test_the_status_filter_offers_exactly_the_statuses_a_claim_can_have():
    assert set(get_args(ClaimStatusFilter)) == set(SCHEMAS["ClaimStatus"]["enum"])


def test_the_approval_filter_matches_what_the_api_documents():
    query = {p["name"]: p for p in SPEC["paths"]["/v1/approvals"]["get"]["parameters"]}["status"]
    for value in get_args(ApprovalStatus):
        assert value in query["description"]
    assert query["schema"]["default"] == "submitted"


def test_every_severity_the_api_has_is_ordered():
    assert set(SEVERITY_ORDER) == set(SCHEMAS["Severity"]["enum"])


def test_the_error_body_has_the_fields_the_client_reads():
    problem = SCHEMAS["Problem"]
    assert {"type", "title", "status", "detail"} <= set(problem["properties"])
    assert {"type", "title", "status"} <= set(problem["required"])


def test_upload_limits_are_not_published_by_the_api_so_they_are_settings():
    """If /v1/meta ever publishes the limits, this fails: read them from there instead."""
    meta = API.resolve(SCHEMAS["MetaInfo"])
    assert not {name for name in meta["properties"] if "upload" in name or "max" in name}


# -- the guard bites -----------------------------------------------------------------------------


def tampered(change: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    spec = copy.deepcopy(SPEC)
    change(spec)
    return spec


def drop_path(path: str) -> Callable[[dict[str, Any]], None]:
    return lambda spec: spec["paths"].pop(path)


def edit(path: str, method: str, change: Callable[[dict[str, Any]], None]):
    return lambda spec: change(spec["paths"][path][method])


def rename_parameter(old: str, new: str) -> Callable[[dict[str, Any]], None]:
    def change(operation: dict[str, Any]) -> None:
        for parameter in operation["parameters"]:
            if parameter["name"] == old:
                parameter["name"] = new

    return change


def require_new_query_parameter(operation: dict[str, Any]) -> None:
    operation["parameters"].append(
        {"name": "tenant", "in": "query", "required": True, "schema": {"type": "string"}}
    )


def swap_body_to_multipart(operation: dict[str, Any]) -> None:
    operation["requestBody"]["content"] = {"multipart/form-data": {"schema": {}}}


def add_a_request_body(operation: dict[str, Any]) -> None:
    operation["requestBody"] = {"content": {"application/json": {"schema": {}}}}


def other_success_status(operation: dict[str, Any]) -> None:
    operation["responses"]["200"] = operation["responses"].pop("202", {})


def point_at_another_schema(operation: dict[str, Any]) -> None:
    operation["responses"]["200"]["content"]["application/json"]["schema"] = {
        "$ref": "#/components/schemas/Stats"
    }


MUTATIONS = [
    pytest.param(REPLY, drop_path("/v1/claims/{claim_id}/reply"), "the API has no POST", id="path"),
    pytest.param(
        GET_CLAIM,
        lambda spec: spec["paths"]["/v1/claims/{claim_id}"].pop("get"),
        "the API has no GET",
        id="method",
    ),
    pytest.param(
        GET_CLAIM,
        edit("/v1/claims/{claim_id}", "get", rename_parameter("claim_id", "id")),
        "path parameter claim_id",
        id="path-parameter",
    ),
    pytest.param(
        LIST_CLAIMS,
        edit("/v1/claims", "get", rename_parameter("status", "state")),
        "no query parameter status",
        id="query-parameter",
    ),
    pytest.param(
        SUBMIT,
        edit("/v1/claims/{claim_id}/submit", "post", rename_parameter("Idempotency-Key", "Key")),
        "no header parameter Idempotency-Key",
        id="idempotency-header",
    ),
    pytest.param(
        ME,
        edit("/v1/me", "get", rename_parameter("x-persona", "x-user")),
        "persona header",
        id="persona-header",
    ),
    pytest.param(
        LIST_CLAIMS,
        edit("/v1/claims", "get", require_new_query_parameter),
        "tenant is not sent",
        id="new-required-parameter",
    ),
    pytest.param(
        REPLY,
        edit("/v1/claims/{claim_id}/reply", "post", swap_body_to_multipart),
        "no json request body",
        id="body-kind",
    ),
    pytest.param(
        READY,
        edit("/readyz", "get", add_a_request_body),
        "now takes a request body",
        id="new-body",
    ),
    pytest.param(
        CREATE_BATCH,
        edit("/v1/batches", "post", other_success_status),
        "status 202",
        id="success-status",
    ),
    pytest.param(
        GET_CLAIM,
        edit("/v1/claims/{claim_id}", "get", point_at_another_schema),
        "no longer ClaimView",
        id="response-schema",
    ),
]


@pytest.mark.parametrize(("op", "change", "expected"), MUTATIONS)
def test_a_changed_contract_is_noticed(
    op: Operation, change: Callable[[dict[str, Any]], None], expected: str
):
    found = violations(op, tampered(change))
    assert any(expected in problem for problem in found), found


def test_an_untouched_copy_raises_no_alarm():
    assert all(violations(op, tampered(lambda spec: None)) == [] for op in OPERATIONS)
