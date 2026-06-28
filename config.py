import os
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_TOKEN        = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_ID       = int(os.getenv("ALLOWED_USER_ID", "0"))
HH_PHONE              = os.getenv("HH_PHONE", "")
OLLAMA_URL            = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL          = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
DB_PATH               = os.getenv("DB_PATH", "hh_bot.db")
SCAN_INTERVAL_HOURS   = int(os.getenv("SCAN_INTERVAL_HOURS", "2"))
MAX_VACANCIES         = int(os.getenv("MAX_VACANCIES_PER_SCAN", "15"))
DEFAULT_QUERY         = os.getenv("DEFAULT_SEARCH_QUERY", "Python разработчик")
SESSION_FILE          = os.getenv("SESSION_FILE", "hh_session.json")

WEB_HOST             = os.getenv("WEB_HOST", "0.0.0.0")
WEB_PORT             = int(os.getenv("WEB_PORT", "8080"))
MIN_MATCH_THRESHOLD  = float(os.getenv("MIN_MATCH_THRESHOLD", "0.65"))
HH_REGION            = os.getenv("HH_REGION", "1")
HH_SEARCH_PERIOD     = int(os.getenv("HH_SEARCH_PERIOD", "1"))
USE_LLM_FOR_KEYWORDS = os.getenv("USE_LLM_FOR_KEYWORDS", "true").lower() == "true"

# Эмбеддинги (семантический матчинг через Ollama)
EMBED_MODEL          = os.getenv("EMBED_MODEL", "bge-m3")
EMBED_SEM_THRESHOLD  = float(os.getenv("EMBED_SEM_THRESHOLD", "0.62"))
MATCH_WEIGHT_EXACT   = float(os.getenv("MATCH_WEIGHT_EXACT", "0.6"))
MATCH_WEIGHT_SEM     = float(os.getenv("MATCH_WEIGHT_SEM", "0.4"))

# Фильтр по требуемому опыту: вакансии, где минимальный стаж заметно больше,
# чем стаж в активном резюме, пропускаются полностью (не попадают в статистику).
EXPERIENCE_FILTER_ENABLED = os.getenv("EXPERIENCE_FILTER_ENABLED", "true").lower() in ("1", "true", "yes", "on")
EXPERIENCE_TOLERANCE_YEARS = float(os.getenv("EXPERIENCE_TOLERANCE_YEARS", "0.5"))

# Сопроводительное письмо: ollama | claude
COVER_LETTER_BACKEND = os.getenv("COVER_LETTER_BACKEND", "ollama").lower()
ANTHROPIC_API_KEY    = os.getenv("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL      = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
