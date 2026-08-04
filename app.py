import os
import json
import requests
import uvicorn
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from langserve import add_routes
from langchain_core.tools import tool
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_agent
from langchain_core.runnables import RunnableLambda
from pydantic import BaseModel, Field
from duckduckgo_search import DDGS

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
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request,
        "index.html",
        {}
    )

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
def search_jobs(role: str) -> str:
    """Search current job openings for a target role."""

    try:
        with DDGS() as ddgs:
            results = list(
                ddgs.text(
                    f"India {role} jobs",
                    max_results=5
                )
            )

        jobs = []

        for r in results:
            jobs.append(
                {
                    "title": r.get("title", "N/A"),
                    "url": r.get("href", "")
                }
            )

        return json.dumps(jobs, indent=2)

    except Exception:
        return json.dumps(
            [
                {"title": "TCS Java Developer"},
                {"title": "Infosys Software Engineer"},
                {"title": "Accenture Full Stack Developer"}
            ],
            indent=2
        )

@tool
def github_check(username: str) -> str:
    """Analyze a GitHub profile using the GitHub public API."""

    try:
        url = f"https://api.github.com/users/{username}"
        data = requests.get(url).json()

        if "login" not in data:
            return f"GitHub user {username} not found."

        score = min(100, 50 + data.get("public_repos", 0) * 2)

        result = {
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

        return json.dumps(result, indent=2)

    except Exception:
        return "Unable to analyze GitHub profile."

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

tools = [
    analyze_resume,
    skill_gap,
    search_jobs,
    github_check,
    recommend_projects
]

# -----------------------------
# 3. MODEL & AGENT
# -----------------------------

GOOGLE_API_KEY = os.environ.get("GOOGLE_API_KEY")

llm = ChatGoogleGenerativeAI(
    model="gemma-4-31b-it",
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
# 4. INPUT SCHEMA
# -----------------------------

class AgentInput(BaseModel):
    input: str = Field(description="Career-related query for the agent")

# -----------------------------
# 5. CHAIN
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
# 6. LANGSERVE ROUTE
# -----------------------------

add_routes(
    app,
    formatted_agent_chain,
    path="/career-agent",
    playground_type="default"
)

# -----------------------------
# 7. MAIN
# -----------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
