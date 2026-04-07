"""Load and prepare data for analysis.

Reads JSON participant files from the data directory and flattens
trial-level data into a single DataFrame.
"""

import json
from pathlib import Path

import pandas as pd


# Path to data directory (relative to project root)
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"


def load_data() -> pd.DataFrame:
    """Load all participant data into a single DataFrame.

    Each JSON file should have a top-level "trials" array.
    Participant-level fields (participant_id, condition, etc.)
    are merged into every trial row.
    """
    records = []
    if not DATA_DIR.exists():
        raise FileNotFoundError(f"Data directory not found: {DATA_DIR}")

    for f in sorted(DATA_DIR.glob("*.json")):
        with open(f) as fh:
            doc = json.load(fh)
        participant_fields = {
            k: v for k, v in doc.items() if k != "trials"
        }
        for trial in doc.get("trials", []):
            row = {**participant_fields, **trial}
            records.append(row)

    if not records:
        raise ValueError(f"No data found in {DATA_DIR}")

    return pd.DataFrame(records)
