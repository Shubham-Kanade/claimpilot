"""C2PA content credentials: does the file declare that AI made it?

C2PA (Content Authenticity Initiative) lets a tool sign a *manifest* into a picture: who made it,
which actions were applied and, for generative tools, an IPTC ``digitalSourceType`` of
``trainedAlgorithmicMedia`` (made by a model) or ``compositeWithTrainedAlgorithmicMedia`` (a real
picture with generated content composited in). OpenAI, Adobe Firefly, Google and Microsoft sign
their generated images this way, so an AI-drawn "receipt" often announces itself.

=====================  ========  ==================================================================
code                   severity  fires when
=====================  ========  ==================================================================
``ai_generated_c2pa``  high      a manifest (active, or an ingredient's) declares AI generation
``c2pa_present``       info      a manifest is present (unverified, unless the native backend ran)
``c2pa_invalid``       warn      native backend only: the manifest does not validate / is unreadable
=====================  ========  ==================================================================

Absence of C2PA is normal (almost no phone, scanner or billing system signs yet) and is never
flagged.

Two backends
------------
**Default: a minimal reader written in pure Python** (this module, no dependency). It finds the
JUMBF manifest store by its ``jumd`` + ``c2pa`` content-type UUID anywhere in the file (JPEG APP11,
PNG ``caBX``, WebP, PDF attachment), then reads the claim generator and the IPTC
``digitalSourceType`` out of the raw CBOR text.

What it can do: tell that a file carries C2PA credentials, name the tool that wrote them, and
catch a declaration of AI generation (``trainedAlgorithmicMedia`` and
``compositeWithTrainedAlgorithmicMedia``, in the active manifest or any ingredient's).

What it cannot do: **it does not check signatures or hashes**, so it can never say a manifest is
genuine. A forged or tampered manifest is reported as present but unverified (``valid=None``,
``source="scan"``), never as valid, and ``c2pa_present`` says so. It does not read the signer, the
actions list or the trust status either. A forged manifest buys an attacker nothing: only an
AI declaration raises a finding, and nobody plants a false AI claim in their own receipt.

**Opt-in: the native ``c2pa-python`` library** (c2pa-rs, a memory-unsafe parser written in Rust
and C), enabled only with the environment variable ``C2PA_NATIVE=1`` *and* the optional extra
installed (``uv sync --extra c2pa``). Without the flag the library is never imported or
initialised, even if it is installed. With it, manifests are cryptographically validated:
``valid`` becomes True/False, tampering after signing is ``c2pa_invalid``, and the signer and the
action list are read. It is off by default because, on the Windows development machine, every
call (even on empty bytes) provoked a first-chance ``access violation`` inside the native parser
(c2pa-python 0.38.0, c2pa-rs 0.91.0), which is not something to run on untrusted uploads; the same
calls were clean in the ``python:3.13-slim`` Linux image. If the flag is set but the library
cannot load, the minimal reader takes over.

Behaviour you should know about
-------------------------------
* **No network, ever.** The native reader would, by default, *fetch* a remote manifest named in
  the file's XMP (an SSRF primitive for anyone who can upload a file) and may query OCSP
  responders. Both are switched off in the settings used here (tested against a local server); a
  remote manifest is reported as present but unverified. The minimal reader never touches the
  network.
* With the native backend, ``valid`` means the signature and the data hashes verify. It does not
  require the signer to be on the C2PA trust list (we ship none), so a self-signed manifest is
  ``valid=True`` with ``trusted=False``; its issuer is shown but not vouched for.
* A manifest that fails validation still counts as AI-generated when it declares so.

Limits: C2PA is present only when the generator embeds it, and any re-save that strips metadata
(most messaging apps, screenshots, a print-and-scan) removes it. A clean result proves nothing.
Findings quote only a sanitised issuer/generator name, never raw manifest text.
"""

from __future__ import annotations

import importlib
import io
import json
import os
import re
from dataclasses import dataclass, field
from functools import cache
from typing import Any, Final, Literal

from claimpilot.domain.findings import Finding, Severity
from claimpilot.trust.forensics import AI_GENERATORS, sniff_kind

NATIVE_ENV: Final = "C2PA_NATIVE"  # set to 1 to use the native library (opt-in, see docstring)
_TRUTHY: Final = frozenset({"1", "true", "yes", "on"})

# Content-type UUID of a JUMBF superbox holding a C2PA manifest store ("c2pa" + JUMBF suffix).
C2PA_STORE_UUID: Final = bytes.fromhex("6332706100110010800000aa00389b71")
_STORE_MARKER: Final = b"jumd" + C2PA_STORE_UUID  # description box of the store

_MIME_TYPES: Final = {
    "jpeg": "image/jpeg",
    "png": "image/png",
    "webp": "image/webp",
    "pdf": "application/pdf",
}
# No network: remote manifests (SSRF) and OCSP lookups are off.
_READER_SETTINGS: Final = {"verify": {"remote_manifest_fetch": False, "ocsp_fetch": False}}

_AI_SOURCE: Final = re.compile(
    r"(?:digitalsourcetype/|\b)(?:trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia)\b",
    re.IGNORECASE,
)
# In raw CBOR the next item follows the text with no separator, so no word boundary here.
_AI_SOURCE_BYTES: Final = re.compile(
    rb"(?:trainedAlgorithmicMedia|compositeWithTrainedAlgorithmicMedia)", re.IGNORECASE
)
MAX_GENERATOR_CHARS: Final = 120
_SAFE_TEXT: Final = re.compile(r"[^A-Za-z0-9 .,&'()+_-]")
MAX_NAME_CHARS: Final = 40

_FAILURE_TEXT: Final = {
    "assertion.dataHash.mismatch": "the picture no longer matches its signature, so it was "
    "changed after it was signed",
    "assertion.hashedURI.mismatch": "part of the manifest was altered after signing",
    "claimSignature.mismatch": "the signature does not match the claim it covers",
    "assertion.missing": "an assertion the claim refers to is missing",
    "signingCredential.invalid": "the signing certificate is not acceptable",
    "signingCredential.expired": "the signing certificate had expired",
}
_UNREADABLE: Final = "the credentials are damaged or malformed and could not be read"


@dataclass(frozen=True, slots=True)
class C2paResult:
    """What the file's content credentials say. ``present=False`` is the normal case."""

    present: bool = False
    valid: bool | None = None  # None: present but NOT verified (the default minimal reader)
    issuer: str | None = None  # native backend only; not vouched for unless trusted
    generator: str | None = None  # the tool that wrote the manifest, e.g. "ChatGPT"
    ai_generated: bool = False
    actions: list[str] = field(default_factory=list)  # native backend only
    trusted: bool | None = None  # signer on the C2PA trust list (we ship none, so rarely True)
    validation_state: str | None = None  # native backend: the library's "Valid"/"Invalid"/...
    failure_codes: list[str] = field(default_factory=list)  # native backend: validation failures
    source: Literal["library", "scan"] | None = None  # which backend produced this result


# --- public API -----------------------------------------------------------------------------


def native_enabled() -> bool:
    """True when ``C2PA_NATIVE`` asks for the native library (read at call time)."""
    return os.environ.get(NATIVE_ENV, "").strip().lower() in _TRUTHY


def native_backend_active() -> bool:
    """True when the native library is switched on and loads (a ~0.5 s first call)."""
    return _library() is not None


def inspect_c2pa(raw: bytes) -> C2paResult:
    """Read the file's C2PA manifest store. Never raises, never touches the network.

    Uses the minimal pure-Python reader unless ``C2PA_NATIVE`` is set and the native library
    loads; see the module docstring for what each can tell.
    """
    marker = _STORE_MARKER in raw
    library = _library()
    kind = sniff_kind(raw)
    if library is not None and kind is not None:
        outcome = _read_with_library(library, raw, _MIME_TYPES[kind], marker)
        if outcome is not None:
            return outcome
    return _scan(raw) if marker else C2paResult()


def c2pa_findings(result: C2paResult) -> list[Finding]:
    findings: list[Finding] = []
    if result.ai_generated:
        who = _safe_name(result.generator)
        findings.append(
            Finding(
                code="ai_generated_c2pa",
                severity=Severity.high,
                message="The file's content credentials (C2PA) declare that it was generated or "
                "altered by AI" + (f", using “{who}”" if who else "") + "; a real bill is not.",
                actual=who or "AI generated",
            )
        )
    elif result.present and result.valid is not False:
        findings.append(_present_finding(result))
    if result.present and result.valid is False:
        reason = _failure_reason(result)
        findings.append(
            Finding(
                code="c2pa_invalid",
                severity=Severity.warn,
                message="The file carries content credentials (C2PA) that do not validate: "
                f"{reason}. Treat the picture as altered.",
                actual=result.failure_codes[0] if result.failure_codes else "unreadable",
            )
        )
    return findings


def _present_finding(result: C2paResult) -> Finding:
    if result.valid:
        issuer = _safe_name(result.issuer)
        vouch = (
            ""
            if result.trusted
            else " (the signer is not on a trust list, so it is not vouched for)"
        )
        message = (
            "The file carries valid content credentials (C2PA)"
            + (f" signed by “{issuer}”" if issuer else "")
            + vouch
            + "."
        )
        return Finding(code="c2pa_present", severity=Severity.info, message=message, actual=issuer)
    maker = _safe_name(result.generator)
    return Finding(
        code="c2pa_present",
        severity=Severity.info,
        message="The file carries content credentials (C2PA)"
        + (f" written by “{maker}”" if maker else "")
        + ", but their signature was not verified, so they say nothing reliable about who made it.",
        actual=maker,
    )


# --- the minimal reader (default) -------------------------------------------------------------


def _scan(raw: bytes) -> C2paResult:
    """Find the AI claim and the generator in the raw CBOR of a store known to be present."""
    return C2paResult(
        present=True,
        valid=None,
        generator=_scan_generator(raw),
        ai_generated=_AI_SOURCE_BYTES.search(raw) is not None,
        source="scan",
    )


def _cbor_text(raw: bytes, position: int) -> str | None:
    """The CBOR text string starting at ``position`` (major type 3, short or 1-byte length)."""
    if position >= len(raw):
        return None
    head = raw[position]
    if 0x60 <= head <= 0x77:
        length, start = head - 0x60, position + 1
    elif head == 0x78 and position + 1 < len(raw):
        length, start = raw[position + 1], position + 2
    else:
        return None
    if length > MAX_GENERATOR_CHARS or start + length > len(raw):
        return None
    text = raw[start : start + length].decode("utf-8", errors="replace")
    # Anything but plain printable text means we are not looking at a CBOR string at all.
    return text if text.isprintable() and "�" not in text else None


def _scan_generator(raw: bytes) -> str | None:
    # v2 claims: "claim_generator_info" -> [ { "name": "<tool>", ... } ]
    marker = raw.find(b"\x74claim_generator_info")
    if marker >= 0:
        name = raw.find(b"\x64name", marker, marker + 64)
        if name >= 0:
            return _cbor_text(raw, name + len(b"\x64name"))
    # v1 claims: "claim_generator": "<tool>/<version> ..."
    legacy = raw.find(b"\x6fclaim_generator")
    if legacy >= 0:
        return _cbor_text(raw, legacy + len(b"\x6fclaim_generator"))
    return None


def _safe_name(value: str | None) -> str | None:
    """A short, plain rendition of a name taken from the file (never the raw text)."""
    if not value:
        return None
    cleaned = " ".join(_SAFE_TEXT.sub("", value).split())[:MAX_NAME_CHARS].strip()
    return cleaned or None


# --- the native library (opt-in) ----------------------------------------------------------------


def _library() -> Any | None:
    """The native module, but only when ``C2PA_NATIVE`` is set: otherwise it is never imported."""
    return _import_native() if native_enabled() else None


@cache
def _import_native() -> Any | None:
    try:
        return importlib.import_module("c2pa")
    except (ImportError, OSError, AttributeError, RuntimeError):
        return None  # extra not installed, or the native library cannot load on this platform


def _error_is(library: Any, exc: Exception, *names: str) -> bool:
    base = library.C2paError
    return any(isinstance(exc, getattr(base, name, ())) for name in names)


def _read_with_library(library: Any, raw: bytes, mime: str, marker: bool) -> C2paResult | None:
    """Parse with c2pa-python. None means "no manifest, or library unusable here": scan instead."""
    try:
        with library.Context.from_dict(_READER_SETTINGS) as context:
            reader = library.Reader.try_create(mime, io.BytesIO(raw), None, context)
            if reader is None:
                return C2paResult() if not marker else None
            with reader:
                store = json.loads(reader.json())
                state = reader.get_validation_state()
    except library.C2paError as exc:
        # The wrapper only types some errors: a remote manifest we refused to fetch arrives as a
        # plain C2paError whose message starts with "Remote:".
        if _error_is(library, exc, "RemoteManifest") or str(exc).startswith("Remote"):
            return C2paResult(present=True, valid=None, source="library")
        if _error_is(library, exc, "ManifestNotFound", "NotSupported"):
            return None if marker else C2paResult()
        # A manifest store that exists but cannot be parsed: damaged or tampered with.
        return C2paResult(present=True, valid=False, source="scan") if marker else None
    except (ValueError, TypeError):
        return None  # unexpected JSON from the library: let the minimal reader have a go
    return _summarise(store, state)


def _summarise(store: dict[str, Any], state: str | None) -> C2paResult:
    manifests = _dict(store.get("manifests"))
    active_label = store.get("active_manifest")
    active = _dict(manifests.get(active_label)) if isinstance(active_label, str) else {}
    return C2paResult(
        present=bool(manifests),
        valid=None if state is None else state in ("Valid", "Trusted"),
        issuer=_issuer(active),
        generator=_generator(active),
        ai_generated=any(_declares_ai(_dict(m)) for m in manifests.values()),
        actions=_actions(active),
        trusted=None if state is None else state == "Trusted",
        validation_state=state,
        failure_codes=_failure_codes(store),
        source="library",
    )


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _generator(manifest: dict[str, Any]) -> str | None:
    for info in _list(manifest.get("claim_generator_info")):
        name = _dict(info).get("name")
        if isinstance(name, str) and name:
            version = _dict(info).get("version")
            return f"{name} {version}" if isinstance(version, str) and version else name
    legacy = manifest.get("claim_generator")
    return legacy if isinstance(legacy, str) and legacy else None


def _issuer(manifest: dict[str, Any]) -> str | None:
    info = _dict(manifest.get("signature_info"))
    for key in ("issuer", "common_name"):
        value = info.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def _actions(manifest: dict[str, Any]) -> list[str]:
    names: list[str] = []
    for assertion in _list(manifest.get("assertions")):
        if not str(_dict(assertion).get("label", "")).startswith("c2pa.actions"):
            continue
        for action in _list(_dict(_dict(assertion).get("data")).get("actions")):
            name = _dict(action).get("action")
            if isinstance(name, str) and name not in names:
                names.append(name)
    return names


def _declares_ai(manifest: dict[str, Any]) -> bool:
    """AI source type anywhere in the assertions, or a generator that is a known AI tool."""
    if _AI_SOURCE.search(json.dumps(manifest.get("assertions", []), default=str)):
        return True
    generator = _generator(manifest) or ""
    return any(pattern.search(generator) for _label, pattern in AI_GENERATORS)


def _failure_codes(store: dict[str, Any]) -> list[str]:
    active = _dict(_dict(store.get("validation_results")).get("activeManifest"))
    codes = [
        str(_dict(item).get("code"))
        for item in _list(active.get("failure"))
        if _dict(item).get("code")
    ]
    # Signer trust is not a tamper signal; everything else the library lists is.
    return [code for code in codes if code != "signingCredential.untrusted"]


def _failure_reason(result: C2paResult) -> str:
    for code in result.failure_codes:
        if code in _FAILURE_TEXT:
            return _FAILURE_TEXT[code]
    return "the signature check failed" if result.failure_codes else _UNREADABLE
