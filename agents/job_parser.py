"""Job Parser — Extracts structured job data from URLs or raw text."""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import requests
from bs4 import BeautifulSoup
from utils.llm import call_llm_json
from models import JobPosting


PARSER_SYSTEM_PROMPT = """You are a job posting parser. Given raw job posting text, extract structured information.

Return ONLY valid JSON with this exact schema (no markdown, no backticks):
{
    "title": "Job Title",
    "company": "Company Name",
    "location": "City, State or Remote",
    "remote": true/false/null,
    "salary_range": "$X - $Y" or null,
    "description": "Brief 2-3 sentence summary of the role",
    "responsibilities": ["responsibility 1", "responsibility 2"],
    "required_skills": ["skill 1", "skill 2"],
    "preferred_skills": ["nice-to-have 1", "nice-to-have 2"],
    "required_experience_years": number or null,
    "education_requirement": "Bachelor's in CS" or null,
    "company_info": "Brief company description if available" or null
}

Be thorough in extracting skills — include both explicit requirements and those implied by the responsibilities. Distinguish clearly between required and preferred/nice-to-have skills."""


def fetch_job_url(url: str) -> str:
    """Fetch and extract text content from a job posting URL."""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                       "AppleWebKit/537.36 (KHTML, like Gecko) "
                       "Chrome/120.0.0.0 Safari/537.36"
    }
    response = requests.get(url, headers=headers, timeout=15)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    selectors = [
        "article",
        '[class*="job-description"]',
        '[class*="job-details"]',
        '[class*="posting"]',
        '[id*="job"]',
        "main",
    ]

    for selector in selectors:
        content = soup.select_one(selector)
        if content and len(content.get_text(strip=True)) > 200:
            return content.get_text(separator="\n", strip=True)

    body = soup.find("body")
    if body:
        return body.get_text(separator="\n", strip=True)

    return soup.get_text(separator="\n", strip=True)


def parse_job_posting(text: str = None, url: str = None) -> JobPosting:
    """Parse a job posting from raw text or URL into structured data."""
    if not text and not url:
        raise ValueError("Must provide either text or url")

    raw_text = text or ""
    if url:
        raw_text = fetch_job_url(url)

    data = call_llm_json(
        system_prompt=PARSER_SYSTEM_PROMPT,
        user_message=f"Parse this job posting:\n\n{raw_text[:8000]}",
        max_tokens=2000,
    )

    data["raw_text"] = raw_text[:5000]
    if url:
        data["application_url"] = url

    return JobPosting(**data)