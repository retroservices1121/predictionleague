from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models, crud

import os
ADMIN_KEY = os.getenv("ADMIN_KEY", "changeme")

app = FastAPI(title="Predictions League Bot API")

def admin_auth(key: str):
    if key != ADMIN_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

@app.post("/admin/predictions/create")
def create_prediction(question: str, options: list[str], db: Session = Depends(get_db), key: str = ""):
    admin_auth(key)
    return crud.create_prediction(db, question, options)

@app.post("/admin/predictions/{prediction_id}/resolve")
def resolve_prediction(prediction_id: int, result: str, db: Session = Depends(get_db), key: str = ""):
    admin_auth(key)
    pred = db.query(models.Prediction).filter(models.Prediction.id == prediction_id).first()
    if not pred:
        raise HTTPException(status_code=404, detail="Prediction not found")
    pred.result = result
    db.commit()
    return {"status": "resolved", "prediction": pred.id}
