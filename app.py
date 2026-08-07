import os
import re
import json
import asyncio
import requests
import uvicorn
from pathlib import Path
from io import BytesIO
from datetime import datetime, timezone

from fastapi import FastAPI, Request, UploadFile, File, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from langserve import add_routes
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field
from duckduckgo_search import DDGS
from pypdf import PdfReader

# -----------------------------
# 1. FASTAPI APP
# -----------------------------

app = FastAPI(title="Placement-Ready AI Career Agent API")

BASE_DIR = Path(__file__).resolve().parent

app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)

templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request, "index.html", {})


# -----------------------------
# 2. GEMINI MODEL
# -----------------------------

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")
ADZUNA_APP_ID = os.environ.get("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.environ.get("ADZUNA_APP_KEY")

llm = None
if GOOGLE_API_KEY:
    try:
        llm = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            api_key=GOOGLE_API_KEY,
            temperature=0.4
        )
    except Exception:
        llm = None


# -----------------------------
# 3. RULE-BASED FALLBACKS (used only if Gemini is unavailable/times out)
# -----------------------------

ROLE_SKILLS = {
    "java developer": ["Java", "Spring Boot", "REST API", "MySQL", "Git"],
    "full stack developer": ["React", "Node.js", "MongoDB", "Express", "Docker"],
    "ai engineer": ["Python", "Machine Learning", "TensorFlow", "SQL", "LangChain"]
}

ROLE_PROJECTS = {
    "java developer": [
        "Banking Management System (Spring Boot)",
        "Employee Portal API",
        "Library Management System"
    ],
    "full stack developer": [
        "Job Portal MERN App",
        "AI Resume Analyzer",
        "Placement Tracker Dashboard"
    ],
    "ai engineer": [
        "Career Agent using LangChain",
        "Document Q&A System",
        "AI Interview Assistant"
    ]
}

ADZUNA_COUNTRY_MAP = {"default": "in"}  # Adzuna country code, change if targeting a different market


def _rule_based_ats(resume_text: str, role: str) -> dict:
    skills = [s.lower() for s in ROLE_SKILLS.get(role.lower(), [])]
    lower_resume = resume_text.lower()
    score, found = 60, []
    for skill in skills:
        if skill in lower_resume:
            score += 8
            found.append(skill)
    return {
        "ats_score": min(score, 100),
        "ats_feedback": "Rule-based estimate from keyword matching against the target role.",
        "extracted_skills": found
    }


def _rule_based_gap(resume_text: str, role: str) -> list:
    required = ROLE_SKILLS.get(role.lower(), [])
    lower_resume = resume_text.lower()
    return [s for s in required if s.lower() not in lower_resume]


def _rule_based_projects(role: str) -> list:
    return ROLE_PROJECTS.get(role.lower(), [])


def _fallback_roadmap(role: str, missing_skills: list) -> list:
    focus_skill = missing_skills[0] if missing_skills else role
    return [
        {"week": "Week 1", "focus": "Foundations",
         "tasks": f"Strengthen fundamentals relevant to {role} and clean up your GitHub profile."},
        {"week": "Week 2", "focus": "Build",
         "tasks": f"Build and deploy a project that demonstrates {focus_skill}."},
        {"week": "Week 3", "focus": "Practice",
         "tasks": "Practice data structures, algorithms, and system design basics daily."},
        {"week": "Week 4", "focus": "Apply",
         "tasks": "Run mock interviews, refine your resume, and start applying."}
    ]


# -----------------------------
# 4. GITHUB EVALUATION - real profile + real repo data, not just a repo-count formula
# -----------------------------

def _github_check_sync(username: str) -> dict:
    empty = {
        "username": username, "found": False, "github_score": 0,
        "public_repositories": 0, "followers": 0, "top_languages": [],
        "active_recently": False, "notable_repos": [],
        "recommendations": [f"GitHub user '{username}' not found."]
    }
    try:
        profile_resp = requests.get(f"https://api.github.com/users/{username}", timeout=5)
        profile = profile_resp.json()
        if "login" not in profile:
            return empty

        repos_resp = requests.get(
            f"https://api.github.com/users/{username}/repos",
            params={"sort": "updated", "per_page": 15},
            timeout=5
        )
        repos = repos_resp.json() if repos_resp.status_code == 200 else []
        if not isinstance(repos, list):
            repos = []

        # Real signal: languages actually used across repos
        lang_counts = {}
        notable_repos = []
        most_recent_push = None

        for r in repos:
            lang = r.get("language")
            if lang:
                lang_counts[lang] = lang_counts.get(lang, 0) + 1

            pushed_at = r.get("pushed_at")
            if pushed_at:
                pushed_dt = datetime.strptime(pushed_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if most_recent_push is None or pushed_dt > most_recent_push:
                    most_recent_push = pushed_dt

            if not r.get("fork") and (r.get("stargazers_count", 0) > 0 or r.get("description")):
                notable_repos.append({
                    "name": r.get("name"),
                    "language": lang,
                    "stars": r.get("stargazers_count", 0),
                    "description": (r.get("description") or "")[:120]
                })

        notable_repos = sorted(notable_repos, key=lambda x: x["stars"], reverse=True)[:5]
        top_languages = sorted(lang_counts, key=lang_counts.get, reverse=True)[:5]

        active_recently = False
        if most_recent_push:
            active_recently = (datetime.now(timezone.utc) - most_recent_push).days <= 180

        repos_count = profile.get("public_repos", 0)
        followers = profile.get("followers", 0)

        # Score blends real activity signals, not just raw repo count
        score = 0
        score += min(40, repos_count * 3)
        score += min(20, len(top_languages) * 5)
        score += 20 if active_recently else 0
        score += min(20, followers)
        score = min(100, score)

        recs = []
        if repos_count < 5:
            recs.append("Push more public repositories - aim for at least 5-6 solid projects.")
        if not active_recently:
            recs.append("No commits in the last 6 months - recent activity signals to recruiters you're actively coding.")
        if not any(r.get("description") for r in notable_repos):
            recs.append("Add clear README descriptions to your top repositories.")
        if len(top_languages) <= 1:
            recs.append("Diversify the tech stack shown across your repos to match the role you're targeting.")
        if not recs:
            recs.append("Solid GitHub activity - keep pinning your best 3-4 projects on your profile.")

        return {
            "username": username,
            "found": True,
            "github_score": score,
            "public_repositories": repos_count,
            "followers": followers,
            "top_languages": top_languages,
            "active_recently": active_recently,
            "notable_repos": notable_repos,
            "recommendations": recs
        }

    except Exception:
        return empty


# -----------------------------
# 5. JOB SEARCH - Adzuna (real, structured listings) with DuckDuckGo fallback
# -----------------------------

def _search_jobs_adzuna(role: str) -> list:
    if not (ADZUNA_APP_ID and ADZUNA_APP_KEY):
        raise RuntimeError("Adzuna not configured")

    country = ADZUNA_COUNTRY_MAP["default"]
    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/1"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": role,
        "results_per_page": 6,
        "content-type": "application/json"
    }
    resp = requests.get(url, params=params, timeout=6)
    resp.raise_for_status()
    data = resp.json()

    jobs = []
    for r in data.get("results", []):
        company = (r.get("company") or {}).get("display_name", "")
        location = (r.get("location") or {}).get("display_name", "")
        jobs.append({
            "title": r.get("title", "N/A"),
            "company": company,
            "location": location,
            "url": r.get("redirect_url", "")
        })
    if not jobs:
        raise RuntimeError("No Adzuna results")
    return jobs


def _search_jobs_ddg(role: str) -> list:
    with DDGS(timeout=5) as ddgs:
        results = list(ddgs.text(f"{role} jobs India", max_results=6))
    return [{"title": r.get("title", "N/A"), "company": "", "location": "", "url": r.get("href", "")}
            for r in results]


def _search_jobs_sync(role: str) -> list:
    fallback = [
        {"title": "TCS Java Developer", "company": "TCS", "location": "India", "url": ""},
        {"title": "Infosys Software Engineer", "company": "Infosys", "location": "India", "url": ""},
        {"title": "Accenture Full Stack Developer", "company": "Accenture", "location": "India", "url": ""}
    ]
    try:
        return _search_jobs_adzuna(role)
    except Exception:
        pass
    try:
        jobs = _search_jobs_ddg(role)
        return jobs if jobs else fallback
    except Exception:
        return fallback


# -----------------------------
# 6. LANGCHAIN TOOLS (agent-facing wrappers around the above)
# -----------------------------

@tool
def analyze_resume(resume_text: str, role: str) -> str:
    """Analyze a resume and provide ATS score and extracted skills."""
    return json.dumps(_rule_based_ats(resume_text, role), indent=2)


@tool
def skill_gap(role: str, resume_text: str) -> str:
    """Identify missing skills for the target role."""
    return json.dumps({"target_role": role, "missing_skills": _rule_based_gap(resume_text, role)}, indent=2)


@tool
def recommend_projects(role: str) -> str:
    """Recommend placement-ready projects for the target role."""
    return json.dumps({"role": role, "recommended_projects": _rule_based_projects(role)}, indent=2)


@tool
def github_check(username: str) -> str:
    """Analyze a GitHub profile using the GitHub public API - real repo/language/activity data."""
    return json.dumps(_github_check_sync(username), indent=2)


@tool
def search_jobs(role: str) -> str:
    """Search current, real job openings for a target role via Adzuna (falls back to web search)."""
    return json.dumps(_search_jobs_sync(role), indent=2)


tools = [analyze_resume, skill_gap, search_jobs, github_check, recommend_projects]


# -----------------------------
# 7. GEMINI-POWERED PERSONALIZED ANALYSIS
# -----------------------------

def _extract_json(text: str) -> dict:
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def _llm_analyze_sync(resume_text: str, role: str, github_stats: dict) -> dict:
    if llm is None:
        raise RuntimeError("LLM not configured")

    truncated_resume = resume_text[:6000]

    github_context = {
        "found": github_stats.get("found", False),
        "public_repos": github_stats.get("public_repositories", 0),
        "followers": github_stats.get("followers", 0),
        "top_languages": github_stats.get("top_languages", []),
        "active_recently": github_stats.get("active_recently", False),
        "notable_repos": github_stats.get("notable_repos", [])
    }

    prompt = f"""You are an expert technical placement coach and ATS (Applicant Tracking System) resume screener.

Target role: {role}

Resume text (extracted from an uploaded PDF):
\"\"\"{truncated_resume}\"\"\"

Candidate's REAL GitHub data (from the GitHub API - use this to cross-check claims in the resume and to make your GitHub recommendations specific): {json.dumps(github_context)}

Analyze the resume against the target role and cross-reference it against the candidate's actual GitHub activity. Return STRICT JSON only, no markdown fences, no commentary outside the JSON. Use exactly these keys:

{{
  "ats_score": <integer 0-100, based on how well THIS resume matches the target role - keyword relevance, quantified achievements, clarity, structure>,
  "ats_feedback": "<one or two sentences explaining the score, specific to this resume>",
  "missing_skills": [<3 to 6 specific, concrete skills or technologies missing from THIS resume for this role - cross-check against what's actually in their GitHub repos too>],
  "strengths": [<2 to 3 specific strengths actually present in THIS resume or their GitHub activity>],
  "recommended_projects": [<3 specific project ideas tailored to the missing skills you identified, each a short title followed by a brief description - do not repeat projects they already have on GitHub>],
  "github_recommendations": [<3 to 4 specific recommendations that reference the actual GitHub stats given - repo count, languages, recency, notable repos>],
  "roadmap": [
    {{"week": "Week 1", "focus": "<short title>", "tasks": "<1-2 sentence plan tailored to this candidate's gaps>"}},
    {{"week": "Week 2", "focus": "<short title>", "tasks": "<...>"}},
    {{"week": "Week 3", "focus": "<short title>", "tasks": "<...>"}},
    {{"week": "Week 4", "focus": "<short title>", "tasks": "<...>"}}
  ]
}}

Be specific to this resume, this role, and this candidate's actual GitHub data. Avoid generic filler advice."""

    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return _extract_json(content)


# -----------------------------
# 8. SAFE ASYNC WRAPPERS
# -----------------------------

async def _safe_github(username: str) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_github_check_sync, username), timeout=6)
    except Exception as e:
        print(f"[github] failed/timed out: {e}", flush=True)
        return {"username": username, "found": False, "github_score": 0, "public_repositories": 0,
                "followers": 0, "top_languages": [], "active_recently": False, "notable_repos": [],
                "recommendations": ["GitHub check timed out."]}


async def _safe_jobs(role: str) -> list:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_jobs_sync, role), timeout=8)
    except Exception as e:
        print(f"[jobs] failed/timed out: {e}", flush=True)
        return [{"title": "Job search timed out - try again", "company": "", "location": "", "url": ""}]


async def _safe_llm(resume_text: str, role: str, github_stats: dict):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_llm_analyze_sync, resume_text, role, github_stats),
            timeout=15
        )
    except Exception as e:
        print(f"[llm] failed/timed out: {type(e).__name__}: {e}", flush=True)
        return None


# -----------------------------
# 9. /analyze ENDPOINT
# -----------------------------

@app.post("/analyze")
async def analyze(
    role: str = Form(...),
    github: str = Form(...),
    resume: UploadFile = File(...)
):
    try:
        print(f"[analyze] START role={role} github={github}", flush=True)

        pdf_bytes = await resume.read()
        reader = PdfReader(BytesIO(pdf_bytes))
        resume_text = ""
        for page in reader.pages:
            resume_text += page.extract_text() or ""

        print(f"[analyze] PDF parsed, {len(resume_text)} chars", flush=True)

        if not resume_text.strip():
            return JSONResponse(
                status_code=400,
                content={"success": False, "error": "Couldn't extract text from that PDF - it may be a scanned image."}
            )

        rule_ats = _rule_based_ats(resume_text, role)
        rule_gap = _rule_based_gap(resume_text, role)
        rule_projects = _rule_based_projects(role)

        print("[analyze] calling GitHub API...", flush=True)
        github_result = await _safe_github(github)
        print(f"[analyze] GitHub done: score={github_result.get('github_score')} langs={github_result.get('top_languages')}", flush=True)

        print(f"[analyze] LLM configured: {llm is not None}. Starting LLM + job search in parallel...", flush=True)
        llm_result, jobs_result = await asyncio.gather(
            _safe_llm(resume_text, role, github_result),
            _safe_jobs(role)
        )
        print(f"[analyze] LLM result present: {llm_result is not None}. Jobs found: {len(jobs_result)}", flush=True)

        if llm_result:
            ats_score = int(llm_result.get("ats_score", rule_ats["ats_score"]))
            ats_feedback = llm_result.get("ats_feedback", "")
            missing_skills = llm_result.get("missing_skills") or rule_gap
            recommended_projects = llm_result.get("recommended_projects") or rule_projects
            roadmap = llm_result.get("roadmap") or _fallback_roadmap(role, missing_skills)
            github_recommendations = llm_result.get("github_recommendations") or github_result.get("recommendations", [])
        else:
            ats_score = rule_ats["ats_score"]
            ats_feedback = rule_ats["ats_feedback"]
            missing_skills = rule_gap
            recommended_projects = rule_projects
            roadmap = _fallback_roadmap(role, missing_skills)
            github_recommendations = github_result.get("recommendations", [])

        github_score = github_result.get("github_score", 0)
        placement_readiness = round(ats_score * 0.6 + github_score * 0.4)

        return {
            "success": True,
            "ats_score": ats_score,
            "ats_feedback": ats_feedback,
            "placement_readiness": placement_readiness,
            "github_score": github_score,
            "github_public_repos": github_result.get("public_repositories", 0),
            "github_followers": github_result.get("followers", 0),
            "github_top_languages": github_result.get("top_languages", []),
            "missing_skills": missing_skills,
            "github_recommendations": github_recommendations,
            "projects": recommended_projects,
            "roadmap": roadmap,
            "jobs": jobs_result
        }

    except Exception as e:
        print(f"[analyze] ERROR: {type(e).__name__}: {e}", flush=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# -----------------------------
# 10. AGENT / LANGSERVE (optional playground, not used by the frontend)
# -----------------------------

agent = None
formatted_agent_chain = None

if llm is not None:
    try:
        agent = create_agent(
            model=llm,
            tools=tools,
            system_prompt=(
                "You are a Placement-Ready AI Career Agent. "
                "Help students prepare for placements by analyzing resumes, "
                "identifying skill gaps, evaluating GitHub profiles, "
                "recommending projects, and suggesting job opportunities."
            )
        )

        class AgentInput(BaseModel):
            input: str = Field(description="Career-related query for the agent")

        def format_for_agent(x):
            user_input = x["input"] if isinstance(x, dict) else x.input
            return {"messages": [("user", user_input)]}

        def extract_text_response(agent_output: dict) -> str:
            if not isinstance(agent_output, dict):
                return str(agent_output)
            messages = agent_output.get("messages")
            if messages is None:
                for value in agent_output.values():
                    if isinstance(value, dict) and "messages" in value:
                        messages = value["messages"]
                        break
            if messages:
                last = messages[-1]
                return getattr(last, "content", str(last))
            return str(agent_output)

        formatted_agent_chain = (
            RunnableLambda(format_for_agent)
            | agent
            | RunnableLambda(extract_text_response)
        ).with_types(input_type=AgentInput, output_type=str)

        add_routes(app, formatted_agent_chain, path="/career-agent", playground_type="default")
    except Exception:
        pass

# -----------------------------
# 11. MAIN
# -----------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
