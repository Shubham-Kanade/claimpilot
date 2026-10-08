from __future__ import annotations

import pytest

from synthgen.adversarial import generate_adversarial_specs
from synthgen.scenarios import generate_base_specs
from synthgen.spec import DocSpec

SEED = 42
BASE_COUNT = 80
ADVERSARIAL_COUNT = 20


@pytest.fixture(scope="session")
def base_specs() -> list[DocSpec]:
    return generate_base_specs(SEED, BASE_COUNT)


@pytest.fixture(scope="session")
def adversarial_specs(base_specs: list[DocSpec]) -> list[DocSpec]:
    return generate_adversarial_specs(
        base_specs, seed=SEED, count=ADVERSARIAL_COUNT, first_index=BASE_COUNT + 1
    )


@pytest.fixture(scope="session")
def all_specs(base_specs: list[DocSpec], adversarial_specs: list[DocSpec]) -> list[DocSpec]:
    return [*base_specs, *adversarial_specs]
