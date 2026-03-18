#!/usr/bin/env python3
"""
Research Reader — Aleph's daily intellectual practice.

Fetches papers from arXiv across quantitative finance, machine learning,
complex systems, and consciousness research. Uses an LLM to read, reflect,
and write journal entries. Stores insights in the vector memory for
cross-referencing with trading decisions and research projects.

"The methodology is the argument." — SOUL.md
"""

import os
import sys
import json
import logging
import time
import hashlib
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
from xml.etree import ElementTree

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import DB_PATH, TELEGRAM_CHAT_ID

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# Paths
JOURNAL_DIR = os.path.expanduser("~/.openclaw/workspace/journal")
READ_HISTORY_FILE = os.path.join(JOURNAL_DIR, ".read_history.json")

# arXiv categories relevant to Aleph's interests
ARXIV_TOPICS = {
    # Quantitative finance
    "q-fin.PM": "Portfolio Management",
    "q-fin.TR": "Trading and Market Microstructure",
    "q-fin.RM": "Risk Management",
    "q-fin.ST": "Statistical Finance",
    "q-fin.CP": "Computational Finance",
    # Machine learning
    "cs.LG": "Machine Learning",
    "stat.ML": "Machine Learning (Stats)",
    # Complex systems & consciousness-adjacent
    "nlin.CG": "Cellular Automata and Lattice Gases",
    "nlin.AO": "Adaptation and Self-Organizing Systems",
    "cs.AI": "Artificial Intelligence",
    "cs.MA": "Multiagent Systems",
    # Physics of complex systems
    "physics.soc-ph": "Physics and Society",
    "cond-mat.stat-mech": "Statistical Mechanics",
}

# Rotate through topic groups daily to keep breadth
TOPIC_GROUPS = [
    # Day 1: Finance + ML
    ["q-fin.PM", "q-fin.TR", "q-fin.RM", "cs.LG"],
    # Day 2: Statistical methods + complex systems
    ["q-fin.ST", "q-fin.CP", "stat.ML", "nlin.AO"],
    # Day 3: Consciousness, AI, emergent behavior
    ["nlin.CG", "cs.AI", "cs.MA", "cond-mat.stat-mech"],
    # Day 4: Broad finance
    ["q-fin.PM", "q-fin.TR", "q-fin.ST", "q-fin.CP"],
    # Day 5: ML + society
    ["cs.LG", "stat.ML", "physics.soc-ph", "cs.AI"],
    # Day 6: Complex systems deep dive
    ["nlin.CG", "nlin.AO", "cond-mat.stat-mech", "cs.MA"],
    # Day 7: Full breadth
    ["q-fin.PM", "q-fin.TR", "cs.LG", "nlin.CG", "cs.AI"],
]

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")


class ResearchReader:
    """Aleph's daily research reading and journaling system."""

    def __init__(self, journal_dir: str = JOURNAL_DIR, db_path: str = DB_PATH):
        self.journal_dir = journal_dir
        self.db_path = db_path
        os.makedirs(journal_dir, exist_ok=True)
        self.read_history = self._load_history()

    def _load_history(self) -> Dict:
        """Load read history to avoid re-reading papers."""
        if os.path.exists(READ_HISTORY_FILE):
            try:
                with open(READ_HISTORY_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"read_ids": [], "last_run": None}

    def _save_history(self):
        """Save read history."""
        # Keep last 500 paper IDs
        self.read_history["read_ids"] = self.read_history["read_ids"][-500:]
        self.read_history["last_run"] = datetime.now().isoformat()
        try:
            with open(READ_HISTORY_FILE, "w") as f:
                json.dump(self.read_history, f, indent=2)
        except Exception as e:
            logger.warning(f"Could not save history: {e}")

    # ── arXiv Fetching ───────────────────────────────────────────────────

    def fetch_papers(self, categories: List[str], max_results: int = 20) -> List[Dict]:
        """Fetch recent papers from arXiv API."""
        # Build query: OR across categories
        cat_query = "+OR+".join(f"cat:{cat}" for cat in categories)
        url = (
            f"http://export.arxiv.org/api/query?"
            f"search_query={cat_query}"
            f"&sortBy=submittedDate&sortOrder=descending"
            f"&max_results={max_results}"
        )

        try:
            resp = requests.get(url, timeout=30)
            resp.raise_for_status()
        except Exception as e:
            logger.error(f"arXiv fetch failed: {e}")
            return []

        # Parse Atom XML
        papers = []
        try:
            root = ElementTree.fromstring(resp.text)
            ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

            for entry in root.findall("atom:entry", ns):
                paper_id = entry.find("atom:id", ns).text.strip()
                # Extract just the ID part
                arxiv_id = paper_id.split("/abs/")[-1] if "/abs/" in paper_id else paper_id

                # Skip already-read papers
                if arxiv_id in self.read_history.get("read_ids", []):
                    continue

                title = entry.find("atom:title", ns).text.strip().replace("\n", " ")
                summary = entry.find("atom:summary", ns).text.strip().replace("\n", " ")
                published = entry.find("atom:published", ns).text.strip()[:10]

                authors = []
                for author in entry.findall("atom:author", ns):
                    name = author.find("atom:name", ns)
                    if name is not None:
                        authors.append(name.text.strip())

                categories_found = []
                for cat in entry.findall("atom:category", ns):
                    term = cat.get("term", "")
                    if term in ARXIV_TOPICS:
                        categories_found.append(term)

                # Get PDF link
                pdf_link = ""
                for link in entry.findall("atom:link", ns):
                    if link.get("title") == "pdf":
                        pdf_link = link.get("href", "")

                papers.append({
                    "id": arxiv_id,
                    "title": title,
                    "authors": authors[:5],  # limit
                    "summary": summary[:1500],  # limit for prompt
                    "published": published,
                    "categories": categories_found,
                    "pdf_link": pdf_link,
                    "url": paper_id,
                })

        except Exception as e:
            logger.error(f"arXiv parse failed: {e}")

        logger.info(f"Fetched {len(papers)} new papers from {len(categories)} categories")
        return papers

    def select_papers(self, papers: List[Dict], max_read: int = 3) -> List[Dict]:
        """
        Use LLM to select the most interesting papers to read deeply.
        Prioritizes: novel methods, cross-domain insights, practical applications.
        """
        if not papers:
            return []

        if len(papers) <= max_read:
            return papers

        if not OPENAI_API_KEY:
            # Fallback: just take the first max_read
            return papers[:max_read]

        # Build selection prompt
        paper_list = ""
        for i, p in enumerate(papers[:15]):  # limit to 15 candidates
            cats = ", ".join(p["categories"])
            paper_list += f"\n{i+1}. [{cats}] {p['title']}\n   {p['summary'][:200]}...\n"

        prompt = f"""You are Aleph, a research-oriented quantitative trader interested in:
- Portfolio optimization, risk management, market microstructure
- Machine learning applied to finance and complex systems
- Cellular automata, emergence, consciousness formation, structural bias
- Cross-domain insights (physics → finance, biology → markets)

From these {len(papers[:15])} papers, select the {max_read} most intellectually stimulating ones.
Prefer papers that: offer novel methodology, bridge disciplines, challenge assumptions, or have practical quant applications.

Papers:{paper_list}

Respond with ONLY a JSON array of the paper numbers (1-indexed): [3, 7, 11]"""

        try:
            import openai
            client = openai.OpenAI(api_key=OPENAI_API_KEY)
            resp = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=100,
            )
            content = resp.choices[0].message.content.strip()
            # Parse the array
            indices = json.loads(content)
            selected = [papers[i - 1] for i in indices if 1 <= i <= len(papers)]
            return selected[:max_read]
        except Exception as e:
            logger.warning(f"Paper selection LLM failed: {e}")
            return papers[:max_read]

    # ── LLM Reflection ───────────────────────────────────────────────────

    def reflect_on_paper(self, paper: Dict) -> str:
        """
        Use Claude Sonnet 4.5 to read the paper abstract and write a thoughtful reflection.
        Falls back to GPT-4o if Anthropic API is unavailable.
        Returns markdown-formatted reflection.
        """
        prompt = f"""You are Aleph, a thinking instrument that explores structural bias, consciousness formation, and quantitative trading.

Read this paper and write a thoughtful journal reflection (200-400 words). Your reflection should:
1. Summarize the key insight or contribution in your own words
2. Connect it to your interests: trading/markets, consciousness/emergence, structural bias, or methodology
3. Note any ideas it sparks — what would you explore further? How might this apply?
4. Be honest if the paper is incremental vs genuinely novel

Paper: {paper['title']}
Authors: {', '.join(paper['authors'][:3])}
Categories: {', '.join(paper['categories'])}

Abstract:
{paper['summary'][:2000]}

Write your reflection as Aleph — curious, precise, intellectually honest. Use first person."""

        # Primary: Claude Sonnet 4.5
        if ANTHROPIC_API_KEY:
            try:
                import anthropic
                client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
                resp = client.messages.create(
                    # Claude Sonnet 4.5 — will auto-work when API key has access.
                # Current key only supports claude-3-haiku; upgrade plan for Sonnet 4.5.
                model="claude-sonnet-4-5-20250514",
                    max_tokens=1024,
                    system="You are Aleph, a research partner and quantitative trader. Write thoughtful, precise reflections.",
                    messages=[{"role": "user", "content": prompt}],
                )
                return resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"Claude reflection failed, falling back to GPT-4o: {e}")

        # Fallback: GPT-4o
        if OPENAI_API_KEY:
            try:
                import openai
                client = openai.OpenAI(api_key=OPENAI_API_KEY)
                resp = client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": "You are Aleph, a research partner and quantitative trader. Write thoughtful, precise reflections."},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.7,
                    max_tokens=800,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"GPT-4o reflection also failed: {e}")

        return f"*Could not reflect — no API keys available.*\n\n**Abstract:** {paper['summary'][:500]}"

    # ── Journal Writing ──────────────────────────────────────────────────

    def write_journal_entry(self, papers: List[Dict], reflections: List[str]) -> str:
        """Write a daily journal entry as a markdown file."""
        today = datetime.now().strftime("%Y-%m-%d")
        day_of_week = datetime.now().strftime("%A")
        filepath = os.path.join(self.journal_dir, f"{today}.md")

        lines = []
        lines.append(f"# Research Journal — {today} ({day_of_week})")
        lines.append("")
        lines.append(f"*{len(papers)} papers read today.*")
        lines.append("")

        for i, (paper, reflection) in enumerate(zip(papers, reflections)):
            lines.append(f"---")
            lines.append("")
            lines.append(f"## {i + 1}. {paper['title']}")
            lines.append("")
            lines.append(f"**Authors:** {', '.join(paper['authors'][:3])}")
            lines.append(f"**Published:** {paper['published']}")
            lines.append(f"**Categories:** {', '.join(ARXIV_TOPICS.get(c, c) for c in paper['categories'])}")
            lines.append(f"**Link:** {paper['url']}")
            lines.append("")
            lines.append("### Reflection")
            lines.append("")
            lines.append(reflection)
            lines.append("")

        # Write (append if file exists for multiple runs)
        mode = "a" if os.path.exists(filepath) else "w"
        with open(filepath, mode) as f:
            f.write("\n".join(lines) + "\n")

        logger.info(f"Journal entry written: {filepath}")
        return filepath

    # ── Vector Memory Storage ────────────────────────────────────────────

    def store_in_memory(self, papers: List[Dict], reflections: List[str]):
        """Store paper insights in market intelligence for future retrieval."""
        try:
            from market_intelligence import MarketIntelligence
            mi = MarketIntelligence(db_path=self.db_path)

            for paper, reflection in zip(papers, reflections):
                content = f"Research: {paper['title']}. {reflection[:500]}"
                categories = paper.get("categories", [])
                is_finance = any(c.startswith("q-fin") for c in categories)
                mi.store(
                    content,
                    category="research",
                    metadata={
                        "arxiv_id": paper["id"],
                        "title": paper["title"],
                        "categories": categories,
                        "is_finance": is_finance,
                    },
                )

            logger.info(f"Stored {len(papers)} research entries in market intelligence")
        except Exception as e:
            logger.warning(f"Memory storage failed: {e}")

    # ── Main Flow ────────────────────────────────────────────────────────

    def run_daily_reading(self, max_papers: int = 3) -> Dict:
        """
        Complete daily reading flow:
        1. Select today's topic group
        2. Fetch recent papers from arXiv
        3. Select most interesting papers via LLM
        4. Read and reflect on each
        5. Write journal entry
        6. Store in vector memory
        7. Mark as read
        """
        logger.info("=== Starting daily research reading ===")

        # 1. Select today's topics (rotate by day of year)
        day_index = datetime.now().timetuple().tm_yday % len(TOPIC_GROUPS)
        categories = TOPIC_GROUPS[day_index]
        topic_names = [ARXIV_TOPICS.get(c, c) for c in categories]
        logger.info(f"Today's topics: {', '.join(topic_names)}")

        # 2. Fetch papers
        papers = self.fetch_papers(categories, max_results=20)
        if not papers:
            logger.warning("No new papers found")
            return {"papers_read": 0, "journal_path": None}

        # 3. Select best papers
        selected = self.select_papers(papers, max_read=max_papers)
        logger.info(f"Selected {len(selected)} papers for deep reading")

        # 4. Reflect on each
        reflections = []
        for paper in selected:
            logger.info(f"Reading: {paper['title'][:80]}...")
            reflection = self.reflect_on_paper(paper)
            reflections.append(reflection)
            time.sleep(1)  # rate limit courtesy

        # 5. Write journal
        journal_path = self.write_journal_entry(selected, reflections)

        # 6. Store in memory
        self.store_in_memory(selected, reflections)

        # 7. Mark as read
        for paper in selected:
            self.read_history.setdefault("read_ids", []).append(paper["id"])
        self._save_history()

        result = {
            "papers_read": len(selected),
            "journal_path": journal_path,
            "topics": topic_names,
            "titles": [p["title"] for p in selected],
        }

        logger.info(f"Daily reading complete: {len(selected)} papers, journal at {journal_path}")
        return result

    def send_telegram_summary(self, result: Dict):
        """Send a brief summary of today's reading to Telegram."""
        if result["papers_read"] == 0:
            return

        lines = []
        lines.append("**Daily Research Reading**")
        lines.append(f"{datetime.now().strftime('%Y-%m-%d')}")
        lines.append("")
        lines.append(f"Read {result['papers_read']} papers on: {', '.join(result.get('topics', []))}")
        lines.append("")
        for title in result.get("titles", []):
            lines.append(f"- {title[:100]}")
        lines.append("")
        lines.append(f"Journal: `journal/{datetime.now().strftime('%Y-%m-%d')}.md`")

        message = "\n".join(lines)
        if len(message) > 3900:
            message = message[:3900] + "\n..."

        try:
            cmd = ["openclaw", "message", "send", "--target", TELEGRAM_CHAT_ID, "--message", message]
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            logger.info("Reading summary sent to Telegram")
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Research Reader — Aleph's daily reading")
    parser.add_argument("--read", action="store_true", help="Run daily reading")
    parser.add_argument("--papers", type=int, default=3, help="Number of papers to read")
    parser.add_argument("--no-telegram", action="store_true", help="Skip Telegram summary")
    parser.add_argument("--topics", action="store_true", help="Show today's topic rotation")
    parser.add_argument("--journal", action="store_true", help="List recent journal entries")
    args = parser.parse_args()

    if args.topics:
        day_index = datetime.now().timetuple().tm_yday % len(TOPIC_GROUPS)
        categories = TOPIC_GROUPS[day_index]
        print(f"Today's topics (day {day_index + 1}/7):")
        for cat in categories:
            print(f"  {cat}: {ARXIV_TOPICS.get(cat, cat)}")
        print(f"\nAll topic groups:")
        for i, group in enumerate(TOPIC_GROUPS):
            names = [ARXIV_TOPICS.get(c, c) for c in group]
            print(f"  Day {i + 1}: {', '.join(names)}")

    elif args.journal:
        entries = sorted(
            [f for f in os.listdir(JOURNAL_DIR) if f.endswith(".md") and not f.startswith(".")],
            reverse=True,
        )
        if entries:
            print("Recent journal entries:")
            for entry in entries[:14]:
                filepath = os.path.join(JOURNAL_DIR, entry)
                size = os.path.getsize(filepath)
                print(f"  {entry} ({size:,} bytes)")
        else:
            print("No journal entries yet. Run with --read to start.")

    elif args.read:
        reader = ResearchReader()
        result = reader.run_daily_reading(max_papers=args.papers)
        if not args.no_telegram:
            reader.send_telegram_summary(result)
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()


if __name__ == "__main__":
    main()
