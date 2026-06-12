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

