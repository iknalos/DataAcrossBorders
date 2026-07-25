import json
import os
import sys
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from models import StudyRecord

NODE_DATA_MAP = {
    "BCH": "data/bch_data.json",
    "MGH": "data/mgh_data.json",
    "BWH": "data/bwh_data.json",
}

HOSPITAL_NODE = os.environ.get("HOSPITAL_NODE", "").upper()

if HOSPITAL_NODE not in NODE_DATA_MAP:
    print(
        f"WARNING: HOSPITAL_NODE='{os.environ.get('HOSPITAL_NODE', '')}' "
        f"is not set or invalid. Valid values: {', '.join(NODE_DATA_MAP)}. "
        f"Defaulting to BCH.",
        file=sys.stderr,
    )
    HOSPITAL_NODE = "BCH"

data_path = Path(__file__).parent / NODE_DATA_MAP[HOSPITAL_NODE]

with open(data_path) as f:
    _raw = json.load(f)

studies: list[StudyRecord] = [StudyRecord(**record) for record in _raw]

app = FastAPI(
    title=f"Hospital Node — {HOSPITAL_NODE}",
    description="Single-node hospital boilerplate for the Open Accelerator Healthcare Hackathon.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"status": "healthy", "node": HOSPITAL_NODE}


@app.get("/api/studies", response_model=list[StudyRecord])
def list_studies():
    return studies


@app.get("/api/studies/{study_id}", response_model=StudyRecord)
def get_study(study_id: str):
    for study in studies:
        if study.StudyID == study_id:
            return study
    raise HTTPException(status_code=404, detail=f"Study '{study_id}' not found on this node.")
