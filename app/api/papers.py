from __future__ import annotations

import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from redis.exceptions import RedisError
from sqlalchemy.orm import Session, selectinload

from app.db import get_db
from app.models import Figure, Paper, PaperAsset, PaperStatus
from app.schemas import AssetRead, FigureRead, PaperRead
from app.services.pdf import (
    LocalMinerUArtifactService,
    PaperCleanupService,
    PaperUploadService,
    audit_table_path_for_paper,
)
from app.services.audit_table_service import (
    chart_fact_records,
    panel_image_map,
    rows_from_records,
)
from app.services.storage import StorageService

router = APIRouter(prefix="/papers", tags=["papers"])


class LocalArtifactImportRequest(BaseModel):
    content_list_path: str
    title: str | None = None


class ChartOnlyRunBatchRequest(BaseModel):
    paper_ids: list[int]


class ChartOnlyRunBatchItem(BaseModel):
    paper_id: int
    status: str
    detail: str | None = None


class ChartOnlyRunBatchResponse(BaseModel):
    total: int
    queued: int
    skipped: int
    not_found: int
    items: list[ChartOnlyRunBatchItem]


@router.post("/upload", response_model=PaperRead, status_code=status.HTTP_201_CREATED)
async def upload_paper(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    db: Session = Depends(get_db),
) -> PaperRead:
    content = await file.read()
    try:
        paper = PaperUploadService(db).create_from_upload(
            filename=file.filename or "paper.pdf",
            content=content,
            title=title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    paper = _paper_or_404(db, paper.id)
    return PaperRead.from_model(paper)


@router.get("", response_model=list[PaperRead])
def list_papers(db: Session = Depends(get_db)) -> list[PaperRead]:
    papers = (
        db.query(Paper)
        .options(selectinload(Paper.assets), selectinload(Paper.figures))
        .filter(Paper.status != PaperStatus.DELETED.value)
        .order_by(Paper.updated_at.desc(), Paper.id.desc())
        .all()
    )
    return [PaperRead.from_model(paper, include_assets=False) for paper in papers]


@router.get("/local-artifacts")
def list_local_artifacts(db: Session = Depends(get_db)) -> list[dict]:
    return LocalMinerUArtifactService(db).list_artifacts()


@router.post("/local-artifacts/import", response_model=PaperRead, status_code=status.HTTP_201_CREATED)
def import_local_artifact(payload: LocalArtifactImportRequest, db: Session = Depends(get_db)) -> PaperRead:
    try:
        paper = LocalMinerUArtifactService(db).import_artifact(
            payload.content_list_path,
            title=payload.title,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PaperRead.from_model(_paper_or_404(db, paper.id))


@router.get("/{paper_id}", response_model=PaperRead)
def get_paper(paper_id: int, db: Session = Depends(get_db)) -> PaperRead:
    return PaperRead.from_model(_paper_or_404(db, paper_id))


@router.get("/{paper_id}/figures", response_model=list[FigureRead])
def list_paper_figures(paper_id: int, db: Session = Depends(get_db)) -> list[FigureRead]:
    paper = _paper_or_404(db, paper_id)
    return [FigureRead.from_model(figure) for figure in paper.figures]


@router.get("/{paper_id}/audit-tables")
def get_paper_audit_tables(paper_id: int, response: Response, db: Session = Depends(get_db)) -> dict:
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    paper = _paper_or_404(db, paper_id)
    audit_file, source = audit_table_path_for_paper(paper)
    audit_path = str(audit_file) if audit_file else None
    if not audit_path:
        return {"tables": {}, "source": None}
    try:
        data = json.loads(open(audit_path, encoding="utf-8").read())
    except Exception:
        return {"tables": {}, "source": source}

        image_by_panel = panel_image_map(data)
        chart_fact_records_local = chart_fact_records(data)
        tables = {
            "chart_facts": rows_from_records(chart_fact_records_local, [
            "source_image", "fact_id", "figure_id", "panel_id", "chart_type", "series_name", "point_index",
            "x_label", "x_value", "x_unit", "y_label", "y_value", "y_unit", "z_label", "z_value", "z_unit",
            "scale_factor", "category_label",
            "confidence", "digitization_status", "needs_review", "source_phase",
        ], image_by_panel=image_by_panel),
        "chart_points": rows_from_records(chart_fact_records_local, [
            "source_image", "fact_id", "figure_id", "panel_id", "chart_type", "series_name", "point_index",
            "x_label", "x_value", "x_unit", "y_label", "y_value", "y_unit", "z_label", "z_value", "z_unit",
            "scale_factor", "category_label",
            "confidence", "digitization_status", "needs_review", "source_phase",
        ], image_by_panel=image_by_panel),
        "heatmap_candidates": rows_from_records(data.get("heatmap_candidates") or [], [
            "source_image", "candidate_id", "figure_id", "panel_id", "metric_name", "series", "condition",
            "value", "value_min", "value_max", "unit", "scale_factor", "evidence_type", "confidence",
            "needs_review", "source_phase",
        ], image_by_panel=image_by_panel),
        "chart_digitization": rows_from_records(data.get("chart_digitization_results") or [], [
            "source_image", "figure_id", "panel_id", "chart_type", "digitization_status",
            "axis_readability", "legend_readability", "calibration_status",
        ], image_by_panel=image_by_panel),
        "metric_candidates": rows_from_records(data.get("metric_candidates") or [], [
            "source_image", "candidate_id", "source_fact_id", "figure_id", "panel_id", "metric_name",
            "matched_target_group_id", "value", "unit", "mapping_reason", "benchmark_relevance",
            "needs_review", "confidence", "verifier_status", "verifier_reason",
        ], image_by_panel=image_by_panel),
        "metric_rows": rows_from_records(data.get("metric_rows") or [], [
            "source_image", "figure_id", "panel_id", "metric_name", "value", "unit", "extraction_source", "release_status",
        ], image_by_panel=image_by_panel),
        "image_observations": rows_from_records(data.get("image_observations") or [], [
            "source_image", "figure_id", "panel_id", "image_kind", "observation_name", "qualitative_value", "numeric_value", "unit",
        ], image_by_panel=image_by_panel),
    }
    return {"tables": tables, "source": source, "audit_path": audit_path}


@router.post("/{paper_id}/chart-only/run", response_model=PaperRead, status_code=status.HTTP_202_ACCEPTED)
def run_paper_chart_only(paper_id: int, db: Session = Depends(get_db)) -> PaperRead:
    return _run_paper_chart_only(paper_id, db)


def _run_paper_chart_only(paper_id: int, db: Session) -> PaperRead:
    paper = _paper_or_404(db, paper_id)
    try:
        paper = PaperUploadService(db).enqueue_chart_only_run(paper)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RedisError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Redis queue unavailable; current results were kept.") from exc
    return PaperRead.from_model(_paper_or_404(db, paper.id))


@router.post("/chart-only/run-batch", response_model=ChartOnlyRunBatchResponse, status_code=status.HTTP_202_ACCEPTED)
def run_chart_only_batch(payload: ChartOnlyRunBatchRequest, db: Session = Depends(get_db)) -> ChartOnlyRunBatchResponse:
    return _run_chart_only_batch(payload, db)


def _run_chart_only_batch(payload: ChartOnlyRunBatchRequest, db: Session) -> ChartOnlyRunBatchResponse:
    service = PaperUploadService(db)
    seen: set[int] = set()
    items: list[ChartOnlyRunBatchItem] = []
    queued = skipped = not_found = 0

    for paper_id in payload.paper_ids:
        if paper_id in seen:
            continue
        seen.add(paper_id)
        paper = db.get(Paper, paper_id)
        if paper is None or str(paper.status) == PaperStatus.DELETED.value:
            not_found += 1
            items.append(ChartOnlyRunBatchItem(paper_id=paper_id, status="not_found"))
            continue
        try:
            service.enqueue_chart_only_run(paper)
        except ValueError as exc:
            skipped += 1
            items.append(ChartOnlyRunBatchItem(paper_id=paper_id, status="skipped", detail=str(exc)))
            continue
        except RedisError:
            skipped += 1
            items.append(ChartOnlyRunBatchItem(paper_id=paper_id, status="skipped", detail="Redis queue unavailable; current results were kept."))
            continue
        queued += 1
        items.append(ChartOnlyRunBatchItem(paper_id=paper_id, status="queued"))

    return ChartOnlyRunBatchResponse(
        total=len(items),
        queued=queued,
        skipped=skipped,
        not_found=not_found,
        items=items,
    )


@router.get("/figures/{figure_id}", response_model=FigureRead)
def get_figure(figure_id: int, db: Session = Depends(get_db)) -> FigureRead:
    figure = db.get(Figure, figure_id)
    if figure is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Figure not found.")
    return FigureRead.from_model(figure)


@router.post("/{paper_id}/retry", response_model=PaperRead, status_code=status.HTTP_202_ACCEPTED)
def retry_paper_parse(paper_id: int, db: Session = Depends(get_db)) -> PaperRead:
    paper = _paper_or_404(db, paper_id)
    try:
        paper = PaperUploadService(db).enqueue_parse(paper, reset=True)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return PaperRead.from_model(_paper_or_404(db, paper.id))


@router.delete("/{paper_id}", status_code=status.HTTP_204_NO_CONTENT)
@router.delete("/{paper_id}/", status_code=status.HTTP_204_NO_CONTENT)
def delete_paper(paper_id: int, db: Session = Depends(get_db)) -> None:
    try:
        deleted = PaperCleanupService(db).delete_paper(paper_id)
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete paper.") from exc
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found.")


@router.post("/{paper_id}/delete", status_code=status.HTTP_204_NO_CONTENT)
def delete_paper_compat(paper_id: int, db: Session = Depends(get_db)) -> None:
    return delete_paper(paper_id, db)


@router.get("/{paper_id}/assets", response_model=list[AssetRead])
def list_assets(paper_id: int, db: Session = Depends(get_db)) -> list[AssetRead]:
    paper = _paper_or_404(db, paper_id)
    return [AssetRead.from_model(asset) for asset in paper.assets]


@router.get("/assets/{asset_id}")
def get_asset_file(asset_id: int, db: Session = Depends(get_db)) -> FileResponse:
    asset = _asset_or_404(db, asset_id)
    path = StorageService().absolute_path(asset.file_path)
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset file not found.")
    return FileResponse(path=path, media_type=asset.mime_type)


def _paper_or_404(db: Session, paper_id: int) -> Paper:
    paper = (
        db.query(Paper)
        .options(selectinload(Paper.assets), selectinload(Paper.figures))
        .filter(Paper.id == paper_id, Paper.status != PaperStatus.DELETED.value)
        .first()
    )
    if paper is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Paper not found.")
    return paper


def _asset_or_404(db: Session, asset_id: int) -> PaperAsset:
    asset = db.get(PaperAsset, asset_id)
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Asset not found.")
    return asset

