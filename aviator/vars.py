"""
AvIator — scoring vocabulary and regex indicators.

All signal lists and patterns the scorer keys off of live here so weights
(in scorer.py) stay separate from vocabulary (here). Add terms freely, but
re-run test_scorer.py afterwards — new words shift scores and can move
borderline cases across a tier boundary.
"""

import re

# Default tier → model-family mappings, written on init so the user has a
# working config they can edit. Families, not pinned versions (see ADR-02).
DEFAULT_TIER_MAPPINGS = {
    "claude_code": {"fast": "haiku", "balanced": "sonnet", "powerful": "opus"},
    "codex":       {"fast": "mini",  "balanced": "gpt",    "powerful": "codex"},
    "gemini_cli":  {"fast": "flash-lite", "balanced": "flash", "powerful": "pro"},
}

# ─────────────────────────────────────────────────────────────
#  1. Tunable hyperparameters
# ─────────────────────────────────────────────────────────────

# Tier thresholds — the single source of truth, imported by the selector
# and the calibration suite so they never drift out of sync.
FAST_MAX = 25
BALANCED_MAX = 64

# History weighting
RECENCY_DECAY = 0.8          # how fast older turns lose influence (0–1)
MAX_DEPTH_BONUS = 35         # cap on the raw conversation-length bonus
DEPTH_BONUS_PER_TURN = 5     # points added per history turn, before the cap

# Query bonuses
RELEVANCE_BONUS = 10         # flat bonus applied to the live query only

# Code-presence bonuses (applied to the current query)
CODE_BONUS_HEAVY = 20        # substantial code block
CODE_BONUS_SOME = 12         # some code syntax
CODE_BONUS_LIGHT = 6         # light code signals

CODE_SCORE_HEAVY = 15        # code_score thresholds the bonuses key off of
CODE_SCORE_SOME = 8
CODE_SCORE_LIGHT = 3

# Keyword signal weights
WEIGHT_STEP = 10
WEIGHT_REASONING = 15
WEIGHT_LOOKUP = 2            # subtracted
WEIGHT_FORMATTING = 3        # subtracted
WEIGHT_DOMAIN_DEPTH = 18
KEYWORD_SCORE_CAP = 40       # ceiling on net keyword contribution

# History code-session bonus
CODE_HEAVY_TURN_THRESHOLD = 8    # code_score above which a turn counts as "code-heavy"
CODE_HEAVY_MIN_TURNS = 2         # how many recent code-heavy turns trigger the bonus
CODE_HEAVY_BONUS_PER_TURN = 5    # points per code-heavy recent turn
HISTORY_RECENT_WINDOW = 4        # how many recent turns to inspect for code

# Hard-floor overrides
MULTIMODAL_FLOOR = 85            # multimodal queries can never score below this
ARCHITECTURE_SIGNAL_MIN = 2      # distinct arch signals needed to trigger the floor


# ---------------------------------------------------------
# 2. Regex-based code syntax indicators
# ---------------------------------------------------------
# Detects: braces/brackets/parens, operators, semicolons, comments,
# indentation, object.method / module.function access, and common
# language-specific delimiters.

CODE_INDICATORS = re.compile(
    r"""
    (
        [{}()\[\];=+\-*/<>!&|%^~]{2,}   # code symbols / operators (2+ in a row)
        |
        \b(?:\w+\.)+\w+\b               # object.method / module.function
        |
        (?:->|=>|::|:=|<-|\|>)          # language-specific operators
        |
        (?:/\*[\s\S]*?\*/)             # C-style multiline comments
        |
        (?://.*$)                       # C / C++ / Java / JS line comments
        |
        (?:\#.*$)                       # Python / bash comments
        |
        (?:</?\w+[^>]*>)                # HTML / XML / JSX tags
        |
        (?:\$\w+)                       # shell / php variables
        |
        (?:@\w+)                        # decorators / annotations
        |
        ^\s{2,}\S+                      # indentation signal
    )
    """,
    re.MULTILINE | re.VERBOSE,
)


# ---------------------------------------------------------
# 3. Programming keyword vocabulary
# ---------------------------------------------------------

CODE_KEYWORDS = {
    # Python
    "import", "from", "as", "def", "class", "return",
    "yield", "lambda", "try", "except", "finally",
    "with", "raise", "pass", "async", "await",
    "global", "nonlocal", "assert", "del", "in", "is", "not",

    # Java / C++ / C#
    "public", "private", "protected", "static",
    "void", "int", "float", "double", "boolean",
    "new", "extends", "implements", "abstract",
    "final", "const", "struct", "enum", "namespace",
    "template", "typename", "virtual", "override",

    # JavaScript / TypeScript
    "function", "let", "var",
    "export", "default", "require", "typeof",
    "instanceof", "interface", "type", "readonly",

    # Rust / Go
    "fn", "let", "mut", "impl", "trait", "match",
    "func", "package", "defer", "chan", "goroutine",

    # Control flow
    "if", "else", "elif",
    "for", "while", "do",
    "switch", "case",
    "break", "continue",
    "then", "when",

    # SQL
    "select", "insert", "update",
    "delete", "create", "table",
    "where", "join", "group", "order",
    "having", "limit", "distinct", "alter",
    "drop", "index", "foreign", "primary",

    # General / literals
    "true", "false", "null",
    "none", "undefined", "nil", "void",
}


# ---------------------------------------------------------
# 4. Formatting indicators
# ---------------------------------------------------------

CODE_FORMAT_INDICATORS = {
    "markdown_fence": r"```",
    "indentation": r"(?m)^\s{2,}\S+",
}


# ---------------------------------------------------------
# 5. Step / sequencing signals
# ---------------------------------------------------------

STEP_WORDS = [
    # explicit sequencing
    "then", "after that", "first", "finally", "step", "next",
    "once you", "before", "followed by", "in order to",
    "start by", "begin with", "end with", "finish by",
    "subsequently", "lastly", "to begin", "afterwards",
    "prior to", "leading up to",

    # numbered structure signals
    "step by step", "step-by-step", "walk me through",
    "take me through", "break it down", "break down",
    "one by one", "in sequence", "sequentially",
    "stage by stage", "phase by phase",

    # workflow / process language
    "workflow", "pipeline", "process", "procedure",
    "instructions", "guide me", "how do i", "how to",
    "implement", "set up", "configure", "provision",
    "migrate", "convert", "transform", "deploy",
    "orchestrate", "automate", "bootstrap", "scaffold",
    "wire up", "hook up", "integrate",
]


# ---------------------------------------------------------
# 6. Reasoning / analysis signals
# ---------------------------------------------------------

REASONING_WORDS = [
    # explanation depth
    "why", "explain", "elaborate", "describe in detail",
    "help me understand", "break down", "clarify", "unpack",
    "what does it mean", "what is the difference",
    "walk through the logic", "reason about", "make sense of",

    # comparison and evaluation
    "compare", "contrast", "versus", "vs", "pros and cons",
    "tradeoffs", "trade-offs", "which is better", "which should i",
    "evaluate", "assess", "weigh", "benchmark",
    "advantages", "disadvantages", "strengths and weaknesses",
    "cost benefit", "when to use",

    # design and architecture
    "design", "architect", "structure",
    "plan", "propose", "recommend", "suggest",
    "best approach", "best practice", "best way",
    "how would you", "how should i", "strategy for",
    "approach for", "pattern for",

    # analysis
    "analyze", "analyse", "review", "audit",
    "diagnose", "debug", "troubleshoot", "investigate",
    "identify", "find the issue", "what's wrong",
    "root cause", "why is this", "figure out why",
    "trace through", "reason why", "optimize", "refactor",

    # synthesis and creativity
    "generate", "create", "write", "draft", "come up with",
    "brainstorm", "ideate", "think through",
    "what if", "hypothetically", "imagine",
    "devise", "formulate", "construct", "model",
]


# ---------------------------------------------------------
# 7. Lookup / retrieval signals  (push toward cheaper models)
# ---------------------------------------------------------

LOOKUP_WORDS = [
    # definition requests
    "what is", "what are", "what does", "what's",
    "who is", "who are", "who was",
    "where is", "where are", "where was",
    "when did", "when was", "when is",
    "which is", "how do you say",
    "define", "definition", "meaning of",
    "what does it stand for", "stands for",

    # simple factual
    "tell me", "give me", "show me", "list",
    "name", "name a few", "give an example",
    "example of", "examples of",
    "how many", "how much", "how long", "how old",
    "what version", "what's the latest",
    "is there a", "does it exist",

    # yes/no framing
    "is it", "is this", "can you", "does it",
    "should i use", "do i need", "are there",
    "will it", "has it", "did it",

    # quick lookups
    "syntax for", "command for", "flag for",
    "shortcut for", "how do you spell",
    "what's the syntax", "remind me",
    "default for", "value of", "look up",
]


# ---------------------------------------------------------
# 8. Formatting / transformation signals  (push toward cheaper models)
# ---------------------------------------------------------

FORMATTING_WORDS = [
    # rewriting
    "format", "reformat", "restructure", "reorganize",
    "rewrite", "rephrase", "paraphrase", "clean up",
    "fix the formatting", "tidy", "tidy up", "proofread",
    "correct the grammar", "fix typos", "spell check",

    # condensing
    "summarize", "summarise", "tldr", "tl;dr",
    "shorten", "condense", "compress", "trim",
    "key points", "main points", "bullet points",
    "in a few words", "in one sentence", "briefly",
    "recap", "sum up", "gist of",

    # expanding
    "expand", "elaborate on", "add more detail",
    "flesh out", "make it longer", "extend",
    "pad out", "lengthen",

    # style changes
    "make it formal", "make it casual", "make it professional",
    "make it simpler", "simplify", "dumb it down",
    "make it friendlier", "make it concise",
    "make it sound like", "write it as",
    "change the tone", "adjust the tone",

    # translation / conversion
    "translate", "convert", "change to", "turn into",
    "transform into", "output as", "in json", "in yaml",
    "in markdown", "in plain english", "in layman's terms",
    "to csv", "to table", "as a table",

    # extraction
    "extract", "pull out", "find all", "list all",
    "get the", "grab the", "pluck", "isolate",
]


# ---------------------------------------------------------
# 9. Domain-depth signals  (high conceptual density / session memory)
# ---------------------------------------------------------

DOMAIN_DEPTH_SIGNALS = [
    # cross-referencing
    "correlate", "relate to", "connect to", "maps to",
    "consistent with", "does this match", "does this align",
    "tie together", "in relation to", "compared to what",

    # prior context references — model needs session memory
    "you already", "as we discussed", "from before",
    "earlier you", "previously", "last time",
    "like we said", "as established", "we agreed",
    "carrying on from", "picking up where",

    # multi-part explicit structure
    "deliverable", "part ",
    "constructor", "method", "step ",
    "first:", "second:", "third:",
    "now that we", "given what we", "building on",
    "section", "phase ", "module ", "component ",

    # validation asks — model must reason to a justified yes/no
    "is this correct", "does this make sense",
    "does this correlate", "is this right",
    "verify", "validate", "confirm",
    "sanity check", "double check", "is this accurate",
    "am i right that", "does this hold",
]


# ---------------------------------------------------------
# 10. Multimodal signals  (hard floor → powerful)
# ---------------------------------------------------------

MULTIMODAL_SIGNALS = [
    "in this image", "in the image", "this diagram",
    "this figure", "this chart", "this screenshot",
    "shown above", "shown below", "attached",
    "this photo", "this picture", "the graph",
    "this mockup", "this wireframe", "the attached",
    "from the image", "in the picture", "this drawing",
]


# ---------------------------------------------------------
# 11. Architecture signals  (2+ hits → floor at powerful)
# ---------------------------------------------------------

ARCHITECTURE_SIGNALS = [
    "design", "architect", "tradeoff", "trade-off",
    "scale to", "multi-tenant", "system design",
    "high availability", "fault tolerant", "distributed",
    "microservice", "load balance", "sharding",
    "horizontally scale", "throughput", "latency budget",
    "data model", "schema design", "capacity planning",
]