"""Rules that turn imported facts into suggested client service needs.

The importer stores many source-specific details exactly as they appear in
HMIS, HTH, JotForm, and eCW. This module is the translation layer that asks:
"Does this source detail imply a follow-up need?"

Keeping these rules together makes future additions easier. To support a new
source phrase or data-quality field, add it here instead of spreading matching
logic across profile and metrics code.
"""


HEALTH_CATEGORY_KEYWORDS = {
    "Behavioral Health": [
        "behavioral health",
        "mental health",
        "therapy",
        "therapist",
        "counseling",
        "counselling",
        "psychiatry",
        "psychiatric",
        "psych",
        "crisis",
        "trauma",
        "substance",
        "sud",
        "addiction",
    ],
    "Primary Care / Medical": [
        "healthcare",
        "health care",
        "medical",
        "primary care",
        "doctor",
        "clinic",
        "wellness",
        "physical",
        "sick",
        "illness",
        "pain",
        "injury",
        "encounter",
        "appointment",
    ],
    "Medication": [
        "medication",
        "medicine",
        "prescription",
        "pharmacy",
        "rx",
    ],
    "Insurance": [
        "insurance",
        "medicaid",
        "medicare",
        "coverage",
    ],
    "Dental": [
        "dental",
        "dentist",
        "teeth",
        "tooth",
    ],
    "Vision": [
        "vision",
        "eye",
        "glasses",
        "optical",
    ],
    "Appointment Follow-Up": [
        "follow-up",
        "follow up",
        "referral",
        "refer",
        "schedule",
        "appointment",
    ],
}


UNHOUSED_REASON_FIELDS = {
    "reasons_for_unhoused",
    "homelessness_primary_reason",
}


UNHOUSED_REASON_NEED_KEYWORDS = {
    "Housing stabilization": [
        "eviction",
        "eviction/foreclosure",
        "foreclosure",
        "lease",
        "landlord",
        "landlord/tenant",
        "tenant dispute",
        "rental issue",
        "rent",
        "housing",
        "affordable housing",
        "no where to go",
        "homeless",
        "unhoused",
        "shelter",
        "street",
        "couch",
        "doubled up",
        "relocation",
        "relocation/displacement",
        "displacement",
        "left her apartment",
        "house fire",
        "water damage",
        "voucher",
    ],
    "Domestic violence support": [
        "domestic violence",
        "dv",
        "dvorced",
        "emotional dv",
        "intimate partner",
        "violence",
        "abuse",
        "asbuse",
        "safety",
        "unsafe",
        "stalking",
        "trafficker",
        "traffi",
    ],
    "Income / employment support": [
        "job",
        "job/income loss",
        "employment",
        "income",
        "no income",
        "low income",
        "unemployed",
        "lost work",
        "layoff",
        "financial",
        "money",
        "wages",
    ],
    "Benefits navigation": [
        "benefits",
        "snap",
        "ssi",
        "ssdi",
        "medicaid",
        "insurance",
        "food stamps",
        "tanf",
    ],
    "Behavioral health support": [
        "mental",
        "health crisis (mental)",
        "behavioral",
        "depression",
        "anxiety",
        "trauma",
        "traumatic event",
        "substance",
        "substance use",
        "addiction",
        "sud",
    ],
    "Healthcare navigation": [
        "medical",
        "health",
        "health crisis",
        "health crisis (physical)",
        "family health crisis",
        "hospital",
        "illness",
        "injury",
        "doctor",
        "clinic",
        "pregnant",
        "disability",
        "automobile accident",
    ],
    "Family / mediation support": [
        "conflict with family/friends",
        "family",
        "relative",
        "parent",
        "child",
        "children",
        "loss of child",
        "loss of family",
        "death of family",
        "death of parents",
        "father passed",
        "mom passed",
        "partner died",
        "conflict",
        "relationship",
        "separation",
        "foster care",
    ],
    "Legal support": [
        "legal",
        "court",
        "warrant",
        "criminal",
        "record",
        "justice",
        "incarceration",
        "incar",
        "incar.",
        "re-entry",
        "jail",
    ],
    "Identity theft support": [
        "identity theft",
        "theft",
    ],
}


MISSING_VALUE_WORDS = {
    "missing",
    "not collected",
    "data not collected",
    "client doesn't know",
    "client does not know",
    "client refused",
    "refused",
    "prefers not to answer",
    "partial",
    "approximate",
    "unable",
}


NEGATIVE_VALUE_WORDS = {
    "no",
    "no (hud)",
    "false",
    "missing",
}


DATA_QUALITY_NEED_FIELDS = {
    "name_data_quality",
    "source_first_name",
    "source_last_name",
    "source_full_name",
    "source_name",
    "source_client_name",
    "ssn_data_quality",
    "dob_data_quality",
    "source_date_of_birth",
    "source_date_of_birth_alternate",
    "zip_code_data_quality",
    "birth_certificate_status",
    "social_security_card_status",
    "state_id_status",
    "health_insurance_status",
    "case_management_status",
    "income_any_source",
    "snap_food_stamps",
    "survivor_of_domestic_violence",
    "currently_fleeing_domestic_violence",
}


NEED_SIGNAL_FIELDS = DATA_QUALITY_NEED_FIELDS | UNHOUSED_REASON_FIELDS


HEALTH_GAP_NEEDS = {
    "Behavioral health support",
    "Healthcare navigation",
    "Health insurance navigation",
    "Benefits navigation",
}


def clean_text(value):
    """Return a lowercase comparable string for matching rules."""

    if value is None:
        return ""
    return str(value).strip().lower()


def value_has_any(value, words):
    """Return True when any configured word or phrase appears in a value."""

    text = clean_text(value)
    return bool(text) and any(word in text for word in words)


def value_is_negative(value):
    """Detect source answers that mean a client does not have something."""

    text = clean_text(value)
    return text in NEGATIVE_VALUE_WORDS or value_has_any(text, {"does not have", "do not have"})


def health_categories_for_value(value):
    """Return health categories implied by a service or healthcare detail value."""

    text = clean_text(value)
    if not text:
        return []

    categories = []
    for category, keywords in HEALTH_CATEGORY_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            categories.append(category)
    return categories


def infer_unhoused_reason_needs(value):
    """Infer service suggestions from why someone is currently unhoused."""

    text = clean_text(value)
    if not text:
        return []

    needs = []
    for need_label, keywords in UNHOUSED_REASON_NEED_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            needs.append({
                "label": need_label,
                "reason": f"Reason for being unhoused includes: {value}",
            })
    return needs


def infer_data_quality_needs(field_name, value):
    """Infer documentation and access needs from missing/quality source fields."""

    field = clean_text(field_name)
    text = clean_text(value)
    if not field or not text:
        return []

    needs = []

    if field in {"name_data_quality", "source_first_name", "source_last_name", "source_full_name", "source_name", "source_client_name"}:
        if value_has_any(text, MISSING_VALUE_WORDS):
            needs.append({
                "label": "Confirm legal name",
                "reason": f"Name quality/source value needs review: {value}",
            })

    if field == "ssn_data_quality" and value_has_any(text, MISSING_VALUE_WORDS):
        needs.append({
            "label": "ID / SSN documentation support",
            "reason": f"SSN data quality indicates missing or incomplete information: {value}",
        })

    if field in {"dob_data_quality", "source_date_of_birth", "source_date_of_birth_alternate"}:
        if value_has_any(text, MISSING_VALUE_WORDS):
            needs.append({
                "label": "Confirm date of birth",
                "reason": f"DOB quality/source value needs review: {value}",
            })

    if field == "zip_code_data_quality" and value_has_any(text, MISSING_VALUE_WORDS):
        needs.append({
            "label": "Confirm address / ZIP",
            "reason": f"ZIP data quality indicates missing or incomplete information: {value}",
        })

    if field == "birth_certificate_status" and value_is_negative(text):
        needs.append({
            "label": "Birth certificate support",
            "reason": f"HMIS says birth certificate status is: {value}",
        })

    if field == "social_security_card_status" and value_is_negative(text):
        needs.append({
            "label": "Social Security card support",
            "reason": f"HMIS says Social Security card status is: {value}",
        })

    if field == "state_id_status" and value_is_negative(text):
        needs.append({
            "label": "State ID support",
            "reason": f"HMIS says state-issued ID status is: {value}",
        })

    if field == "health_insurance_status" and value_is_negative(text):
        needs.append({
            "label": "Health insurance navigation",
            "reason": f"HMIS says health insurance status is: {value}",
        })

    if field == "case_management_status" and value_is_negative(text):
        needs.append({
            "label": "Case management engagement",
            "reason": f"HMIS says case management engagement is: {value}",
        })

    if field == "income_any_source" and value_is_negative(text):
        needs.append({
            "label": "Income / employment support",
            "reason": f"HMIS says income from any source is: {value}",
        })

    if field == "snap_food_stamps" and value_is_negative(text):
        needs.append({
            "label": "Benefits / SNAP follow-up",
            "reason": f"HMIS says SNAP/Food Stamps status is: {value}",
        })

    if field in {"survivor_of_domestic_violence", "currently_fleeing_domestic_violence"}:
        if text.startswith("yes") or text == "true":
            needs.append({
                "label": "Domestic violence support",
                "reason": f"HMIS domestic violence field says: {value}",
            })

    return needs


def infer_source_detail_needs(field_name, value):
    """Infer all suggested needs for one saved source detail."""

    field = clean_text(field_name)
    if field in UNHOUSED_REASON_FIELDS:
        return infer_unhoused_reason_needs(value)
    if field in DATA_QUALITY_NEED_FIELDS:
        return infer_data_quality_needs(field, value)
    return []
