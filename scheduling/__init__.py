"""
Construction schedule (CPM) and Gantt chart built from the material take-off.

The BOQ work-item quantities of a DetailedResult are grouped into construction
activities (floor by floor for the structure, masonry, rough-ins, plaster and
flooring), each duration = quantity / (output per gang-day x gangs), activities
are linked finish-to-start with curing lags, and a critical-path pass on a
working-day calendar gives dates, float and the critical path.

    from scheduling import ScheduleSettings, build_schedule
    sched = build_schedule(result, ScheduleSettings(start_date="2026-10-01"))
"""
from scheduling.engine import Activity, Schedule, ScheduleSettings, build_schedule, material_delivery_plan
from scheduling.export import build_schedule_workbook

__all__ = ["Activity", "Schedule", "ScheduleSettings", "build_schedule", "material_delivery_plan",
           "build_schedule_workbook"]
