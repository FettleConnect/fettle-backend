from __future__ import annotations

PRELOADED_DOCTORS: list[dict[str, str]] = [
    {"name": "Dr. Kishore B Reddy", "department": "Orthopedics Oncology"},
    {"name": "Dr. Santoshini Nemuri", "department": "Pediatric Orthopedics"},
    {"name": "Dr. Prathyush Chitithoti", "department": "Orthopedics"},
    {"name": "Dr. Imran Ul Haq", "department": "Cardiology"},
    {"name": "Dr. Ravindra Amjuru", "department": "Cardiology"},
]


def slugify_path_segment(value: str) -> str:
    return value.strip().lower().replace("&", " and ").replace(".", "")
