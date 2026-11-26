"""Furniture and appliance catalog.

The IR stores a free-form ``kind`` for every placed item. Free-form strings
are convenient for the model but dangerous for the kernel: one typo and a
whole rule family stops firing without anyone noticing. The catalog gives
each kind a category, default footprint, semantic flags and localized labels,
and makes unknown kinds visible instead of silently ignorable.

Sizes are millimetres and are defaults, not limits. Clearances come from
common practice; a renovation engineer should confirm them per market.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: Category names double as the page's colour keys.
CATEGORIES: Final = (
    "bed",
    "seat",
    "table",
    "storage",
    "kitchen",
    "laundry",
    "sanitary",
    "electrical",
)

#: MEP point types (water and power, kept apart from furniture).
MEP_TYPES: Final = frozenset({"socket", "switch", "water", "drain", "ac", "gas"})

MEP_LABELS: Final[dict[str, tuple[str, str]]] = {
    "socket": ("插座", "socket"),
    "switch": ("开关", "switch"),
    "water": ("给水", "water"),
    "drain": ("排水", "drain"),
    "ac": ("空调", "air-con"),
    "gas": ("燃气", "gas"),
}


@dataclass(frozen=True)
class CatalogItem:
    """One placeable kind."""

    kind: str
    category: str
    label_zh: str
    label_en: str
    w: float
    d: float
    against_wall: bool = False
    wet: bool = False
    electrical: bool = False
    appliance: bool = False
    needs_water: bool = False
    needs_drain: bool = False
    #: Clear space measured from the item's front face, in millimetres.
    #: ``None`` means "use the category default"; ``0`` means "no requirement"
    #: (a bedside table is reached from the bed, not from the floor).
    front_clearance: float | None = None


def _item(
    kind: str,
    category: str,
    label_zh: str,
    label_en: str,
    w: float,
    d: float,
    **flags: bool | float,
) -> CatalogItem:
    return CatalogItem(
        kind=kind,
        category=category,
        label_zh=label_zh,
        label_en=label_en,
        w=w,
        d=d,
        **flags,  # type: ignore[arg-type]
    )


CATALOG: Final[dict[str, CatalogItem]] = {
    item.kind: item
    for item in (
        # sleeping
        _item("bed_double", "bed", "双人床", "double bed", 1500, 2000, against_wall=True),
        _item("bed_king", "bed", "大床", "king bed", 1800, 2000, against_wall=True),
        _item("bed_single", "bed", "单人床", "single bed", 1000, 2000, against_wall=True),
        _item("crib", "bed", "婴儿床", "crib", 700, 1200, against_wall=True),
        # seating
        _item("sofa", "seat", "沙发", "sofa", 2000, 900),
        _item("armchair", "seat", "单椅", "armchair", 800, 800),
        _item("chair", "seat", "椅子", "chair", 450, 450),
        _item("stool", "seat", "凳子", "stool", 350, 350),
        _item("bench", "seat", "长凳", "bench", 1200, 400),
        # tables
        _item("dining_table", "table", "餐桌", "dining table", 1400, 800),
        _item("coffee_table", "table", "茶几", "coffee table", 1000, 500),
        _item("desk", "table", "书桌", "desk", 1200, 600, against_wall=True),
        _item("side_table", "table", "边几", "side table", 450, 450),
        # storage
        _item(
            "wardrobe",
            "storage",
            "衣柜",
            "wardrobe",
            1800,
            600,
            against_wall=True,
            front_clearance=700,
        ),
        _item(
            "cabinet",
            "storage",
            "储物柜",
            "cabinet",
            1000,
            450,
            against_wall=True,
            front_clearance=700,
        ),
        _item(
            "bookshelf",
            "storage",
            "书架",
            "bookshelf",
            900,
            320,
            against_wall=True,
            front_clearance=600,
        ),
        _item(
            "shoe_cabinet",
            "storage",
            "鞋柜",
            "shoe cabinet",
            1000,
            350,
            against_wall=True,
            front_clearance=700,
        ),
        _item(
            "tv_stand",
            "storage",
            "电视柜",
            "TV stand",
            1600,
            400,
            against_wall=True,
            front_clearance=600,
        ),
        _item(
            "bedside_table",
            "storage",
            "床头柜",
            "bedside table",
            450,
            400,
            against_wall=True,
            front_clearance=0.0,
        ),
        # kitchen
        _item(
            "kitchen_counter",
            "kitchen",
            "橱柜台面",
            "kitchen counter",
            1800,
            600,
            against_wall=True,
            front_clearance=900,
        ),
        _item(
            "sink",
            "kitchen",
            "水槽",
            "sink",
            800,
            500,
            against_wall=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=900,
        ),
        _item(
            "stove",
            "kitchen",
            "灶台",
            "stove",
            700,
            500,
            against_wall=True,
            appliance=True,
            electrical=True,
            front_clearance=900,
        ),
        _item(
            "hood",
            "kitchen",
            "抽油烟机",
            "range hood",
            750,
            500,
            against_wall=True,
            appliance=True,
            electrical=True,
        ),
        _item(
            "fridge",
            "kitchen",
            "冰箱",
            "fridge",
            700,
            700,
            against_wall=True,
            appliance=True,
            electrical=True,
            front_clearance=900,
        ),
        _item(
            "oven",
            "kitchen",
            "烤箱",
            "oven",
            600,
            600,
            against_wall=True,
            appliance=True,
            electrical=True,
            front_clearance=600,
        ),
        _item(
            "dishwasher",
            "kitchen",
            "洗碗机",
            "dishwasher",
            600,
            600,
            against_wall=True,
            appliance=True,
            electrical=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=600,
        ),
        # laundry
        _item(
            "washer",
            "laundry",
            "洗衣机",
            "washer",
            600,
            600,
            against_wall=True,
            appliance=True,
            electrical=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=600,
        ),
        _item(
            "dryer",
            "laundry",
            "烘干机",
            "dryer",
            600,
            600,
            against_wall=True,
            appliance=True,
            electrical=True,
            front_clearance=600,
        ),
        # sanitary
        _item(
            "toilet",
            "sanitary",
            "马桶",
            "toilet",
            400,
            700,
            against_wall=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=600,
        ),
        _item(
            "basin",
            "sanitary",
            "洗手盆",
            "basin",
            600,
            450,
            against_wall=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=600,
        ),
        _item(
            "shower",
            "sanitary",
            "淋浴",
            "shower",
            900,
            900,
            wet=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=600,
        ),
        _item(
            "bathtub",
            "sanitary",
            "浴缸",
            "bathtub",
            1700,
            750,
            against_wall=True,
            wet=True,
            needs_water=True,
            needs_drain=True,
            front_clearance=600,
        ),
        # electrical points that used to be modelled as furniture
        _item("socket", "electrical", "插座", "socket", 86, 86, electrical=True),
        _item("switch", "electrical", "开关", "switch", 86, 86, electrical=True),
    )
}

#: Spelling variants the model or the user may reach for.
ALIASES: Final[dict[str, str]] = {
    "closet": "wardrobe",
    "bookcase": "bookshelf",
    "tv_cabinet": "tv_stand",
    "nightstand": "bedside_table",
    "refrigerator": "fridge",
    "washing_machine": "washer",
    "washing machine": "washer",
    "tumble_dryer": "dryer",
    "cooker": "stove",
    "cooktop": "stove",
    "hob": "stove",
    "range_hood": "hood",
    "rangehood": "hood",
    "bath": "bathtub",
    "wc": "toilet",
    "lavatory": "basin",
    "washbasin": "basin",
    "sink_basin": "basin",
    "diningtable": "dining_table",
    "coffee table": "coffee_table",
    "double_bed": "bed_double",
    "king_bed": "bed_king",
    "single_bed": "bed_single",
}

_ALIASES_CASEFOLDED: Final[dict[str, str]] = {
    key.casefold(): value for key, value in ALIASES.items()
}


def normalize_kind(kind: str) -> str:
    """Return the canonical kind for *kind* (alias and case aware)."""
    text = (kind or "").strip().casefold()
    if not text:
        return ""
    if text in CATALOG:
        return text
    if text in _ALIASES_CASEFOLDED:
        return _ALIASES_CASEFOLDED[text]
    # ``Washer`` / ``WASHER`` and the spaces-for-underscores variant.
    spaced = text.replace(" ", "_")
    if spaced in CATALOG:
        return spaced
    if spaced in _ALIASES_CASEFOLDED:
        return _ALIASES_CASEFOLDED[spaced]
    return text


def resolve(kind: str) -> CatalogItem | None:
    """Look a kind up in the catalog, or return ``None`` when unknown."""
    canonical = normalize_kind(kind)
    return CATALOG.get(canonical)


def category_of(kind: str) -> str:
    """Category name, or an empty string for unknown kinds."""
    item = resolve(kind)
    return item.category if item else ""


def is_mep_type(point_type: str) -> bool:
    return (point_type or "").strip().casefold() in MEP_TYPES


def default_footprint(kind: str) -> tuple[float, float] | None:
    """Default ``(width, depth)`` for a kind, or ``None`` when unknown."""
    item = resolve(kind)
    return (item.w, item.d) if item else None


def catalog_rows() -> list[dict[str, object]]:
    """Flatten the catalog for docs, the expert, or a debug endpoint."""
    return [
        {
            "kind": item.kind,
            "category": item.category,
            "label_zh": item.label_zh,
            "label_en": item.label_en,
            "w": item.w,
            "d": item.d,
            "against_wall": item.against_wall,
            "appliance": item.appliance,
            "wet": item.wet,
            "electrical": item.electrical,
            "needs_water": item.needs_water,
            "needs_drain": item.needs_drain,
            "front_clearance": item.front_clearance,
        }
        for item in CATALOG.values()
    ]
