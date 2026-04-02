import requests
import os
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from webdriver_manager.chrome import ChromeDriverManager
import time

API_KEY = "e4780af5dbb0851c8c9fade24f5f822e"
BASE_URL = "https://api.elsevier.com/content/search/scopus"


import requests

def search_scopus(start, query, field_type=0, count=20, year_from=None, year_to=None):
    """
    field_type:
        0 -> TITLE
        1 -> ABSTRACT

    year_from, year_to:
        Publication year interval
    """

    # 🔹 Field selector
    if field_type == 0:
        query_part = f"TITLE({query})"
    elif field_type == 1:
        query_part = f"ABS({query})"
    else:
        query_part = query

    # 🔹 Year filter (Scopus-compatible, same as working URL)
    year_filter = ""
    if year_from and year_to:
        year_filter = f" AND PUBYEAR > {year_from - 1} AND PUBYEAR < {year_to + 1}"
    elif year_from:
        year_filter = f" AND PUBYEAR > {year_from - 1}"
    elif year_to:
        year_filter = f" AND PUBYEAR < {year_to + 1}"

    final_query = query_part + year_filter

    params = {
        "start": start,
        "count": count,
        "query": final_query
    }

    headers = {
        "X-ELS-APIKey": API_KEY,
        "Accept": "application/json"
    }

    try:
        response = requests.get(BASE_URL, params=params, headers=headers, timeout=15)
        response.raise_for_status()
    except Exception:
        return {
            "totalResults": 0,
            "dois": [],
            "entries": []
        }

    data = response.json()

    results = data.get("search-results", {})
    entries = results.get("entry", [])

    dois = []
    articles = []

    for entry in entries:
        doi = entry.get("prism:doi")
        title = entry.get("dc:title")
        abstract = entry.get("dc:description")
        cover_date = entry.get("prism:coverDate")  # ✅ added

        if doi:
            dois.append(doi)

        articles.append({
            "title": title,
            "doi": doi,
            "abstract": abstract,
            "coverDate": cover_date
        })

    return {
        "totalResults": int(results.get("opensearch:totalResults", 0)),
        "dois": dois,
        "entries": articles
    }



def get_paper_by_doi(doi: str) -> dict | None:
    """
    Fetch and extract paper metadata from Crossref using a DOI
    """
    url = f"https://api.crossref.org/works/{doi}"
    
    try:
        response = requests.get(url, timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        return None

    data = response.json()
    item = data.get("message")
    if not item:
        return None

    # Basic fields
    title = item.get("title", ["No title"])[0]
    doi = item.get("DOI", "N/A")
    year = (
        item.get("issued", {})
            .get("date-parts", [[None]])[0][0]
        or "Unknown"
    )
    journal = item.get("container-title", ["Unknown Journal"])[0]
    volume = item.get("volume", "N/A")
    paper_type = item.get("type", "Unknown")
    publisher = item.get("publisher", "Unknown Publisher")

    # Authors
    authors_list = []
    for author in item.get("author", []):
        given = author.get("given", "")
        family = author.get("family", "")
        full_name = f"{given} {family}".strip()
        if full_name:
            authors_list.append(full_name)

    authors = ", ".join(authors_list)

    # PDF link (prefer application/pdf, fallback to unspecified)
    pdf_link = None
    for link in item.get("link", []):
        if link.get("content-type") == "application/pdf":
            pdf_link = link.get("URL")
            break

    if not pdf_link:
        for link in item.get("link", []):
            if link.get("content-type") == "unspecified":
                pdf_link = link.get("URL")
                break

    return {
        "title": title,
        "doi": doi,
        "authors": authors,
        "year": year,
        "journal": journal,
        "volume": volume,
        "type": paper_type,
        "publisher": publisher,
        "pdf_link": pdf_link
    }


def download_pdf_from_scihub(doi: str) -> str | None:
    
    base_url = "https://sci-hub.se"
    scihub_url = f"{base_url}/{doi}"

    headers = {
        "User-Agent": "PostmanRuntime/7.49.0",
        "Accept": "text/html"
    }

    # Step 1: Fetch Sci-Hub page
    try:
        response = requests.get(
            scihub_url,
            headers=headers,
            timeout=15,
            allow_redirects=True
        )
        response.raise_for_status()
    except requests.RequestException:
        return None

    html = response.text
    if not html:
        return None

    # Step 2: Parse HTML and find PDF link
    soup = BeautifulSoup(html, "html.parser")

    download_div = soup.find("div", class_="download")
    if not download_div:
        return None

    link_tag = download_div.find("a", href=True)
    if not link_tag:
        return None

    pdf_url = link_tag["href"]

    # Step 3: Fix relative URLs
    if pdf_url.startswith("//"):
        pdf_url = "https:" + pdf_url
    elif pdf_url.startswith("/"):
        pdf_url = base_url + pdf_url

    # Create papers directory
    save_dir = "papers"
    os.makedirs(save_dir, exist_ok=True)
    filename = doi.replace("/", "_") + ".pdf"
    file_path = os.path.join(save_dir, filename)

    # Step 6: Download PDF
    try:
        with requests.get(pdf_url, headers=headers, stream=True, timeout=20) as r:
            r.raise_for_status()
            with open(file_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
    except requests.RequestException:
        return None

    return filename

def download_pdf_from_doi(doi):
    # 1️⃣ Prepare output directory
    save_dir = "papers"
    os.makedirs(save_dir, exist_ok=True)

    filename = doi.replace("/", "_") + ".pdf"
    output_file = os.path.join(save_dir, filename)

    doi_url = f"https://doi.org/{doi}"

    HEADERS = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/pdf,application/xhtml+xml"
    }

    # 2️⃣ Resolve DOI (redirects to publisher)
    response = requests.get(
        doi_url,
        headers=HEADERS,
        allow_redirects=True,
        timeout=15
    )
    response.raise_for_status()

    base_url = response.url
    soup = BeautifulSoup(response.text, "html.parser")

    pdf_links = []

    # 3️⃣ Look for direct PDF URLs
    for link in soup.select("a[href]"):
        href = link["href"].strip()

        # Skip javascript / empty links
        if href.startswith(("javascript:", "#")):
            continue

        # Strong PDF detection
        if href.lower().endswith(".pdf") or "/pdf" in href.lower():
            pdf_links.append(urljoin(base_url, href))

    # 4️⃣ Fallback: search by link text
    if not pdf_links:
        keywords = ["pdf", "download", "view pdf"]
        for link in soup.select("a[href]"):
            text = link.get_text(strip=True).lower()
            if any(k in text for k in keywords):
                pdf_links.append(urljoin(base_url, link["href"]))

    # 5️⃣ No PDF found
    if not pdf_links:
        print("❌ No PDF link found (paywall, JS-rendered, or login required)")
        return False

    pdf_url = pdf_links[0]
    print(f"📥 PDF candidate found: {pdf_url}")

    # 6️⃣ Download PDF
    pdf_response = requests.get(
        pdf_url,
        headers=HEADERS,
        stream=True,
        timeout=20
    )
    pdf_response.raise_for_status()

    # 7️⃣ Validate content-type
    content_type = pdf_response.headers.get("Content-Type", "").lower()
    if "pdf" not in content_type:
        print("⚠️ Link does not return a real PDF")
        return False

    with open(output_file, "wb") as f:
        for chunk in pdf_response.iter_content(chunk_size=8192):
            if chunk:
                f.write(chunk)

    print(f"✅ PDF saved as: {output_file}")
    return True

# conmpte eressources imist 
def login_and_save_cookies(
    email="email",
    password="password",
    login_url="https://login.eressources.imist.ma/login"
):
    session = requests.Session()
    cookie_file="cookie.txt"
    payload = {
        "user": email,
        "pass": password
    }

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": login_url
    }

    response = session.post(login_url, data=payload, headers=headers)

    if response.status_code != 200:
        raise Exception(f"Login failed: {response.status_code}")

    print("✅ Login request sent successfully")

    # Convert cookies to header format
    cookies_dict = session.cookies.get_dict()
    cookie_string = "; ".join([f"{k}={v}" for k, v in cookies_dict.items()])

    # Save cookies
    with open(cookie_file, "w", encoding="utf-8") as f:
        f.write(cookie_string)

    print(f"🍪 Cookies saved to {cookie_file}")
    return cookie_string

def open_sites_with_cookies_selenium(
    start_url="https://link-springer-com.eressources.imist.ma",
    extra_urls=None,
    cookie_file="cookie.txt"
):
    if extra_urls is None:
        extra_urls = [
            "https://www-tandfonline-com.eressources.imist.ma"
        ]

    # 🔹 Load cookies from file
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookie_string = f.read().strip()

    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--disable-blink-features=AutomationControlled")

    driver = webdriver.Chrome(
        service=Service(ChromeDriverManager().install()),
        options=options
    )

    # 1️⃣ Open base domain (required before adding cookies)
    driver.get(start_url)
    time.sleep(2)

    # 2️⃣ Inject cookies
    for item in cookie_string.split(";"):
        if "=" not in item:
            continue

        name, value = item.strip().split("=", 1)
        driver.add_cookie({
            "name": name,
            "value": value,
            "domain": ".eressources.imist.ma",
            "path": "/"
        })

    # 3️⃣ Refresh to activate session
    driver.refresh()
    time.sleep(2)

    # 4️⃣ Open additional protected sites
    for url in extra_urls:
        driver.get(url)
        time.sleep(2)

    print("✅ Cookies applied successfully")
    return driver


def download_pdf_by_publisher(doi, pub, cookie_file="cookie.txt"):
    # Create papers directory
    save_dir = "papers"
    os.makedirs(save_dir, exist_ok=True)
    filename = doi.replace("/", "_") + ".pdf"
    file_path = os.path.join(save_dir, filename)
    if pub == "tandfonline":
        pdf_url = f"https://www-tandfonline-com.eressources.imist.ma/doi/pdf/{doi}?download=true"

    elif pub == "springer":
        pdf_url = f"https://link-springer-com.eressources.imist.ma/content/pdf/{doi}.pdf"

    else:
        raise ValueError("Unsupported publisher")

    # Read cookies
    with open(cookie_file, "r", encoding="utf-8") as f:
        cookie_string = f.read().strip()

    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": cookie_string,
        "Accept": "application/pdf"
    }

    # Download PDF
    response = requests.get(pdf_url, headers=headers, stream=True, timeout=30)

    # Validate PDF
    if (
        response.status_code == 200 and
        response.headers.get("Content-Type", "").lower().startswith("application/pdf")
    ):
        with open(file_path, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
        return True

    return False

def download_pdf(doi, pub, year):
    try:
        

        # 2️⃣ Known publishers
        if pub in ["springer", "tandfonline"]:
            try:
                print(f"🔎 Trying publisher method ({pub})...")
                if download_pdf_by_publisher(doi, pub):
                    return True
            except Exception as e:
                print(f"⚠️ Publisher download failed: {e}")

            # 3️⃣ Login + cookies fallback
            try:
                print("🔐 Trying login + cookies...")
                login_and_save_cookies()
                open_sites_with_cookies_selenium()
                if download_pdf_by_publisher(doi, pub):
                    return True
            except Exception as e:
                print(f"⚠️ Cookie-based download failed: {e}")
        # 1️⃣ Old papers → Sci-Hub first
        if year <= 2022:
            try:
                print("🔎 Trying Sci-Hub...")
                if download_pdf_from_scihub(doi):
                    return True
            except Exception as e:
                print(f"⚠️ Sci-Hub failed: {e}")
        # 4️⃣ Generic DOI resolver
        try:
            print("🌐 Trying generic DOI resolver...")
            if download_pdf_from_doi(doi):
                return True
        except Exception as e:
            print(f"⚠️ DOI resolver failed: {e}")

    except Exception as e:
        # 🔥 Absolute last-resort catch (never crash)
        print(f"❌ Unexpected error for DOI {doi}: {e}")

    print(f"❌ All methods failed for DOI: {doi}")
    return False


