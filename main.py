import os
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Literal
from datetime import datetime, timezone

from database import db, create_document, get_documents
from schemas import PhoneNumber as PhoneNumberSchema

try:
    from bson import ObjectId
except Exception:
    ObjectId = None

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

COLLECTION = "phonenumber"

class PhoneIn(BaseModel):
    phone: str = Field(..., description="Phone number")
    country: Optional[str] = None
    status: Literal["unknown", "has_fb", "no_fb", "review"] = "unknown"
    note: Optional[str] = None

class PhoneUpdate(BaseModel):
    phone: Optional[str] = None
    country: Optional[str] = None
    status: Optional[Literal["unknown", "has_fb", "no_fb", "review"]] = None
    note: Optional[str] = None

class BulkPhones(BaseModel):
    items: List[PhoneIn]

@app.get("/")
def read_root():
    return {"message": "Phone status tracker backend running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set",
        "database_name": "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set",
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            try:
                response["collections"] = db.list_collection_names()
                response["connection_status"] = "Connected"
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️ Connected but Error: {str(e)[:80]}"
        else:
            response["database"] = "❌ Not initialized (check env)"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    return response

@app.get("/phones")
def list_phones(
    status: Optional[str] = Query(None, description="Filter by status"),
    q: Optional[str] = Query(None, description="Search in phone or note"),
    limit: Optional[int] = Query(500, ge=1, le=5000)
):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")

    flt = {}
    if status:
        flt["status"] = status
    if q:
        # Simple case-insensitive contains search on phone or note
        flt["$or"] = [
            {"phone": {"$regex": q, "$options": "i"}},
            {"note": {"$regex": q, "$options": "i"}},
            {"country": {"$regex": q, "$options": "i"}},
        ]

    docs = get_documents(COLLECTION, flt, limit)
    # Convert ObjectId
    for d in docs:
        d["id"] = str(d.pop("_id", ""))
        if "created_at" in d and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
        if "updated_at" in d and hasattr(d["updated_at"], "isoformat"):
            d["updated_at"] = d["updated_at"].isoformat()
    return {"items": docs}

@app.post("/phones")
def add_phone(item: PhoneIn):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    # Validate via schema
    PhoneNumberSchema(**item.model_dump())
    new_id = create_document(COLLECTION, item)
    return {"id": new_id}

@app.post("/phones/bulk")
def add_phones_bulk(payload: BulkPhones):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    inserted = []
    for item in payload.items:
        try:
            PhoneNumberSchema(**item.model_dump())
            new_id = create_document(COLLECTION, item)
            inserted.append(new_id)
        except Exception as e:
            # skip invalid row
            continue
    return {"inserted": inserted, "count": len(inserted)}

@app.patch("/phones/{item_id}")
def update_phone(item_id: str, data: PhoneUpdate):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if ObjectId is None:
        raise HTTPException(status_code=500, detail="ObjectId not available")
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

    update_doc = {k: v for k, v in data.model_dump(exclude_none=True).items()}
    if not update_doc:
        return {"updated": 0}
    update_doc["updated_at"] = datetime.now(timezone.utc)

    res = db[COLLECTION].update_one({"_id": oid}, {"$set": update_doc})
    return {"updated": res.modified_count}

@app.delete("/phones/{item_id}")
def delete_phone(item_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    if ObjectId is None:
        raise HTTPException(status_code=500, detail="ObjectId not available")
    try:
        oid = ObjectId(item_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid id")

    res = db[COLLECTION].delete_one({"_id": oid})
    return {"deleted": res.deleted_count}

@app.get("/phones/export")
def export_phones(status: Optional[str] = None, q: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not available")
    flt = {}
    if status:
        flt["status"] = status
    if q:
        flt["$or"] = [
            {"phone": {"$regex": q, "$options": "i"}},
            {"note": {"$regex": q, "$options": "i"}},
            {"country": {"$regex": q, "$options": "i"}},
        ]
    rows = list(db[COLLECTION].find(flt))
    # Build CSV
    headers = ["phone", "country", "status", "note"]
    import io, csv
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    for r in rows:
        writer.writerow({
            "phone": r.get("phone", ""),
            "country": r.get("country", ""),
            "status": r.get("status", ""),
            "note": r.get("note", ""),
        })
    return output.getvalue()

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
