# SEI News Analyzer

A real-time news analyzer for RSS feeds.  
It fetches news articles, analyzes their sentiment, classifies them into categories, triggers alerts for critical topics (war, earthquake, kidnapping, economy, etc.), stores everything in a SQLite database, and provides both a command-line interface and a Streamlit dashboard.

---

## Features

- Fetches news from multiple RSS feeds (e.g. BBC World, TRT Haber, Anadolu Ajansı, DW Türkçe).
- Performs simple sentiment analysis (using TextBlob) on the article title + summary.
- Rule-based categorization using English and Turkish keywords:
  - `conflict/crisis`, `politics`, `economy`, `technology`, `society`, `other`
- Alert system for critical topics in the CLI:
  - Earthquake, war/conflict, bombing/explosion, kidnapping, economy
- Stores all processed articles in a local SQLite database (`news.db`).
- Command-line modes:
  - **live** (default): real-time fetching + alerts + saving to DB
  - **report**: summary of the database and most negative articles
  - **recent**: most negative articles from the last X hours
  - **export**: export all records to CSV
- Web dashboard built with Streamlit:
  - Filters by category, time range, and sentiment range
  - Option to show only alert-triggering articles
  - Summary metrics and charts
  - Detailed, clickable table with links to the original news articles

---

## Installation

### Requirements

- Python 3.10+  
- A Unix-like environment (developed on macOS, but should work on Linux/Windows with small adjustments)
- `git` (optional, for cloning the repository)

### 1. Clone the repository

```bash
git clone https://github.com/safaenesiscioglu/sei-news-analyzer.git
cd sei-news-analyzer
