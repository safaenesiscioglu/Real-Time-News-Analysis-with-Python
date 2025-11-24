import sqlite3
from pathlib import Path

import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent / "news.db"

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


def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)


def load_data(category: str = "all", hours: int | None = 24, limit: int = 500) -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT
            title,
            summary,
            link,
            published,
            source,
            sentiment,
            category,
            created_at
        FROM articles
        WHERE 1=1
    """

    params: list = []

    # Kategori filtresi
    if category != "all":
        query += " AND category = ?"
        params.append(category)

    # Zaman filtresi (son X saat)
    if hours is not None:
        query += " AND created_at >= datetime('now', ?)"
        params.append(f"-{hours} hours")

    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)

    df = pd.read_sql_query(query, conn, params=params)
    conn.close()
    return df


def main():
    st.set_page_config(page_title="SEI News Analyzer", layout="wide")
    st.title("📰 SEI News Analyzer Dashboard")

    if not DB_PATH.exists():
        st.error(f"Veritabanı bulunamadı: {DB_PATH}\nÖnce ana script ile haber çekip kaydet.")
        return

    st.sidebar.header("Filtreler")

    # Kategori seçimi
    category = st.sidebar.selectbox(
        "Kategori",
        options=[
            "all",
            "conflict/crisis",
            "economy",
            "politics",
            "technology",
            "society",
            "other",
        ],
        index=0,
    )

    # Saat filtresi
    time_range = st.sidebar.selectbox(
        "Zaman aralığı",
        options=[
            ("Son 6 saat", 6),
            ("Son 24 saat", 24),
            ("Son 3 gün", 72),
            ("Son 7 gün", 24 * 7),
            ("Tüm kayıtlar", None),
        ],
        format_func=lambda x: x[0],
        index=1,
    )
    hours = time_range[1]

    # Limit
    limit = st.sidebar.slider("En fazla kaç haber gösterilsin?", min_value=50, max_value=1000, value=300, step=50)

    # Duygu filtresi
    min_sentiment, max_sentiment = st.sidebar.slider(
        "Duygu skoru aralığı (−1 = çok negatif, +1 = çok pozitif)",
        min_value=-1.0,
        max_value=1.0,
        value=(-1.0, 1.0),
        step=0.1,
    )

    st.sidebar.markdown("---")
    st.sidebar.caption("Veriler: news.db")

    # Veriyi yükle
    df = load_data(category=category, hours=hours, limit=limit)

    if df.empty:
        st.warning("Bu filtrelere uyan haber bulunamadı.")
        return

    # Sentiment filtrele
    df = df[(df["sentiment"] >= min_sentiment) & (df["sentiment"] <= max_sentiment)]

    # ALERT etiketlerini ekle
    df["alerts"] = df.apply(
        lambda row: detect_alert_labels(row["title"], row["summary"]),
        axis=1,
    )

    # Sidebar'a "sadece alert'li" filtresi
    only_alerts = st.sidebar.checkbox("Sadece uyarı tetikleyen haberler", value=False)
    if only_alerts:
        df = df[df["alerts"] != ""]

    # ==== ÖZET ====
    st.subheader("Özet")

    # Tarih (sadece gün) kolonu
    df["date"] = pd.to_datetime(df["created_at"]).dt.date


    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("Toplam haber", len(df))
    with col2:
        st.metric("Farklı kaynak", df["source"].nunique())
    with col3:
        st.metric("Kategori sayısı", df["category"].nunique())

    # ==== ZAMAN İÇİNDE ORTALAMA DUYGU SKORU ====
    # created_at kolonundan gün bazlı tarih çıkar
    df["date"] = pd.to_datetime(df["created_at"]).dt.date

    st.subheader("Zaman içinde ortalama duygu skoru")

    sent_by_day = (
        df.groupby(["date", "category"])["sentiment"]
        .mean()
        .reset_index()
        .sort_values("date")
    )

    if not sent_by_day.empty:
        pivot = sent_by_day.pivot(index="date", columns="category", values="sentiment")
        st.line_chart(pivot)
    else:
        st.info("Seçilen filtrelerle zaman serisi grafiği için yeterli veri yok.")


    # Kategori dağılımı
    st.subheader("Kategori dağılımı")
    cat_counts = df["category"].value_counts().rename_axis("category").reset_index(name="count")
    st.bar_chart(data=cat_counts, x="category", y="count")

    # Sentiment dağılımı (basit histogram)
    st.subheader("Duygu skoru dağılımı")
    st.bar_chart(df["sentiment"])

    # Haber tablosu
    st.subheader("Haberler (detaylı)")
    # Linkleri daha kullanışlı hale getirelim
    df_display = df.copy()
    df_display["link"] = df_display["link"].apply(lambda x: f"[Aç]({x})" if isinstance(x, str) else x)

    tabs = st.tabs(["Tümü", "Conflict/Crisis", "Economy", "Politics", "Society", "Technology", "Other"])
    tab_configs = [
        ("Tümü", df_display),
        ("Conflict/Crisis", df_display[df_display["category"] == "conflict/crisis"]),
        ("Economy", df_display[df_display["category"] == "economy"]),
        ("Politics", df_display[df_display["category"] == "politics"]),
        ("Society", df_display[df_display["category"] == "society"]),
        ("Technology", df_display[df_display["category"] == "technology"]),
        ("Other", df_display[df_display["category"] == "other"]),
    ]

    columns_to_show = [
        "published",
        "created_at",
        "source",
        "category",
        "sentiment",
        "alerts",
        "title",
        "summary",
        "link",
    ]

    for tab, (_, df_tab) in zip(tabs, tab_configs):
        with tab:
            st.dataframe(df_tab[columns_to_show], width="stretch")


    st.dataframe(
        df_display[
            [
                "published",
                "created_at",
                "source",
                "category",
                "sentiment",
                "alerts",
                "title",
                "summary",
                "link",
            ]
        ],
        width="stretch",
    )
    search_text = st.sidebar.text_input("Başlık / özet içinde ara", value="")

    if search_text:
        s = search_text.lower()
        df = df[
            df["title"].str.lower().str.contains(s, na=False)
            | df["summary"].str.lower().str.contains(s, na=False)
        ]



if __name__ == "__main__":
    main()
