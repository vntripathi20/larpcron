"""Quote sources: a bundled local list, plus ZenQuotes (free, no key) when online."""
import random

import requests

LOCAL = [
    ("Discipline is choosing between what you want now and what you want most.", "Abraham Lincoln"),
    ("He who has a why to live can bear almost any how.", "Friedrich Nietzsche"),
    ("We suffer more often in imagination than in reality.", "Seneca"),
    ("You have power over your mind, not outside events. Realize this, and you will find strength.", "Marcus Aurelius"),
    ("Luck is what happens when preparation meets opportunity.", "Seneca"),
    ("The best revenge is massive success.", "Frank Sinatra"),
    ("Whether you think you can, or you think you can't, you're right.", "Henry Ford"),
    ("Don't watch the clock; do what it does. Keep going.", "Sam Levenson"),
    ("It's not the man who has too little, but the man who craves more, that is poor.", "Seneca"),
    ("The man who moves a mountain begins by carrying away small stones.", "Confucius"),
    ("Waste no more time arguing what a good man should be. Be one.", "Marcus Aurelius"),
    ("Success is walking from failure to failure with no loss of enthusiasm.", "Winston Churchill"),
    ("Do what you have to do until you can do what you want to do.", "Oprah Winfrey"),
    ("Fortune favors the bold.", "Virgil"),
    ("The obstacle is the way.", "Marcus Aurelius"),
    ("Opportunities multiply as they are seized.", "Sun Tzu"),
    ("Dream big. Work quietly. Let results make the noise.", "Unknown"),
    ("A lion doesn't concern himself with the opinion of sheep.", "Unknown"),
    ("Hard work beats talent when talent doesn't work hard.", "Tim Notke"),
    ("The cave you fear to enter holds the treasure you seek.", "Joseph Campbell"),
    ("Everything you've ever wanted is on the other side of fear.", "George Addair"),
    ("Wealth consists not in having great possessions, but in having few wants.", "Epictetus"),
    ("No man is free who is not master of himself.", "Epictetus"),
]


def _online() -> tuple[str, str]:
    r = requests.get("https://zenquotes.io/api/random", timeout=10)
    r.raise_for_status()
    item = r.json()[0]
    q, a = item["q"].strip(), item["a"].strip()
    if "too many requests" in q.lower() or len(q) > 180:
        raise ValueError("unusable quote")
    return q, a


def get_quote(source: str = "mixed") -> tuple[str, str]:
    if source == "online" or (source == "mixed" and random.random() < 0.5):
        try:
            return _online()
        except Exception:
            pass
    return random.choice(LOCAL)
