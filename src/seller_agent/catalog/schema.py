from __future__ import annotations

from dataclasses import dataclass, fields


@dataclass
class MasterCatalogRow:
    master_sku: str
    title: str = ""
    product_group: str = ""
    pack_qty: str = ""
    supplier_sku: str = ""
    ozon_offer_id: str = ""
    ozon_product_id: str = ""
    ozon_sku: str = ""
    ozon_barcode: str = ""
    wb_vendor_code: str = ""
    wb_nm_id: str = ""
    wb_barcode: str = ""
    status_ozon: str = ""
    status_wb: str = ""
    match_status: str = ""
    notes: str = ""


MASTER_CATALOG_FIELDS = [field.name for field in fields(MasterCatalogRow)]


@dataclass
class UnifiedCatalogProduct:
    internal_product_id: str
    internal_sku: str = ""
    product_name: str = ""
    product_group: str = ""
    pack_qty: str = "1"
    cost_total: str = ""
    cost_per_unit: str = ""
    ozon_offer_id: str = ""
    ozon_product_id: str = ""
    ozon_sku: str = ""
    wb_vendor_code: str = ""
    wb_nm_id: str = ""
    mapping_status: str = ""
    active_ozon: str = "false"
    active_wb: str = "false"
    notes: str = ""


UNIFIED_CATALOG_FIELDS = [field.name for field in fields(UnifiedCatalogProduct)]
