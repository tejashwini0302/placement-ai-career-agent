import os
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
# 2. TOOLS
# -----------------------------

@tool
def analyze_resume(resume_text: str, role: str) -> str:
    """Analyze a resume and provide ATS score, strengths, weaknesses, and extracted skills."""

    role_skills = {
        "java developer": ["java", "spring", "mysql", "git"],
        "full stack developer": ["react", "node.js", "mongodb", "javascript"],
        "ai engineer": ["python", "machine learning", "tensorflow", "sql"]
    }

    skills = role_skills.get(role.lower(), [])

    score = 60
    found = []

    lower_resume = resume_text.lower()

    for skill in skills:
        if skill in lower_resume:
            score += 10
            found.append(skill)

    score = min(score, 100)

    result = {
        "ats_score": score,
        "extracted_skills": found,
        "strengths": [
            "Technical foundation",
            "Relevant programming knowledge"
        ],
        "weaknesses": [
            "Add quantified achievements",
            "Improve project descriptions"
        ]
    }

    return json.dumps(result, indent=2)


@tool
def skill_gap(role: str, resume_text: str) -> str:
    """Identify missing skills for the target role."""

    role_skills = {
        "java developer": ["Java", "Spring Boot", "REST API", "MySQL", "Git"],
        "full stack developer": ["React", "Node.js", "MongoDB", "Express", "Docker"],
        "ai engineer": ["Python", "Machine Learning", "TensorFlow", "SQL", "LangChain"]
    }

    required = role_skills.get(role.lower(), [])

    lower_resume = resume_text.lower()

    missing = [s for s in required if s.lower() not in lower_resume]

    return json.dumps(
        {
            "target_role": role,
            "missing_skills": missing
        },
        indent=2
    )


@tool
def recommend_projects(role: str) -> str:
    """Recommend placement-ready projects for the target role."""

    projects = {
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

    return json.dumps(
        {
            "role": role,
            "recommended_projects": projects.get(role.lower(), [])
        },
        indent=2
    )


# ---- Network-bound helpers (used by both the /analyze endpoint and the agent tools) ----
# These are plain, synchronous functions with EXPLICIT TIMEOUTS. The /analyze endpoint
# runs them in a thread pool via asyncio.to_thread + asyncio.wait_for so a slow/blocked
# GitHub API or DuckDuckGo call can never hang the whole request.

def _github_check_sync(username: str) -> dict:
    try:
        url = f"https://api.github.com/users/{username}"
        resp = requests.get(url, timeout=5)
        data = resp.json()

        if "login" not in data:
            return {
                "username": username,
                "github_score": 0,
                "recommendations": [f"GitHub user '{username}' not found."]
            }

        score = min(100, 50 + data.get("public_repos", 0) * 2)

        return {
            "username": username,
            "public_repositories": data.get("public_repos", 0),
            "followers": data.get("followers", 0),
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

        jobs = [
            {"title": r.get("title", "N/A"), "url": r.get("href", "")}
            for r in results
        ]

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


tools = [
    analyze_resume,
    skill_gap,
    search_jobs,
    github_check,
    recommend_projects
]


# -----------------------------
# 3. /analyze ENDPOINT
# -----------------------------

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

        # Fast, local, deterministic tools - safe to call directly
        ats_result = json.loads(analyze_resume.invoke({
            "resume_text": resume_text,
            "role": role
        }))

        gap_result = json.loads(skill_gap.invoke({
            "role": role,
            "resume_text": resume_text
        }))

        project_result = json.loads(recommend_projects.invoke({
            "role": role
        }))

        # Network-bound calls: run off the event loop with a hard timeout each,
        # so a slow GitHub API or blocked DuckDuckGo search can never hang the request.
        try:
            github_result = await asyncio.wait_for(
                asyncio.to_thread(_github_check_sync, github),
                timeout=8
            )
        except asyncio.TimeoutError:
            github_result = {
                "github_score": 0,
                "recommendations": ["GitHub check timed out."]
            }

        try:
            jobs_result = await asyncio.wait_for(
                asyncio.to_thread(_search_jobs_sync, role),
                timeout=8
            )
        except asyncio.TimeoutError:
            jobs_result = [{"title": "Job search timed out - try again", "url": ""}]

        return {
            "success": True,
            "ats_score": ats_result["ats_score"],
            "placement_readiness": min(100, ats_result["ats_score"] + 5),
            "github_score": github_result.get("github_score", 0),
            "missing_skills": gap_result["missing_skills"],
            "github_recommendations": github_result.get("recommendations", []),
            "projects": project_result["recommended_projects"],
            "jobs": jobs_result
        }

    except Exception as e:
        return JSONResponse(
            status_code=500,
            content={"success": False, "error": str(e)}
        )


# -----------------------------
# 4. MODEL & AGENT (used only by the optional /career-agent LangServe route)
# -----------------------------

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

llm = ChatGoogleGenerativeAI(
    model="gemini-2.0-flash",
    api_key=GOOGLE_API_KEY,
    temperature=0
)

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

# -----------------------------
# 5. INPUT SCHEMA
# -----------------------------

class AgentInput(BaseModel):
    input: str = Field(description="Career-related query for the agent")

# -----------------------------
# 6. CHAIN
# -----------------------------

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
).with_types(
    input_type=AgentInput,
    output_type=str
)

# -----------------------------
# 7. LANGSERVE ROUTE
# -----------------------------

add_routes(
    app,
    formatted_agent_chain,
    path="/career-agent",
    playground_type="default"
)

# -----------------------------
# 8. MAIN
# -----------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
