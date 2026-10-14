"""Har profile (general / lawyer / finance / student) ka system prompt aur quick-action ke prompts.

Profile ka naam, buttons aur disclaimer license se aate hain (Supabase license_profiles table, db/schema_v3.sql).
Yahan sirf wo likha hai jo model ko batana hai.
"""

COMMON = (
    "You are a careful assistant that works on documents the user has given you. "
    "Use ONLY the DOCUMENTS section for facts about the user's case, ledger or notes. "
    "Quote the file name and page like [file p.3] for every fact you take from it. "
    "If the answer is not in the documents, say clearly that it was not found. Never invent names, numbers, dates or laws. "
    "Reply in the language the user writes in (Hindi, English or Hinglish). Keep answers short and structured."
)

PROFILES = {
    "general": COMMON,
    "lawyer": COMMON + " You help an Indian advocate with drafting and analysis. You are not a lawyer and you do not give "
              "final legal advice. Mark every missing detail in a draft as [____] instead of guessing.",
    "finance": COMMON + " You help with ledgers and statements. Show the numbers you used. Do not give investment or tax advice.",
    "student": COMMON + " You help a student revise. Explain simply, use short points, and say when something is not in the notes.",
}

# Button ka naam (license ke actions se) -> model ko diya jaane wala kaam.
ACTIONS = {
    "Notice ka draft": "Using only the documents, draft a formal legal notice: parties, facts in order, the demand, and a deadline. "
                       "Mark every missing detail as [____].",
    "Bail application ka draft": "Using only the documents, draft a bail application: facts, grounds for bail, and prayer. "
                                 "Mark every missing detail as [____].",
    "Balance analysis": "Using only the ledger or statement text, work out the opening and closing balance, the biggest debits and "
                        "credits, and anything that does not add up. Show the numbers you used.",
    "Statement ka saar": "Summarise this statement: total credits, total debits, notable items, and anything unusual.",
    "Notes ka saar": "Summarise the notes into clear revision points, with the most important ideas first.",
    "Case aur ratios nikaalo": "List every case name, section, formula or ratio that appears in the documents, each with its page.",
    "Syllabus sync": "List which topics from the syllabus in the documents are covered by the notes, and which are missing.",
}


def system_prompt(profile: str) -> str:
    return PROFILES.get(profile, COMMON)


def is_upload_action(label: str) -> bool:
    """'Case PDF upload karo' jaise buttons PDF chunne ke liye hain, model ko kaam dene ke liye nahi."""
    return "upload" in label.lower()


def action_prompt(label: str) -> str | None:
    return ACTIONS.get(label)
