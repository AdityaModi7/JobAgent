"""Job Parser — Extracts structured job data from URLs or raw text."""

import sys
import re
import json
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
    "remote": true,
    "salary_range": "$X - $Y" or null,
    "description": "Brief 2-3 sentence summary of the role",
    "responsibilities": ["responsibility 1", "responsibility 2"],
    "required_skills": ["skill 1", "skill 2"],
    "preferred_skills": ["nice-to-have 1", "nice-to-have 2"],
    "required_experience_years": null,
    "education_requirement": "Bachelor's in CS" or null,
    "company_info": "Brief company description if available" or null
}

IMPORTANT: Every field must have a value. For title, company, and description use your best guess from the text — never return null for these. Be thorough in extracting skills — include both explicit requirements and those implied by the responsibilities."""


# ── URL Fetching Strategies ───────────────────────────────────────────────

def fetch_ashby_api(url: str) -> str | None:
    """Try to fetch job data from Ashby's API (used by many startups)."""
    # Extract job ID from Ashby URLs
    # Format: jobs.ashbyhq.com/COMPANY/JOB_ID
    match = re.search(r"jobs\.ashbyhq\.com/([^/]+)/([a-f0-9-]+)", url)
    if not match:
        return None

    company_slug = match.group(1)
    job_id = match.group(2)

    # Ashby has a public API for job postings
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}/job/{job_id}"
    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            info = data.get("info", data)
            title = info.get("title", "")
            location = info.get("locationName", info.get("location", ""))
            description_html = info.get("descriptionHtml", info.get("description", ""))
            
            # Strip HTML from description
            if description_html:
                soup = BeautifulSoup(description_html, "html.parser")
                description_text = soup.get_text(separator="\n", strip=True)
            else:
                description_text = ""

            company_name = info.get("organizationName", company_slug)
            
            return f"Title: {title}\nCompany: {company_name}\nLocation: {location}\n\n{description_text}"
    except Exception:
        pass
    return None


def fetch_greenhouse_api(url: str) -> str | None:
    """Try to fetch job data from Greenhouse's API."""
    match = re.search(r"boards\.greenhouse\.io/(\w+)/jobs/(\d+)", url)
    if not match:
        match = re.search(r"greenhouse\.io/.*?/jobs/(\d+)", url)
    if not match:
        return None

    job_id = match.group(2) if match.lastindex >= 2 else match.group(1)
    api_url = f"https://boards-api.greenhouse.io/v1/boards/jobs/{job_id}"
    
    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("title", "")
            location = data.get("location", {}).get("name", "")
            content_html = data.get("content", "")
            
            if content_html:
                soup = BeautifulSoup(content_html, "html.parser")
                content_text = soup.get_text(separator="\n", strip=True)
            else:
                content_text = ""
            
            return f"Title: {title}\nLocation: {location}\n\n{content_text}"
    except Exception:
        pass
    return None


def fetch_lever_api(url: str) -> str | None:
    """Try to fetch job data from Lever's API."""
    match = re.search(r"jobs\.lever\.co/([^/]+)/([a-f0-9-]+)", url)
    if not match:
        return None

    company = match.group(1)
    job_id = match.group(2)
    api_url = f"https://api.lever.co/v0/postings/{company}/{job_id}"

    try:
        resp = requests.get(api_url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            title = data.get("text", "")
            location = data.get("categories", {}).get("location", "")
            description = data.get("descriptionPlain", "")
            lists = data.get("lists", [])
            
            extra = ""
            for lst in lists:
                extra += f"\n\n{lst.get('text', '')}:\n"
                for item in lst.get("content", []):
                    if isinstance(item, str):
                        extra += f"- {item}\n"
                    elif isinstance(item, dict):
                        extra += f"- {BeautifulSoup(item.get('text', ''), 'html.parser').get_text()}\n"
            
            return f"Title: {title}\nLocation: {location}\n\n{description}{extra}"
    except Exception:
        pass
    return None


def fetch_html_generic(url: str) -> str:
    """Generic HTML fetcher as a fallback."""
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

    # Try common job posting containers
    selectors = [
        "article",
        '[class*="job-description"]',
        '[class*="job-details"]',
        '[class*="posting"]',
        '[class*="content"]',
        '[id*="job"]',
        "main",
    ]

    for selector in selectors:
        content = soup.select_one(selector)
        if content and len(content.get_text(strip=True)) > 200:
            return content.get_text(separator="\n", strip=True)

    body = soup.find("body")
    if body:
        text = body.get_text(separator="\n", strip=True)
        if len(text) > 200:
            return text

    return soup.get_text(separator="\n", strip=True)


def fetch_job_url(url: str) -> str:
    """Fetch job posting text using the best strategy for the URL."""
    
    # Try ATS-specific APIs first (they return clean data)
    strategies = [
        ("Ashby API", fetch_ashby_api),
        ("Greenhouse API", fetch_greenhouse_api),
        ("Lever API", fetch_lever_api),
    ]

    for name, strategy in strategies:
        result = strategy(url)
        if result and len(result.strip()) > 100:
            print(f"   ✅ Fetched via {name}")
            return result

    # Fall back to generic HTML scraping
    print(f"   ⚡ Using HTML scraper...")
    text = fetch_html_generic(url)
    
    if len(text.strip()) < 100:
        raise ValueError(
            f"Could not extract enough content from URL.\n"
            f"This site likely loads content with JavaScript.\n\n"
            f"Try instead:\n"
            f"  1. Copy the job description text from the page\n"
            f"  2. Save it to a file (e.g., job.txt)\n"
            f"  3. Run: python main.py apply --job-file job.txt"
        )
    
    return text


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

    # Ensure required fields have values
    data["title"] = data.get("title") or "Unknown Title"
    data["company"] = data.get("company") or "Unknown Company"
    data["description"] = data.get("description") or "No description available"
    
    data["raw_text"] = raw_text[:5000]
    if url:
        data["application_url"] = url

    return JobPosting(**data)