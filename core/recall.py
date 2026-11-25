"""Relevance ranking for explicit memories (BM25, pure Python, tuned for short French facts)."""
import math
import re
import unicodedata
from collections import Counter
from core.i18n import value

WORD = re.compile(r"\w+")


def terms(text):
    """Accent-insensitive, lower-cased words with stopwords removed and plurals folded."""
    text = unicodedata.normalize("NFKD", text.casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    stopwords = value("STOPWORDS")
    words = []
    for word in WORD.findall(text):
        if word in stopwords or (len(word) < 3 and not word.isdigit()):
            continue
        if len(word) > 4 and word[-1] in "sx":
            word = word[:-1]
        words.append(word)
    return words


def rank(facts, query, k1=1.5, b=0.75):
    """Indexes of `facts`, most relevant to `query` first, then the rest from newest to oldest."""
    query_terms = set(terms(query))
    documents = [Counter(terms(fact)) for fact in facts]
    scores = {}
    if query_terms and documents:
        average = sum(sum(doc.values()) for doc in documents) / len(documents) or 1
        frequency = Counter(term for doc in documents for term in doc)
        for index, doc in enumerate(documents):
            length = sum(doc.values())
            score = 0.0
            for term in query_terms & doc.keys():
                idf = math.log(1 + (len(documents) - frequency[term] + .5) / (frequency[term] + .5))
                tf = doc[term]
                score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * length / average))
            if score > 0:
                scores[index] = score
    relevant = sorted(scores, key=lambda index: (-scores[index], -index))
    rest = [index for index in reversed(range(len(facts))) if index not in scores]
    return relevant + rest
