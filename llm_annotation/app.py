#!/usr/bin/env python3

import asyncio
import base64
import csv
import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

import requests
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from PIL import Image


ROOT = Path(__file__).resolve().parent
IMAGE_ROOT = ROOT / "data" / "images"
RESULTS_ROOT = ROOT / "results"
STATIC_ROOT = ROOT / "static"
DB_PATH = RESULTS_ROOT / "annotations.sqlite3"
JSONL_PATH = RESULTS_ROOT / "annotations.jsonl"
CSV_PATH = RESULTS_ROOT / "annotations.csv"
REQUIRED_SIZE = (356, 130)

DEFAULT_PROMPT = """Analyze one cropped US license plate image.

Return JSON only with exactly these two string fields:
{"state":"<full state name, District of Columbia, or UNKNOWN>","plate":"<complete plate number or UNKNOWN>"}

Rules:
- Use the full state name. If the state is not fully readable, return UNKNOWN for state.
- Return the complete plate number using uppercase A-Z and digits only. Remove spaces and hyphens.
- If any plate character is uncertain, obscured, or only partially readable, return UNKNOWN for plate.
- Do not infer hidden characters from plate formats.
- Do not add explanations, Markdown, or extra fields."""

load_dotenv(ROOT / ".env")
API_KEY = os.getenv("AUTODL_API_KEY", "").strip()
BASE_URL = os.getenv("AUTODL_BASE_URL", "https://www.autodl.art/api/v1").rstrip("/")
MODEL = os.getenv("AUTODL_MODEL", "GLM-5.3-flash").strip()
CONCURRENCY = max(1, int(os.getenv("LLM_CONCURRENCY", "3")))

app = FastAPI(title="DDOT License Plate Annotation")
app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")

job_lock = threading.Lock()
export_lock = threading.Lock()
batch_job = {
    "running": False,
    "total": 0,
    "completed": 0,
    "failed": 0,
    "current": "",
    "last_error": "",
}


class PromptUpdate(BaseModel):
    prompt: str


class HumanUpdate(BaseModel):
    state: str
    plate: str


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def connection() -> sqlite3.Connection:
    db = sqlite3.connect(DB_PATH, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def initialize() -> None:
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
    with connection() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                relative_path TEXT NOT NULL UNIQUE,
                filename TEXT NOT NULL,
                date_group TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                sha256 TEXT NOT NULL UNIQUE
            );
            CREATE TABLE IF NOT EXISTS annotations (
                image_id INTEGER PRIMARY KEY,
                llm_state TEXT,
                llm_plate TEXT,
                llm_raw TEXT,
                llm_updated_at TEXT,
                human_state TEXT,
                human_plate TEXT,
                human_status TEXT NOT NULL DEFAULT 'pending',
                human_updated_at TEXT,
                FOREIGN KEY(image_id) REFERENCES images(id)
            );
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO settings(key, value) VALUES('prompt', ?)",
            (DEFAULT_PROMPT,),
        )

        for path in sorted(IMAGE_ROOT.rglob("*")):
            if not path.is_file():
                continue
            try:
                with Image.open(path) as image:
                    image.load()
                    if image.size != REQUIRED_SIZE:
                        continue
            except Exception:
                continue
            relative = path.relative_to(IMAGE_ROOT).as_posix()
            date_group = relative.split("/", 1)[0] if "/" in relative else ""
            db.execute(
                """
                INSERT OR IGNORE INTO images(
                    relative_path, filename, date_group, width, height, sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (relative, path.name, date_group, REQUIRED_SIZE[0], REQUIRED_SIZE[1], sha256(path)),
            )
        db.execute(
            """
            INSERT OR IGNORE INTO annotations(image_id)
            SELECT id FROM images
            """
        )
    export_results()


def get_prompt() -> str:
    with connection() as db:
        row = db.execute("SELECT value FROM settings WHERE key='prompt'").fetchone()
    return row["value"] if row else DEFAULT_PROMPT


def normalize_state(value) -> str:
    text = str(value or "").strip()
    if not text or text.upper() == "UNKNOWN":
        return "UNKNOWN"
    return " ".join(text.split())[:80]


def normalize_plate(value) -> str:
    text = str(value or "").strip().upper()
    if not text or "UNKNOWN" in text:
        return "UNKNOWN"
    normalized = re.sub(r"[^A-Z0-9]", "", text)
    return normalized or "UNKNOWN"


def parse_llm_json(content: str) -> tuple[str, str, dict]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise ValueError(f"The model did not return JSON: {content[:300]}")
        data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("The model response is not a JSON object")
    return normalize_state(data.get("state")), normalize_plate(data.get("plate")), data


def image_data_url(path: Path) -> str:
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def request_llm(path: Path, prompt: str) -> tuple[str, str, str]:
    if not API_KEY:
        raise RuntimeError("AUTODL_API_KEY is missing from .env")
    payload = {
        "model": MODEL,
        "temperature": 0,
        "max_tokens": 160,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_data_url(path)}},
                ],
            }
        ],
    }
    response = requests.post(
        f"{BASE_URL}/chat/completions",
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(f"API returned {response.status_code}: {response.text[:500]}")
    data = response.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected API response: {json.dumps(data)[:500]}") from exc
    state, plate, parsed = parse_llm_json(content)
    raw = json.dumps({"response": data, "parsed": parsed}, ensure_ascii=False)
    return state, plate, raw


def label_image(image_id: int) -> dict:
    with connection() as db:
        row = db.execute(
            """
            SELECT i.*, a.human_status
            FROM images i JOIN annotations a ON a.image_id=i.id
            WHERE i.id=?
            """,
            (image_id,),
        ).fetchone()
    if not row:
        raise KeyError(image_id)

    path = (IMAGE_ROOT / row["relative_path"]).resolve()
    if IMAGE_ROOT.resolve() not in path.parents or not path.is_file():
        raise RuntimeError("Image file is missing")
    state, plate, raw = request_llm(path, get_prompt())

    with connection() as db:
        db.execute(
            """
            UPDATE annotations
            SET llm_state=?, llm_plate=?, llm_raw=?, llm_updated_at=?,
                human_state=CASE WHEN human_status='confirmed' THEN human_state ELSE ? END,
                human_plate=CASE WHEN human_status='confirmed' THEN human_plate ELSE ? END
            WHERE image_id=?
            """,
            (state, plate, raw, now(), state, plate, image_id),
        )
    export_results()
    return {"state": state, "plate": plate}


def export_results() -> None:
    with export_lock:
        with connection() as db:
            rows = db.execute(
                """
                SELECT i.relative_path, i.sha256, i.width, i.height,
                       a.llm_state, a.llm_plate, a.llm_updated_at,
                       a.human_state, a.human_plate, a.human_status, a.human_updated_at
                FROM images i JOIN annotations a ON a.image_id=i.id
                ORDER BY i.relative_path
                """
            ).fetchall()
        fields = [
            "relative_path", "sha256", "width", "height", "llm_state", "llm_plate",
            "llm_updated_at", "human_state", "human_plate", "human_status", "human_updated_at",
        ]
        with JSONL_PATH.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(dict(row), ensure_ascii=False) + "\n")
        with CSV_PATH.open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(dict(row) for row in rows)


def serialize_item(row: sqlite3.Row) -> dict:
    data = dict(row)
    data["image_url"] = f"/api/images/{row['id']}"
    data.pop("llm_raw", None)
    return data


def counts() -> dict:
    with connection() as db:
        row = db.execute(
            """
            SELECT COUNT(*) AS total,
                   SUM(CASE WHEN llm_state IS NOT NULL THEN 1 ELSE 0 END) AS llm_labeled,
                   SUM(CASE WHEN human_status='confirmed' THEN 1 ELSE 0 END) AS confirmed
            FROM annotations
            """
        ).fetchone()
    return {key: int(row[key] or 0) for key in ("total", "llm_labeled", "confirmed")}


async def run_batch(ids: list[int]) -> None:
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def run_one(image_id: int) -> None:
        with connection() as db:
            item = db.execute("SELECT relative_path FROM images WHERE id=?", (image_id,)).fetchone()
        with job_lock:
            batch_job["current"] = item["relative_path"] if item else str(image_id)
        try:
            async with semaphore:
                await asyncio.to_thread(label_image, image_id)
        except Exception as exc:
            with job_lock:
                batch_job["failed"] += 1
                batch_job["last_error"] = str(exc)
        finally:
            with job_lock:
                batch_job["completed"] += 1

    await asyncio.gather(*(run_one(image_id) for image_id in ids))
    with job_lock:
        batch_job["running"] = False
        batch_job["current"] = ""


@app.on_event("startup")
def startup() -> None:
    initialize()


@app.get("/", response_class=HTMLResponse)
def index():
    return FileResponse(STATIC_ROOT / "index.html")


@app.get("/browse", response_class=HTMLResponse)
def browse():
    return FileResponse(STATIC_ROOT / "browse.html")


@app.get("/api/config")
def config():
    return {
        "model": MODEL,
        "api_configured": bool(API_KEY),
        "prompt": get_prompt(),
        "counts": counts(),
    }


@app.put("/api/prompt")
def update_prompt(update: PromptUpdate):
    prompt = update.prompt.strip()
    if not prompt:
        raise HTTPException(400, "Prompt cannot be empty")
    with connection() as db:
        db.execute(
            "INSERT INTO settings(key, value) VALUES('prompt', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (prompt,),
        )
    return {"prompt": prompt}


@app.get("/api/items")
def items(
    status: str = Query("all", pattern="^(all|unlabeled|unreviewed|confirmed)$"),
    search: str = "",
):
    conditions = []
    params = []
    if status == "unlabeled":
        conditions.append("a.llm_state IS NULL")
    elif status == "unreviewed":
        conditions.append("a.llm_state IS NOT NULL AND a.human_status!='confirmed'")
    elif status == "confirmed":
        conditions.append("a.human_status='confirmed'")
    if search.strip():
        conditions.append(
            "(i.relative_path LIKE ? OR a.llm_state LIKE ? OR a.llm_plate LIKE ? "
            "OR a.human_state LIKE ? OR a.human_plate LIKE ?)"
        )
        token = f"%{search.strip()}%"
        params.extend([token] * 5)
    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with connection() as db:
        rows = db.execute(
            f"""
            SELECT i.id, i.relative_path, i.filename, i.date_group, i.width, i.height, i.sha256,
                   a.llm_state, a.llm_plate, a.llm_updated_at,
                   a.human_state, a.human_plate, a.human_status, a.human_updated_at
            FROM images i JOIN annotations a ON a.image_id=i.id
            {where}
            ORDER BY i.relative_path
            """,
            params,
        ).fetchall()
    return {"items": [serialize_item(row) for row in rows], "counts": counts()}


@app.get("/api/browse")
def browse_items(
    status: str = Query(
        "all",
        pattern="^(all|unlabeled|unreviewed|confirmed|match|disagreement|unknown)$",
    ),
    search: str = "",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=12, le=96),
):
    conditions = []
    params = []
    if status == "unlabeled":
        conditions.append("a.llm_state IS NULL")
    elif status == "unreviewed":
        conditions.append("a.llm_state IS NOT NULL AND a.human_status!='confirmed'")
    elif status == "confirmed":
        conditions.append("a.human_status='confirmed'")
    elif status == "match":
        conditions.append(
            "a.human_status='confirmed' AND a.llm_state IS NOT NULL "
            "AND UPPER(TRIM(a.llm_state))=UPPER(TRIM(a.human_state)) "
            "AND UPPER(TRIM(a.llm_plate))=UPPER(TRIM(a.human_plate))"
        )
    elif status == "disagreement":
        conditions.append(
            "a.human_status='confirmed' AND a.llm_state IS NOT NULL AND "
            "(UPPER(TRIM(a.llm_state))!=UPPER(TRIM(a.human_state)) "
            "OR UPPER(TRIM(a.llm_plate))!=UPPER(TRIM(a.human_plate)))"
        )
    elif status == "unknown":
        conditions.append(
            "(a.llm_state='UNKNOWN' OR a.llm_plate='UNKNOWN' "
            "OR a.human_state='UNKNOWN' OR a.human_plate='UNKNOWN')"
        )
    if search.strip():
        conditions.append(
            "(i.relative_path LIKE ? OR a.llm_state LIKE ? OR a.llm_plate LIKE ? "
            "OR a.human_state LIKE ? OR a.human_plate LIKE ?)"
        )
        token = f"%{search.strip()}%"
        params.extend([token] * 5)

    where = "WHERE " + " AND ".join(conditions) if conditions else ""
    with connection() as db:
        total = db.execute(
            f"""
            SELECT COUNT(*)
            FROM images i JOIN annotations a ON a.image_id=i.id
            {where}
            """,
            params,
        ).fetchone()[0]
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        rows = db.execute(
            f"""
            SELECT i.id, i.relative_path, i.filename, i.date_group, i.width, i.height, i.sha256,
                   a.llm_state, a.llm_plate, a.llm_updated_at,
                   a.human_state, a.human_plate, a.human_status, a.human_updated_at
            FROM images i JOIN annotations a ON a.image_id=i.id
            {where}
            ORDER BY i.relative_path
            LIMIT ? OFFSET ?
            """,
            [*params, page_size, (page - 1) * page_size],
        ).fetchall()
    return {
        "items": [serialize_item(row) for row in rows],
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": total_pages,
        "counts": counts(),
    }


@app.get("/api/images/{image_id}")
def image(image_id: int):
    with connection() as db:
        row = db.execute("SELECT relative_path FROM images WHERE id=?", (image_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Image not found")
    path = (IMAGE_ROOT / row["relative_path"]).resolve()
    if IMAGE_ROOT.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "Image file is missing")
    return FileResponse(path, media_type="image/jpeg")


@app.post("/api/items/{image_id}/label")
async def label(image_id: int):
    try:
        result = await asyncio.to_thread(label_image, image_id)
    except KeyError:
        raise HTTPException(404, "Image not found")
    except Exception as exc:
        raise HTTPException(502, str(exc))
    return {"llm": result, "counts": counts()}


@app.patch("/api/items/{image_id}/human")
def save_human(image_id: int, update: HumanUpdate):
    state = normalize_state(update.state)
    plate = normalize_plate(update.plate)
    with connection() as db:
        changed = db.execute(
            """
            UPDATE annotations
            SET human_state=?, human_plate=?, human_status='confirmed', human_updated_at=?
            WHERE image_id=?
            """,
            (state, plate, now(), image_id),
        ).rowcount
    if not changed:
        raise HTTPException(404, "Image not found")
    export_results()
    return {"state": state, "plate": plate, "status": "confirmed", "counts": counts()}


@app.post("/api/batch")
async def start_batch():
    with job_lock:
        if batch_job["running"]:
            return dict(batch_job)
    with connection() as db:
        ids = [
            row["image_id"]
            for row in db.execute(
                "SELECT image_id FROM annotations WHERE llm_state IS NULL ORDER BY image_id"
            ).fetchall()
        ]
    with job_lock:
        batch_job.update(
            running=bool(ids), total=len(ids), completed=0, failed=0, current="", last_error=""
        )
    if ids:
        asyncio.create_task(run_batch(ids))
    return dict(batch_job)


@app.get("/api/batch")
def batch_status():
    with job_lock:
        return dict(batch_job)


@app.get("/api/export/{format_name}")
def export(format_name: str):
    export_results()
    if format_name == "jsonl":
        return FileResponse(JSONL_PATH, filename="annotations.jsonl")
    if format_name == "csv":
        return FileResponse(CSV_PATH, filename="annotations.csv")
    raise HTTPException(404, "Export format not found")
