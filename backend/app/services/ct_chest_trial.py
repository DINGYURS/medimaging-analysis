"""Read the source-bound trial result for the authenticated study."""
import json
from pathlib import Path

from app.core.config import settings
from app.services.insight_payload import signature


TRIALS = Path(settings.STORAGE_DIR) / "ct_chest_trials"


def result_for(task_id, study):
    if not study or study.modality != "CT" or not study.nifti_path:
        return None
    record = TRIALS / f"task_{task_id}.json"
    try:
        stored = json.loads(record.read_text())
        if stored["source"] != signature(study.nifti_path):
            return None
        if stored["series_uid"] != (study.metadata_json or {}).get("series_instance_uid"):
            return None
        return stored["result"]
    except (OSError, KeyError, ValueError, TypeError):
        return None
