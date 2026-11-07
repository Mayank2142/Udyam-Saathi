from __future__ import annotations

import csv
import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    unit: str
    monthly: int
    max_monthly: int
    setup: int
    months: int
    tiers: frozenset[int]
    employees: int
    sites: int
    needs_gst: bool
    dependencies: frozenset[str]
    days: int

    @classmethod
    def from_csv(cls, row: dict[str, str]) -> Product:
        if row["requires_gstin"] not in {"Y", "N"}:
            raise ValueError("Invalid GST rule")
        product = cls(
            row["product_id"], row["product_name"], row["unit"],
            int(row["price_min_inr_per_month"]), int(row["price_max_inr_per_month"]),
            int(row["one_time_setup_inr"]), int(row["min_contract_months"]),
            frozenset(int(tier) for tier in row["available_city_tiers"].split(";")),
            int(row["min_employees"]), int(row["min_sites"]), row["requires_gstin"] == "Y",
            frozenset(filter(None, row["requires_one_of"].split("|"))),
            int(row["typical_install_days"]),
        )
        if (
            not re.fullmatch(r"P\d{2}", product.sku) or not product.name or not product.unit
            or not product.tiers or not product.tiers <= {1, 2}
            or not 0 <= product.monthly <= product.max_monthly or product.setup < 0
            or min(product.months, product.employees, product.sites) < 1 or product.days < 0
        ):
            raise ValueError(f"Invalid catalogue product {product.sku}")
        return product


class Catalogue:
    def __init__(self, path: Path):
        raw = path.read_bytes()
        records = [Product.from_csv(row) for row in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))]
        self.products = {product.sku: product for product in records}
        if not records or len(records) != len(self.products):
            raise ValueError("Empty catalogue or duplicate product IDs")
        for product in records:
            if not product.dependencies <= self.products.keys() or product.sku in product.dependencies:
                raise ValueError(f"Invalid dependencies for {product.sku}")
        self.version = hashlib.sha256(raw).hexdigest()
