"""FastAPI application factory."""

from __future__ import annotations

import shutil
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select, text

from clipper import __version__
from clipper.api import assets as assets_api
from clipper.api import clips as clips_api
from clipper.api import errors
from clipper.api.errors import ApiError
from clipper.api.schemas import JobCreate, JobOut, Page, ProjectCreate, ProjectOut, ProjectUpdate
from clipper.api.security import LocalGuardMiddleware
from clipper.core.config import Settings, get_settings
from clipper.core.ids import is_valid_id
from clipper.core.logging import configure_logging
from clipper.core.services import Services
from clipper.db import migrate
from clipper.db.models import TERMINAL, Job, JobStatus, Project


def state(request: Request) -> Services:
    st: Services = request.app.state.clipper
    return st


St = Annotated[Services, Depends(state)]


def _check_id(value: str) -> str:
    if not is_valid_id(value):
        raise ApiError(404, "NOT_FOUND", "Not found.")
    return value


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.data_dir.mkdir(
            parents=True, exist_ok=True
        )  # fresh install: folder must exist before SQLite opens
        migrate.upgrade(settings.db_url)
        app.state.clipper = Services(settings)
        yield
        app.state.clipper.close()

    app = FastAPI(
        title="Clipper API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )
    app.add_middleware(LocalGuardMiddleware, allowed_hosts=settings.allowed_hosts)
    errors.install(app)

    # ---------------- health ----------------
    @app.get("/healthz", include_in_schema=False)
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    def readyz(st: St) -> Response:
        checks: dict[str, str] = {}
        try:
            with st.db.engine.connect() as c:
                c.execute(text("SELECT 1"))
            checks["database"] = "ok" if migrate.current_revision(st.db) else "not migrated"
        except Exception:
            checks["database"] = "unavailable"
        checks["ffmpeg"] = "ok" if shutil.which("ffmpeg") and shutil.which("ffprobe") else "missing"
        free = st.storage.free_bytes()
        checks["disk"] = "ok" if free > 2 * 1024**3 else "low"
        ok = checks["database"] == "ok" and checks["ffmpeg"] == "ok"
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "checks": checks, "version": __version__},
            status_code=200 if ok else 503,
        )

    api = APIRouter(prefix="/api/v1")

    # ---------------- projects ----------------
    @api.post("/projects", status_code=201, response_model=ProjectOut)
    def create_project(body: ProjectCreate, st: St) -> Project:
        p = Project(name=body.name, settings={})
        with st.db.session() as s:
            s.add(p)
        return p

    @api.get("/projects", response_model=Page[ProjectOut])
    def list_projects(
        st: St, limit: Annotated[int, Query(ge=1, le=200)] = 50, cursor: str | None = None
    ) -> dict[str, object]:
        with st.db.session() as s:
            q = select(Project).order_by(Project.id.desc()).limit(limit + 1)
            if cursor:
                q = q.where(Project.id < _check_id(cursor))
            rows = list(s.scalars(q))
        nxt = rows[limit - 1].id if len(rows) > limit else None
        return {"items": rows[:limit], "next_cursor": nxt}

    @api.get("/projects/{project_id}", response_model=ProjectOut)
    def get_project(project_id: str, st: St) -> Project:
        with st.db.session() as s:
            p = s.get(Project, _check_id(project_id))
            if p is None:
                raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
            return p

    @api.patch("/projects/{project_id}", response_model=ProjectOut)
    def update_project(project_id: str, body: ProjectUpdate, st: St) -> Project:
        with st.db.session() as s:
            p = s.get(Project, _check_id(project_id))
            if p is None:
                raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
            if body.name is not None:
                p.name = body.name
            s.flush()
            return p

    @api.delete("/projects/{project_id}", status_code=204)
    def delete_project(project_id: str, st: St) -> Response:
        """Hard delete: cancels the project's jobs, removes DB rows and every file under the project."""
        pid = _check_id(project_id)
        with st.db.session() as s:
            p = s.get(Project, pid)
            if p is None:
                raise ApiError(404, "PROJECT_NOT_FOUND", "Project not found.")
            running = s.scalars(
                select(Job.id).where(Job.project_id == pid, Job.status == JobStatus.RUNNING)
            ).all()
            if running:
                for jid in running:
                    st.queue.request_cancel(jid)
                raise ApiError(
                    409,
                    "PROJECT_BUSY",
                    "This project has running jobs.",
                    "They are being cancelled. Try deleting again in a few seconds.",
                )
            s.delete(p)
        try:
            st.storage.delete_prefix(f"projects/{pid}")
        except OSError as e:  # e.g. Windows: a file is still open by an upload or player
            raise ApiError(
                409,
                "FILES_IN_USE",
                "Some project files are still in use.",
                "The project was removed; leftover files will be cleaned up. Try again shortly.",
            ) from e
        return Response(status_code=204)

    # ---------------- jobs ----------------
    @api.get("/jobs", response_model=Page[JobOut])
    def list_jobs(
        st: St,
        status: str | None = None,
        project_id: str | None = None,
        limit: Annotated[int, Query(ge=1, le=200)] = 50,
    ) -> dict[str, object]:
        with st.db.session() as s:
            q = select(Job).order_by(Job.created_at.desc()).limit(limit)
            if status:
                if status not in set(JobStatus):
                    raise ApiError(422, "VALIDATION_ERROR", "Unknown job status.")
                q = q.where(Job.status == status)
            if project_id:
                q = q.where(Job.project_id == _check_id(project_id))
            return {"items": list(s.scalars(q)), "next_cursor": None}

    @api.post("/jobs", status_code=202, response_model=JobOut)
    def create_job(body: JobCreate, st: St) -> Job:
        jid = st.queue.enqueue(body.type, body.payload)
        with st.db.session() as s:
            job = s.get(Job, jid)
            if job is None:  # cannot happen unless the DB is reset concurrently
                raise ApiError(500, "INTERNAL_ERROR", "Job was not saved.")
            return job

    @api.get("/jobs/{job_id}", response_model=JobOut)
    def get_job(job_id: str, st: St) -> Job:
        with st.db.session() as s:
            job = s.get(Job, _check_id(job_id))
            if job is None:
                raise ApiError(404, "JOB_NOT_FOUND", "Job not found.")
            return job

    @api.post("/jobs/{job_id}/cancel", status_code=202, response_model=JobOut)
    def cancel_job(job_id: str, st: St) -> Job:
        if st.queue.request_cancel(_check_id(job_id)) is None:
            raise ApiError(404, "JOB_NOT_FOUND", "Job not found.")
        return get_job(job_id, st)

    @api.post("/jobs/{job_id}/retry", status_code=202, response_model=JobOut)
    def retry_job(job_id: str, st: St) -> Job:
        job = get_job(job_id, st)
        if job.status not in TERMINAL or job.status == JobStatus.SUCCEEDED:
            raise ApiError(409, "JOB_NOT_RETRYABLE", "Only failed or cancelled jobs can be retried.")
        st.queue.retry(job.id)
        return get_job(job_id, st)

    app.include_router(api)
    app.include_router(assets_api.router)
    app.include_router(clips_api.router)
    return app
