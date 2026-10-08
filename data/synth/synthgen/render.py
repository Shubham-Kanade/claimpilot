"""Render document specs to HTML (Jinja2) and then to PNG / PDF with Playwright.

Browser choice: ``auto`` tries Playwright's bundled Chromium, then the locally installed
Microsoft Edge (``channel="msedge"``), then Google Chrome. The corporate proxy blocks the
Chromium download, so on managed Windows laptops ``auto`` usually ends up on Edge.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

from claimpilot.domain import DocType, ExtractedReceipt, PaymentMethod
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from playwright.sync_api import Browser, BrowserContext, Error, Page, sync_playwright

from synthgen.spec import DocSpec, OutputLayout
from synthgen.textfmt import format_date, format_time, indian_grouping, rupees_in_words

SYNTH_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = SYNTH_DIR / "templates"
FONTS_CSS = SYNTH_DIR / "fonts" / "fonts.css"

BROWSER_CHOICES = ("auto", "chromium", "msedge", "chrome")
DEVICE_SCALE = {DocType.upi_payment: 2.75}  # phone screenshots are ~1080 px wide
DEFAULT_SCALE = 2.0
PREVIEW_SCALE = 1.5  # PNG previews of PDF documents

PAYMENT_LABELS = {
    PaymentMethod.cash: "Cash",
    PaymentMethod.card: "Card",
    PaymentMethod.upi: "UPI",
    PaymentMethod.wallet: "Wallet",
    PaymentMethod.netbanking: "Net Banking",
}


def _rate(value: float) -> str:
    return f"{value:g}"


def tax_lines(receipt: ExtractedReceipt) -> list[tuple[str, float]]:
    """Printed tax rows, e.g. [('CGST @ 2.5%', 30.85), ('SGST @ 2.5%', 30.85)]."""
    taxes, rate = receipt.taxes, receipt.taxes.gst_rate_percent
    rows: list[tuple[str, float]] = []
    for label, amount, share in (
        ("CGST", taxes.cgst, 0.5),
        ("SGST", taxes.sgst, 0.5),
        ("IGST", taxes.igst, 1.0),
        ("Cess", taxes.cess, None),
    ):
        if amount is None:
            continue
        suffix = f" @ {_rate(rate * share)}%" if rate is not None and share is not None else ""
        rows.append((label + suffix, amount))
    return rows


def make_environment() -> Environment:
    env = Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(["html", "j2"]),
        undefined=StrictUndefined,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters.update(
        inr=indian_grouping,
        num=lambda value: f"{value:.2f}",
        fdate=format_date,
        ftime=format_time,
        words=rupees_in_words,
    )
    env.globals.update(
        tax_lines=tax_lines,
        payment_label=lambda method: PAYMENT_LABELS.get(method),
        fonts_css=FONTS_CSS.as_uri(),
    )
    return env


def render_html(spec: DocSpec, env: Environment | None = None) -> str:
    env = env or make_environment()
    template = env.get_template(f"{spec.doc_type.value}.html.j2")
    return template.render(r=spec.truth.receipt, x=spec.extras, s=spec.style)


@contextmanager
def open_browser(choice: str = "auto") -> Iterator[Browser]:
    """Launch a headless Chromium-family browser according to ``choice``."""
    if choice not in BROWSER_CHOICES:
        raise ValueError(f"browser must be one of {BROWSER_CHOICES}")
    channels = [None, "msedge", "chrome"] if choice == "auto" else [choice]
    with sync_playwright() as playwright:
        errors: list[str] = []
        for channel in channels:
            try:
                kwargs = {} if channel in (None, "chromium") else {"channel": channel}
                browser = playwright.chromium.launch(**kwargs)
            except Error as exc:
                errors.append(f"{channel or 'chromium'}: {str(exc).splitlines()[0]}")
                continue
            try:
                yield browser
            finally:
                browser.close()
            return
        raise RuntimeError("no browser could be launched:\n" + "\n".join(errors))


def browser_available(choice: str = "auto") -> bool:
    try:
        with open_browser(choice):
            return True
    except RuntimeError:
        return False


class Renderer:
    """Renders specs with one browser and one page per device scale factor."""

    def __init__(self, browser: Browser, layout: OutputLayout) -> None:
        self._browser = browser
        self._layout = layout
        self._env = make_environment()
        self._contexts: dict[float, BrowserContext] = {}
        self._pages: dict[float, Page] = {}

    def _page(self, scale: float) -> Page:
        if scale not in self._pages:
            context = self._browser.new_context(
                viewport={"width": 900, "height": 1200}, device_scale_factor=scale
            )
            self._contexts[scale] = context
            self._pages[scale] = context.new_page()
        return self._pages[scale]

    def _load(self, spec: DocSpec, scale: float) -> Page:
        html_path = self._layout.html / f"{spec.id}.html"
        html_path.write_text(render_html(spec, self._env), encoding="utf-8")
        page = self._page(scale)
        page.goto(html_path.resolve().as_uri(), wait_until="load")
        page.evaluate("document.fonts.ready.then(() => true)")
        return page

    def render(self, spec: DocSpec) -> Path:
        """Write the clean PNG (and the PDF for PDF document types); return the PNG path."""
        png = self._layout.clean / f"{spec.id}.png"
        if spec.is_pdf:
            page = self._load(spec, PREVIEW_SCALE)
            page.pdf(path=self._layout.clean / f"{spec.id}.pdf", format="A4", print_background=True)
        else:
            page = self._load(spec, DEVICE_SCALE.get(spec.doc_type, DEFAULT_SCALE))
        page.locator("#doc").screenshot(path=png)
        return png

    def close(self) -> None:
        for context in self._contexts.values():
            context.close()


def render_specs(specs: Iterable[DocSpec], layout: OutputLayout, browser: str = "auto") -> int:
    """Render every spec into ``layout.clean``; returns the number rendered."""
    layout.ensure()
    rendered = 0
    with open_browser(browser) as handle:
        renderer = Renderer(handle, layout)
        try:
            for spec in specs:
                renderer.render(spec)
                rendered += 1
        finally:
            renderer.close()
    return rendered
