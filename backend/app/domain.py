from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .catalogue import Catalogue


class BusinessNeeds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment: Literal["office", "retail", "other"]
    city_tier: Literal[1, 2]
    employees: int = Field(ge=1, le=250)
    sites: int = Field(ge=1, le=25)
    speed: Literal[100, 200, 300]
    email_seats: int = Field(ge=0, le=250)
    devices: int = Field(ge=0, le=1000)
    monthly_budget: int = Field(ge=0, le=1_000_000)
    coverage: Literal["fibre", "wireless", "unknown"]

    @model_validator(mode="after")
    def validate_staff(self):
        if self.email_seats > self.employees:
            raise ValueError("Email seats cannot exceed staff count")
        return self


class QuoteLine(BaseModel):
    sku: str
    name: str
    unit: str
    quantity: int
    rate_paise: int
    monthly_paise: int
    setup_paise: int
    minimum_months: int
    reason: Literal["fibre100", "fibre300", "wireless", "email", "security", "failover"]


class Quote(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    version: str
    monthly_paise: int
    setup_paise: int
    lines: list[QuoteLine]
    expires_at: datetime


class Case(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    revision: int
    needs: BusinessNeeds
    broadband: str | None
    failover: bool
    options: list[str]
    quote: Quote | None
    blocker: Literal["multiple_sites", "unsupported_speed", "ineligible_bundle", "coverage_pending", "over_budget"] | None
    accepted_at: datetime | None = None


class Configure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=0)
    needs: BusinessNeeds
    broadband: Literal["P01", "P02", "P04"] | None = None
    failover: bool = False


class Accept(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision: int = Field(ge=1)
    quote_id: UUID


class UnavailableSelection(ValueError):
    pass


CONNECTIVITY_REASON = {"P01": "fibre100", "P02": "fibre300", "P04": "wireless"}


def build_case(catalogue: Catalogue, config: Configure, previous: Case | None) -> Case:
    needs = config.needs
    fields = {
        "id": previous.id if previous else uuid4(),
        "revision": config.revision + 1,
        "needs": needs,
        "broadband": None,
        "failover": False,
        "options": [],
        "quote": None,
        "blocker": None,
    }
    if needs.sites > 1:
        return Case(**{**fields, "blocker": "multiple_sites"})
    if needs.coverage == "wireless" and needs.speed > 100:
        return Case(**{**fields, "blocker": "unsupported_speed"})

    def eligible(sku: str) -> bool:
        item = catalogue.products.get(sku)
        return bool(
            item and needs.city_tier in item.tiers and needs.employees >= item.employees
            and needs.sites >= item.sites and not item.needs_gst
        )

    if needs.coverage == "wireless":
        candidates = ["P04"]
    elif needs.speed == 100:
        candidates = ["P01", "P02"]
    else:
        candidates = ["P02"]
    options = sorted(
        (sku for sku in candidates if eligible(sku)),
        key=lambda sku: catalogue.products[sku].monthly,
    )
    if not options:
        return Case(**{**fields, "blocker": "ineligible_bundle"})
    if config.broadband and config.broadband not in options:
        raise UnavailableSelection("broadband_unavailable")

    broadband = config.broadband or options[0]
    selection = [(broadband, 1, CONNECTIVITY_REASON[broadband])]
    if needs.email_seats:
        selection.append(("P16", needs.email_seats, "email"))
    if needs.devices:
        selection.append(("P20", needs.devices, "security"))
    if config.failover:
        selection.append(("P09", 1, "failover"))
    selected = {sku for sku, _, _ in selection}
    lines = []
    for sku, quantity, reason in selection:
        item = catalogue.products.get(sku)
        if not eligible(sku) or (item.dependencies and not item.dependencies & selected):
            if sku == "P09":
                raise UnavailableSelection("failover_unavailable")
            return Case(**{**fields, "blocker": "ineligible_bundle"})
        lines.append(QuoteLine(
            sku=sku, name=item.name, unit=item.unit, quantity=quantity,
            rate_paise=item.monthly * 100, monthly_paise=item.monthly * quantity * 100,
            setup_paise=item.setup * quantity * 100, minimum_months=item.months, reason=reason,
        ))
    quote = Quote(
        version=catalogue.version,
        monthly_paise=sum(line.monthly_paise for line in lines),
        setup_paise=sum(line.setup_paise for line in lines),
        lines=lines,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
    )
    blocker = None
    if needs.coverage == "unknown":
        blocker = "coverage_pending"
    elif needs.monthly_budget and quote.monthly_paise > needs.monthly_budget * 100:
        blocker = "over_budget"
    return Case(**{
        **fields, "broadband": broadband, "options": options, "failover": config.failover,
        "quote": quote, "blocker": blocker,
    })
