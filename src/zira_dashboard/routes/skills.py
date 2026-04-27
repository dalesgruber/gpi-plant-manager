"""Skills matrix + roster mutation routes.

Routes:
  GET  /staffing/skills          — render the skills matrix
  POST /staffing/skills          — save the skills matrix
  POST /staffing/people/add      — add a new person
  POST /staffing/people/delete   — remove a person
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import staffing
from ..deps import templates

router = APIRouter()


@router.get("/staffing/skills", response_class=HTMLResponse)
def staffing_skills(request: Request):
    roster = staffing.load_roster()
    roster.sort(key=lambda p: (not p.active, p.name.lower()))
    active_count = sum(1 for p in roster if p.active)
    return templates.TemplateResponse(
        request,
        "skills.html",
        {
            "active": "skills",
            "people": roster,
            "skills": list(staffing.SKILLS),
            "active_count": active_count,
            "inactive_count": len(roster) - active_count,
        },
    )


@router.post("/staffing/skills")
async def staffing_skills_save(request: Request):
    form = await request.form()
    roster = staffing.load_roster()
    for person in roster:
        name = person.name
        if form.get(f"active_present__{name}"):
            person.active = form.get(f"active__{name}") in ("on", "1", "true")
        if form.get(f"reserve_present__{name}"):
            person.reserve = form.get(f"reserve__{name}") in ("on", "1", "true")
        for s in staffing.SKILLS:
            v = form.get(f"skill__{name}__{s}")
            if v is not None:
                try:
                    lvl = int(v)
                    if 0 <= lvl <= 3:
                        person.skills[s] = lvl
                except (TypeError, ValueError):
                    pass
    staffing.save_roster(roster)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True})
    return RedirectResponse(url="/staffing/skills", status_code=303)


@router.post("/staffing/people/add")
async def staffing_person_add(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()[:80]
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    roster = staffing.load_roster()
    if any(p.name.lower() == name.lower() for p in roster):
        return JSONResponse({"ok": False, "error": f"'{name}' already exists"}, status_code=409)
    skills = {s: 0 for s in staffing.SKILLS}
    roster.append(staffing.Person(name=name, active=True, skills=skills))
    staffing.save_roster(roster)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True, "name": name})
    return RedirectResponse(url="/staffing/skills", status_code=303)


@router.post("/staffing/people/delete")
async def staffing_person_delete(request: Request):
    form = await request.form()
    name = (form.get("name") or "").strip()
    if not name:
        return JSONResponse({"ok": False, "error": "name required"}, status_code=400)
    roster = staffing.load_roster()
    before = len(roster)
    roster = [p for p in roster if p.name != name]
    if len(roster) == before:
        return JSONResponse({"ok": False, "error": f"'{name}' not found"}, status_code=404)
    staffing.save_roster(roster)
    if (request.headers.get("accept") or "").startswith("application/json"):
        return JSONResponse({"ok": True, "removed": name})
    return RedirectResponse(url="/staffing/skills", status_code=303)
