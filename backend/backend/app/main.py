import os
import json
import uuid
import redis
import pandas as pd
import psycopg
from fastapi import FastAPI, UploadFile, File, BackgroundTasks, HTTPException

app = FastAPI()

DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
r = redis.from_url(REDIS_URL, decode_responses=True)


def process_excel_in_background(
    task_id: str,
    schema_name: str,
    role_name: str,
    client_id: str,
    file_bytes: bytes
):
    try:
        r.set(f"task:{task_id}", "processing", ex=3600)
        df = pd.read_excel(file_bytes).fillna("")
        records = df.to_dict(orient="records")

        with psycopg.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute(f"SET LOCAL ROLE {role_name}")
                cur.execute(f"SET LOCAL search_path TO {schema_name}")
                cur.execute("DELETE FROM client_data WHERE client_id = %s", (client_id,))
                rows_to_insert = [(client_id, json.dumps(row)) for row in records]
                cur.executemany(
                    "INSERT INTO client_data (client_id, row_data) VALUES (%s, %s::jsonb)",
                    rows_to_insert
                )
                cur.execute(
                    "UPDATE clients SET data_version = data_version + 1, updated_at = NOW() WHERE client_id = %s",
                    (client_id,)
                )
            conn.commit()

        r.set(f"task:{task_id}", f"completed:{len(records)}", ex=3600)
    except Exception as e:
        r.set(f"task:{task_id}", f"failed:{str(e)}", ex=3600)


@app.post("/upload-excel/{client_id}")
async def upload_excel(
    client_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...)
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(status_code=400, detail="Sirf Excel file (.xlsx/.xls) allow hai")

    schema_name = "agency_demo"
    role_name = "role_demo"

    file_bytes = await file.read()
    task_id = str(uuid.uuid4())
    r.set(f"task:{task_id}", "queued", ex=3600)

    background_tasks.add_task(
        process_excel_in_background,
        task_id,
        schema_name,
        role_name,
        client_id,
        file_bytes
    )
    return {"message": "File background me process ho rahi hai", "task_id": task_id}


@app.get("/upload-status/{task_id}")
def get_upload_status(task_id: str):
    status = r.get(f"task:{task_id}")
    if not status:
        raise HTTPException(status_code=404, detail="Task nahi mila")
    return {"task_id": task_id, "status": status}
