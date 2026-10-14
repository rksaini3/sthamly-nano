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

# --- UNIFIED CHAMELEON BUTTON MAPPING MATRIX ---
# Supports both pure Hindi strings, standard UI labels, and database alphanumeric slugs seamlessly
ACTIONS = {
    # Lawyer Actions Mapping Matrix
    "notice ka draft": "Using only the documents, draft a formal legal notice: parties, facts in order, the demand, and a deadline. Mark every missing detail as [____].",
    "draft_notice": "Using only the documents, draft a formal legal notice: parties, facts in order, the demand, and a deadline. Mark every missing detail as [____].",
    "draft bail notice": "Using only the documents, draft a formal legal notice: parties, facts in order, the demand, and a deadline. Mark every missing detail as [____].",
    
    "bail application ka draft": "Using only the documents, draft a bail application: facts, grounds for bail, and prayer. Mark every missing detail as [____].",
    "draft_bail": "Using only the documents, draft a bail application: facts, grounds for bail, and prayer. Mark every missing detail as [____].",
    "draft bail/notices": "Using only the documents, draft a bail application: facts, grounds for bail, and prayer. Mark every missing detail as [____].",
    
    "case aur ratios nikaalo": "List every case name, section, formula or ratio that appears in the documents, each with its page.",
    "extract_ratios": "List every case name, section, formula or ratio that appears in the documents, each with its page.",
    "extract case ratios": "List every case name, section, formula or ratio that appears in the documents, each with its page.",

    # Finance Actions Mapping Matrix
    "balance analysis": "Using only the ledger or statement text, work out the opening and closing balance, the biggest debits and credits, and anything that does not add up. Show the numbers you used.",
    "run balance analysis": "Using only the ledger or statement text, work out the opening and closing balance, the biggest debits and credits, and anything that does not add up. Show the numbers you used.",
    "balance_analysis": "Using only the ledger or statement text, work out the opening and closing balance, the biggest debits and credits, and anything that does not add up. Show the numbers you used.",
    
    "statement ka saar": "Summarise this statement: total credits, total debits, notable items, and anything unusual.",
    "statement_summary": "Summarise this statement: total credits, total debits, notable items, and anything unusual.",

    # Student Actions Mapping Matrix
    "notes ka saar": "Summarise the notes into clear revision points, with the most important ideas first.",
    "syllabus sync": "List which topics from the syllabus in the documents are covered by the notes, and which are missing.",
    "syllabus_sync": "List which topics from the syllabus in the documents are covered by the notes, and which are missing.",
}


def system_prompt(profile: str) -> str:
    return PROFILES.get(profile, COMMON)


def is_upload_action(label: str) -> bool:
    """'Case PDF upload karo' jaise buttons PDF chunne ke liye hain, model ko kaam dene ke liye nahi."""
    label_clean = str(label).lower()
    return "upload" in label_clean or "sync" in label_clean


def action_prompt(label: str) -> str | None:
    """Normalizes incoming UI strings or keys to pull the accurate prompt template safely."""
    if not label:
        return None
    # Strips case, underscores and spaces to ensure a flexible handshake loop
    lookup = str(label).strip().lower().replace("_", " ")
    
    # Try looking up direct match, then normalized match
    if label in ACTIONS:
        return ACTIONS[label]
    if lookup in ACTIONS:
        return ACTIONS[lookup]
        
    # Final flexible substring matching fallback to secure UI variations
    for key, prompt in ACTIONS.items():
        if key in lookup or lookup in key:
            return prompt
            
    return None
