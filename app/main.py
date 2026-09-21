import logging
import os
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.database import init_db
from app.routers import invoices, purchase_orders

STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="KanjoAI",
    description=(
        "Autonomous invoice/vendor reconciliation agent built for AI HACK 2026. "
        "Uses OrcaRouter (https://www.orcarouter.ai) for tiered, cost-aware, "
        "auto-failover LLM routing between extraction and decision steps."
    ),
    version="0.1.0",
)


@app.on_event("startup")
def on_startup():
    init_db()


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Any exception that escapes a route handler lands here instead of
    leaking a raw traceback to the client. Each failure gets a short
    error id that's logged server-side with the full stack trace, so it
    stays debuggable without exposing internals over the API.
    """
    error_id = uuid.uuid4().hex[:8]
    logging.getLogger("kanjoai.errors").exception(
        "Unhandled exception [error_id=%s] on %s %s", error_id, request.method, request.url.path
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error.", "error_id": error_id},
    )


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(purchase_orders.router, tags=["purchase-orders"])
app.include_router(invoices.router, tags=["invoices"])

# Sample invoice images used by the try-it-yourself UI below.
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def demo_ui():
    """The interactive demo page — upload or pick a sample invoice and
    watch it move through the pipeline in real time. Served directly so
    the whole thing runs off one process with no separate frontend build.
    """
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))
