"""Desk ke prompts aur buttons. Ek hi jagah: button ka naam, uska prompt, aur kis profile par kaunsa allowed hai.

v1 mein sirf lawyer (Sthamly Legal Desk) hai. general sirf developer test ke liye.
"""

COMMON = (
    "You are a careful assistant that works on documents the user has given you. "
    "Use ONLY the DOCUMENTS section for facts about the user's case or notes. "
    "Quote the file name and page like [file p.3] for every fact you take from it. "
    "If the answer is not in the documents, say clearly that it was not found. Never invent names, numbers, dates or laws. "
    "Reply in the language the user writes in (Hindi, English or Hinglish). Keep answers short and structured."
)

PROFILES = {
    "general": COMMON,
    "lawyer": COMMON + " You help an Indian advocate with drafting and analysis. You are not a lawyer and you do not give "
              "final legal advice. Mark every missing detail in a draft as [____] instead of guessing.",
}

# Ye button UI me PDF chunne ke liye hai, model ke liye nahi (UI isko client par hi sambhalta hai).
UPLOAD_LABEL = "Case PDF upload karo"

# key -> (button label, prompt)
ACTIONS = {
    "draft_notice": (
        "Notice ka draft",
        "Using only the documents, draft a formal legal notice: parties, facts in order, the demand, and a deadline. "
        "Mark every missing detail as [____].",
    ),
    "draft_bail": (
        "Bail application ka draft",
        "Using only the documents, draft a bail application: facts, grounds for bail, and prayer. "
        "Mark every missing detail as [____].",
    ),
    "extract_ratios": (
        "Case aur ratios nikaalo",
        "List every case name, section, formula or ratio that appears in the documents, each with its page.",
    ),
}

# Kis profile par kaunsa action allowed hai.
PROFILE_ACTIONS = {
    "lawyer": ["draft_notice", "draft_bail", "extract_ratios"],
    "general": [],
}


def canonical(action: str | None) -> str | None:
    """Button ka label ya key ('Notice ka draft' ya 'draft_notice') -> key. Sirf exact match, substring nahi."""
    if not action:
        return None
    text = str(action).strip().lower().replace("_", " ")
    for key, (label, _prompt) in ACTIONS.items():
        if text == key.replace("_", " ") or text == label.lower():
            return key
    return None


def action_prompt(action: str | None) -> str | None:
    key = canonical(action)
    return ACTIONS[key][1] if key else None


def allowed(profile: str | None, action: str | None) -> bool:
    key = canonical(action)
    return key is not None and key in PROFILE_ACTIONS.get(profile or "general", [])


def button_labels(profile: str | None) -> list[str]:
    keys = PROFILE_ACTIONS.get(profile or "", [])
    if not keys:
        return []
    return [UPLOAD_LABEL] + [ACTIONS[k][0] for k in keys]


def system_prompt(profile: str | None) -> str:
    return PROFILES.get(profile or "general", COMMON)
