"""
Detailed Material Take-Off driven by the Master Material Database.

Additive module: it reads the app's existing state (ProjectInputs,
ExtractedBuildingParams, PackageFacts, uploaded files) and never changes it.

    from detailed_mto import run_detailed_mto
    result, xlsx_bytes = run_detailed_mto(project_inputs, params, facts, files)
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from detailed_mto.builder import build_project
from detailed_mto.engine import DetailedResult, compute
from detailed_mto.export import build_detailed_mto_workbook
from detailed_mto.model import DetailedProject, Options
from detailed_mto.text_scanner import ScanResult, scan_pdf_bytes
from knowledge.loader import load_knowledge_base

__all__ = ["run_detailed_mto", "build_project", "compute", "build_detailed_mto_workbook", "Options",
           "DetailedProject", "DetailedResult", "scan_pdf_bytes", "ScanResult"]


def run_detailed_mto(project_inputs, params, facts=None, files=None, options: Optional[Options] = None,
                     overrides: Optional[Dict[str, float]] = None, scan: Optional[ScanResult] = None,
                     db_path: Optional[str] = None) -> Tuple[DetailedResult, bytes]:
    kb = load_knowledge_base(db_path)
    project = build_project(project_inputs, params, kb, facts=facts, files=files, scan=scan, options=options,
                            overrides=overrides)
    result = compute(project, kb, scope=(options.scope if options else None))
    return result, build_detailed_mto_workbook(result)
