import os
import re
import json
import asyncio
import requests
import uvicorn
from pathlib import Path
from io import BytesIO

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
    return templates.TemplateResponse(
        request,
        "index.html",
        {}
    )
# Homepage


# -----------------------------
# 2. GEMINI MODEL (defined early so /analyze can use it)
# -----------------------------

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

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
# 3. RULE-BASED TOOLS (used as a fallback if Gemini is unavailable/slow)
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


@tool
def analyze_resume(resume_text: str, role: str) -> str:
    """Analyze a resume and provide ATS score, strengths, weaknesses, and extracted skills."""
    skills = [s.lower() for s in ROLE_SKILLS.get(role.lower(), [])]
    lower_resume = resume_text.lower()

    score = 60
    found = []
    for skill in skills:
        if skill in lower_resume:
            score += 8
            found.append(skill)
    score = min(score, 100)

    result = {
        "ats_score": score,
        "ats_feedback": "Rule-based estimate from keyword matching against the target role.",
        "extracted_skills": found,
        "strengths": ["Technical foundation", "Relevant programming knowledge"],
        "weaknesses": ["Add quantified achievements", "Improve project descriptions"]
    }
    return json.dumps(result, indent=2)


@tool
def skill_gap(role: str, resume_text: str) -> str:
    """Identify missing skills for the target role."""
    required = ROLE_SKILLS.get(role.lower(), [])
    lower_resume = resume_text.lower()
    missing = [s for s in required if s.lower() not in lower_resume]
    return json.dumps({"target_role": role, "missing_skills": missing}, indent=2)


@tool
def recommend_projects(role: str) -> str:
    """Recommend placement-ready projects for the target role."""
    return json.dumps(
        {"role": role, "recommended_projects": ROLE_PROJECTS.get(role.lower(), [])},
        indent=2
    )


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


# ---- Network-bound helpers: explicit timeouts, never allowed to hang the request ----

def _github_check_sync(username: str) -> dict:
    try:
        url = f"https://api.github.com/users/{username}"
        resp = requests.get(url, timeout=5)
        data = resp.json()

        if "login" not in data:
            return {
                "username": username,
                "github_score": 0,
                "public_repositories": 0,
                "followers": 0,
                "recommendations": [f"GitHub user '{username}' not found."]
            }

        repos = data.get("public_repos", 0)
        followers = data.get("followers", 0)
        score = min(100, 50 + repos * 2)

        return {
            "username": username,
            "public_repositories": repos,
            "followers": followers,
            "github_score": score,
            "recommendations": [
                "Improve repository README files",
                "Pin your best projects",
                "Maintain consistent commits"
            ]
        }
    except Exception:
        return {
            "username": username,
            "github_score": 0,
            "public_repositories": 0,
            "followers": 0,
            "recommendations": ["Unable to reach GitHub right now. Try again shortly."]
        }


def _search_jobs_sync(role: str) -> list:
    fallback = [
        {"title": "TCS Java Developer", "url": ""},
        {"title": "Infosys Software Engineer", "url": ""},
        {"title": "Accenture Full Stack Developer", "url": ""}
    ]
    try:
        with DDGS(timeout=5) as ddgs:
            results = list(ddgs.text(f"India {role} jobs", max_results=5))
        jobs = [{"title": r.get("title", "N/A"), "url": r.get("href", "")} for r in results]
        return jobs if jobs else fallback
    except Exception:
        return fallback


@tool
def github_check(username: str) -> str:
    """Analyze a GitHub profile using the GitHub public API."""
    return json.dumps(_github_check_sync(username), indent=2)


@tool
def search_jobs(role: str) -> str:
    """Search current job openings for a target role."""
    return json.dumps(_search_jobs_sync(role), indent=2)


tools = [analyze_resume, skill_gap, search_jobs, github_check, recommend_projects]


# ---- Gemini-powered personalized analysis ----

def _extract_json(text: str) -> dict:
    """Strip markdown code fences if present and parse JSON."""
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def _llm_analyze_sync(resume_text: str, role: str, github_stats: dict) -> dict:
    if llm is None:
        raise RuntimeError("LLM not configured")

    truncated_resume = resume_text[:6000]

    prompt = f"""You are an expert technical placement coach and ATS (Applicant Tracking System) resume screener.

Target role: {role}

Resume text (extracted from an uploaded PDF):
\"\"\"{truncated_resume}\"\"\"

Candidate's real GitHub stats: {json.dumps(github_stats)}

Analyze the resume against the target role. Return STRICT JSON only, no markdown fences, no commentary outside the JSON. Use exactly these keys:

{{
  "ats_score": <integer 0-100, based on how well THIS resume matches the target role - keyword relevance, quantified achievements, clarity, structure>,
  "ats_feedback": "<one or two sentences explaining the score, specific to this resume, not generic>",
  "missing_skills": [<3 to 6 specific, concrete skills or technologies missing from THIS resume for this role>],
  "strengths": [<2 to 3 specific strengths actually present in THIS resume>],
  "recommended_projects": [<3 specific project ideas tailored to the missing skills you identified, each a short title followed by a brief description>],
  "github_recommendations": [<3 to 4 specific recommendations that reference the actual GitHub stats given>],
  "roadmap": [
    {{"week": "Week 1", "focus": "<short title>", "tasks": "<1-2 sentence plan tailored to this candidate's gaps>"}},
    {{"week": "Week 2", "focus": "<short title>", "tasks": "<...>"}},
    {{"week": "Week 3", "focus": "<short title>", "tasks": "<...>"}},
    {{"week": "Week 4", "focus": "<short title>", "tasks": "<...>"}}
  ]
}}

Be specific to this resume and role. Avoid generic filler advice."""

    response = llm.invoke(prompt)
    content = response.content if hasattr(response, "content") else str(response)
    return _extract_json(content)


# -----------------------------
# 4. /analyze ENDPOINT
# -----------------------------

async def _safe_github(username: str) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_github_check_sync, username), timeout=8)
    except Exception:
        return {"username": username, "github_score": 0, "public_repositories": 0,
                "followers": 0, "recommendations": ["GitHub check timed out."]}


async def _safe_jobs(role: str) -> list:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_jobs_sync, role), timeout=8)
    except Exception:
        return [{"title": "Job search timed out - try again", "url": ""}]


async def _safe_llm(resume_text: str, role: str, github_stats: dict):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_llm_analyze_sync, resume_text, role, github_stats),
            timeout=20
        )
    except Exception:
        return None


@app.post("/analyze")
async def analyze(
    role: str = Form(...),
    github: str = Form(...),
    resume: UploadFile = File(...)
):
    try:
        pdf_bytes = await resume.read()
        reader = PdfReader(BytesIO(pdf_bytes))

        resume_text = ""
        for page in reader.pages:
            resume_text += page.extract_text() or ""

        if not resume_text.strip():
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "error": "Couldn't extract any text from that PDF. It may be a scanned image - "
                             "try a text-based PDF export of your resume."
                }
            )

        # Cheap, deterministic fallback results - computed up front so we always have something
        rule_ats = json.loads(analyze_resume.invoke({"resume_text": resume_text, "role": role}))
        rule_gap = json.loads(skill_gap.invoke({"role": role, "resume_text": resume_text}))
        rule_projects = json.loads(recommend_projects.invoke({"role": role}))

        # First get real GitHub stats (needed as context for the LLM), then run the LLM
        # call and job search in parallel.
        github_result = await _safe_github(github)
        llm_result, jobs_result = await asyncio.gather(
            _safe_llm(resume_text, role, github_result),
            _safe_jobs(role)
        )

        if llm_result:
            ats_score = int(llm_result.get("ats_score", rule_ats["ats_score"]))
            ats_feedback = llm_result.get("ats_feedback", "")
            missing_skills = llm_result.get("missing_skills") or rule_gap["missing_skills"]
            recommended_projects = llm_result.get("recommended_projects") or rule_projects["recommended_projects"]
            roadmap = llm_result.get("roadmap") or _fallback_roadmap(role, missing_skills)
            github_recommendations = llm_result.get("github_recommendations") or github_result.get("recommendations", [])
        else:
            ats_score = rule_ats["ats_score"]
            ats_feedback = rule_ats["ats_feedback"]
            missing_skills = rule_gap["missing_skills"]
            recommended_projects = rule_projects["recommended_projects"]
            roadmap = _fallback_roadmap(role, missing_skills)
            github_recommendations = github_result.get("recommendations", [])

        github_score = github_result.get("github_score", 0)
        # Explainable, deterministic combination of two real signals rather than a flat "+5"
        placement_readiness = round(ats_score * 0.6 + github_score * 0.4)

        return {
            "success": True,
            "ats_score": ats_score,
            "ats_feedback": ats_feedback,
            "placement_readiness": placement_readiness,
            "github_score": github_score,
            "github_public_repos": github_result.get("public_repositories", 0),
            "github_followers": github_result.get("followers", 0),
            "missing_skills": missing_skills,
            "github_recommendations": github_recommendations,
            "projects": recommended_projects,
            "roadmap": roadmap,
            "jobs": jobs_result
        }

    except Exception as e:
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


# -----------------------------
# 5. AGENT / LANGSERVE (optional playground route, not used by the frontend)
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
# 6. MAIN
# -----------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
