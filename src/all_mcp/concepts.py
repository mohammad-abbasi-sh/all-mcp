"""Multilingual text normalisation + concept expansion for tool search.

The router must understand a question asked in ANY of the four
languages (EN/TR/DE/FA) and match it against tool descriptions written
in any of them. Two mechanisms:

1. normalise(): folds case, Turkish/German diacritics, Arabic->Persian
   letter variants, ZWNJ/tatweel, and combining marks, so "Fußball",
   "futbol" and "فوتبال" all reduce to comparable tokens.
2. CONCEPTS: small hand-built groups of equivalent words across the
   four languages ("train" = "Zug" = "tren" = "قطار"). A query token
   is expanded to its whole group before matching, so a Persian
   question finds a tool whose description only says "train".

No external calls, no API keys, no ML — just string handling.
"""
from __future__ import annotations

import re
import unicodedata

_TR_MAP = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "ğ": "g", "ü": "u", "ö": "o", "ç": "c",
    "Ş": "s", "Ğ": "g", "Ü": "u", "Ö": "o", "Ç": "c",
})
_FA_MAP = str.maketrans({
    "ي": "ی", "ك": "ک", "ة": "ه", "ؤ": "و", "إ": "ا", "أ": "ا", "ٱ": "ا",
})

_TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)


def normalise(text: str) -> str:
    """Fold one string to a comparable lowercase form."""
    text = text.replace("ß", "ss")
    text = text.translate(_TR_MAP).translate(_FA_MAP)
    # ZWNJ and tatweel join words in Persian ("آب‌وهوا" -> "آبوهوا");
    # joining keeps them one token on both the query and index side.
    text = text.replace("‌", "").replace("ـ", "")
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    return text.lower()


def _stem(token: str) -> str:
    """Very light plural stripping for Latin-script tokens only.

    English rule: strip "es" only after s/x/z/ch/sh ("buses"->"bus",
    but "prices"->"price"); otherwise strip a single trailing "s".
    Concept forms (e.g. "news") are never stemmed.
    """
    if not token.isascii() or token in _EXPANSION:
        return token
    if (token.endswith("es") and len(token) > 4
            and token[:-2].endswith(("s", "x", "z", "ch", "sh"))):
        return token[:-2]
    if token.endswith("s") and len(token) > 3 \
            and not token.endswith(("ss", "us")):
        return token[:-1]
    return token


# Function words + question words in all four languages (stored
# normalised). Filtered from queries AND field text: without this,
# Persian "به" ("to") alone matches nearly every FA description and
# swamps real topical matches. Never contains a concept form.
STOPWORDS = frozenset({
    # Persian
    "از", "به", "در", "و", "که", "را", "با", "برای", "این", "آن",
    "است", "هست", "بود", "شد", "کن", "چیست", "چیه", "چقدره",
    "چقدر", "چطوره", "چطور", "چگونه", "چرا", "کجا", "کی", "آیا",
    "یا", "هم", "نه", "اگر", "تا", "روی", "زیر", "بعد", "قبل",
    "من", "تو", "او", "ما", "شما", "ایشان", "لطفا", "بده", "بگو",
    "میخوام", "میخواهم", "یک", "دو", "سه", "همون", "همین", "اون",
    "داره", "داری", "دارم", "هستن", "باشه", "ببین", "بیار", "نشون",
    # English
    "a", "an", "the", "is", "are", "was", "were", "be", "been",
    "what", "how", "much", "many", "do", "does", "did", "can",
    "could", "will", "would", "please", "give", "me", "show",
    "find", "get", "to", "of", "in", "on", "for", "and", "or",
    "i", "you", "he", "she", "we", "they", "it", "its", "this",
    "that", "there", "here", "when", "where", "why", "which",
    "who", "whom", "am", "as", "at", "by", "from", "with",
    "about", "into", "over", "after", "before", "between",
    "under", "again", "further", "then", "once", "so", "than",
    "too", "very", "just", "also", "no", "nor", "not", "only",
    "own", "same", "should",
    # German (normalised: ü->u, ß->ss)
    "der", "die", "das", "den", "dem", "ein", "eine", "einen",
    "ist", "sind", "war", "waren", "was", "wie", "viel", "viele",
    "bitte", "zeig", "mir", "zeige", "gib", "finde", "und",
    "oder", "in", "im", "am", "an", "von", "zu", "fur", "mit",
    "bei", "nach", "vor", "uber", "unter", "ich", "du", "er",
    "sie", "es", "wir", "ihr", "mein", "dein", "sein", "wann",
    "wo", "warum", "welcher", "welche", "gibt", "auch", "nicht",
    "nur", "noch", "schon", "sehr", "da", "dort", "hier",
    "dann", "als", "wenn", "aber", "doch", "ja", "nein",
    # Turkish (normalised)
    "ve", "veya", "ne", "nasil", "kac", "nedir", "kim", "nerede",
    "neden", "lutfen", "goster", "bana", "bir", "bu", "su", "o",
    "da", "de", "ki", "mi", "mu", "icin", "ile", "var", "yok",
    "kadar", "sonra", "once", "gibi", "cok", "daha", "en", "hic",
    "ama", "fakat", "ise",
})


def tokenise(text: str) -> list[str]:
    """Split text into normalised, lightly stemmed tokens, minus
    stopwords."""
    return [_stem(t) for t in _TOKEN_RE.findall(normalise(text))
            if t and _stem(t) not in STOPWORDS]


def name_tokens(tool_name: str) -> list[str]:
    """Split a snake_case tool name into tokens (e.g. flixtrain_search_trains)."""
    out: list[str] = []
    for part in tool_name.split("_"):
        out.extend(tokenise(part))
    return out


# Each group lists equivalent words across EN / DE / TR / FA.
# Forms are stored already normalised (lowercase, folded).
CONCEPTS: list[frozenset[str]] = [
    frozenset({"train", "zug", "tren", "قطار"}),
    frozenset({"ticket", "fahrkarte", "bilet", "بلیط", "بلیت", "karte"}),
    frozenset({"price", "preis", "fiyat", "قیمت"}),
    frozenset({"hotel", "otel", "هتل"}),
    frozenset({"weather", "wetter", "hava", "هوا", "آبوهوا"}),
    frozenset({"forecast", "vorhersage", "tahmin", "پیشبینی"}),
    frozenset({"news", "nachrichten", "haber", "خبر", "اخبار"}),
    frozenset({"football", "fussball", "futbol", "فوتبال"}),
    frozenset({"score", "ergebnis", "skor", "نتیجه", "نتایج"}),
    frozenset({"exchange", "rate", "currency", "wechselkurs", "kurs",
               "kur", "wahrung", "doviz", "ارز", "نرخ"}),
    frozenset({"flight", "flug", "ucus", "پرواز"}),
    frozenset({"bus", "otobus", "اتوبوس"}),
    frozenset({"food", "essen", "yemek", "غذا"}),
    frozenset({"recipe", "rezept", "tarif", "دستورپخت", "آشپزی"}),
    frozenset({"job", "stellenangebot", "شغل", "کار", "استخدام"}),
    frozenset({"house", "wohnung", "emlak", "خانه", "مسکن", "آپارتمان"}),
    frozenset({"rent", "miete", "kira", "اجاره", "رهن"}),
    frozenset({"car", "auto", "araba", "ماشین", "خودرو"}),
    frozenset({"exam", "prufung", "sinav", "آزمون", "امتحان"}),
    frozenset({"prayer", "gebet", "namaz", "نماز", "اذان"}),
    frozenset({"stock", "aktie", "borsa", "بورس", "سهام"}),
    frozenset({"crypto", "krypto", "kripto", "رمزارز", "کریپتو",
               "بیتکوین", "bitcoin"}),
    frozenset({"dollar", "dolar", "دلار", "usd"}),
    frozenset({"euro", "یورو", "eur"}),
    frozenset({"lira", "لیر"}),
    frozenset({"movie", "film", "cinema", "kino", "sinema", "فیلم", "سینما"}),
    frozenset({"series", "serie", "dizi", "سریال"}),
    frozenset({"music", "musik", "muzik", "موسیقی", "آهنگ"}),
    frozenset({"book", "buch", "kitap", "کتاب"}),
    frozenset({"dictionary", "worterbuch", "sozluk", "دیکشنری", "لغت", "واژه"}),
    frozenset({"word", "wort", "kelime", "کلمه"}),
    frozenset({"language", "sprache", "dil", "زبان"}),
    frozenset({"grammar", "grammatik", "dilbilgisi", "گرامر", "دستورزبان"}),
    frozenset({"translation", "ubersetzung", "ceviri", "ترجمه"}),
    frozenset({"health", "gesundheit", "saglik", "سلامت"}),
    frozenset({"pharmacy", "apotheke", "eczane", "داروخانه", "دارو"}),
    frozenset({"traffic", "verkehr", "trafik", "ترافیک"}),
    frozenset({"road", "strasse", "yol", "جاده"}),
    frozenset({"fuel", "benzin", "akaryakit", "بنزین", "سوخت"}),
    frozenset({"supermarket", "supermarkt", "market", "سوپرمارکت", "فروشگاه"}),
    frozenset({"discount", "rabatt", "indirim", "تخفیف", "حراج"}),
    frozenset({"coupon", "gutschein", "kupon", "کدتخفیف", "کد"}),
    frozenset({"tv", "fernsehen", "televizyon", "تلویزیون"}),
    frozenset({"radio", "رادیو"}),
    frozenset({"podcast", "پادکست"}),
    frozenset({"holiday", "feiertag", "tatil", "تعطیل", "تعطیلات"}),
    frozenset({"calendar", "kalender", "takvim", "تقویم"}),
    frozenset({"university", "universitat", "universite", "دانشگاه"}),
    frozenset({"law", "gesetz", "kanun", "قانون"}),
    frozenset({"bank", "banka", "بانک"}),
    frozenset({"restaurant", "restoran", "رستوران"}),
    frozenset({"menu", "speisekarte", "منو"}),
    frozenset({"election", "wahl", "secim", "انتخابات"}),
    frozenset({"energy", "energie", "enerji", "انرژی"}),
    frozenset({"environment", "umwelt", "cevre", "محیطزیست"}),
    frozenset({"postal", "postleitzahl", "posta", "پستی", "کدپستی"}),
    frozenset({"transport", "ulasim", "حملونقل", "رفتوآمد"}),
    frozenset({"visa", "vize", "ویزا"}),
    frozenset({"city", "stadt", "sehir", "شهر"}),
    frozenset({"station", "bahnhof", "istasyon", "gar", "ایستگاه"}),
    frozenset({"time", "zeit", "saat", "زمان", "ساعت"}),
    frozenset({"date", "datum", "tarih", "تاریخ"}),
    frozenset({"search", "suche", "ara", "جستجو"}),
    frozenset({"live", "canli", "زنده"}),
    frozenset({"table", "tabelle", "tablo", "جدول"}),
    frozenset({"video", "ویدیو", "ویدئو"}),
    frozenset({"article", "artikel", "makale", "مقاله"}),
    frozenset({"business", "unternehmen", "isletme", "کسبوکار", "شرکت"}),
    frozenset({"reservation", "buchung", "rezervasyon", "رزرو"}),
]

_EXPANSION: dict[str, frozenset[str]] = {}
for _group in CONCEPTS:
    for _form in _group:
        if _form in _EXPANSION and _EXPANSION[_form] is not _group:
            raise ValueError(f"concept form {_form!r} in two groups")
        _EXPANSION[_form] = _group


def expand(token: str) -> frozenset[str]:
    """All cross-language equivalents of a token (or just itself)."""
    return _EXPANSION.get(token, frozenset({token}))


def token_match(query_token: str, field_tokens: set[str]) -> str | None:
    """How a query token matches a set of field tokens.

    Returns "exact", "prefix", or None. Prefix matching covers
    plurals and inflections ("prices"~"price", "هوای"~"هوا").
    Minimum prefix length is 4 for Latin-script tokens (so "bus"
    does not match "business") and 3 otherwise.
    """
    if query_token in field_tokens:
        return "exact"
    min_len = 4 if query_token.isascii() else 3
    if len(query_token) < min_len:
        return None
    for ft in field_tokens:
        if len(ft) < min_len:
            continue
        if ft.startswith(query_token) or query_token.startswith(ft):
            return "prefix"
    return None


# Category affinity: which concepts belong to which catalog category.
# Sets hold one (English) form per concept; query expansion covers
# the other languages. A +2 bonus when the question's topic matches
# the server's category — what lifts transport servers for a bus
# question even when the closest German tool is a train, not a bus.
CATEGORY_TOPICS: dict[str, frozenset[str]] = {
    "transport": frozenset({"train", "ticket", "bus", "flight",
                            "station", "transport", "road", "traffic"}),
    "travel": frozenset({"train", "ticket", "bus", "flight", "station",
                         "transport", "hotel", "reservation", "visa"}),
    "finance": frozenset({"exchange", "rate", "stock", "crypto",
                          "bank", "price", "dollar", "euro", "lira"}),
    "shopping": frozenset({"price", "discount", "coupon", "supermarket"}),
    "discounts": frozenset({"discount", "coupon", "price"}),
    "grocery": frozenset({"food", "supermarket", "recipe"}),
    "food": frozenset({"food", "recipe"}),
    "news": frozenset({"news"}),
    "media": frozenset({"tv", "series", "movie", "music", "radio",
                        "podcast", "video"}),
    "entertainment": frozenset({"movie", "series", "tv", "music"}),
    "sports": frozenset({"football", "score"}),
    "jobs": frozenset({"job"}),
    "real-estate": frozenset({"house", "rent"}),
    "housing": frozenset({"house", "rent"}),
    "cars": frozenset({"car"}),
    "weather": frozenset({"weather", "forecast"}),
    "language": frozenset({"dictionary", "word", "language", "grammar",
                           "translation"}),
    "health": frozenset({"health", "pharmacy"}),
    "services": frozenset({"prayer"}),
}
