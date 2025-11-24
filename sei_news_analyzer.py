from dataclasses import dataclass
from typing import List, Dict, Optional
import time

import ssl
import certifi  # ssl sertifika sorun çözücü
import sqlite3
from pathlib import Path
import sys
import csv
import subprocess
import requests



import feedparser
from textblob import TextBlob

# SSL sertifikası için certifi kullan (BBC vs. için gerekli)
ssl._create_default_https_context = lambda *args, **kwargs: ssl.create_default_context(
    cafile=certifi.where()
)

USE_MACOS_NOTIFICATIONS = False  # İstersen bunu True yaparız
USE_ADVANCED_SENTIMENT = False  # True yaparsan gelişmiş modeli kullanır (kurman gerekir)
USE_TELEGRAM_ALERTS = False  # kullanmak istersen True yap
TELEGRAM_BOT_TOKEN = "BURAYA_BOT_TOKEN"
TELEGRAM_CHAT_ID = "BURAYA_CHAT_ID"


ALERT_KEYWORDS = {
    "Deprem / Earthquake": [
        "earthquake", "aftershock", "tremor", "quake",
        "deprem",
    ],
    "Savaş / War / Çatışma": [
        "war", "invasion", "offensive", "airstrike", "air strike",
        "missile", "rocket attack", "shelling",
        "savaş", "çatışma",
    ],
    "Patlama / Bombalama": [
        "bombing", "blast", "explosion", "suicide attack",
        "car bomb", "roadside bomb",
        "patlama", "bombalı saldırı", "intihar saldırısı",
    ],
    "Rehine / Kaçırma": [
        "kidnapped", "abducted", "hostage", "abduction",
        "rehine", "kaçırıldı", "kaçırılan",
    ],
    "Ekonomi / Economy": [
        "inflation", "recession", "interest rate", "interest rates",
        "stock market", "exchange rate", "currency crisis",
        "economy", "economic crisis",
        "enflasyon", "resesyon", "faiz", "faiz oranı",
        "kur krizi", "döviz krizi", "döviz kuru",
        "borsa", "dolar", "euro",
    ],
}

def detect_alert_labels(title: str, summary: str) -> str:
    text = (str(title) + " " + str(summary)).lower()
    labels: list[str] = []

    for label, keywords in ALERT_KEYWORDS.items():
        if any(k.lower() in text for k in keywords):
            labels.append(label)

    return ", ".join(labels) if labels else ""



@dataclass
class Article:
    title: str
    summary: str
    link: str
    published: str
    source: str
    sentiment: Optional[float] = None
    category: Optional[str] = None


# RSS kaynaklarını burada tanımlıyoruz
RSS_FEEDS: Dict[str, str] = {
    # İngilizce
    "BBC World": "https://feeds.bbci.co.uk/news/world/rss.xml",

    # Türkçe kaynaklar
    "TRT Haber Manşet": "https://www.trthaber.com/manset_articles.rss",         # Manşetler :contentReference[oaicite:0]{index=0}
    "TRT Haber Dünya": "https://www.trthaber.com/dunya_articles.rss",           # Dünya haberleri :contentReference[oaicite:1]{index=1}
    "AA Teyit Hattı - Tüm": "https://www.aa.com.tr/tr/teyithatti/rss/news?cat=0",  # Tüm haberler :contentReference[oaicite:2]{index=2}
    "DW Türkçe": "https://rss.dw.com/rdf/rss-tur-all",                          # DW Türkçe tüm haberler :contentReference[oaicite:3]{index=3}
}



# Aynı haberi iki kez işlememek için linkleri burada tutacağız
seen_links: set[str] = set()

DB_PATH = Path(__file__).parent / "news.db"


def init_db() -> sqlite3.Connection:
    """
    SQLite veritabanını hazırlar ve bağlantıyı döner.
    news.db dosyası proje klasöründe oluşur.
    """
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT,
            summary TEXT,
            link TEXT UNIQUE,
            published TEXT,
            source TEXT,
            sentiment REAL,
            category TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    return conn

def print_db_summary() -> None:
    """
    Veritabanı hakkında basit bir özet basar:
    - Toplam haber sayısı
    - Farklı kaynak sayısı
    - İlk ve son kayıt tarihi
    """
    conn = init_db()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM articles")
    total = cur.fetchone()[0] or 0

    cur.execute("SELECT COUNT(DISTINCT source) FROM articles")
    sources = cur.fetchone()[0] or 0

    cur.execute("SELECT MIN(created_at), MAX(created_at) FROM articles")
    earliest, latest = cur.fetchone()

    print("=== Veritabanı Özeti ===")
    print(f"Toplam kayıtlı haber : {total}")
    print(f"Farklı kaynak sayısı : {sources}")
    print(f"İlk kayıt tarihi     : {earliest}")
    print(f"Son kayıt tarihi     : {latest}")
    print()

    conn.close()

def print_most_negative(limit: int = 10) -> None:
    """
    Veritabanındaki en negatif (en düşük sentiment) haberleri listeler.
    """
    conn = init_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT title, source, published, sentiment, category, link
        FROM articles
        WHERE sentiment IS NOT NULL
        ORDER BY sentiment ASC
        LIMIT ?
        """,
        (limit,),
    )

    rows = cur.fetchall()
    if not rows:
        print("Veritabanında kayıtlı haber yok veya sentiment verisi yok.")
        conn.close()
        return

    print(f"=== En negatif {len(rows)} haber ===")
    for i, (title, source, published, sentiment, category, link) in enumerate(rows, start=1):
        print("-" * 80)
        print(f"#{i}")
        print(f"Kaynak   : {source}")
        print(f"Tarih    : {published}")
        print(f"Kategori : {category}")
        print(f"Duygu    : {sentiment:.3f}")
        print(f"Başlık   : {title}")
        print(f"Link     : {link}")
    print()

    conn.close()

def print_recent_by_category(category: str = "conflict", hours: int = 24, limit: int = 20) -> None:
    """
    Son X saatte eklenmiş, belirtilen kategoriye ait haberleri
    en negatiften başlayarak listeler.

    category:
      - 'conflict', 'economy', 'politics', 'technology', 'other'
      - veya 'all' → kategori filtrelemez

    hours:
      - Kaç saat geriye bakılacağı (created_at alanına göre)
    """
    conn = init_db()
    cur = conn.cursor()

    modifier = f"-{hours} hours"

    cur.execute(
        """
        SELECT title, source, published, sentiment, category, link, created_at
        FROM articles
        WHERE (? = 'all' OR category = ?)
          AND created_at >= datetime('now', ?)
          AND sentiment IS NOT NULL
        ORDER BY sentiment ASC
        LIMIT ?
        """,
        (category, category, modifier, limit),
    )

    rows = cur.fetchall()
    if not rows:
        print(f"Son {hours} saatte bu kritere uyan haber yok. (kategori: {category})")
        conn.close()
        return

    print(f"=== Son {hours} saatin en negatif {len(rows)} haberi (kategori: {category}) ===")
    for i, (title, source, published, sentiment, cat, link, created_at) in enumerate(rows, start=1):
        print("-" * 80)
        print(f"#{i}")
        print(f"Kaynak     : {source}")
        print(f"Kategori   : {cat}")
        print(f"Duygu      : {sentiment:.3f}")
        print(f"Yayın tarihi : {published}")
        print(f"Kayıt tarihi : {created_at}")
        print(f"Başlık     : {title}")
        print(f"Link       : {link}")
    print()

    conn.close()

def export_to_csv(filename: str = "news_export.csv") -> None:
    """
    Tüm kayıtlı haberleri bir CSV dosyasına aktarır.
    Dosya proje klasöründe oluşur.
    """
    conn = init_db()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT title, summary, link, published, source, sentiment, category, created_at
        FROM articles
        ORDER BY created_at DESC
        """
    )

    rows = cur.fetchall()
    if not rows:
        print("Veritabanında export edilecek haber yok.")
        conn.close()
        return

    out_path = Path(__file__).parent / filename

    with out_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            ["title", "summary", "link", "published", "source", "sentiment", "category", "created_at"]
        )
        writer.writerows(rows)

    conn.close()
    print(f"CSV dosyası oluşturuldu: {out_path}")


def fetch_latest_articles() -> List[Article]:
    """RSS kaynaklarından yeni haberleri çeker."""
    articles: List[Article] = []

    for source_name, url in RSS_FEEDS.items():
        print(f"\n[DEBUG] Kaynak kontrol ediliyor: {source_name} ({url})")
        feed = feedparser.parse(url)

        # Hata kontrolü
        if getattr(feed, "bozo", 0):
            print("[DEBUG]  -> Hata (bozo):", feed.bozo_exception)

        print("[DEBUG]  -> Entry sayısı:", len(getattr(feed, "entries", [])))

        for entry in feed.entries:
            link = getattr(entry, "link", None)
            if not link or link in seen_links:
                continue

            seen_links.add(link)

            title = getattr(entry, "title", "")
            summary = getattr(entry, "summary", "")
            published = str(getattr(entry, "published", ""))

            articles.append(
                Article(
                    title=title,
                    summary=summary,
                    link=link,
                    published=published,
                    source=source_name,
                )
            )

    print(f"[DEBUG] Toplam yeni article sayısı: {len(articles)}")
    return articles

def analyze_sentiment(text: str) -> float:
    """
    -1.0 (çok negatif) ile +1.0 (çok pozitif) arası skor.
    USE_ADVANCED_SENTIMENT True ise gelişmiş modeli kullanmaya hazırlanmış yapı.
    Şimdilik TextBlob varsayılan.
    """
    if not text:
        return 0.0

    if not USE_ADVANCED_SENTIMENT:
        blob = TextBlob(text)
        return float(blob.sentiment.polarity)

    # Buraya istersen ileride transformers tabanlı bir model entegre edebilirsin.
    # Örn:
    # from transformers import pipeline
    # sentiment_model = pipeline("sentiment-analysis", model="...çok dilli model...")
    # result = sentiment_model(text[:512])[0]
    # ... sonucu -1/+1'e ölçeklersin.
    # Şimdilik TextBlob'a fallback yapalım:
    blob = TextBlob(text)
    return float(blob.sentiment.polarity)


def categorize_article(article: Article) -> str:
    """
    İngilizce + Türkçe keyword'lere göre haber kategorisi.
    Öncelik sırası:
      1) conflict/crisis
      2) politics
      3) economy
      4) technology
      5) society
      6) other
    """
    text = (article.title + " " + article.summary).lower()

    # 1) Savaş / kriz / afet
    conflict_keywords = [
        # EN
        "war", "invasion", "offensive", "airstrike", "air strike",
        "missile", "rocket attack", "shelling", "frontline",
        "military clash", "gunmen", "mass abduction", "kidnapped",
        "hostage", "terrorist", "suicide attack", "bombing",
        "explosion", "blast", "attack", "conflict", "clashes",
        "earthquake", "aftershock", "tremor", "quake",
        "flood", "wildfire", "hurricane",
        # TR
        "savaş", "çatışma", "baskın", "askeri operasyon",
        "roket", "füze", "bombalı saldırı", "bombalı",
        "patlama", "terör", "rehine", "kaçırıldı", "kaçırılan",
        "deprem", "artçı", "sel", "yangın", "fırtına",
    ]
    if any(k in text for k in conflict_keywords):
        return "conflict/crisis"

    # 2) Siyaset
    politics_keywords = [
        # EN
        "election", "elections", "vote", "voting", "ballot",
        "government", "minister", "prime minister",
        "president", "parliament", "senate", "congress",
        "coalition", "opposition", "ruling party",
        "politician", "political",
        # TR
        "seçim", "oy", "sandık", "hükümet", "hükümeti",
        "bakan", "bakanlık", "başbakan", "cumhurbaşkanı",
        "meclis", "parlamento", "milletvekili",
        "koalisyon", "muhalefet", "iktidar", "siyasi", "siyaset",
    ]
    if any(k in text for k in politics_keywords):
        return "politics"

    # 3) Ekonomi
    economy_keywords = [
        # EN
        "economy", "economic", "recession", "growth",
        "inflation", "interest rate", "interest rates",
        "stock market", "stocks", "shares", "bond",
        "currency", "exchange rate", "dollar", "euro",
        "unemployment", "wage", "salary", "budget", "debt",
        # TR
        "ekonomi", "ekonomik", "resesyon", "büyüme",
        "enflasyon", "faiz", "faiz oranı", "faiz oranları",
        "borsa", "hisse", "tahvil",
        "kur", "döviz", "dolar", "euro",
        "işsizlik", "maaş", "ücret", "bütçe", "borç",
        "zam", "indirim", "piyasa", "fiyat artışı",
    ]
    if any(k in text for k in economy_keywords):
        return "economy"

    # 4) Teknoloji
    tech_keywords = [
        # EN
        "ai", "artificial intelligence", "machine learning",
        "app", "application", "software", "hardware",
        "social media", "platform", "startup", "tech company",
        "cyber", "hacker", "data breach", "privacy",
        "smartphone", "device", "robot",
        # TR
        "yapay zeka", "makine öğrenmesi",
        "uygulama", "yazılım", "donanım",
        "sosyal medya", "platform", "teknoloji", "teknolojik",
        "siber", "siber saldırı", "veri ihlali", "gizlilik",
        "telefon", "akıllı telefon", "cihaz", "robot",
    ]
    if any(k in text for k in tech_keywords):
        return "technology"

    # 5) Toplum / sosyal konular
    society_keywords = [
        # EN
        "school", "university", "student", "students",
        "teacher", "family", "families", "children", "kids",
        "gender", "violence", "domestic violence",
        "rights", "human rights", "protest", "demonstration",
        "police", "crime", "murder", "shooting",
        # TR
        "okul", "üniversite", "öğrenci", "öğretmen",
        "aile", "çocuk", "kadın", "erkek",
        "şiddet", "aile içi şiddet",
        "hak", "insan hakları", "protesto", "gösteri",
        "polis", "suç", "cinayet", "saldırı",
    ]
    if any(k in text for k in society_keywords):
        return "society"

    return "other"


def process_articles(articles: List[Article]) -> List[Article]:
    """Her habere duygu skoru ve kategori ekler."""
    for article in articles:
        text = article.title + " " + article.summary
        article.sentiment = analyze_sentiment(text)
        article.category = categorize_article(article)
    return articles

def filter_articles(articles: List[Article]) -> List[Article]:
    """
    Şimdilik sadece duygu skoruna göre filtre:
      - Duygu skoru 0.0'dan küçük (negatif) olan haberleri döndür.
    Kategoriye şimdilik bakmıyoruz.
    """
    max_sentiment = 0.0  # 0'dan küçük = negatif

    filtered: List[Article] = []
    for a in articles:
        if a.sentiment is None:
            continue
        if a.sentiment >= max_sentiment:
            continue
        filtered.append(a)

    return filtered

def check_alerts(article: Article) -> list[str]:
    """
    Haberin başlık + özet metninde ALERT_KEYWORDS'teki
    anahtar kelimelerden hangileri geçiyor, onları döndürür.
    """
    text = (article.title + " " + article.summary).lower()
    triggered: list[str] = []

    for label, keywords in ALERT_KEYWORDS.items():
        if any(k.lower() in text for k in keywords):
            triggered.append(label)

    return triggered

def send_macos_notification(title: str, message: str) -> None:
    """
    macOS Bildirim Merkezi'ne uyarı yollar.
    USE_MACOS_NOTIFICATIONS = True olursa aktif olur.
    """
    if not USE_MACOS_NOTIFICATIONS:
        return

    try:
        subprocess.run(
            [
                "osascript",
                "-e",
                f'display notification "{message}" with title "{title}"'
            ],
            check=False,
        )
    except Exception as e:
        print(f"[WARN] macOS notification failed: {e}")

def send_telegram_alert(text: str) -> None:
    if not USE_TELEGRAM_ALERTS:
        return
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": text}
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"[WARN] Telegram alert failed: {e}")


def save_articles(conn: sqlite3.Connection, articles: List[Article]) -> None:
    """
    Haber listesini veritabanına kaydeder.
    Aynı link'e sahip haberler (UNIQUE) tekrar eklenmez.
    """
    if not articles:
        return

    cur = conn.cursor()
    for a in articles:
        try:
            cur.execute(
                """
                INSERT OR IGNORE INTO articles
                (title, summary, link, published, source, sentiment, category)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    a.title,
                    a.summary,
                    a.link,
                    a.published,
                    a.source,
                    a.sentiment,
                    a.category,
                ),
            )
        except Exception as e:
            # Basit log, istersen kaldırabilirsin
            print(f"[DB] Kaydetme hatası ({a.link}): {e}")

    conn.commit()



def print_report(articles: List[Article]) -> None:
    """Haberleri konsola okunaklı bir şekilde yazdırır."""
    for a in articles:
        alerts = check_alerts(a)

        print("-" * 80)
        if alerts:
            alert_text = "; ".join(alerts)
            print(f"!!! ALERT !!! [{alert_text}]")

            msg = f"{alert_text}: {a.title} ({a.source})"

            # macOS bildirimi (isteğe bağlı)
            send_macos_notification(
                title="SEI News Alert",
                message=f"{alert_text}: {a.title[:80]}",
            )
            
            # Telegram bildirimi
            send_telegram_alert(msg)

        print(f"Kaynak   : {a.source}")
        print(f"Başlık   : {a.title}")
        print(f"Kategori : {a.category}")
        print(f"Duygu    : {a.sentiment:.3f}")
        print(f"Tarih    : {a.published}")
        print(f"Link     : {a.link}")

    print(f"\nToplam yeni haber: {len(articles)}")



def main_loop(poll_interval: int = 60):
    """
    poll_interval: Kaç saniyede bir yeni haber kontrol edileceği.
    """
    print("Gerçek zamanlı haber analizatörü başlıyor...\n")

    # Veritabanını hazırla
    conn = init_db()
    print(f"[DB] Veritabanı: {DB_PATH}")

    try:
        while True:
            new_articles = fetch_latest_articles()
            if new_articles:
                processed = process_articles(new_articles)

                # 1) TÜM haberleri DB'ye kaydet
                save_articles(conn, processed)
                print(f"[DB] Kaydedilen (toplam) haber sayısı: {len(processed)}")

                # 2) Sadece filtreye uyanları ekrana ve alarma ver
                filtered = filter_articles(processed)

                if filtered:
                    print_report(filtered)
                else:
                    print("Filtreye uyan yeni haber yok.")


            time.sleep(poll_interval)
    except KeyboardInterrupt:
        print("\nProgram kullanıcı tarafından durduruldu.")
    finally:
        conn.close()
        print("[DB] Bağlantı kapatıldı.")




if __name__ == "__main__":
    # Kullanım:
    #   python sei_news_analyzer.py
    #       -> canlı izleme + DB'ye kaydetme
    #
    #   python sei_news_analyzer.py report
    #       -> veritabanı özeti + en negatif 10 haber
    #
    #   python sei_news_analyzer.py recent [kategori] [saat]
    #       -> son X saatin en negatif haberleri
    #          kategori boşsa varsayılan: conflict
    #          saat boşsa varsayılan: 24
    #
    #   python sei_news_analyzer.py export [dosya_adi.csv]
    #       -> tüm haberleri CSV olarak dışa aktar

    if len(sys.argv) > 1:
        mode = sys.argv[1]

        if mode == "report":
            print("[MODE] Rapor modu (veritabanındaki haberler)\n")
            print_db_summary()
            print_most_negative(limit=10)

        elif mode == "recent":
            category = sys.argv[2] if len(sys.argv) > 2 else "conflict"
            try:
                hours = int(sys.argv[3]) if len(sys.argv) > 3 else 24
            except ValueError:
                hours = 24

            print(f"[MODE] Son {hours} saatin haberleri (kategori: {category})\n")
            print_recent_by_category(category=category, hours=hours, limit=20)

        elif mode == "export":
            filename = sys.argv[2] if len(sys.argv) > 2 else "news_export.csv"
            print(f"[MODE] Export modu (dosya: {filename})\n")
            export_to_csv(filename)

        else:
            # Bilinmeyen mod → canlı moda düş
            print(f"[MODE] Bilinmeyen mod: {mode} -> canlı moda geçiliyor\n")
            main_loop(poll_interval=60)
    else:
        main_loop(poll_interval=60)
    


